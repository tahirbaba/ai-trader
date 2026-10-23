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
SLIPPAGE_PCT: float = 0.05  # percent (0.05%)
MAX_LEVERAGE: int = 3
LIQ_BUFFER_MULTIPLE: float = 2.0
MAINT_MARGIN_PCT: float = 0.5  # typical for low leverage on most exchanges

# max notional = margin × leverage
MAX_NOTIONAL_USD: float = MAX_MARGIN_USD * MAX_LEVERAGE  # $6.00

# Taker fees per exchange (percent)
TAKER_FEES: dict[str, float] = {
    "Binance": 0.040,
    "Bybit": 0.055,
    "OKX": 0.050,
    "MEXC": 0.010,
}

SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
TIMEOUT = 10  # seconds per request

console = Console()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class ContractInfo:
    exchange: str
    symbol: str
    inst_type: str  # "USDT-PERP" or "SPOT"
    price: float
    min_qty: float
    min_notional: float
    lot_size: float
    tick_size: float
    atr_15m: float
    taker_fee_pct: float
    error: str = ""
    # computed fields
    min_notional_usd: float = 0.0
    max_notional_ok: bool = False
    required_leverage: float = 0.0
    round_trip_fee_usd: float = 0.0
    net_loss_budget: float = 0.0
    max_stop_pct: float = 0.0
    typical_stop_pct: float = 0.0
    liq_distance_pct: float = 0.0
    tradable: bool = False


# ---------------------------------------------------------------------------
# ATR computation
# ---------------------------------------------------------------------------
def compute_atr(candles: list[dict[str, Any]]) -> float:
    """Compute ATR(14) from a list of candle dicts with open/high/low/close."""
    trs = []
    for i, c in enumerate(candles[1:], 1):
        prev_close = float(candles[i - 1]["close"])
        high = float(c["high"])
        low = float(c["low"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-14:]) / min(14, len(trs)) if trs else 0.0


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------
def compute_tradability(info: ContractInfo) -> ContractInfo:
    """Fill in all computed fields and set tradable flag."""
    if info.error:
        return info

    # min_notional_usd = max(exchange min_notional, min_qty × price)
    info.min_notional_usd = max(info.min_notional, info.min_qty * info.price)

    # max notional allowed = MAX_MARGIN × MAX_LEVERAGE = $6.00
    info.max_notional_ok = info.min_notional_usd <= MAX_NOTIONAL_USD

    # required leverage = min_notional / MAX_MARGIN
    info.required_leverage = info.min_notional_usd / MAX_MARGIN_USD if MAX_MARGIN_USD > 0 else 999

    # round-trip fee = notional × (taker + taker) / 100
    info.round_trip_fee_usd = info.min_notional_usd * (info.taker_fee_pct + info.taker_fee_pct) / 100

    # net loss budget = MAX_LOSS - fees
    info.net_loss_budget = MAX_LOSS_USD - info.round_trip_fee_usd

    # max stop pct = (net_loss_budget / min_notional - slippage_pct/100) × 100
    if info.min_notional_usd > 0 and info.net_loss_budget > 0:
        info.max_stop_pct = (
            info.net_loss_budget / info.min_notional_usd - SLIPPAGE_PCT / 100
        ) * 100
    else:
        info.max_stop_pct = 0.0

    # typical stop pct = ATR(15m) / price × 100
    info.typical_stop_pct = (info.atr_15m / info.price * 100) if info.price > 0 else 0.0

    # liq distance pct = (1/required_leverage - MAINT_MARGIN_PCT/100) × 100
    req_lev = max(info.required_leverage, 1.0)
    info.liq_distance_pct = (1.0 / req_lev - MAINT_MARGIN_PCT / 100) * 100

    # tradable = all three conditions met
    cond1 = info.max_notional_ok
    cond2 = info.typical_stop_pct > 0 and info.typical_stop_pct <= info.max_stop_pct
    cond3 = info.liq_distance_pct >= LIQ_BUFFER_MULTIPLE * info.typical_stop_pct
    info.tradable = cond1 and cond2 and cond3

    return info


