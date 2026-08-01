from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import sys
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from crypto_oi_monitor.http_client import HttpJsonClient
from crypto_oi_monitor.market_caps import CachedMarketCapLoader, parse_cmc_id_overrides
from crypto_oi_monitor.notifier import WeComNotifier
from crypto_oi_monitor.refresh import RefreshCoordinator
from crypto_oi_monitor.sources import (
    BINGX_REQUEST_TIMEOUT_SECONDS,
    fetch_aster_open_interest,
    fetch_binance_open_interest,
    fetch_binance_universe,
    fetch_bingx_open_interest,
    fetch_bitget_open_interest,
    fetch_bybit_open_interest,
    fetch_gate_open_interest,
    fetch_hyperliquid_open_interest,
    fetch_kucoin_open_interest,
    fetch_lighter_open_interest,
    fetch_mexc_open_interest,
    fetch_okx_open_interest,
)
from crypto_oi_monitor.storage import SnapshotStore
from crypto_oi_monitor.trade_dispatch import (
    TradeConditionScanResult,
    dispatch_trade_condition_list,
    dispatch_trade_signals,
    scan_trade_conditions,
)
from crypto_oi_monitor.trading import fetch_binance_closed_candles

LOGGER = logging.getLogger("crypto_oi_monitor")


def _condition_scan_payload(result: TradeConditionScanResult) -> dict[str, Any]:
    for failure in result.failures:
        LOGGER.warning(
            "交易条件扫描已跳过 %s：%s",
            failure.canonical_symbol,
            failure.message,
        )
    return {
        "trade_condition_scans": [scan.as_dict() for scan in result.scans],
        "trade_condition_scan_failures": [
            {
                "canonical_symbol": failure.canonical_symbol,
                "message": failure.message,
            }
            for failure in result.failures
        ],
    }


