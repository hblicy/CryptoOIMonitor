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
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from crypto_oi_monitor.dispatch import dispatch_alerts
from crypto_oi_monitor.http_client import HttpJsonClient
from crypto_oi_monitor.market_caps import fetch_market_caps
from crypto_oi_monitor.notifier import WeComNotifier
from crypto_oi_monitor.refresh import RefreshCoordinator
from crypto_oi_monitor.sources import (
    fetch_aster_open_interest,
    fetch_binance_open_interest,
    fetch_binance_universe,
    fetch_bitget_open_interest,
    fetch_bybit_open_interest,
    fetch_gate_open_interest,
    fetch_hyperliquid_open_interest,
    fetch_kucoin_open_interest,
    fetch_mexc_open_interest,
    fetch_okx_open_interest,
)
from crypto_oi_monitor.storage import SnapshotStore

LOGGER = logging.getLogger("crypto_oi_monitor")


class MonitorApplication:
    def __init__(self) -> None:
        self.store = SnapshotStore(ROOT / "data" / "monitor.db")
        public_client = HttpJsonClient()
        cmc_api_key = os.environ["COINMARKETCAP_API_KEY"]
        cmc_client = HttpJsonClient({"X-CMC_PRO_API_KEY": cmc_api_key})
        self.coordinator = RefreshCoordinator(
            universe_loader=lambda: fetch_binance_universe(public_client),
            venue_loaders={
                "Binance": lambda universe: fetch_binance_open_interest(
                    public_client, universe
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
            },
            market_cap_loader=lambda assets: fetch_market_caps(cmc_client, assets),
            store=self.store,
            now=lambda: datetime.now(timezone.utc).isoformat(),
            persist=False,
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
            if self.notifier is None:
                snapshot["notification"] = {
                    "status": "not_configured",
                    "message": "WECOM_ROBOT_WEBHOOK_URL 未配置，企业微信关注提醒未启用。",
                }
            elif not snapshot["complete"]:
                snapshot["notification"] = {
                    "status": "suppressed",
                    "message": "数据源不完整，本轮不会推送企业微信关注提醒。",
                }
            else:
                try:
                    events = dispatch_alerts(snapshot, self.store, self.notifier)
                    snapshot["notification"] = {"status": "ok", "events": events}
                except Exception as error:
                    LOGGER.exception("企业微信推送失败")
                    snapshot["notification"] = {
                        "status": "error",
                        "message": f"{type(error).__name__}: {error}",
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


def start_refresh_loop(application: MonitorApplication, interval_seconds: int) -> threading.Event:
    stopped = threading.Event()

    def run() -> None:
        while not stopped.is_set():
            try:
                application.refresh()
            except Exception:
                LOGGER.exception("定时刷新发生未处理异常")
            stopped.wait(interval_seconds)

    threading.Thread(target=run, name="market-data-refresh", daemon=True).start()
    return stopped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--refresh-seconds", type=int, default=int(os.environ.get("REFRESH_SECONDS", "120"))
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    application = MonitorApplication()
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