# ---------------------------------------------------------------------------
# Binance USDT Perp
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
        # Exchange info
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        sym_data = next(
            (s for s in data.get("symbols", []) if s["symbol"] == sym and s.get("contractType") == "PERPETUAL"),
            None,
        )
        if sym_data is None:
            info.error = f"Symbol {sym} not found"
            return info

        for filt in sym_data.get("filters", []):
            if filt["filterType"] == "LOT_SIZE":
                info.min_qty = float(filt["minQty"])
                info.lot_size = float(filt["stepSize"])
            elif filt["filterType"] == "PRICE_FILTER":
                info.tick_size = float(filt["tickSize"])
            elif filt["filterType"] == "MIN_NOTIONAL":
                info.min_notional = float(filt.get("notional", 0))

        # Price
        r2 = requests.get(
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}",
            timeout=TIMEOUT,
        )
        r2.raise_for_status()
        info.price = float(r2.json()["price"])

        # Klines for ATR
        r3 = requests.get(
            f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&limit=20",
            timeout=TIMEOUT,
        )
        r3.raise_for_status()
        raw_candles = r3.json()
        candles = [
            {"open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            for c in raw_candles
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# Bybit Linear (USDT Perp)
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
        # Instrument info
        r = requests.get(
            f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={sym}",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("result", {}).get("list", [])
        if not items:
            info.error = f"Symbol {sym} not found"
            return info
        item = items[0]
        lot_filter = item.get("lotSizeFilter", {})
        price_filter = item.get("priceFilter", {})
        info.min_qty = float(lot_filter.get("minOrderQty", 0))
        info.lot_size = float(lot_filter.get("qtyStep", 0))
        info.tick_size = float(price_filter.get("tickSize", 0))
        info.min_notional = float(lot_filter.get("minOrderAmt", 0))

        # Ticker
        r2 = requests.get(
            f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={sym}",
            timeout=TIMEOUT,
        )
        r2.raise_for_status()
        tickers = r2.json().get("result", {}).get("list", [])
        if tickers:
            info.price = float(tickers[0].get("lastPrice", 0))

        # Klines
        r3 = requests.get(
            f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=20",
            timeout=TIMEOUT,
        )
        r3.raise_for_status()
        raw = r3.json().get("result", {}).get("list", [])
        # Bybit returns newest first: [ts, open, high, low, close, vol, turnover]
        raw_sorted = list(reversed(raw))
        candles = [
            {"open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            for c in raw_sorted
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# OKX USDT Perp (SWAP)
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
        # Instrument info
        r = requests.get(
            f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst_id}",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("data", [])
        if not items:
            info.error = f"Instrument {inst_id} not found"
            return info
        item = items[0]
        # OKX: lotSz=lot_size (in contracts), minSz=min_qty, tickSz, ctVal=contract_value (USD per contract)
        lot_sz = float(item.get("lotSz", 0))
        min_sz = float(item.get("minSz", 0))
        ct_val = float(item.get("ctVal", 0))  # contract face value in base asset
        tick_sz = float(item.get("tickSz", 0))

        info.lot_size = lot_sz
        info.min_qty = min_sz  # in contracts
        info.tick_size = tick_sz

        # Ticker
        r2 = requests.get(
            f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
            timeout=TIMEOUT,
        )
        r2.raise_for_status()
        tdata = r2.json().get("data", [])
        if tdata:
            info.price = float(tdata[0].get("last", 0))

        # min notional = min_sz (contracts) × ct_val (base/contract) × price
        # ct_val is in base asset (e.g., 0.01 BTC per contract)
        if info.price > 0 and ct_val > 0:
            info.min_notional = min_sz * ct_val * info.price
        else:
            info.min_notional = 0.0

        # Candles
        r3 = requests.get(
            f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=15m&limit=20",
            timeout=TIMEOUT,
        )
        r3.raise_for_status()
        raw = r3.json().get("data", [])
        # OKX returns newest first: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        raw_sorted = list(reversed(raw))
        candles = [
            {"open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            for c in raw_sorted
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# MEXC Futures (contract symbol: BTC_USDT)
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
        # Contract detail
        r = requests.get(
            f"https://contract.mexc.com/api/v1/contract/detail?symbol={sym}",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        detail = data.get("data", {})
        if not detail:
            info.error = f"Symbol {sym} not found"
            return info

        # MEXC: minVol=min_qty (in contracts), contractSize=contract face value
        info.min_qty = float(detail.get("minVol", 1))
        contract_size = float(detail.get("contractSize", 1))  # e.g., 0.0001 BTC per contract
        info.lot_size = float(detail.get("volUnit", 1))
        info.tick_size = float(detail.get("priceUnit", 0))

        # Ticker
        r2 = requests.get(
            f"https://contract.mexc.com/api/v1/contract/ticker?symbol={sym}",
            timeout=TIMEOUT,
        )
        r2.raise_for_status()
        tdata = r2.json().get("data", {})
        info.price = float(tdata.get("lastPrice", 0))

        # min notional = min_qty (contracts) × contract_size (base/contract) × price
        if info.price > 0 and contract_size > 0:
            info.min_notional = info.min_qty * contract_size * info.price

        # Klines: use last 2-hour window
        now_ts = int(time.time())
        start_ts = now_ts - 7200  # 2 hours ago
        r3 = requests.get(
            f"https://contract.mexc.com/api/v1/contract/kline/{sym}?interval=Min15&start={start_ts}&end={now_ts}",
            timeout=TIMEOUT,
        )
        r3.raise_for_status()
        kdata = r3.json().get("data", {})
        # MEXC kline data: {time:[], open:[], high:[], low:[], close:[], ...}
        times = kdata.get("time", [])
        opens = kdata.get("open", [])
        highs = kdata.get("high", [])
        lows = kdata.get("low", [])
        closes = kdata.get("close", [])
        candles = [
            {"open": str(opens[i]), "high": str(highs[i]), "low": str(lows[i]), "close": str(closes[i])}
            for i in range(min(len(times), 20))
        ]
        info.atr_15m = compute_atr(candles)

    except Exception as exc:
        info.error = str(exc)[:80]

    return compute_tradability(info)


# ---------------------------------------------------------------------------
# Main
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


def print_table(results: list[ContractInfo]) -> None:
    table = Table(
        title=f"Phase 0 Feasibility — MAX_MARGIN=${MAX_MARGIN_USD} MAX_LEV={MAX_LEVERAGE}x → MAX_NOTIONAL=${MAX_NOTIONAL_USD:.2f}",
        show_lines=True,
    )
    table.add_column("Exchange", style="cyan", no_wrap=True)
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Price", justify="right")
    table.add_column("MinNotional", justify="right")
    table.add_column("MinQty", justify="right")
    table.add_column("LotSize", justify="right")
    table.add_column(f"MaxNot≤${MAX_NOTIONAL_USD:.0f}?", justify="center")
    table.add_column("ReqLev", justify="right")
    table.add_column("TakerFee%", justify="right")
    table.add_column("MaxStop%", justify="right")
    table.add_column("TypStop%(ATR)", justify="right")
    table.add_column("LiqDist%", justify="right")
    table.add_column("Tradable", justify="center")

    for r in results:
        if r.error:
            table.add_row(
                r.exchange, r.symbol, r.inst_type,
                "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR",
                f"[red]ERROR: {r.error[:30]}[/red]",
            )
            continue

        tradable_str = "[green]YES[/green]" if r.tradable else "[red]NO[/red]"
        max_not_str = "[green]YES[/green]" if r.max_notional_ok else "[red]NO[/red]"
        req_lev_str = f"{r.required_leverage:.1f}x"
        req_lev_colored = f"[green]{req_lev_str}[/green]" if r.required_leverage <= MAX_LEVERAGE else f"[red]{req_lev_str}[/red]"

        table.add_row(
            r.exchange,
            r.symbol,
            r.inst_type,
            f"${r.price:,.2f}" if r.price >= 1 else f"${r.price:.5f}",
            f"${r.min_notional_usd:.4f}",
            f"{r.min_qty}",
            f"{r.lot_size}",
            max_not_str,
            req_lev_colored,
            f"{r.taker_fee_pct:.3f}%",
            f"{r.max_stop_pct:.3f}%" if r.max_stop_pct > 0 else "N/A",
            f"{r.typical_stop_pct:.3f}%" if r.typical_stop_pct > 0 else "N/A",
            f"{r.liq_distance_pct:.2f}%",
            tradable_str,
        )

    console.print(table)


def write_markdown_report(results: list[ContractInfo], output_path: Path) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append("# Phase 0 Feasibility Report\n")
    lines.append(f"Generated: {now}\n")
    lines.append("\n## Risk Constants Used\n")
    lines.append(f"- `MAX_MARGIN_USD` = ${MAX_MARGIN_USD}\n")
    lines.append(f"- `MAX_LOSS_USD` = ${MAX_LOSS_USD}\n")
    lines.append(f"- `SLIPPAGE_PCT` = {SLIPPAGE_PCT}%\n")
    lines.append(f"- `MAX_LEVERAGE` = {MAX_LEVERAGE}x\n")
    lines.append(f"- `LIQ_BUFFER_MULTIPLE` = {LIQ_BUFFER_MULTIPLE}\n")
    lines.append(f"- `MAINT_MARGIN_PCT` = {MAINT_MARGIN_PCT}%\n")
    lines.append(f"- `MAX_NOTIONAL_USD` = ${MAX_NOTIONAL_USD:.2f} (= margin × leverage)\n")
    lines.append("\n## Summary Table\n")
    lines.append(
        "| Exchange | Symbol | Type | Price | MinNotional | MinQty | LotSize"
        f" | MaxNot≤${MAX_NOTIONAL_USD:.0f}? | ReqLev | TakerFee% | MaxStop% | TypStop%(ATR) | LiqDist% | Tradable |\n"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")

    for r in results:
        if r.error:
            lines.append(
                f"| {r.exchange} | {r.symbol} | {r.inst_type} | ERROR | ERROR | ERROR | ERROR"
                f" | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR | ERROR: {r.error[:40]} |\n"
            )
            continue
        lines.append(
            f"| {r.exchange} | {r.symbol} | {r.inst_type}"
            f" | ${r.price:,.4f}"
            f" | ${r.min_notional_usd:.4f}"
            f" | {r.min_qty}"
            f" | {r.lot_size}"
            f" | {'YES' if r.max_notional_ok else 'NO'}"
            f" | {r.required_leverage:.1f}x"
            f" | {r.taker_fee_pct:.3f}%"
            f" | {r.max_stop_pct:.3f}%"
            f" | {r.typical_stop_pct:.3f}%"
            f" | {r.liq_distance_pct:.2f}%"
            f" | {'**YES**' if r.tradable else 'NO'} |\n"
        )

    lines.append("\n## Per-Row Math\n")
    for r in results:
        lines.append(f"\n### {r.exchange} — {r.symbol} ({r.inst_type})\n")
        if r.error:
            lines.append(f"**ERROR:** {r.error}\n")
            continue
        lines.append(f"- Price: ${r.price:,.6f}\n")
        lines.append(f"- min_qty: {r.min_qty}, lot_size: {r.lot_size}, tick_size: {r.tick_size}\n")
        lines.append(f"- Exchange min_notional: ${r.min_notional:.4f}\n")
        lines.append(f"- **min_notional_usd** = max(${r.min_notional:.4f}, {r.min_qty} × ${r.price:.4f}) = **${r.min_notional_usd:.4f}**\n")
        lines.append(f"- max_notional_allowed = ${MAX_NOTIONAL_USD:.2f} → fits? **{'YES' if r.max_notional_ok else 'NO'}**\n")
        lines.append(f"- **required_leverage** = ${r.min_notional_usd:.4f} / ${MAX_MARGIN_USD} = **{r.required_leverage:.2f}x** (cap: {MAX_LEVERAGE}x)\n")
        lines.append(f"- round_trip_fee_usd = ${r.min_notional_usd:.4f} × 2 × {r.taker_fee_pct:.3f}% = **${r.round_trip_fee_usd:.5f}**\n")
        lines.append(f"- net_loss_budget = ${MAX_LOSS_USD} - ${r.round_trip_fee_usd:.5f} = **${r.net_loss_budget:.5f}**\n")
        if r.min_notional_usd > 0 and r.net_loss_budget > 0:
            lines.append(f"- **max_stop_pct** = (${r.net_loss_budget:.5f} / ${r.min_notional_usd:.4f} - {SLIPPAGE_PCT/100:.5f}) × 100 = **{r.max_stop_pct:.4f}%**\n")
        else:
            lines.append("- max_stop_pct: N/A (net_loss_budget ≤ 0)\n")
        lines.append(f"- ATR(15m): {r.atr_15m:.6f}\n")
        lines.append(f"- **typical_stop_pct** = {r.atr_15m:.6f} / {r.price:.6f} × 100 = **{r.typical_stop_pct:.4f}%**\n")
        req_lev_safe = max(r.required_leverage, 1.0)
        lines.append(f"- **liq_distance_pct** = (1/{req_lev_safe:.2f} - {MAINT_MARGIN_PCT/100:.4f}) × 100 = **{r.liq_distance_pct:.4f}%**\n")
        lines.append(f"- Condition 1 (min_notional ≤ max_notional): {'PASS' if r.max_notional_ok else 'FAIL'}\n")
        cond2 = r.typical_stop_pct > 0 and r.typical_stop_pct <= r.max_stop_pct
        cond3 = r.liq_distance_pct >= LIQ_BUFFER_MULTIPLE * r.typical_stop_pct
        lines.append(f"- Condition 2 (typical_stop ≤ max_stop): {'PASS' if cond2 else 'FAIL'} ({r.typical_stop_pct:.4f}% vs {r.max_stop_pct:.4f}%)\n")
        lines.append(f"- Condition 3 (liq_dist ≥ {LIQ_BUFFER_MULTIPLE}× typical_stop): {'PASS' if cond3 else 'FAIL'} ({r.liq_distance_pct:.4f}% vs {LIQ_BUFFER_MULTIPLE * r.typical_stop_pct:.4f}%)\n")
        lines.append(f"- **TRADABLE: {'YES' if r.tradable else 'NO'}**\n")

    lines.append("\n## Tradable Summary\n")
    tradable = [(r.exchange, r.symbol) for r in results if r.tradable and not r.error]
    not_tradable = [(r.exchange, r.symbol, r.error or "conditions failed") for r in results if not r.tradable and not r.error]
    errored = [(r.exchange, r.symbol, r.error) for r in results if r.error]

    lines.append(f"\n**Tradable ({len(tradable)}):**\n")
    if tradable:
        for exc, sym in tradable:
            lines.append(f"- {exc} {sym}\n")
    else:
        lines.append("- None under current risk constraints ($2 margin, 3x leverage)\n")

    lines.append(f"\n**Not tradable ({len(not_tradable)}):**\n")
    for exc, sym, reason in not_tradable:
        lines.append(f"- {exc} {sym}: {reason}\n")

    if errored:
        lines.append(f"\n**Errors ({len(errored)}):**\n")
        for exc, sym, err in errored:
            lines.append(f"- {exc} {sym}: {err}\n")

    lines.append("\n## Owner Action Required\n")
    lines.append(
        "1. Review the tradable/not-tradable table above.\n"
        "2. If no symbols are tradable, consider raising `max_margin_per_trade_usd` or `max_leverage` in `config/risk.yaml`.\n"
        "3. Choose exchange + symbols based on this report.\n"
        "4. See ADR-002 for cap semantics options.\n"
        "5. Confirm exchange availability in your country (see ADR-001).\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")
    console.print(f"\n[green]Markdown report written to:[/green] {output_path}")


def main() -> None:
    console.rule("[bold cyan]Phase 0 Feasibility Analysis[/bold cyan]")
    console.print(
        f"Risk constants: MAX_MARGIN=${MAX_MARGIN_USD}, MAX_LOSS=${MAX_LOSS_USD}, "
        f"MAX_LEV={MAX_LEVERAGE}x, MAX_NOTIONAL=${MAX_NOTIONAL_USD:.2f}\n"
    )

    results = run_all()

    console.rule("[bold]Results[/bold]")
    print_table(results)

    # Tradable summary
    tradable = [r for r in results if r.tradable and not r.error]
    not_tradable = [r for r in results if not r.tradable and not r.error]
    errors = [r for r in results if r.error]

    console.print(f"\n[bold green]Tradable:[/bold green] {len(tradable)}")
    for r in tradable:
        console.print(f"  [green]✓[/green] {r.exchange} {r.symbol}")

    console.print(f"\n[bold red]Not tradable:[/bold red] {len(not_tradable)}")
    for r in not_tradable:
        console.print(
            f"  [red]✗[/red] {r.exchange} {r.symbol} "
            f"(reqLev={r.required_leverage:.1f}x, maxStop={r.max_stop_pct:.3f}%, typStop={r.typical_stop_pct:.3f}%)"
        )

    if errors:
        console.print(f"\n[bold yellow]Errors:[/bold yellow] {len(errors)}")
        for r in errors:
            console.print(f"  [yellow]![/yellow] {r.exchange} {r.symbol}: {r.error}")

    # Write markdown report
    report_path = Path(__file__).parent.parent / "docs" / "specs" / "phase0-feasibility.md"
    write_markdown_report(results, report_path)

    console.rule()
    console.print(
        "\n[bold]Next steps:[/bold]\n"
        "1. Review [cyan]docs/specs/phase0-feasibility.md[/cyan]\n"
        "2. Read ADR-001 (exchange), ADR-002 (cap semantics), ADR-003 (economics), ADR-004 (TradingView)\n"
        "3. Confirm: exchange + symbols + cap values + TradingView option\n"
        "4. Owner approves Phase 0 → Phase 1 begins\n"
    )


if __name__ == "__main__":
    main()
