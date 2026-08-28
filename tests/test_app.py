import json
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, patch
from argparse import ArgumentTypeError

from app import (
    configure_logging,
    encode_json_payload,
    ManualRefreshRejected,
    MonitorApplication,
    next_refresh_schedule,
    non_negative_usd_amount,
    positive_refresh_seconds,
    required_environment_value,
)
from crypto_oi_monitor.trade_dispatch import (
    EXIT_LONG,
    NO_ADD,
    REENTRY_COOLDOWN,
    TradeConditionScan,
    TradeConditionScanResult,
    TradeSignalEvent,
    TradeSignalDispatchFailure,
    TradeSignalDispatchResult,
    TradeSignalState,
)
from crypto_oi_monitor.trading import LONG


class UsdAmountArgumentTests(unittest.TestCase):
    def test_parses_plain_and_suffixed_usd_amounts(self) -> None:
        expected = {
            "0": 0,
            "5000000": 5_000_000,
            "5M": 5_000_000,
            "7.5m": 7_500_000,
            "12K": 12_000,
            "0.02B": 20_000_000,
        }
        for value, amount in expected.items():
            with self.subTest(value=value):
                self.assertEqual(non_negative_usd_amount(value), amount)

    def test_rejects_invalid_usd_amounts(self) -> None:
        for value in ("-1", "NaN", "inf", "5T", "5 M", "5,000,000", "", "1e6"):
            with self.subTest(value=value):
                with self.assertRaises(ArgumentTypeError):
                    non_negative_usd_amount(value)


class FakeCoordinator:
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot or {"complete": True, "comparisons": []}
        self.snapshot.setdefault("captured_at", "2026-08-07T00:15:00+00:00")

    def refresh(self):
        return self.snapshot


class FakeStore:
    def __init__(self) -> None:
        self.saved = []
        self.trade_states = {}

    def save_snapshot(self, snapshot) -> None:
        self.saved.append(snapshot)

    def load_complete_snapshot_near(self, target, tolerance):
        self.reference_request = (target, tolerance)
        return None

    def clear_states_outside(self, active_assets) -> None:
        self.active_assets = active_assets
        return 0, 0

    def list_trade_signal_states(self):
        return dict(self.trade_states)

class TradeSignalOnlyNotifier:
    def send(self, event, comparison) -> None:
        raise AssertionError("不应发送 OI 埋伏候选提醒")


def new_application(binance_min_turnover_usd: float = 5_000_000) -> MonitorApplication:
    application = MonitorApplication.__new__(MonitorApplication)
    application.binance_min_turnover_usd = binance_min_turnover_usd
    return application


class AppRefreshTests(unittest.TestCase):
    def test_incomplete_snapshot_still_dispatches_active_risk_signals(self) -> None:
        application = new_application()
        application.coordinator = FakeCoordinator(
            {"complete": False, "comparisons": [], "unmapped_assets": ["PEPE"]}
        )
        application.store = FakeStore()
        application.notifier = TradeSignalOnlyNotifier()
        application.trade_kline_loader = object()
        application._lock = threading.Lock()
        exit_event = TradeSignalEvent(
            event_type=EXIT_LONG,
            canonical_symbol="PEPE",
            candle_close_time=1,
            rsi=40,
            close=90,
            ema200=100,
            oi_to_market_cap=None,
            reasons=("atr_stop_loss",),
        )

        with patch(
            "app.dispatch_trade_signals",
            return_value=TradeSignalDispatchResult(
                (EXIT_LONG,), (), (exit_event,), ()
            ),
        ) as dispatch_mock, patch(
            "app.dispatch_trade_condition_list"
        ) as dispatch_list_mock:
            snapshot = application.refresh()

        dispatch_mock.assert_called_once()
        dispatch_list_mock.assert_not_called()
        self.assertEqual(
            snapshot["notification"]["trade_signal_events"], [EXIT_LONG]
        )
        self.assertEqual(
            snapshot["notification"]["trade_condition_list_status"], "suppressed"
        )

    def test_scans_conditions_when_wecom_is_not_configured(self) -> None:
        application = new_application()
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
        application = new_application()
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
        application = new_application()
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.notifier = None
        application.trade_kline_loader = object()
        application._lock = threading.Lock()

        application.refresh()

        self.assertEqual(application.store.active_assets, set())

    def test_keeps_states_for_cmc_unmapped_assets_in_a_complete_snapshot(self) -> None:
        application = new_application()
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

    def test_keeps_active_trade_states_after_assets_leave_the_binance_universe(self) -> None:
        application = new_application()
        application.coordinator = FakeCoordinator()
        application.store = FakeStore()
        application.store.trade_states = {
            "PEPE": TradeSignalState(status=LONG),
            "DOGE": TradeSignalState(status=NO_ADD),
            "OLD": TradeSignalState(status=REENTRY_COOLDOWN),
        }
        application.notifier = None
        application.trade_kline_loader = object()
        application._lock = threading.Lock()

        application.refresh()

        self.assertEqual(application.store.active_assets, {"PEPE", "DOGE", "OLD"})

    def test_records_trade_signal_failures_without_breaking_snapshot_json(self) -> None:
        application = new_application()
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
        application = new_application(8_000_000)
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
        self.assertEqual(
            snapshot["settings"]["binance_min_turnover_usd"],
            8_000_000,
        )

    def test_summary_exposes_the_current_binance_turnover_threshold(self) -> None:
        application = new_application(8_000_000)
        application._latest = {"complete": True}

        summary = application.summary()

        self.assertEqual(summary["settings"]["binance_min_turnover_usd"], 8_000_000)
        self.assertNotIn("settings", application._latest)

    def test_pushes_condition_list_after_a_complete_condition_scan(self) -> None:
        application = new_application()
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
        ) as lighter_loader, patch("app.fetch_binance_universe") as universe_loader:
            store_class.return_value.load_latest_snapshot.return_value = None
            application = MonitorApplication(binance_min_turnover_usd=8_000_000)
            universe = {"ETH": object()}

            application.coordinator.universe_loader()
            application.coordinator.venue_loaders["BingX"](universe)
            application.coordinator.venue_loaders["Lighter"](universe)

        self.assertIn("BingX", application.coordinator.venue_loaders)
        self.assertIn("Lighter", application.coordinator.venue_loaders)
        self.assertEqual(bingx_loader.call_args.args[0].timeout_seconds, 12)
        self.assertEqual(bingx_loader.call_args.args[1], {"ETH"})
        self.assertEqual(lighter_loader.call_args.args[1], {"ETH"})
        universe_loader.assert_called_once_with(ANY, 8_000_000)