class MonitorApplication:
    def __init__(
        self,
        cmc_refresh_seconds: int = 600,
        snapshot_retention_days: int = 30,
        min_free_disk_bytes: int = 2 * 1024**3,
    ) -> None:
        self.store = SnapshotStore(
            ROOT / "data" / "monitor.db",
            snapshot_retention_days,
            min_free_disk_bytes,
        )
        public_client = HttpJsonClient()
        bingx_client = HttpJsonClient(timeout_seconds=BINGX_REQUEST_TIMEOUT_SECONDS)
        cmc_api_key = os.environ["COINMARKETCAP_API_KEY"]
        cmc_client = HttpJsonClient({"X-CMC_PRO_API_KEY": cmc_api_key})
        market_cap_loader = CachedMarketCapLoader(
            cmc_client,
            cmc_refresh_seconds,
            id_overrides=parse_cmc_id_overrides(os.environ.get("CMC_ID_OVERRIDES")),
        )
        self.coordinator = RefreshCoordinator(
            universe_loader=lambda: fetch_binance_universe(public_client),
            venue_loaders={
                "Binance": lambda universe: fetch_binance_open_interest(
                    public_client, universe
                ),
                "BingX": lambda universe: fetch_bingx_open_interest(
                    bingx_client, set(universe)
                ),
                "OKX": lambda universe: fetch_okx_open_interest(
                    public_client, set(universe)
                ),
                "Bybit": lambda universe: fetch_bybit_open_interest(
                    public_client, set(universe)
                ),
                "Bitget": lambda universe: fetch_bitget_open_interest(
                    public_client, set(universe)
                ),
                "Gate": lambda universe: fetch_gate_open_interest(
                    public_client, set(universe)
                ),
                "KuCoin": lambda universe: fetch_kucoin_open_interest(
                    public_client, set(universe)
                ),
                "MEXC": lambda universe: fetch_mexc_open_interest(
                    public_client, set(universe)
                ),
                "Hyperliquid": lambda universe: fetch_hyperliquid_open_interest(
                    public_client, set(universe)
                ),
                "Aster": lambda universe: fetch_aster_open_interest(
                    public_client, set(universe)
                ),
                "Lighter": lambda universe: fetch_lighter_open_interest(
                    public_client, set(universe)
                ),
            },
            market_cap_loader=lambda universe: market_cap_loader(
                set(universe),
                {
                    asset: instrument.last_price
                    for asset, instrument in universe.items()
                    if instrument.last_price is not None
                },
            ),
            store=self.store,
            now=lambda: datetime.now(timezone.utc).isoformat(),
            persist=False,
        )
        self.trade_kline_loader = lambda symbol: fetch_binance_closed_candles(
            public_client, symbol
        )
        webhook_url = os.environ.get("WECOM_ROBOT_WEBHOOK_URL")
        self.notifier = (
            WeComNotifier(webhook_url, HttpJsonClient()) if webhook_url else None
        )
        self._lock = threading.Lock()
        self._latest = self.store.load_latest_snapshot()

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self.coordinator.refresh()
            if snapshot["complete"]:
                active_assets = {
                    comparison["canonical_symbol"]
                    for comparison in snapshot["comparisons"]
                } | set(snapshot.get("unmapped_assets", []))
                removed_alerts, removed_trade_signals = self.store.clear_states_outside(
                    active_assets
                )
                removed_condition_list_symbols = (
                    self.store.clear_trade_condition_list_state_outside(active_assets)
                )
                if (
                    removed_alerts
                    or removed_trade_signals
                    or removed_condition_list_symbols
                ):
                    LOGGER.info(
                        "清除不在当前比较范围内的状态：%s 个关注提醒，%s 个交易信号，%s 个交易条件列表币种",
                        removed_alerts,
                        removed_trade_signals,
                        removed_condition_list_symbols,
                    )
            if self.notifier is None:
                snapshot["notification"] = {
                    "status": "not_configured",
                    "message": "WECOM_ROBOT_WEBHOOK_URL 未配置，企业微信推送未启用。",
                    **_condition_scan_payload(
                        scan_trade_conditions(snapshot, self.trade_kline_loader)
                    ),
                }
            elif not snapshot["complete"]:
                snapshot["notification"] = {
                    "status": "suppressed",
                    "message": "数据源不完整，本轮不会推送企业微信消息。",
                }
            else:
                try:
                    trade_result = dispatch_trade_signals(
                        snapshot,
                        self.trade_kline_loader,
                        self.store,
                        self.notifier,
                    )
                except Exception as error:
                    LOGGER.exception("交易信号推送失败")
                    snapshot["notification"] = {
                        "status": "partial",
                        "trade_signal_status": "error",
                        "message": f"交易信号失败：{type(error).__name__}: {error}",
                        **_condition_scan_payload(
                            scan_trade_conditions(snapshot, self.trade_kline_loader)
                        ),
                    }
                else:
                    if trade_result.failures:
                        for failure in trade_result.failures:
                            LOGGER.warning(
                                "交易信号已跳过 %s：%s",
                                failure.canonical_symbol,
                                failure.message,
                            )
                        snapshot["notification"] = {
                            "status": "partial",
                            "trade_signal_events": list(trade_result.events),
                            "trade_signal_details": [
                                detail.as_dict() for detail in trade_result.details
                            ],
                            "trade_condition_scans": [
                                scan.as_dict() for scan in trade_result.scans
                            ],
                            "trade_signal_failures": [
                                {
                                    "canonical_symbol": failure.canonical_symbol,
                                    "message": failure.message,
                                }
                                for failure in trade_result.failures
                            ],
                            "trade_condition_list_status": "suppressed",
                            "message": "交易信号部分失败："
                            + "；".join(
                                f"{failure.canonical_symbol}: {failure.message}"
                                for failure in trade_result.failures
                            ),
                        }
                    else:
                        try:
                            condition_list_event = dispatch_trade_condition_list(
                                trade_result.scans,
                                self.store,
                                self.notifier,
                                datetime.now(timezone.utc),
                            )
                        except Exception as error:
                            LOGGER.exception("交易条件列表推送失败")
                            snapshot["notification"] = {
                                "status": "partial",
                                "trade_signal_events": list(trade_result.events),
                                "trade_signal_details": [
                                    detail.as_dict() for detail in trade_result.details
                                ],
                                "trade_condition_scans": [
                                    scan.as_dict() for scan in trade_result.scans
                                ],
                                "trade_condition_list_status": "error",
                                "message": f"交易条件列表失败：{type(error).__name__}: {error}",
                            }
                        else:
                            snapshot["notification"] = {
                                "status": "ok",
                                "trade_signal_events": list(trade_result.events),
                                "trade_signal_details": [
                                    detail.as_dict() for detail in trade_result.details
                                ],
                                "trade_condition_scans": [
                                    scan.as_dict() for scan in trade_result.scans
                                ],
                                "trade_condition_list_event": condition_list_event,
                            }
            self.store.save_snapshot(snapshot)
            self._latest = snapshot
            return snapshot

    def summary(self) -> dict[str, Any]:
        return self._latest or {
            "state": "waiting_for_first_refresh",
            "message": "等待首次数据刷新。",
        }


