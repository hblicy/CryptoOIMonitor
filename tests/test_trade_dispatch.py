import unittest
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

from crypto_oi_monitor.http_client import DataSourceRequestError
from crypto_oi_monitor.trade_dispatch import (
    EXIT_LONG,
    REENTRY_COOLDOWN,
    REENTRY_COOLDOWN_CANDLES,
    STOP_LONG,
    TradeSignalState,
    TradeConditionScan,
    dispatch_trade_condition_list,
    dispatch_trade_signals as _dispatch_trade_signals,
    scan_trade_conditions as _scan_trade_conditions,
)
from crypto_oi_monitor.trading import Candle, FIFTEEN_MINUTES_MILLISECONDS


def _candles(
    closes: list[float], quote_volumes: list[float] | None = None
) -> list[Candle]:
    volumes = quote_volumes or [100] * len(closes)
    return [
        Candle(
            close_time=(index + 1) * 15 * 60 * 1000 - 1,
            high=close + 1,
            low=close - 1,
            close=close,
            quote_volume=volumes[index],
        )
        for index, close in enumerate(closes)
    ]


def _warm_candles(closes: list[float], warmup_close: float = 100) -> list[Candle]:
    return _candles([warmup_close] * 1000 + closes)


def _long_setup_candles() -> list[Candle]:
    closes = (
        [100] * 950
        + [100 + (index % 2) * 2 for index in range(49)]
        + [95, 100.5]
    )
    return _candles(closes, [100] * 1000 + [120])


def _rsi_above_50_candles() -> list[Candle]:
    return _warm_candles(
        [100 + index for index in range(200)]
        + [298 - index for index in range(60)]
        + [260]
    )


def _post_entry_candles() -> list[Candle]:
    candles = _long_setup_candles()
    previous = candles[-1]
    candles.append(
        Candle(
            close_time=previous.close_time + FIFTEEN_MINUTES_MILLISECONDS,
            high=101.4,
            low=99.4,
            close=100.4,
            quote_volume=80,
        )
    )
    return candles


def _close_below_ema200_candles() -> list[Candle]:
    return _warm_candles([300 - index for index in range(201)], warmup_close=300)


def _reference_snapshot(snapshot: dict, oi_multiplier: float = 0.9) -> dict:
    return {
        "complete": True,
        "comparisons": [
            {
                "canonical_symbol": comparison["canonical_symbol"],
                "total_oi_usd": comparison.get("total_oi_usd", 100) * oi_multiplier,
            }
            for comparison in snapshot["comparisons"]
        ],
    }


def dispatch_trade_signals(snapshot, kline_loader, store, notifier):
    enriched = {
        **snapshot,
        "comparisons": [
            {"total_oi_usd": 100, **comparison}
            for comparison in snapshot["comparisons"]
        ],
    }
    return _dispatch_trade_signals(
        enriched,
        _reference_snapshot(enriched),
        kline_loader,
        store,
        notifier,
    )


def scan_trade_conditions(snapshot, kline_loader):
    enriched = {
        **snapshot,
        "comparisons": [
            {"total_oi_usd": 100, **comparison}
            for comparison in snapshot["comparisons"]
        ],
    }
    return _scan_trade_conditions(
        enriched,
        _reference_snapshot(enriched),
        kline_loader,
    )


class MemoryStore:
    def __init__(self) -> None:
        self.states: dict[str, TradeSignalState | str] = {}

    def get_trade_signal_state(self, symbol: str) -> TradeSignalState | str | None:
        return self.states.get(symbol)

    def set_trade_signal_state(self, symbol: str, state: TradeSignalState) -> None:
        self.states[symbol] = state

    def clear_trade_signal_state(self, symbol: str) -> None:
        self.states.pop(symbol, None)


class RecordingNotifier:
    def __init__(self) -> None:
        self.signals = []
        self.stop_longs = []
        self.exit_longs = []

    def send_trade_signal(self, signal, comparison, previous_aggregate_oi_usd) -> None:
        self.signals.append(
            (signal.side, comparison["canonical_symbol"], previous_aggregate_oi_usd)
        )

    def send_stop_long(self, comparison, indicators, reasons) -> None:
        self.stop_longs.append((comparison["canonical_symbol"], indicators, reasons))

    def send_exit_long(self, comparison, indicators, state, reasons) -> None:
        self.exit_longs.append(
            (comparison["canonical_symbol"], indicators.close, state.stop_loss)
        )