class ManualRefreshTests(unittest.TestCase):
    def test_manual_refresh_requires_the_configured_token(self) -> None:
        application = new_application()
        application._manual_refresh_token = "test-refresh-token"

        self.assertFalse(application.manual_refresh_is_authorized(None))
        self.assertFalse(application.manual_refresh_is_authorized("wrong-token"))
        self.assertTrue(
            application.manual_refresh_is_authorized("test-refresh-token")
        )

    def test_manual_refresh_is_disabled_without_a_configured_token(self) -> None:
        application = new_application()
        application._manual_refresh_token = None

        self.assertFalse(application.manual_refresh_is_authorized("any-token"))

    def test_limits_successive_manual_refreshes_from_the_last_manual_completion(self) -> None:
        application = new_application()
        application._lock = threading.RLock()
        application._manual_refresh_min_interval_seconds = 120
        times = iter((100.0, 110.0, 150.0))
        application._clock = lambda: next(times)
        application.refresh = lambda: {"complete": True}

        self.assertEqual(application.manual_refresh(), {"complete": True})
        with self.assertRaises(ManualRefreshRejected) as raised:
            application.manual_refresh()

        self.assertEqual(raised.exception.retry_after_seconds, 80)

    def test_rejects_immediately_when_another_refresh_holds_the_lock(self) -> None:
        application = new_application()
        application._lock = threading.Lock()
        application._lock.acquire()
        application._manual_refresh_min_interval_seconds = 120
        application._clock = lambda: 200.0

        try:
            with self.assertRaisesRegex(ManualRefreshRejected, "正在刷新"):
                application.manual_refresh()
        finally:
            application._lock.release()

    def test_rejects_manual_refresh_inside_the_minimum_interval(self) -> None:
        application = new_application()
        application._lock = threading.Lock()
        application._manual_refresh_min_interval_seconds = 120
        application._last_manual_refresh_completed_at = 100.0
        application._clock = lambda: 150.0

        with self.assertRaises(ManualRefreshRejected) as raised:
            application.manual_refresh()

        self.assertEqual(raised.exception.retry_after_seconds, 70)


class JsonResponseTests(unittest.TestCase):
    def test_rejects_non_standard_json_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "Out of range float values"):
            encode_json_payload({"ratio": float("nan")})


class StartupConfigurationTests(unittest.TestCase):
    def test_rejects_empty_required_environment_value(self) -> None:
        with patch.dict(os.environ, {"COINMARKETCAP_API_KEY": "   "}):
            with self.assertRaisesRegex(RuntimeError, "must not be empty"):
                required_environment_value("COINMARKETCAP_API_KEY")

    def test_configures_a_rotating_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.logging.basicConfig"
        ) as basic_config:
            configure_logging(Path(temp_dir) / "monitor.log", 3, 2)
            handler = basic_config.call_args.kwargs["handlers"][0]
            self.assertIsInstance(handler, RotatingFileHandler)
            self.assertEqual(handler.maxBytes, 3 * 1024**2)
            self.assertEqual(handler.backupCount, 2)
            self.assertTrue(basic_config.call_args.kwargs["force"])
            handler.close()


class RefreshScheduleTests(unittest.TestCase):
    def test_waits_only_until_the_fixed_refresh_deadline(self) -> None:
        next_deadline, wait_seconds = next_refresh_schedule(
            previous_deadline=0,
            finished_at=90,
            interval_seconds=120,
        )

        self.assertEqual(next_deadline, 120)
        self.assertEqual(wait_seconds, 30)

    def test_waits_a_full_interval_after_a_refresh_exceeds_its_interval(self) -> None:
        next_deadline, wait_seconds = next_refresh_schedule(
            previous_deadline=120,
            finished_at=250,
            interval_seconds=120,
        )

        self.assertEqual(next_deadline, 370)
        self.assertEqual(wait_seconds, 120)

    def test_repeated_overruns_never_create_a_zero_wait_loop(self) -> None:
        next_deadline, wait_seconds = next_refresh_schedule(
            previous_deadline=370,
            finished_at=500,
            interval_seconds=120,
        )

        self.assertEqual(next_deadline, 620)
        self.assertEqual(wait_seconds, 120)


class RefreshIntervalValidationTests(unittest.TestCase):
    def test_accepts_a_positive_refresh_interval(self) -> None:
        self.assertEqual(positive_refresh_seconds("120"), 120)

    def test_rejects_zero_or_negative_refresh_intervals(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(ArgumentTypeError):
                positive_refresh_seconds(value)


if __name__ == "__main__":
    unittest.main()
