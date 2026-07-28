import json
import os
import threading
import unittest
from unittest.mock import patch
from argparse import ArgumentTypeError

from app import MonitorApplication, next_refresh_schedule, positive_refresh_seconds
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

    def clear_states_outside(self, active_assets) -> None:
        self.active_assets = active_assets
        return 0, 0


class AppRefreshTests(unittest.TestCase):
    def test_clears_states_not_in_a_complete_snapshot(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.notifier = None
        application._lock = threading.Lock()

        application.refresh()

        self.assertEqual(application.store.active_assets, set())

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

    def test_registers_bingx_and_lighter_oi_loaders(self) -> None:
        with patch.dict(os.environ, {"COINMARKETCAP_API_KEY": "test-key"}), patch(
            "app.SnapshotStore"
        ) as store_class, patch("app.fetch_bingx_open_interest") as bingx_loader, patch(
            "app.fetch_lighter_open_interest"
        ) as lighter_loader:
            store_class.return_value.load_latest_snapshot.return_value = None
            application = MonitorApplication()
            universe = {"ETH": object()}

            application.coordinator.venue_loaders["BingX"](universe)
            application.coordinator.venue_loaders["Lighter"](universe)

        self.assertIn("BingX", application.coordinator.venue_loaders)
        self.assertIn("Lighter", application.coordinator.venue_loaders)
        self.assertEqual(bingx_loader.call_args.args[0].timeout_seconds, 12)
        self.assertEqual(bingx_loader.call_args.args[1], {"ETH"})
        self.assertEqual(lighter_loader.call_args.args[1], {"ETH"})


class RefreshScheduleTests(unittest.TestCase):
    def test_waits_only_until_the_fixed_refresh_deadline(self) -> None:
        next_deadline, wait_seconds = next_refresh_schedule(
            previous_deadline=0,
            finished_at=90,
            interval_seconds=120,
        )

        self.assertEqual(next_deadline, 120)
        self.assertEqual(wait_seconds, 30)

    def test_starts_once_immediately_when_refresh_exceeds_its_interval(self) -> None:
        next_deadline, wait_seconds = next_refresh_schedule(
            previous_deadline=120,
            finished_at=250,
            interval_seconds=120,
        )

        self.assertEqual(next_deadline, 250)
        self.assertEqual(wait_seconds, 0)

    def test_resumes_the_interval_after_the_immediate_catch_up_refresh(self) -> None:
        next_deadline, wait_seconds = next_refresh_schedule(
            previous_deadline=250,
            finished_at=260,
            interval_seconds=120,
        )

        self.assertEqual(next_deadline, 370)
        self.assertEqual(wait_seconds, 110)


class RefreshIntervalValidationTests(unittest.TestCase):
    def test_accepts_a_positive_refresh_interval(self) -> None:
        self.assertEqual(positive_refresh_seconds("120"), 120)

    def test_rejects_zero_or_negative_refresh_intervals(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(ArgumentTypeError):
                positive_refresh_seconds(value)


if __name__ == "__main__":
    unittest.main()
