import json
import threading
import unittest
from unittest.mock import patch

from app import MonitorApplication
from crypto_oi_monitor.trade_dispatch import (
    TradeSignalDispatchFailure,
    TradeSignalDispatchResult,
)


class FakeCoordinator:
    def refresh(self):
        return {"complete": True, "comparisons": []}


class FakeStore:
    def __init__(self) -> None:
        self.saved = []

    def save_snapshot(self, snapshot) -> None:
        self.saved.append(snapshot)


class AppRefreshTests(unittest.TestCase):
    def test_records_trade_signal_failures_without_breaking_snapshot_json(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.notifier = object()
        application.trade_kline_loader = object()
        application._lock = threading.Lock()
        dispatch_result = TradeSignalDispatchResult(
            (),
            (TradeSignalDispatchFailure("NEW", "仅获取到 200 根已收盘 15m K 线，需要至少 201 根"),),
        )

        with self.assertLogs("crypto_oi_monitor", level="WARNING") as logs, patch(
            "app.dispatch_alerts", return_value=[]
        ), patch("app.dispatch_trade_signals", return_value=dispatch_result):
            snapshot = application.refresh()

        self.assertEqual(snapshot["notification"]["status"], "partial")
        self.assertEqual(snapshot["notification"]["trade_signal_events"], [])
        self.assertEqual(snapshot["notification"]["trade_signal_failures"][0]["canonical_symbol"], "NEW")
        self.assertIn("NEW", logs.output[0])
        json.dumps(snapshot)


if __name__ == "__main__":
    unittest.main()
