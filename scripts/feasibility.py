#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "requests>=2.32",
#   "rich>=13.0",
# ]
# ///
"""
Phase 0 Feasibility Script
==========================
Queries public endpoints of Binance, Bybit, OKX, and MEXC to determine
whether USDT perpetual futures for BTC, ETH, SOL, XRP, DOGE are tradable
under the Phase 5 risk constraints.

Run with:
    uv run scripts/feasibility.py

No API keys required — public endpoints only.

Fix log (v2):
  - Bybit: min_notional = max(minOrderAmt, minNotionalValue, min_qty*price).
  - MEXC: min_notional = minVol * contractSize * price (not minVol * price).
  - MaxStop%: computed at planned_notional = MAX_MARGIN*MAX_LEVERAGE ($6),
    not at exchange minimum. This is the realistic stop budget for our position.
  - Binance: exponential backoff (2 retries) on timeout/connection errors.
  - OKX: marked "Re-run from VPS" when geo-restricted; results skipped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Hard-coded risk constants from BUILD_PLAN.md Phase 5 / config/risk.yaml
# ---------------------------------------------------------------------------
MAX_MARGIN_USD: float = 2.0
MAX_LOSS_USD: float = 1.0
SLIPPAGE_PCT: float = 0.05  # percent (i.e. 0.05% = 0.0005 as a fraction)
MAX_LEVERAGE: int = 3
LIQ_BUFFER_MULTIPLE: float = 2.0
MAINT_MARGIN_PCT: float = 0.5  # typical maintenance margin % at low leverage

# Derived: max notional we will ever put on = margin × leverage
MAX_NOTIONAL_USD: float = MAX_MARGIN_USD * MAX_LEVERAGE  # $6.00

# planned_notional: the notional we SIZE to when feasibility is positive.
# = min(max_margin * max_leverage, sized_notional) per BUILD_PLAN instructions.
# Since sized_notional = MAX_MARGIN * MAX_LEVERAGE (we use the full budget),
# planned_notional is always MAX_NOTIONAL_USD.  MaxStop% and LiqDist% are
# computed here so the stop-budget check is realistic, not artificially generous
# from a tiny exchange-minimum notional.
PLANNED_NOTIONAL_USD: float = MAX_NOTIONAL_USD  # $6.00

# Taker fees per exchange (percent)
TAKER_FEES: dict[str, float] = {
    "Binance": 0.040,
    "Bybit": 0.055,
    "OKX": 0.050,
    "MEXC": 0.010,
}

SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
TIMEOUT = 10  # seconds per HTTP request
RETRY_ATTEMPTS = 2  # extra attempts after the first for Binance

console = Console()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class ContractInfo:
    exchange: str
    symbol: str
    inst_type: str  # "USDT-PERP"
    price: float
    min_qty: float
    min_notional: float  # raw exchange floor (USD)
    lot_size: float
    tick_size: float
    atr_15m: float
    taker_fee_pct: float
    contract_size: float = 1.0  # base-asset units per contract (1.0 for spot-like)
    error: str = ""
    note: str = ""  # non-fatal notes (e.g. geo-restriction warnings)
    # ---- computed by compute_tradability() --------------------------------
    min_notional_usd: float = 0.0  # max(min_notional, min_qty*contract_size*price)
    max_notional_ok: bool = False  # min_notional_usd <= MAX_NOTIONAL_USD
    required_leverage: float = 0.0  # min_notional_usd / MAX_MARGIN (informational)
    planned_notional: float = 0.0  # PLANNED_NOTIONAL_USD (always $6 at 3x cap)
    round_trip_fee_usd: float = 0.0  # at planned_notional
    net_loss_budget: float = 0.0  # MAX_LOSS - round_trip_fee
    max_stop_pct: float = 0.0  # at planned_notional
    typical_stop_pct: float = 0.0  # ATR-based
    liq_distance_pct: float = 0.0  # at planned leverage (3x)
    tradable: bool = False


# ---------------------------------------------------------------------------
# Per-exchange parser functions (pure; no HTTP — used in tests)
# ---------------------------------------------------------------------------
def parse_binance_filters(sym_data: dict[str, Any]) -> dict[str, float]:
    """Return {min_qty, min_notional, lot_size, tick_size} from a Binance symbol dict."""
    result: dict[str, float] = {
        "min_qty": 0.0,
        "min_notional": 0.0,
        "lot_size": 0.0,
        "tick_size": 0.0,
    }
    for filt in sym_data.get("filters", []):
        ft = filt.get("filterType", "")
        if ft == "LOT_SIZE":
            result["min_qty"] = float(filt.get("minQty", 0))
            result["lot_size"] = float(filt.get("stepSize", 0))
        elif ft == "PRICE_FILTER":
            result["tick_size"] = float(filt.get("tickSize", 0))
        elif ft == "MIN_NOTIONAL":
            result["min_notional"] = float(filt.get("notional", 0))
    return result


def parse_bybit_lot_filter(item: dict[str, Any]) -> dict[str, float]:
    """Return {min_qty, min_notional, lot_size, tick_size} from a Bybit instrument dict.

    min_notional = max(minOrderAmt, minNotionalValue) so that whichever field
    Bybit populates is captured correctly.
    """
    lot = item.get("lotSizeFilter", {})
    price = item.get("priceFilter", {})
    return {
        "min_qty": float(lot.get("minOrderQty", 0)),
        "lot_size": float(lot.get("qtyStep", 0)),
        "tick_size": float(price.get("tickSize", 0)),
        # Fix: include both fields; take the larger (one may be zero)
        "min_notional": max(
            float(lot.get("minOrderAmt", 0)),
            float(lot.get("minNotionalValue", 0)),
        ),
    }


def parse_okx_instrument(item: dict[str, Any]) -> dict[str, float]:
    """Return {min_qty, min_notional, lot_size, tick_size, contract_size} from an OKX instrument dict.

    contract_size = ctVal (base-asset units per contract, e.g. 0.01 BTC).
    min_notional is left 0 here; compute_tradability derives it via
    min_qty * contract_size * price.
    """
    return {
        "min_qty": float(item.get("minSz", 0)),
        "lot_size": float(item.get("lotSz", 0)),
        "tick_size": float(item.get("tickSz", 0)),
        "min_notional": 0.0,  # derived from ctVal * price
        "contract_size": float(item.get("ctVal", 0)),
    }


def parse_mexc_detail(detail: dict[str, Any]) -> dict[str, float]:
    """Return {min_qty, min_notional, lot_size, tick_size, contract_size} from a MEXC detail dict.

    Fix: min_notional is left 0 here so that compute_tradability uses
    min_qty * contractSize * price — not the raw min_qty * price which would
    treat each contract as worth its full underlying price.
    """
    return {
        "min_qty": float(detail.get("minVol", 1)),
        "lot_size": float(detail.get("volUnit", 1)),
        "tick_size": float(detail.get("priceUnit", 0)),
        "min_notional": 0.0,  # derived from minVol * contractSize * price
        "contract_size": float(detail.get("contractSize", 1)),
    }


# ---------------------------------------------------------------------------
# ATR computation
# ---------------------------------------------------------------------------
def compute_atr(candles: list[dict[str, Any]]) -> float:
    """Compute ATR(14) from a list of candle dicts with open/high/low/close keys."""
    trs: list[float] = []
    for i, c in enumerate(candles[1:], 1):
        prev_close = float(candles[i - 1]["close"])
        high = float(c["high"])
        low = float(c["low"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-14:]) / min(14, len(trs)) if trs else 0.0


# ---------------------------------------------------------------------------
# Core feasibility computation (pure, no HTTP)
# ---------------------------------------------------------------------------
def compute_tradability(info: ContractInfo) -> ContractInfo:
    """Populate all derived fields on a ContractInfo and set the tradable flag.

    Key formula changes vs v1:
    - min_notional_usd uses contract_size so MEXC/OKX are correct.
    - planned_notional = PLANNED_NOTIONAL_USD ($6) — the notional we actually
      intend to trade, capped at MAX_MARGIN*MAX_LEVERAGE.  MaxStop% and
      LiqDist% are computed here to give a realistic, conservative check.
    """
    if info.error:
        return info

    # ---- Step 1: exchange-floor notional ----------------------------------
    # For MEXC/OKX: min_qty is in contracts; multiply by contract_size to get
    # base-asset units, then by price to get USD.
    info.min_notional_usd = max(
        info.min_notional,
        info.min_qty * info.contract_size * info.price,
    )

    # ---- Step 2: notional check -------------------------------------------
    info.max_notional_ok = info.min_notional_usd <= MAX_NOTIONAL_USD
    info.required_leverage = info.min_notional_usd / MAX_MARGIN_USD if MAX_MARGIN_USD > 0 else 999.0

    # ---- Step 3: stop budget at PLANNED notional --------------------------
    # We always plan to use MAX_MARGIN × MAX_LEVERAGE ($6) regardless of the
    # exchange floor.  This prevents artificially huge MaxStop% numbers when
    # the exchange minimum is very small (e.g. 1 DOGE = $0.08).
    info.planned_notional = PLANNED_NOTIONAL_USD  # $6.00

    info.round_trip_fee_usd = info.planned_notional * 2.0 * info.taker_fee_pct / 100.0
    info.net_loss_budget = MAX_LOSS_USD - info.round_trip_fee_usd

    if info.net_loss_budget > 0 and info.planned_notional > 0:
        info.max_stop_pct = (
            info.net_loss_budget / info.planned_notional - SLIPPAGE_PCT / 100.0
        ) * 100.0
    else:
        info.max_stop_pct = 0.0

    # ---- Step 4: typical stop from ATR ------------------------------------
    info.typical_stop_pct = (info.atr_15m / info.price * 100.0) if info.price > 0 else 0.0

    # ---- Step 5: liquidation distance at planned leverage (3x) ------------
    planned_lev = max(info.planned_notional / MAX_MARGIN_USD, 1.0)  # 3.0x
    info.liq_distance_pct = (1.0 / planned_lev - MAINT_MARGIN_PCT / 100.0) * 100.0

    # ---- Step 6: three tradability conditions -----------------------------
    cond1 = info.max_notional_ok
    cond2 = info.typical_stop_pct > 0 and info.typical_stop_pct <= info.max_stop_pct
    cond3 = info.liq_distance_pct >= LIQ_BUFFER_MULTIPLE * info.typical_stop_pct
    info.tradable = cond1 and cond2 and cond3

    return info


# ---------------------------------------------------------------------------
# HTTP helper: GET with exponential backoff
# ---------------------------------------------------------------------------
def _get(url: str, max_retries: int = 0, backoff: float = 3.0, **kwargs: Any) -> requests.Response:
    """GET with optional exponential backoff on transient network errors."""
    kwargs.setdefault("timeout", TIMEOUT)
    last_exc: Exception = RuntimeError("unreachable")
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < max_retries:
                sleep_s = backoff * (2**attempt)
                console.print(
                    f"  [yellow]retry {attempt + 1}/{max_retries} after {sleep_s:.0f}s "
                    f"({type(exc).__name__})[/yellow]"
                )
                time.sleep(sleep_s)
        except requests.HTTPError as exc:
            raise exc  # don't retry HTTP errors (4xx/5xx)
    raise last_exc


# ---------------------------------------------------------------------------
# Binance USDT Perp  (retry on timeout)
# ---------------------------------------------------------------------------
def fetch_binance(symbol: str) -> ContractInfo:
    sym = f"{symbol}USDT"
    info = ContractInfo(
        exchange="Binance",
        symbol=symbol,
        inst_type="USDT-PERP",
        price=0.0,
        min_qty=0.0,
        min_notional=0.0,
        lot_size=0.0,
        tick_size=0.0,
        atr_15m=0.0,
        taker_fee_pct=TAKER_FEES["Binance"],
    )
    try:
        r = _get(
            "https://fapi.binance.com/fapi/v1/exchangeInfo",
            max_retries=RETRY_ATTEMPTS,
        )
        data = r.json()
        sym_data = next(
            (
                s
                for s in data.get("symbols", [])
                if s["symbol"] == sym and s.get("contractType") == "PERPETUAL"
            ),
            None,
        )
        if sym_data is None:
            info.error = f"Symbol {sym} not found in exchangeInfo"
            return info

        parsed = parse_binance_filters(sym_data)
        info.min_qty = parsed["min_qty"]
        info.lot_size = parsed["lot_size"]
        info.tick_size = parsed["tick_size"]
        info.min_notional = parsed["min_notional"]

        r2 = _get(
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}",
            max_retries=RETRY_ATTEMPTS,
        )
        info.price = float(r2.json().get("price", 0))

        r3 = _get(
            f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&limit=20",
            max_retries=RETRY_ATTEMPTS,
        )
        candles = [
            {"open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            for c in r3.json()
            if len(c) >= 5
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# Bybit Linear (USDT Perp)  — fix: minNotionalValue included
# ---------------------------------------------------------------------------
def fetch_bybit(symbol: str) -> ContractInfo:
    sym = f"{symbol}USDT"
    info = ContractInfo(
        exchange="Bybit",
        symbol=symbol,
        inst_type="USDT-PERP",
        price=0.0,
        min_qty=0.0,
        min_notional=0.0,
        lot_size=0.0,
        tick_size=0.0,
        atr_15m=0.0,
        taker_fee_pct=TAKER_FEES["Bybit"],
    )
    try:
        r = _get(
            f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={sym}",
        )
        items = r.json().get("result", {}).get("list", [])
        if not items:
            info.error = f"Symbol {sym} not found"
            return info

        parsed = parse_bybit_lot_filter(items[0])
        info.min_qty = parsed["min_qty"]
        info.lot_size = parsed["lot_size"]
        info.tick_size = parsed["tick_size"]
        info.min_notional = parsed["min_notional"]

        r2 = _get(
            f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={sym}",
        )
        tickers = r2.json().get("result", {}).get("list", [])
        if tickers:
            info.price = float(tickers[0].get("lastPrice", 0))

        r3 = _get(
            f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=20",
        )
        raw = r3.json().get("result", {}).get("list", [])
        raw_sorted = list(reversed(raw))  # Bybit returns newest-first
        candles = [
            {"open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            for c in raw_sorted
            if len(c) >= 5
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# OKX USDT Perp (SWAP) — marked as "Re-run from VPS" on geo-block
# ---------------------------------------------------------------------------
def fetch_okx(symbol: str) -> ContractInfo:
    inst_id = f"{symbol}-USDT-SWAP"
    info = ContractInfo(
        exchange="OKX",
        symbol=symbol,
        inst_type="USDT-PERP",
        price=0.0,
        min_qty=0.0,
        min_notional=0.0,
        lot_size=0.0,
        tick_size=0.0,
        atr_15m=0.0,
        taker_fee_pct=TAKER_FEES["OKX"],
    )
    try:
        r = _get(
            f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst_id}",
        )
        items = r.json().get("data", [])
        if not items:
            info.error = f"Instrument {inst_id} not found"
            return info

        parsed = parse_okx_instrument(items[0])
        info.min_qty = parsed["min_qty"]
        info.lot_size = parsed["lot_size"]
        info.tick_size = parsed["tick_size"]
        info.min_notional = parsed["min_notional"]
        info.contract_size = parsed["contract_size"]

        r2 = _get(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}")
        tdata = r2.json().get("data", [])
        if tdata:
            info.price = float(tdata[0].get("last", 0))

        r3 = _get(
            f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=15m&limit=20",
        )
        raw = r3.json().get("data", [])
        raw_sorted = list(reversed(raw))  # OKX returns newest-first
        candles = [
            {"open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            for c in raw_sorted
            if len(c) >= 5
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        err_str = str(exc)
        # OKX geo-restricts some residential IPs; flag for VPS re-run
        if "Max retries" in err_str or "Connection" in err_str or "refused" in err_str:
            info.note = "Re-run from VPS (OKX geo-restricts residential IPs)"
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# MEXC Futures — fix: min_notional = minVol * contractSize * price
# ---------------------------------------------------------------------------
def fetch_mexc(symbol: str) -> ContractInfo:
    sym = f"{symbol}_USDT"
    info = ContractInfo(
        exchange="MEXC",
        symbol=symbol,
        inst_type="USDT-PERP",
        price=0.0,
        min_qty=0.0,
        min_notional=0.0,
        lot_size=0.0,
        tick_size=0.0,
        atr_15m=0.0,
        taker_fee_pct=TAKER_FEES["MEXC"],
    )
    try:
        r = _get(f"https://contract.mexc.com/api/v1/contract/detail?symbol={sym}")
        detail = r.json().get("data", {})
        if not detail:
            info.error = f"Symbol {sym} not found"
            return info

        parsed = parse_mexc_detail(detail)
        info.min_qty = parsed["min_qty"]
        info.lot_size = parsed["lot_size"]
        info.tick_size = parsed["tick_size"]
        info.min_notional = parsed["min_notional"]
        info.contract_size = parsed["contract_size"]

        r2 = _get(f"https://contract.mexc.com/api/v1/contract/ticker?symbol={sym}")
        tdata = r2.json().get("data", {})
        info.price = float(tdata.get("lastPrice", 0))

        now_ts = int(time.time())
        start_ts = now_ts - 7200
        r3 = _get(
            f"https://contract.mexc.com/api/v1/contract/kline/{sym}"
            f"?interval=Min15&start={start_ts}&end={now_ts}",
        )
        kdata = r3.json().get("data", {})
        times = kdata.get("time", [])
        opens = kdata.get("open", [])
        highs = kdata.get("high", [])
        lows = kdata.get("low", [])
        closes = kdata.get("close", [])
        # guard: use only indices valid in ALL arrays
        safe_len = min(len(times), len(opens), len(highs), len(lows), len(closes), 20)
        candles = [
            {
                "open": str(opens[i]),
                "high": str(highs[i]),
                "low": str(lows[i]),
                "close": str(closes[i]),
            }
            for i in range(safe_len)
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_all() -> list[ContractInfo]:
    results: list[ContractInfo] = []
    fetchers = [
        ("Binance", fetch_binance),
        ("Bybit", fetch_bybit),
        ("OKX", fetch_okx),
        ("MEXC", fetch_mexc),
    ]
    for exchange_name, fetcher in fetchers:
        for symbol in SYMBOLS:
            console.print(f"[dim]Fetching {exchange_name} {symbol}...[/dim]")
            result = fetcher(symbol)
            results.append(result)
    return results


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def print_table(results: list[ContractInfo]) -> None:
    table = Table(
        title=(
            f"Phase 0 Feasibility — MAX_MARGIN=${MAX_MARGIN_USD} "
            f"MAX_LEV={MAX_LEVERAGE}x → PLANNED_NOTIONAL=${PLANNED_NOTIONAL_USD:.2f}"
        ),
        show_lines=True,
    )
    table.add_column("Exchange", style="cyan", no_wrap=True)
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Price", justify="right")
    table.add_column("MinNotional", justify="right")
    table.add_column(f"≤${MAX_NOTIONAL_USD:.0f}?", justify="center")
    table.add_column("ReqLev", justify="right")
    table.add_column("TakerFee%", justify="right")
    table.add_column("MaxStop%", justify="right")
    table.add_column("TypStop%", justify="right")
    table.add_column("LiqDist%", justify="right")
    table.add_column("Tradable", justify="center")

    for r in results:
        if r.error:
            note = f" [{r.note}]" if r.note else ""
            table.add_row(
                r.exchange,
                r.symbol,
                "ERR",
                "ERR",
                "ERR",
                "ERR",
                "ERR",
                "ERR",
                "ERR",
                "ERR",
                f"[yellow]ERR{note}[/yellow]",
            )
            continue

        tradable_str = "[green]YES[/green]" if r.tradable else "[red]NO[/red]"
        max_not_str = "[green]YES[/green]" if r.max_notional_ok else "[red]NO[/red]"
        req_lev_str = f"{r.required_leverage:.1f}x"
        req_lev_col = (
            f"[green]{req_lev_str}[/green]"
            if r.required_leverage <= MAX_LEVERAGE
            else f"[red]{req_lev_str}[/red]"
        )

        table.add_row(
            r.exchange,
            r.symbol,
            f"${r.price:,.4f}" if r.price >= 1 else f"${r.price:.5f}",
            f"${r.min_notional_usd:.4f}",
            max_not_str,
            req_lev_col,
            f"{r.taker_fee_pct:.3f}%",
            f"{r.max_stop_pct:.2f}%" if r.max_stop_pct > 0 else "N/A",
            f"{r.typical_stop_pct:.3f}%",
            f"{r.liq_distance_pct:.2f}%",
            tradable_str,
        )

    console.print(table)
    console.print(
        f"[dim]MaxStop% and LiqDist% computed at planned_notional=${PLANNED_NOTIONAL_USD:.2f} "
        f"(MAX_MARGIN × MAX_LEV). MinNotional check uses exchange floor.[/dim]"
    )


def write_markdown_report(results: list[ContractInfo], output_path: Path) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append("# Phase 0 Feasibility Report\n")
    lines.append(f"Generated: {now} (v2 — fixed MEXC contractSize, Bybit minNotionalValue,\n")
    lines.append("MaxStop% at planned notional, Binance retry)\n\n")
    lines.append("## Risk Constants Used\n")
    lines.append(f"- `MAX_MARGIN_USD` = ${MAX_MARGIN_USD}\n")
    lines.append(f"- `MAX_LOSS_USD` = ${MAX_LOSS_USD}\n")
    lines.append(f"- `SLIPPAGE_PCT` = {SLIPPAGE_PCT}% (0.0005 as fraction)\n")
    lines.append(f"- `MAX_LEVERAGE` = {MAX_LEVERAGE}x\n")
    lines.append(f"- `LIQ_BUFFER_MULTIPLE` = {LIQ_BUFFER_MULTIPLE}\n")
    lines.append(f"- `MAINT_MARGIN_PCT` = {MAINT_MARGIN_PCT}%\n")
    lines.append(f"- `MAX_NOTIONAL_USD` = ${MAX_NOTIONAL_USD:.2f} (= margin × leverage)\n")
    lines.append(
        f"- `PLANNED_NOTIONAL_USD` = ${PLANNED_NOTIONAL_USD:.2f} "
        "(what we size to; MaxStop% and LiqDist% computed here)\n\n"
    )

    lines.append("## Summary Table\n\n")
    header = (
        "| Exchange | Symbol | Price | MinNotional | "
        f"≤${MAX_NOTIONAL_USD:.0f}? | ReqLev | TakerFee% | "
        "MaxStop%† | TypStop%(ATR) | LiqDist%† | Tradable |\n"
    )
    lines.append(header)
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|\n")

    for r in results:
        if r.error:
            note = f" {r.note}" if r.note else ""
            lines.append(
                f"| {r.exchange} | {r.symbol} | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR"
                f" | ERR: {r.error[:35]}{note} |\n"
            )
            continue
        lines.append(
            f"| {r.exchange} | {r.symbol}"
            f" | ${r.price:,.4f}"
            f" | ${r.min_notional_usd:.4f}"
            f" | {'YES' if r.max_notional_ok else 'NO'}"
            f" | {r.required_leverage:.1f}x"
            f" | {r.taker_fee_pct:.3f}%"
            f" | {r.max_stop_pct:.2f}%"
            f" | {r.typical_stop_pct:.3f}%"
            f" | {r.liq_distance_pct:.2f}%"
            f" | {'**YES**' if r.tradable else 'NO'} |\n"
        )

    lines.append(
        "\n† MaxStop% and LiqDist% computed at **planned_notional = "
        f"${PLANNED_NOTIONAL_USD:.2f}** (MAX_MARGIN × MAX_LEV), "
        "not at the exchange-minimum notional.\n\n"
    )

    lines.append("## Per-Row Math\n")
    for r in results:
        lines.append(f"\n### {r.exchange} — {r.symbol} ({r.inst_type})\n")
        if r.error:
            note = f"  Note: {r.note}" if r.note else ""
            lines.append(f"**ERROR:** {r.error}{note}\n")
            continue

        lines.append(f"- Price: ${r.price:,.6f}\n")
        lines.append(
            f"- min_qty={r.min_qty}, contract_size={r.contract_size}, "
            f"lot_size={r.lot_size}, tick_size={r.tick_size}\n"
        )
        lines.append(f"- Exchange min_notional (raw): ${r.min_notional:.4f}\n")
        lines.append(
            f"- **min_notional_usd** = max(${r.min_notional:.4f}, "
            f"{r.min_qty} × {r.contract_size} × ${r.price:.4f}) "
            f"= **${r.min_notional_usd:.4f}**\n"
        )
        lines.append(
            f"- max_notional_allowed = ${MAX_NOTIONAL_USD:.2f} → "
            f"fits? **{'YES' if r.max_notional_ok else 'NO'}**\n"
        )
        lines.append(
            f"- **required_leverage** = ${r.min_notional_usd:.4f} / ${MAX_MARGIN_USD} "
            f"= **{r.required_leverage:.2f}x** (cap: {MAX_LEVERAGE}x)\n"
        )
        lines.append(
            f"- **planned_notional** = ${PLANNED_NOTIONAL_USD:.2f} "
            f"(MAX_MARGIN × MAX_LEV — stop budget computed here)\n"
        )
        lines.append(
            f"- round_trip_fee = ${PLANNED_NOTIONAL_USD:.2f} × 2 × {r.taker_fee_pct:.3f}% "
            f"= **${r.round_trip_fee_usd:.5f}**\n"
        )
        lines.append(
            f"- net_loss_budget = ${MAX_LOSS_USD} − ${r.round_trip_fee_usd:.5f} "
            f"= **${r.net_loss_budget:.5f}**\n"
        )
        lines.append(
            f"- **max_stop_pct** = (${r.net_loss_budget:.5f} / ${PLANNED_NOTIONAL_USD:.2f} "
            f"− {SLIPPAGE_PCT / 100:.5f}) × 100 = **{r.max_stop_pct:.4f}%**\n"
        )
        lines.append(f"- ATR(15m) = {r.atr_15m:.6f}\n")
        lines.append(
            f"- **typical_stop_pct** = {r.atr_15m:.6f} / {r.price:.6f} × 100 "
            f"= **{r.typical_stop_pct:.4f}%**\n"
        )
        planned_lev = max(PLANNED_NOTIONAL_USD / MAX_MARGIN_USD, 1.0)
        lines.append(
            f"- planned_leverage = ${PLANNED_NOTIONAL_USD:.2f} / ${MAX_MARGIN_USD} "
            f"= {planned_lev:.1f}x\n"
        )
        lines.append(
            f"- **liq_distance_pct** = (1/{planned_lev:.1f} − {MAINT_MARGIN_PCT / 100:.4f}) × 100 "
            f"= **{r.liq_distance_pct:.4f}%**\n"
        )
        cond1 = r.max_notional_ok
        cond2 = r.typical_stop_pct > 0 and r.typical_stop_pct <= r.max_stop_pct
        cond3 = r.liq_distance_pct >= LIQ_BUFFER_MULTIPLE * r.typical_stop_pct
        lines.append(
            f"- Cond 1 (min_notional ≤ ${MAX_NOTIONAL_USD:.2f}): {'PASS' if cond1 else 'FAIL'}\n"
        )
        lines.append(
            f"- Cond 2 (typical_stop ≤ max_stop): {'PASS' if cond2 else 'FAIL'} "
            f"({r.typical_stop_pct:.4f}% vs {r.max_stop_pct:.4f}%)\n"
        )
        lines.append(
            f"- Cond 3 (liq_dist ≥ {LIQ_BUFFER_MULTIPLE}× typical_stop): "
            f"{'PASS' if cond3 else 'FAIL'} "
            f"({r.liq_distance_pct:.4f}% vs {LIQ_BUFFER_MULTIPLE * r.typical_stop_pct:.4f}%)\n"
        )
        lines.append(f"- **TRADABLE: {'YES' if r.tradable else 'NO'}**\n")

    # Tradable summary
    tradable = [r for r in results if r.tradable and not r.error]
    not_tradable = [r for r in results if not r.tradable and not r.error]
    errored = [r for r in results if r.error]

    lines.append("\n## Tradable Summary\n")
    lines.append(f"\n**Tradable ({len(tradable)}):**\n")
    if tradable:
        for r in tradable:
            lines.append(f"- {r.exchange} {r.symbol}\n")
    else:
        lines.append("- None under current risk constraints ($2 margin, 3x leverage)\n")

    lines.append(f"\n**Not tradable ({len(not_tradable)}):**\n")
    for r in not_tradable:
        reason = "conditions failed" if r.tradable is False else "unknown"
        lines.append(f"- {r.exchange} {r.symbol}: {reason}\n")

    if errored:
        lines.append(f"\n**Errors / re-run needed ({len(errored)}):**\n")
        for r in errored:
            note = f" — {r.note}" if r.note else ""
            lines.append(f"- {r.exchange} {r.symbol}: {r.error}{note}\n")

    lines.append("\n## Owner Action Required\n")
    lines.append(
        "1. Review the tradable/not-tradable table above.\n"
        "2. If no symbols are tradable, consider raising `max_margin_per_trade_usd` "
        "or `max_leverage` in `config/risk.yaml` (owner-only edit).\n"
        "3. Re-run this script from a VPS for OKX results.\n"
        "4. Choose exchange + symbols based on this report.\n"
        "5. See ADR-002 for cap semantics options.\n"
        "6. Confirm exchange availability in your country (see ADR-001).\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")
    console.print(f"\n[green]Markdown report written to:[/green] {output_path}")


def main() -> None:
    console.rule("[bold cyan]Phase 0 Feasibility Analysis (v2)[/bold cyan]")
    console.print(
        f"Risk: MAX_MARGIN=${MAX_MARGIN_USD}, MAX_LOSS=${MAX_LOSS_USD}, "
        f"MAX_LEV={MAX_LEVERAGE}x, PLANNED_NOTIONAL=${PLANNED_NOTIONAL_USD:.2f}\n"
        f"MaxStop% and LiqDist% computed at ${PLANNED_NOTIONAL_USD:.2f} "
        "(not at exchange-minimum notional)\n"
    )

    results = run_all()

    console.rule("[bold]Results[/bold]")
    print_table(results)

    tradable = [r for r in results if r.tradable and not r.error]
    not_tradable = [r for r in results if not r.tradable and not r.error]
    errors = [r for r in results if r.error]

    console.print(f"\n[bold green]Tradable:[/bold green] {len(tradable)}")
    for r in tradable:
        console.print(
            f"  [green]✓[/green] {r.exchange} {r.symbol} "
            f"(minNot=${r.min_notional_usd:.2f}, "
            f"reqLev={r.required_leverage:.1f}x, "
            f"maxStop={r.max_stop_pct:.2f}%, typStop={r.typical_stop_pct:.3f}%)"
        )

    console.print(f"\n[bold red]Not tradable:[/bold red] {len(not_tradable)}")
    for r in not_tradable:
        console.print(
            f"  [red]✗[/red] {r.exchange} {r.symbol} "
            f"(minNot=${r.min_notional_usd:.2f}, "
            f"reqLev={r.required_leverage:.1f}x, "
            f"maxStop={r.max_stop_pct:.2f}%, typStop={r.typical_stop_pct:.3f}%)"
        )

    if errors:
        console.print(f"\n[bold yellow]Errors / re-run needed:[/bold yellow] {len(errors)}")
        for r in errors:
            note = f" — {r.note}" if r.note else ""
            console.print(f"  [yellow]![/yellow] {r.exchange} {r.symbol}: {r.error}{note}")

    report_path = Path(__file__).parent.parent / "docs" / "specs" / "phase0-feasibility.md"
    write_markdown_report(results, report_path)

    console.rule()
    console.print(
        "\n[bold]Next steps:[/bold]\n"
        "1. Review [cyan]docs/specs/phase0-feasibility.md[/cyan]\n"
        "2. Re-run from VPS for OKX data\n"
        "3. Read ADR-001 through ADR-004\n"
        "4. Owner confirms: exchange + symbols + cap values + TradingView option\n"
    )


if __name__ == "__main__":
    main()