class MonitorHandler(BaseHTTPRequestHandler):
    application: MonitorApplication
    static_root: Path

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/summary":
            self._send_json(HTTPStatus.OK, self.application.summary())
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/refresh":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            self._send_json(HTTPStatus.OK, self.application.refresh())
        except Exception as error:
            LOGGER.exception("手动刷新失败")
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{type(error).__name__}: {error}"},
            )

    def _serve_static(self, request_path: str) -> None:
        relative_path = request_path.lstrip("/") or "index.html"
        candidate = (self.static_root / relative_path).resolve()
        try:
            candidate.relative_to(self.static_root.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            candidate = self.static_root / "index.html"
        if not candidate.is_file():
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "前端尚未构建，请在 frontend 目录执行 npm run build。"},
            )
            return
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(content)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)


def next_refresh_schedule(
    previous_deadline: float, finished_at: float, interval_seconds: int
) -> tuple[float, float]:
    next_deadline = max(previous_deadline + interval_seconds, finished_at)
    return next_deadline, max(0.0, next_deadline - finished_at)


def positive_refresh_seconds(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("refresh seconds must be greater than zero")
    return seconds


def start_refresh_loop(application: MonitorApplication, interval_seconds: int) -> threading.Event:
    stopped = threading.Event()

    def run() -> None:
        next_deadline = monotonic()
        while not stopped.is_set():
            try:
                application.refresh()
            except Exception:
                LOGGER.exception("定时刷新发生未处理异常")
            next_deadline, wait_seconds = next_refresh_schedule(
                next_deadline, monotonic(), interval_seconds
            )
            stopped.wait(wait_seconds)

    threading.Thread(target=run, name="market-data-refresh", daemon=True).start()
    return stopped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--refresh-seconds",
        type=positive_refresh_seconds,
        default=os.environ.get("REFRESH_SECONDS", "120"),
    )
    parser.add_argument(
        "--cmc-refresh-seconds",
        type=positive_refresh_seconds,
        default=os.environ.get("CMC_REFRESH_SECONDS", "600"),
    )
    parser.add_argument(
        "--snapshot-retention-days",
        type=positive_refresh_seconds,
        default=os.environ.get("SNAPSHOT_RETENTION_DAYS", "30"),
    )
    parser.add_argument(
        "--min-free-disk-gb",
        type=positive_refresh_seconds,
        default=os.environ.get("MIN_FREE_DISK_GB", "2"),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    application = MonitorApplication(
        args.cmc_refresh_seconds,
        args.snapshot_retention_days,
        int(args.min_free_disk_gb * 1024**3),
    )
    stopped = start_refresh_loop(application, args.refresh_seconds)
    handler = type(
        "ConfiguredMonitorHandler",
        (MonitorHandler,),
        {"application": application, "static_root": ROOT / "frontend" / "dist"},
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    try:
        server.serve_forever()
    finally:
        stopped.set()
        server.server_close()


if __name__ == "__main__":
    main()
