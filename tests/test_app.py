import json
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, patch
from argparse import ArgumentTypeError

from app import MonitorApplication, next_refresh_schedule, positive_refresh_seconds
from crypto_oi_monitor.trade_dispatch import (
    TradeConditionScan,
    TradeConditionScanResult,
    TradeSignalEvent,
    TradeSignalDispatchFailure,
    TradeSignalDispatchResult,
)


class FakeCoordinator:
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot or {"complete": True, "comparisons": []}
        self.snapshot.setdefault("captured_at", "2026-08-07T00:15:00+00:00")

    def refresh(self):
        return self.snapshot


class FakeStore:
    def __init__(self) -> None:
        self.saved = []

    def save_snapshot(self, snapshot) -> None:
        self.saved.append(snapshot)

    def load_complete_snapshot_near(self, target, tolerance):
        self.reference_request = (target, tolerance)
        return None

    def clear_states_outside(self, active_assets) -> None:
        self.active_assets = active_assets
        return 0, 0

class TradeSignalOnlyNotifier:
    def send(self, event, comparison) -> None:
        raise AssertionError("不应发送 OI 埋伏候选提醒")


class AppRefreshTests(unittest.TestCase):
    def test_scans_conditions_when_wecom_is_not_configured(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.notifier = None
        application.trade_kline_loader = object()
        application._lock = threading.Lock()
        scan = TradeConditionScan(
            status="can_long",
            canonical_symbol="PEPE",
            candle_close_time=1_722_269_700_000,
            rsi=42.5,
            close=0.00001234,
            ema200=0.00001111,
            oi_to_market_cap=1.2,
        )

        with patch(
            "app.scan_trade_conditions",
            return_value=TradeConditionScanResult((scan,), ()),
        ):
            snapshot = application.refresh()

        self.assertEqual(snapshot["notification"]["status"], "not_configured")
        self.assertEqual(
            snapshot["notification"]["trade_condition_scans"], [scan.as_dict()]
        )

    def test_skips_ambush_notifications_and_dispatches_trade_signals(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator(
            {
                "complete": True,
                "comparisons": [
                    {
                        "canonical_symbol": "PEPE",
                        "oi_to_market_cap": 2.1,
                        "status": "high_risk",
                    }
                ],
            }
        )
        application.store = FakeStore()
        application.notifier = TradeSignalOnlyNotifier()
        application.trade_kline_loader = object()
        application._lock = threading.Lock()

        with patch(
            "app.dispatch_trade_signals",
            return_value=TradeSignalDispatchResult((), ()),
        ) as dispatch_trade_signals_mock, patch(
            "app.scan_trade_conditions",
            return_value=TradeConditionScanResult((), ()),
        ), patch(
            "app.dispatch_trade_condition_list",
            return_value="periodic",
        ):
            snapshot = application.refresh()

        self.assertEqual(snapshot["notification"]["status"], "ok")
        dispatch_trade_signals_mock.assert_called_once_with(
            snapshot,
            None,
            application.trade_kline_loader,
            application.store,
            application.notifier,
        )
        self.assertEqual(
            application.store.reference_request,
            (
                datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
                timedelta(minutes=5),
            ),
        )

    def test_clears_states_not_in_a_complete_snapshot(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.notifier = None
        application.trade_kline_loader = object()
        application._lock = threading.Lock()

        application.refresh()

        self.assertEqual(application.store.active_assets, set())

    def test_keeps_states_for_cmc_unmapped_assets_in_a_complete_snapshot(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator(
            {
                "complete": True,
                "comparisons": [
                    {"canonical_symbol": "PEPE", "oi_to_market_cap": 0.8}
                ],
                "unmapped_assets": ["AAA"],
            }
        )
        application.store = FakeStore()
        application.notifier = None
        application.trade_kline_loader = object()
        application._lock = threading.Lock()

        application.refresh()

        self.assertEqual(application.store.active_assets, {"PEPE", "AAA"})

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
            "app.dispatch_trade_signals", return_value=dispatch_result
        ), patch("app.dispatch_trade_condition_list") as dispatch_list_mock:
            snapshot = application.refresh()

        self.assertEqual(snapshot["notification"]["status"], "partial")
        self.assertEqual(snapshot["notification"]["trade_signal_events"], [])
        self.assertEqual(snapshot["notification"]["trade_signal_failures"][0]["canonical_symbol"], "NEW")
        self.assertEqual(snapshot["notification"]["trade_condition_list_status"], "suppressed")
        dispatch_list_mock.assert_not_called()
        self.assertIn("NEW", logs.output[0])
        json.dumps(snapshot)

    def test_exposes_trade_signal_details_to_the_web_summary(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.notifier = object()
        application.trade_kline_loader = object()
        application._lock = threading.Lock()
        detail = TradeSignalEvent(
            event_type="long",
            canonical_symbol="PEPE",
            candle_close_time=1_722_269_700_000,
            rsi=42.5,
            close=0.00001234,
            ema200=0.00001111,
            oi_to_market_cap=1.2,
            entry_price=0.00001234,
            stop_loss=0.00001000,
            atr=0.00000117,
        )
        scan = TradeConditionScan(
            status="can_long",
            canonical_symbol="PEPE",
            candle_close_time=1_722_269_700_000,
            rsi=42.5,
            close=0.00001234,
            ema200=0.00001111,
            oi_to_market_cap=1.2,
        )
        dispatch_result = TradeSignalDispatchResult(("long",), (), (detail,), (scan,))

        with patch("app.dispatch_trade_signals", return_value=dispatch_result), patch(
            "app.dispatch_trade_condition_list", return_value="updated"
        ):
            snapshot = application.refresh()

        self.assertEqual(
            snapshot["notification"]["trade_signal_details"],
            [detail.as_dict()],
        )
        self.assertEqual(
            snapshot["notification"]["trade_condition_scans"],
            [scan.as_dict()],
        )

    def test_pushes_condition_list_after_a_complete_condition_scan(self) -> None:
        application = MonitorApplication.__new__(MonitorApplication)
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.notifier = object()
        application.trade_kline_loader = object()
        application._lock = threading.Lock()
        scan = TradeConditionScan(
            status="can_long",
            canonical_symbol="BULLA",
            candle_close_time=1_722_269_700_000,
            rsi=42.5,
            close=0.00001234,
            ema200=0.00001111,
            oi_to_market_cap=1.2,
        )
        dispatch_result = TradeSignalDispatchResult((), (), (), (scan,))

        with patch("app.dispatch_trade_signals", return_value=dispatch_result), patch(
            "app.dispatch_trade_condition_list", return_value="updated"
        ) as dispatch_list_mock:
            snapshot = application.refresh()

        self.assertEqual(snapshot["notification"]["trade_condition_list_event"], "updated")
        dispatch_list_mock.assert_called_once_with(
            (scan,), application.store, application.notifier, ANY
        )

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