class ConditionListStore:
    def __init__(self, state) -> None:
        self.state = state

    def get_trade_condition_list_state(self):
        return self.state

    def set_trade_condition_list_state(self, state) -> None:
        self.state = state


class ConditionListNotifier:
    def __init__(self) -> None:
        self.lists = []

    def send_trade_condition_list(
        self, can_long, stop_long, exit_long, periodic
    ) -> None:
        self.lists.append((can_long, stop_long, exit_long, periodic))


class FailingConditionListNotifier:
    def send_trade_condition_list(
        self, can_long, stop_long, exit_long, periodic
    ) -> None:
        raise RuntimeError("WeCom failed")


class ExitConditionListNotifier:
    def __init__(self) -> None:
        self.lists = []

    def send_trade_condition_list(
        self, can_long, stop_long, exit_long=(), periodic=False
    ) -> None:
        self.lists.append((can_long, stop_long, exit_long, periodic))


def _condition_scan(symbol: str, status: str) -> TradeConditionScan:
    return TradeConditionScan(
        status=status,
        canonical_symbol=symbol,
        candle_close_time=1_722_269_700_000,
        rsi=42.5,
        close=1,
        ema200=0.9,
        oi_to_market_cap=1.2,
    )


class TradeDispatchTests(unittest.TestCase):
    def test_requires_exit_when_an_active_long_hits_its_atr_stop(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
        )
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }
        candles = _long_setup_candles()
        candles[-1] = Candle(
            close_time=candles[-1].close_time,
            high=99,
            low=97,
            close=97.5,
            quote_volume=candles[-1].quote_volume,
        )

        result = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertIn("atr_stop_loss", result.details[0].reasons)
        self.assertEqual(notifier.exit_longs, [("PEPE", 97.5, 98)])
        state = store.get_trade_signal_state("PEPE")
        self.assertEqual(state.status, REENTRY_COOLDOWN)
        self.assertEqual(
            state.cooldown_until_candle_close_time,
            candles[-1].close_time
            + REENTRY_COOLDOWN_CANDLES * FIFTEEN_MINUTES_MILLISECONDS,
        )

    def test_sends_current_lists_when_new_condition_symbols_appear(self) -> None:
        now = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        store = ConditionListStore(
            SimpleNamespace(
                can_long=("AKE",),
                stop_long=("ON",),
                exit_long=(),
                last_sent_at=now - timedelta(minutes=5),
            )
        )
        notifier = ConditionListNotifier()

        result = dispatch_trade_condition_list(
            (
                _condition_scan("BULLA", "can_long"),
                _condition_scan("AKE", "can_long"),
                _condition_scan("ESPORTS", "stop_long"),
                _condition_scan("ON", "stop_long"),
            ),
            store,
            notifier,
            now,
        )

        self.assertEqual(result, "updated")
        self.assertEqual(
            notifier.lists,
            [(("AKE", "BULLA"), ("ESPORTS", "ON"), (), False)],
        )
        self.assertEqual(store.state.can_long, ("AKE", "BULLA"))
        self.assertEqual(store.state.stop_long, ("ESPORTS", "ON"))
        self.assertEqual(store.state.exit_long, ())
        self.assertEqual(store.state.last_sent_at, now)

    def test_sends_must_exit_symbols_when_they_first_appear(self) -> None:
        now = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        store = ConditionListStore(
            SimpleNamespace(
                can_long=(),
                stop_long=(),
                exit_long=(),
                last_sent_at=now - timedelta(minutes=5),
            )
        )
        notifier = ExitConditionListNotifier()

        result = dispatch_trade_condition_list(
            (_condition_scan("KOMA", "exit_long"),), store, notifier, now
        )

        self.assertEqual(result, "updated")
        self.assertEqual(notifier.lists, [((), (), ("KOMA",), False)])
        self.assertEqual(store.state.exit_long, ("KOMA",))

    def test_keeps_last_notified_list_when_removals_are_not_sent(self) -> None:
        now = datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc)
        last_sent_at = now - timedelta(minutes=30)
        store = ConditionListStore(
            SimpleNamespace(
                can_long=("AKE", "BULLA"),
                stop_long=("ON",),
                exit_long=(),
                last_sent_at=last_sent_at,
            )
        )
        notifier = ConditionListNotifier()

        result = dispatch_trade_condition_list(
            (
                _condition_scan("AKE", "can_long"),
                _condition_scan("ON", "stop_long"),
            ),
            store,
            notifier,
            now,
        )

        self.assertIsNone(result)
        self.assertEqual(notifier.lists, [])
        self.assertEqual(store.state.can_long, ("AKE", "BULLA"))
        self.assertEqual(store.state.stop_long, ("ON",))
        self.assertEqual(store.state.exit_long, ())
        self.assertEqual(store.state.last_sent_at, last_sent_at)

    def test_does_not_repush_when_a_symbol_returns_before_periodic_notification(
        self,
    ) -> None:
        now = datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc)
        store = ConditionListStore(
            SimpleNamespace(
                can_long=("AKE", "BULLA"),
                stop_long=("ON",),
                exit_long=(),
                last_sent_at=now - timedelta(minutes=30),
            )
        )
        notifier = ConditionListNotifier()

        removed = dispatch_trade_condition_list(
            (
                _condition_scan("AKE", "can_long"),
                _condition_scan("ON", "stop_long"),
            ),
            store,
            notifier,
            now,
        )
        returned = dispatch_trade_condition_list(
            (
                _condition_scan("AKE", "can_long"),
                _condition_scan("BULLA", "can_long"),
                _condition_scan("ON", "stop_long"),
            ),
            store,
            notifier,
            now + timedelta(minutes=2),
        )

        self.assertIsNone(removed)
        self.assertIsNone(returned)
        self.assertEqual(notifier.lists, [])

    def test_sends_current_lists_every_hour_without_new_symbols(self) -> None:
        now = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
        store = ConditionListStore(
            SimpleNamespace(
                can_long=("AKE",),
                stop_long=("ON",),
                exit_long=(),
                last_sent_at=now - timedelta(hours=1),
            )
        )
        notifier = ConditionListNotifier()

        result = dispatch_trade_condition_list(
            (
                _condition_scan("AKE", "can_long"),
                _condition_scan("ON", "stop_long"),
            ),
            store,
            notifier,
            now,
        )

        self.assertEqual(result, "periodic")
        self.assertEqual(notifier.lists, [(("AKE",), ("ON",), (), True)])
        self.assertEqual(store.state.last_sent_at, now)

    def test_does_not_update_list_state_when_notification_fails(self) -> None:
        now = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
        previous_state = SimpleNamespace(
            can_long=("AKE",),
            stop_long=("ON",),
            exit_long=(),
            last_sent_at=now - timedelta(minutes=5),
        )
        store = ConditionListStore(previous_state)

        with self.assertRaisesRegex(RuntimeError, "WeCom failed"):
            dispatch_trade_condition_list(
                (
                    _condition_scan("AKE", "can_long"),
                    _condition_scan("BULLA", "can_long"),
                    _condition_scan("ON", "stop_long"),
                ),
                store,
                FailingConditionListNotifier(),
                now,
            )

        self.assertIs(store.state, previous_state)

    def test_sends_one_long_signal_until_rsi_returns_to_50(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.4,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        first = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )
        repeated = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )
        stopped = dispatch_trade_signals(
            snapshot, lambda _: _rsi_above_50_candles(), store, notifier
        )
        reentered = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )

        self.assertEqual(first.events, ("long",))
        self.assertEqual(first.details[0].event_type, "long")
        self.assertEqual(first.details[0].canonical_symbol, "PEPE")
        self.assertEqual(first.details[0].candle_close_time, 900_899_999)
        self.assertEqual(first.details[0].reasons, ())
        self.assertEqual(repeated.events, ())
        self.assertEqual(stopped.events, ("stop_long",))
        self.assertEqual(stopped.details[0].event_type, "stop_long")
        self.assertEqual(stopped.details[0].reasons, ("rsi_not_below_50",))
        self.assertEqual(reentered.events, ("long",))
        self.assertEqual(
            notifier.signals,
            [("long", "PEPE", 90), ("long", "PEPE", 90)],
        )
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertGreater(notifier.stop_longs[0][1].rsi, 50)

    def test_requires_oi_to_market_cap_strictly_above_100_percent(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.0,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(snapshot, lambda _: _long_setup_candles(), store, notifier)

        self.assertEqual(result.events, ())
        self.assertEqual(notifier.signals, [])

    def test_requires_aggregate_oi_to_increase_from_15_minutes_ago(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }
        reference = _reference_snapshot(snapshot, oi_multiplier=1)

        result = _scan_trade_conditions(
            snapshot, reference, lambda _: _long_setup_candles()
        )

        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons, ("aggregate_oi_not_increasing",)
        )

    def test_marks_missing_aggregate_oi_history_as_stop_long(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = _scan_trade_conditions(
            snapshot, None, lambda _: _long_setup_candles()
        )

        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons, ("aggregate_oi_history_unavailable",)
        )

    def test_active_long_ignores_one_shot_entry_confirmations(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState("long", 100.5, 90)
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = _dispatch_trade_signals(
            snapshot,
            _reference_snapshot(snapshot, oi_multiplier=1),
            lambda _: _post_entry_candles(),
            store,
            notifier,
        )

        self.assertEqual(result.events, ())
        self.assertEqual(store.states["PEPE"].status, "long")
        self.assertEqual(notifier.stop_longs, [])

    def test_does_not_scan_assets_at_exactly_100_percent(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.0,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = scan_trade_conditions(snapshot, lambda _: _long_setup_candles())

        self.assertEqual(result.scans, ())

    def test_does_not_stop_active_long_at_exactly_110_percent(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState("long", 100, 98)
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.1,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )

        self.assertEqual(result.events, ())
        self.assertEqual(notifier.stop_longs, [])
        self.assertEqual(store.states["PEPE"].status, "long")

    def test_scans_all_eligible_assets_into_can_long_and_stop_long_groups(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
                {
                    "canonical_symbol": "DOGE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "DOGEUSDT"}],
                },
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda symbol: _long_setup_candles()
            if symbol == "PEPEUSDT"
            else _rsi_above_50_candles(),
            store,
            notifier,
        )

        self.assertEqual(
            [
                (scan.canonical_symbol, scan.status, scan.reasons)
                for scan in result.scans
            ],
            [
                ("PEPE", "can_long", ()),
                (
                    "DOGE",
                    "stop_long",
                    (
                        "ema200_not_crossed_up",
                        "quote_volume_not_increasing",
                        "rsi_not_below_50",
                    ),
                ),
            ],
        )

    def test_scans_conditions_without_a_notifier_or_signal_state(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = scan_trade_conditions(snapshot, lambda _: _long_setup_candles())

        self.assertEqual(result.failures, ())
        self.assertEqual(result.scans[0].status, "can_long")
        self.assertEqual(result.scans[0].canonical_symbol, "PEPE")

    def test_records_scan_kline_failure_without_a_notifier_or_signal_state(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = scan_trade_conditions(
            snapshot,
            lambda _: (_ for _ in ()).throw(DataSourceRequestError("Binance timed out")),
        )

        self.assertEqual(result.scans[0].status, "kline_error")
        self.assertIn("Binance timed out", result.scans[0].error)
        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")

    def test_rejects_scan_history_without_ema200_warmup(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = scan_trade_conditions(snapshot, lambda _: _candles([100] * 1000))

        self.assertEqual(result.scans[0].status, "kline_error")
        self.assertIn("1001", result.scans[0].error)

    def test_stops_active_long_at_or_below_100_percent_before_loading_rsi(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.0,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _rsi_above_50_candles(), store, notifier
        )

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(
            result.details[0].reasons,
            ("oi_to_market_cap_not_above_100",),
        )
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertEqual(store.states["PEPE"].status, "no_add")

    def test_stops_active_long_when_oi_to_market_cap_is_below_100_percent(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.99,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(
            result.details[0].reasons, ("oi_to_market_cap_not_above_100",)
        )
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertEqual(store.states["PEPE"].status, "no_add")

    def test_stops_active_long_for_oi_threshold_when_kline_load_fails(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.99,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda _: (_ for _ in ()).throw(DataSourceRequestError("Binance timed out")),
            store,
            notifier,
        )

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(result.failures, ())
        self.assertEqual(
            result.details[0].reasons, ("oi_to_market_cap_not_above_100",)
        )
        self.assertIsNone(result.details[0].candle_close_time)
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertEqual(store.states["PEPE"].status, "no_add")

    def test_sends_oi_threshold_stop_before_loading_other_trade_candles(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.0,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
                {
                    "canonical_symbol": "DOGE",
                    "oi_to_market_cap": 1.4,
                    "contracts": [{"venue": "Binance", "symbol": "DOGEUSDT"}],
                },
            ],
        }

        def load(symbol: str):
            self.assertEqual(notifier.stop_longs, [("PEPE", None, ("oi_to_market_cap_not_above_100",))])
            self.assertEqual(store.states["PEPE"].status, "no_add")
            self.assertEqual(symbol, "DOGEUSDT")
            return _candles([100] * 1001)

        result = dispatch_trade_signals(snapshot, load, store, notifier)

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(result.failures, ())

    def test_stops_active_long_when_rsi_is_exactly_50(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }
        candles = _candles([100] * 1001)

        result = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(notifier.signals, [])
        self.assertIn("rsi_not_below_50", notifier.stop_longs[0][2])
        self.assertEqual(store.states["PEPE"].status, "no_add")

    def test_stops_active_long_when_close_is_below_ema200(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _close_below_ema200_candles(), store, notifier
        )

        self.assertEqual(result.events, ("stop_long",))
        self.assertIn(
            "close_not_above_ema200", result.details[0].reasons
        )
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertLess(notifier.stop_longs[0][1].rsi, 50)
        self.assertLess(
            notifier.stop_longs[0][1].close,
            notifier.stop_longs[0][1].ema200,
        )
        self.assertEqual(store.states["PEPE"].status, "no_add")

    def test_clears_legacy_short_state_then_scans_and_evaluates_long(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "short"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda _: _long_setup_candles(),
            store,
            notifier,
        )

        self.assertEqual(result.events, ("long",))
        self.assertEqual(result.failures, ())
        self.assertEqual(notifier.signals, [("long", "PEPE", 90)])
        self.assertEqual(result.scans[0].status, "can_long")
        self.assertEqual(store.states["PEPE"].status, "long")

    def test_skips_short_history_and_continues_with_other_symbols(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "NEW",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "NEWUSDT"}],
                },
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda symbol: _candles([100] * 1000)
            if symbol == "NEWUSDT"
            else _long_setup_candles(),
            store,
            notifier,
        )

        self.assertEqual(result.events, ("long",))
        self.assertEqual(notifier.signals, [("long", "PEPE", 90)])
        self.assertEqual(result.failures[0].canonical_symbol, "NEW")
        self.assertIn("1001", result.failures[0].message)

    def test_loads_trade_signal_candidates_concurrently(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        barrier = Barrier(2)
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
                {
                    "canonical_symbol": "DOGE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "DOGEUSDT"}],
                },
            ],
        }

        def load(symbol: str):
            barrier.wait(timeout=3)
            return _long_setup_candles() if symbol == "PEPEUSDT" else _candles([100] * 1001)

        result = dispatch_trade_signals(snapshot, load, store, notifier)

        self.assertEqual(result.events, ("long",))
        self.assertEqual(result.failures, ())

    def test_records_kline_request_failure_and_continues_with_other_symbols(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "NEW",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "NEWUSDT"}],
                },
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
            ],
        }

        def load(symbol: str):
            if symbol == "NEWUSDT":
                raise DataSourceRequestError("Binance request timed out")
            return _long_setup_candles()

        result = dispatch_trade_signals(snapshot, load, store, notifier)

        self.assertEqual(result.events, ("long",))
        self.assertEqual(notifier.signals, [("long", "PEPE", 90)])
        self.assertEqual(result.failures[0].canonical_symbol, "NEW")
        self.assertIn("Binance request timed out", result.failures[0].message)
        self.assertEqual(result.scans[0].canonical_symbol, "NEW")
        self.assertEqual(result.scans[0].status, "kline_error")
        self.assertIn("Binance request timed out", result.scans[0].error)

    def test_records_malformed_binance_kline_and_continues_with_other_symbols(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "BAD",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "BADUSDT"}],
                },
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
            ],
        }

        def load(symbol: str):
            if symbol == "BADUSDT":
                raise ValueError("malformed Binance kline")
            return _long_setup_candles()

        result = dispatch_trade_signals(snapshot, load, store, notifier)

        self.assertEqual(result.events, ("long",))
        self.assertEqual(notifier.signals, [("long", "PEPE", 90)])
        self.assertEqual(result.failures[0].canonical_symbol, "BAD")
        self.assertIn("malformed Binance kline", result.failures[0].message)


if __name__ == "__main__":
    unittest.main()
