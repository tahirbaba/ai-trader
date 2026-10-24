"""Phase 2A: 1-hour soak test for market-data collectors.

Polls XRPUSDT and DOGEUSDT every 30 seconds for 1 hour using live
Bybit public endpoints, then prints a summary table.

Exit code 0 if error rate < 5%, else 1.

Usage:
    uv run python scripts/soak.py
    uv run python scripts/soak.py --duration 300  # 5-minute quick test
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time
from collections import defaultdict
from typing import Any

from tabulate import tabulate

from trader.data.bybit_rest import BybitRestClient, BybitRestError
from trader.data.bybit_ws import BybitWsClient
from trader.data.derivatives import DerivativesCollector
from trader.data.market import BookCollector, OHLCVCollector
from trader.data.orderflow import OrderFlowCollector
from trader.data.quality import DataQualityMonitor
from trader.data.session import format_for_display, tag_session
from trader.data.snapshot_builder import build_snapshot
from trader.ops.logging import configure_logging

configure_logging("INFO")

SYMBOLS = ["XRPUSDT", "DOGEUSDT"]
POLL_INTERVAL = 30  # seconds
DEFAULT_DURATION = 3600  # 1 hour

# Soak-test counters per symbol
_Counters = dict[str, int]


class SoakStats:
    def __init__(self) -> None:
        self.polls: dict[str, int] = defaultdict(int)
        self.success: dict[str, int] = defaultdict(int)
        self.rest_errors: dict[str, int] = defaultdict(int)
        self.quality_flags: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.ws_messages: int = 0
        self.ws_reconnects: int = 0

    def record_poll(self, symbol: str, success: bool, flags: list[str]) -> None:
        self.polls[symbol] += 1
        if success:
            self.success[symbol] += 1
        else:
            self.rest_errors[symbol] += 1
        for flag in flags:
            self.quality_flags[symbol][flag] += 1

    def error_rate(self) -> float:
        total_polls = sum(self.polls.values())
        total_errors = sum(self.rest_errors.values())
        if total_polls == 0:
            return 0.0
        return total_errors / total_polls

    def summary_table(self) -> str:
        rows = []
        for sym in SYMBOLS:
            polls = self.polls[sym]
            success = self.success[sym]
            errors = self.rest_errors[sym]
            err_pct = (errors / polls * 100) if polls else 0.0
            flags_str = (
                ", ".join(f"{k}={v}" for k, v in sorted(self.quality_flags[sym].items())) or "none"
            )
            rows.append([sym, polls, success, errors, f"{err_pct:.1f}%", flags_str])

        table = tabulate(
            rows,
            headers=["Symbol", "Polls", "Success", "Errors", "Error%", "Quality Flags"],
            tablefmt="grid",
        )
        table += f"\n\nWebSocket messages received : {self.ws_messages}"
        table += f"\nWebSocket reconnects        : {self.ws_reconnects}"
        return table


def _on_ws_message(msg: dict[str, Any], stats: SoakStats) -> None:
    topic = msg.get("topic", "")
    if topic:
        stats.ws_messages += 1


def run_soak(duration_seconds: int = DEFAULT_DURATION) -> bool:
    """Run the soak test. Returns True if error rate < 5%."""
    stats = SoakStats()
    quality_monitor = DataQualityMonitor()
    rest_client = BybitRestClient()

    # Start WebSocket client
    def _ws_callback(msg: dict[str, Any]) -> None:
        _on_ws_message(msg, stats)

    ws_client = BybitWsClient(SYMBOLS, _ws_callback)
    ws_client.start()
    print(f"Starting {duration_seconds}s soak test. Polling every {POLL_INTERVAL}s.", flush=True)
    print(f"Symbols: {', '.join(SYMBOLS)}", flush=True)
    print("-" * 60, flush=True)

    end_time = time.time() + duration_seconds
    price_history: dict[str, list[float]] = defaultdict(list)

    try:
        while time.time() < end_time:
            now_utc = datetime.datetime.utcnow()
            session = tag_session(now_utc)
            display_ts = format_for_display(now_utc)
            print(f"\n[{display_ts}]  session={session}", flush=True)

            for sym in SYMBOLS:
                success = True
                all_flags: list[str] = []

                # --- Collect all data ----------------------------------------
                ohlcv_result = None
                book_result = None
                orderflow_result = None
                deriv_result = None

                try:
                    ohlcv_result = OHLCVCollector(sym, rest_client).collect()
                    book_result = BookCollector(sym, rest_client).collect()
                    orderflow_result = OrderFlowCollector(sym, rest_client).collect()
                    deriv_result = DerivativesCollector(sym, rest_client).collect()
                except BybitRestError as exc:
                    print(f"  [{sym}] REST error: {exc}")
                    success = False

                # --- Accumulate quality flags --------------------------------
                for r in [ohlcv_result, book_result, orderflow_result, deriv_result]:
                    if r:
                        all_flags.extend(r.quality_flags)

                # --- Data quality check --------------------------------------
                latest_close: float | None = None
                if ohlcv_result and "5m" in ohlcv_result.data:
                    latest_close = float(ohlcv_result.data["5m"]["close"])
                if latest_close:
                    price_history[sym].append(latest_close)
                    price_history[sym] = price_history[sym][-50:]  # keep last 50

                ts_for_quality = ohlcv_result.ts if ohlcv_result else None
                quality_flags = quality_monitor.check(
                    symbol=sym,
                    latest_ts=ts_for_quality,
                    max_data_age_seconds=120,
                    price_history=price_history[sym],
                )
                all_flags.extend(quality_flags)

                # --- Build snapshot ----------------------------------------
                if success:
                    build_snapshot(
                        symbol=sym,
                        timeframe="5m",
                        ohlcv=ohlcv_result,
                        book=book_result,
                        orderflow=orderflow_result,
                        derivatives=deriv_result,
                        session=session,
                        quality_flags=quality_flags,
                    )

                stats.record_poll(sym, success, all_flags)

                close_str = f"{latest_close:.5f}" if latest_close else "N/A"
                flags_str = ", ".join(all_flags) if all_flags else "ok"
                print(f"  [{sym}]  close={close_str}  flags=[{flags_str}]", flush=True)

            # Update WS reconnect count
            stats.ws_reconnects = ws_client.reconnect_count

            remaining = end_time - time.time()
            if remaining > 0:
                sleep_time = min(POLL_INTERVAL, remaining)
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        ws_client.stop()
        rest_client.close()

    print("\n" + "=" * 60)
    print("SOAK TEST SUMMARY")
    print("=" * 60)
    print(stats.summary_table())

    error_rate = stats.error_rate()
    print(f"\nOverall error rate: {error_rate * 100:.1f}%")
    if error_rate < 0.05:
        print("PASS: error rate < 5%")
        return True
    else:
        print("FAIL: error rate >= 5%")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2A soak test")
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Test duration in seconds (default: {DEFAULT_DURATION})",
    )
    args = parser.parse_args()
    passed = run_soak(duration_seconds=args.duration)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
