import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

from crypto_oi_monitor.http_client import DataSourceRequestError
from crypto_oi_monitor.trade_dispatch import (
    CAN_LONG,
    EXIT_LONG,
    NO_ADD,
    RESUME_LONG,
    REENTRY_COOLDOWN,
    STOP_LONG,
    TradeSignalState,
    TradeConditionScan,
    _aggregate_oi_entry_reasons,
    dispatch_trade_condition_list,
    dispatch_trade_signals as _dispatch_trade_signals,
    scan_trade_conditions as _scan_trade_conditions,
)
from crypto_oi_monitor.trading import (
    Candle,
    FIFTEEN_MINUTES_MILLISECONDS,
    TradeIndicators,
    dynamic_cooldown_candles,
)


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
        + [100 + (index % 2) * 2 for index in range(45)]
        + [97, 98, 102, 102, 100, 100.5]
    )
    return _candles(closes, [100] * 1000 + [201])


def _rsi_above_60_candles() -> list[Candle]:
    candles = _long_setup_candles()
    for index in range(9):
        previous = candles[-1]
        close = 100.5 + (index + 1) * 0.5
        candles.append(
            Candle(
                close_time=previous.close_time + FIFTEEN_MINUTES_MILLISECONDS,
                high=close + 1,
                low=close - 1,
                close=close,
                quote_volume=100,
            )
        )
    return candles


def _second_long_setup_candles() -> list[Candle]:
    candles = _rsi_above_60_candles()
    for index, close in enumerate(
        [99.85, 106.61, 104.84, 97.52, 105.47, 107.15, 100.72, 97.12, 105.68]
    ):
        previous = candles[-1]
        candles.append(
            Candle(
                close_time=previous.close_time + FIFTEEN_MINUTES_MILLISECONDS,
                high=close + 1,
                low=close - 1,
                close=close,
                quote_volume=250 if index == 8 else 100,
            )
        )
    return candles


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


def _atr_exit_candles() -> list[Candle]:
    candles = _long_setup_candles()
    candles[-1] = Candle(
        close_time=candles[-1].close_time,
        high=99,
        low=97,
        close=97.5,
        quote_volume=candles[-1].quote_volume,
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


def scan_trade_conditions(snapshot, kline_loader, trade_signal_states=None):
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
        trade_signal_states,
    )


class MemoryStore:
    def __init__(self) -> None:
        self.states: dict[str, TradeSignalState | str] = {}
        self.delivered_notifications: set[str] = set()
        self.pending_trade_states: dict[str, TradeSignalState] = {}

    def get_trade_signal_state(self, symbol: str) -> TradeSignalState | str | None:
        return self.states.get(symbol)

    def list_trade_signal_states(self) -> dict[str, TradeSignalState | str]:
        return dict(self.states)

    def set_trade_signal_state(self, symbol: str, state: TradeSignalState) -> None:
        self.states[symbol] = state

    def clear_trade_signal_state(self, symbol: str) -> None:
        self.states.pop(symbol, None)

    def notification_was_delivered(self, event_id: str) -> bool:
        return event_id in self.delivered_notifications

    def mark_notification_delivered(self, event_id: str) -> None:
        self.delivered_notifications.add(event_id)

    def mark_trade_notification_delivered(
        self, event_id: str, symbol: str, state: TradeSignalState
    ) -> None:
        self.delivered_notifications.add(event_id)
        self.pending_trade_states[symbol] = state

    def mark_trade_notification_state_applied(self, event_id: str) -> None:
        symbol = event_id.split(":", 3)[2]
        self.pending_trade_states.pop(symbol, None)

    def apply_pending_trade_signal_states(self) -> None:
        self.states.update(self.pending_trade_states)
        self.pending_trade_states.clear()


class RecordingNotifier:
    def __init__(self) -> None:
        self.signals = []
        self.stop_longs = []
        self.exit_longs = []

    def send_trade_signal(
        self, signal, comparison, previous_aggregate_oi_usd, event_type="long"
    ) -> None:
        self.signals.append(
            (event_type, comparison["canonical_symbol"], previous_aggregate_oi_usd)
        )

    def send_stop_long(self, comparison, indicators, reasons) -> None:
        self.stop_longs.append((comparison["canonical_symbol"], indicators, reasons))

    def send_exit_long(
        self, comparison, indicators, state, reasons, cooldown_candles
    ) -> None:
        self.exit_longs.append(
            (
                comparison["canonical_symbol"],
                indicators.close,
                state.stop_loss,
                cooldown_candles,
            )
        )


class FailingTradeSignalNotifier(RecordingNotifier):
    def send_trade_signal(
        self, signal, comparison, previous_aggregate_oi_usd, event_type="long"
    ) -> None:
        raise RuntimeError("WeCom failed")


class FailingExitNotifier(RecordingNotifier):
    def send_exit_long(
        self, comparison, indicators, state, reasons, cooldown_candles
    ) -> None:
        raise RuntimeError("WeCom exit failed")


class FailingFirstExitNotifier(RecordingNotifier):
    def send_exit_long(
        self, comparison, indicators, state, reasons, cooldown_candles
    ) -> None:
        if comparison["canonical_symbol"] == "PEPE":
            raise RuntimeError("WeCom exit failed")
        super().send_exit_long(
            comparison, indicators, state, reasons, cooldown_candles
        )


class FailFirstTradeStateWriteStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_state_write = True

    def set_trade_signal_state(self, symbol: str, state: TradeSignalState) -> None:
        if self.fail_next_state_write:
            self.fail_next_state_write = False
            raise RuntimeError("state write failed")
        super().set_trade_signal_state(symbol, state)


class ConditionListStore:
    def __init__(self, state) -> None:
        self.state = state
        self.delivered_notifications: set[str] = set()

    def get_trade_condition_list_state(self):
        return self.state

    def set_trade_condition_list_state(self, state) -> None:
        self.state = state

    def notification_was_delivered(self, event_id: str) -> bool:
        return event_id in self.delivered_notifications

    def mark_notification_delivered(self, event_id: str) -> None:
        self.delivered_notifications.add(event_id)


class FailFirstConditionListStateWriteStore(ConditionListStore):
    def __init__(self, state) -> None:
        super().__init__(state)
        self.fail_next_state_write = True

    def set_trade_condition_list_state(self, state) -> None:
        if self.fail_next_state_write:
            self.fail_next_state_write = False
            raise RuntimeError("list state write failed")
        super().set_trade_condition_list_state(state)


class ConditionListNotifier:
    def __init__(self) -> None:
        self.lists = []

    def send_trade_condition_list(
        self, can_long, exit_long, periodic
    ) -> None:
        self.lists.append((can_long, exit_long, periodic))


class FailingConditionListNotifier:
    def send_trade_condition_list(
        self, can_long, exit_long, periodic
    ) -> None:
        raise RuntimeError("WeCom failed")


class ExitConditionListNotifier:
    def __init__(self) -> None:
        self.lists = []

    def send_trade_condition_list(
        self, can_long, exit_long=(), periodic=False
    ) -> None:
        self.lists.append((can_long, exit_long, periodic))


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
    def test_incomplete_snapshot_still_runs_active_position_exit_checks(self) -> None:
        store = MemoryStore()
        candles = _atr_exit_candles()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=899_999_999,
        )
        notifier = RecordingNotifier()
        snapshot = {
            "complete": False,
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
            snapshot, None, lambda _: candles, store, notifier
        )

        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertIn("atr_stop_loss", result.details[0].reasons)

    def test_cmc_outage_uses_persisted_binance_symbol_for_active_exit(self) -> None:
        store = MemoryStore()
        candles = _atr_exit_candles()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=899_999_999,
            binance_symbol="PEPEUSDT",
        )
        notifier = RecordingNotifier()

        result = _dispatch_trade_signals(
            {"complete": False, "comparisons": []},
            None,
            lambda symbol: candles if symbol == "PEPEUSDT" else [],
            store,
            notifier,
        )

        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertIsNone(result.details[0].oi_to_market_cap)

    def test_reports_legacy_active_state_without_a_persisted_binance_symbol(
        self,
    ) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
        )

        result = _dispatch_trade_signals(
            {"complete": False, "comparisons": []},
            None,
            lambda _: self.fail("K-line loader must not receive a guessed symbol"),
            store,
            RecordingNotifier(),
        )

        self.assertEqual(result.events, ())
        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertIn("Binance symbol is missing", result.failures[0].message)

    def test_complete_missing_comparison_stops_unmapped_active_long(self) -> None:
        store = MemoryStore()
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
        )
        store.states["PEPE"] = state

        result = _dispatch_trade_signals(
            {"complete": True, "comparisons": []},
            None,
            lambda _: self.fail("K-line loader must not receive a guessed symbol"),
            store,
            RecordingNotifier(),
        )

        self.assertEqual(result.events, (STOP_LONG,))
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Binance symbol is missing", result.failures[0].message)
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("data_source_incomplete",))
        self.assertEqual(store.states["PEPE"], replace(state, status=NO_ADD))

    def test_unmapped_no_add_state_does_not_attempt_resume_without_oi_data(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState(
            status=NO_ADD,
            entry_price=100,
            stop_loss=90,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=_long_setup_candles()[-1].close_time,
            binance_symbol="PEPEUSDT",
        )

        result = _dispatch_trade_signals(
            {"complete": True, "comparisons": []},
            None,
            lambda _: _long_setup_candles(),
            store,
            RecordingNotifier(),
        )

        self.assertEqual(result.events, ())
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("data_source_incomplete",))
        self.assertEqual(store.states["PEPE"].status, NO_ADD)

    def test_unmapped_active_long_stops_additions_when_oi_data_is_unavailable(self) -> None:
        store = MemoryStore()
        candles = _post_entry_candles()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=90,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=candles[-1].close_time,
            binance_symbol="PEPEUSDT",
            position_id="PEPE:entry",
        )
        notifier = RecordingNotifier()

        result = _dispatch_trade_signals(
            {"complete": True, "comparisons": []},
            None,
            lambda _: candles,
            store,
            notifier,
        )

        self.assertEqual(result.events, (STOP_LONG,))
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("data_source_incomplete",))
        self.assertEqual(store.states["PEPE"].status, NO_ADD)
        self.assertEqual(notifier.stop_longs, [])

    def test_requires_exit_when_an_active_long_hits_its_atr_stop(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=899_999_999,
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
        self.assertEqual(
            notifier.exit_longs,
            [("PEPE", 97.5, 98, dynamic_cooldown_candles(candles))],
        )
        state = store.get_trade_signal_state("PEPE")
        self.assertEqual(state.status, REENTRY_COOLDOWN)
        self.assertEqual(
            state.cooldown_until_candle_close_time,
            candles[-1].close_time
            + dynamic_cooldown_candles(candles) * FIFTEEN_MINUTES_MILLISECONDS,
        )

    def test_mandatory_exit_takes_priority_over_oi_threshold_stop(self) -> None:
        store = MemoryStore()
        candles = _atr_exit_candles()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=899_999_999,
        )
        notifier = RecordingNotifier()
        kline_calls = []
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 0.89,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda _: (kline_calls.append("PEPEUSDT") or candles),
            store,
            notifier,
        )

        self.assertEqual(kline_calls, ["PEPEUSDT"])
        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertIn("atr_stop_loss", result.details[0].reasons)
        self.assertEqual(result.scans[0].status, EXIT_LONG)

    def test_active_position_uses_persisted_binance_symbol_when_current_comparison_lacks_it(
        self,
    ) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=899_999_999,
            binance_symbol="PEPEUSDT",
        )
        loaded_symbols = []
        snapshot = {
            "complete": False,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [
                        {"venue": "OKX", "symbol": "PEPE-USDT-SWAP"}
                    ],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
            store,
            RecordingNotifier(),
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.failures, ())
        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertIn("trailing_take_profit", result.details[0].reasons)

    def test_trailing_stop_is_persisted_and_can_force_exit(self) -> None:
        store = MemoryStore()
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
        profit_candles = _long_setup_candles()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=96,
            entry_atr=2,
            highest_close=100,
            last_processed_candle_close_time=profit_candles[-1].close_time,
        )
        previous = profit_candles[-1]
        profit_candles.append(Candle(
            close_time=previous.close_time + FIFTEEN_MINUTES_MILLISECONDS,
            high=107,
            low=105,
            close=106,
            quote_volume=100,
        ))
        previous = profit_candles[-1]
        profit_candles.append(Candle(
            close_time=previous.close_time + FIFTEEN_MINUTES_MILLISECONDS,
            high=106,
            low=104,
            close=105,
            quote_volume=100,
        ))

        dispatch_trade_signals(snapshot, lambda _: profit_candles, store, notifier)

        protected = store.get_trade_signal_state("PEPE")
        self.assertEqual(protected.stop_loss, 102)
        self.assertEqual(protected.highest_close, 106)
        self.assertEqual(
            protected.last_processed_candle_close_time,
            profit_candles[-1].close_time,
        )

        exit_candles = list(profit_candles)
        exit_candles[-1] = Candle(
            close_time=profit_candles[-1].close_time + FIFTEEN_MINUTES_MILLISECONDS,
            high=103,
            low=100,
            close=101,
            quote_volume=100,
        )
        result = dispatch_trade_signals(
            snapshot, lambda _: exit_candles, store, notifier
        )

        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertIn("trailing_take_profit", result.details[0].reasons)
        self.assertEqual(result.scans[0].status, EXIT_LONG)
        self.assertIn("trailing_take_profit", result.scans[0].reasons)

    def test_replays_every_unprocessed_close_and_exits_on_an_intermediate_breach(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        last_processed = candles[-1].close_time
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=96,
            entry_atr=2,
            highest_close=100,
            last_processed_candle_close_time=last_processed,
            binance_symbol="PEPEUSDT",
        )
        for close in (108, 100, 106):
            previous = candles[-1]
            candles.append(
                Candle(
                    close_time=previous.close_time + FIFTEEN_MINUTES_MILLISECONDS,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    quote_volume=200,
                )
            )
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

        result = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertEqual(notifier.exit_longs[0][1], 100)
        self.assertEqual(notifier.exit_longs[0][2], 104)
        self.assertEqual(result.details[0].candle_close_time, candles[-2].close_time)
        self.assertEqual(store.states["PEPE"].status, REENTRY_COOLDOWN)

    def test_does_not_advance_replay_state_when_historical_exit_notification_fails(self) -> None:
        store = MemoryStore()
        candles = _long_setup_candles()
        original = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=96,
            entry_atr=2,
            highest_close=100,
            last_processed_candle_close_time=candles[-1].close_time,
            binance_symbol="PEPEUSDT",
        )
        store.states["PEPE"] = original
        for close in (108, 100, 106):
            previous = candles[-1]
            candles.append(
                Candle(
                    close_time=previous.close_time + FIFTEEN_MINUTES_MILLISECONDS,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    quote_volume=200,
                )
            )
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

        result = dispatch_trade_signals(
            snapshot, lambda _: candles, store, FailingExitNotifier()
        )

        self.assertEqual(store.states["PEPE"], original)
        self.assertEqual(result.events, ())
        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertIn("WeCom exit failed", result.failures[0].message)

    def test_failed_exit_preserves_backfilled_symbol_for_the_next_replay(self) -> None:
        store = MemoryStore()
        candles = _atr_exit_candles()
        original = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=899_999_999,
        )
        store.states["PEPE"] = original
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        failed = dispatch_trade_signals(
            snapshot, lambda _: candles, store, FailingExitNotifier()
        )

        safe_state = replace(original, binance_symbol="PEPEUSDT")
        self.assertEqual(failed.events, ())
        self.assertIn("WeCom exit failed", failed.failures[0].message)
        self.assertEqual(store.states["PEPE"], safe_state)

        loaded_symbols = []
        notifier = RecordingNotifier()
        recovered = dispatch_trade_signals(
            {"complete": False, "comparisons": []},
            lambda symbol: loaded_symbols.append(symbol) or candles,
            store,
            notifier,
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(recovered.events, (EXIT_LONG,))
        self.assertEqual(notifier.exit_longs[0][0], "PEPE")

    def test_notification_failure_for_one_symbol_does_not_block_later_exits(self) -> None:
        store = MemoryStore()
        candles = _atr_exit_candles()
        for symbol in ("PEPE", "DOGE"):
            store.states[symbol] = TradeSignalState(
                status="long",
                entry_price=100,
                stop_loss=98,
                entry_atr=1,
                highest_close=100,
                last_processed_candle_close_time=899_999_999,
                binance_symbol=f"{symbol}USDT",
                position_id=f"{symbol}:entry",
            )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": symbol,
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": f"{symbol}USDT"}
                    ],
                }
                for symbol in ("PEPE", "DOGE")
            ],
        }
        notifier = FailingFirstExitNotifier()

        result = dispatch_trade_signals(
            snapshot, lambda _: candles, store, notifier
        )

        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertEqual(notifier.exit_longs[0][0], "DOGE")
        self.assertEqual(store.states["PEPE"].status, "long")
        self.assertEqual(store.states["DOGE"].status, REENTRY_COOLDOWN)

    def test_delivered_exit_is_not_repeated_when_state_write_retries(self) -> None:
        store = FailFirstTradeStateWriteStore()
        candles = _atr_exit_candles()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=899_999_999,
            binance_symbol="PEPEUSDT",
            position_id="PEPE:entry",
        )
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
        notifier = RecordingNotifier()

        failed = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)
        retried = dispatch_trade_signals(
            snapshot, lambda _: _post_entry_candles(), store, notifier
        )

        self.assertEqual(failed.events, ())
        self.assertIn("state write failed", failed.failures[0].message)
        self.assertEqual(retried.events, ())
        self.assertEqual(len(notifier.exit_longs), 1)
        self.assertEqual(store.states["PEPE"].status, REENTRY_COOLDOWN)

    def test_records_trailing_replay_gap_and_continues_other_symbols(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=96,
            entry_atr=2,
            highest_close=100,
            last_processed_candle_close_time=(
                candles[0].close_time - 2 * FIFTEEN_MINUTES_MILLISECONDS
            ),
            binance_symbol="PEPEUSDT",
        )
        store.states["DOGE"] = TradeSignalState(
            status="long",
            entry_price=110,
            stop_loss=101,
            entry_atr=2,
            highest_close=110,
            last_processed_candle_close_time=candles[-1].close_time,
            binance_symbol="DOGEUSDT",
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
                {
                    "canonical_symbol": "DOGE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [{"venue": "Binance", "symbol": "DOGEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertIn("does not cover the trailing replay gap", result.failures[0].message)
        self.assertEqual(notifier.exit_longs[0][0], "DOGE")
        self.assertEqual(store.states["PEPE"].status, "long")
        self.assertEqual(store.states["DOGE"].status, REENTRY_COOLDOWN)

    def test_expired_cooldown_still_requires_a_new_ema200_cross(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        next_close_time = (
            candles[-1].close_time + FIFTEEN_MINUTES_MILLISECONDS
        )
        store.states["PEPE"] = TradeSignalState(
            status=REENTRY_COOLDOWN,
            cooldown_until_candle_close_time=next_close_time,
        )
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

        blocked = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)
        candles.append(
            Candle(
                close_time=next_close_time,
                high=101.6,
                low=99.6,
                close=100.6,
                quote_volume=130,
            )
        )
        expired = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

        self.assertEqual(blocked.events, ())
        self.assertEqual(expired.events, ())
        self.assertEqual(notifier.signals, [])
        self.assertEqual(
            store.get_trade_signal_state("PEPE").status,
            REENTRY_COOLDOWN,
        )
        self.assertIn("ema200_not_crossed_up", expired.scans[0].reasons)

    def test_expired_cooldown_accepts_a_breakout_at_cooldown_end(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        store.states["PEPE"] = TradeSignalState(
            status=REENTRY_COOLDOWN,
            cooldown_until_candle_close_time=candles[-1].close_time,
            binance_symbol="PEPEUSDT",
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "OKX", "symbol": "PEPE-USDT-SWAP"}
                    ],
                }
            ],
        }

        loaded_symbols = []
        result = dispatch_trade_signals(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or candles,
            store,
            notifier,
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.events, (RESUME_LONG,))
        self.assertEqual(store.get_trade_signal_state("PEPE").status, "long")

    def test_active_cooldown_is_not_reported_as_can_long(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        store.states["PEPE"] = TradeSignalState(
            status=REENTRY_COOLDOWN,
            cooldown_until_candle_close_time=(
                candles[-1].close_time + 4 * FIFTEEN_MINUTES_MILLISECONDS
            ),
        )
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

        result = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

        self.assertEqual(result.events, ())
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("reentry_cooldown_active",))
        self.assertEqual(
            store.get_trade_signal_state("PEPE").binance_symbol,
            "PEPEUSDT",
        )

    def test_dispatch_backfills_binance_symbol_before_a_kline_failure(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=899_999_999,
        )
        store.states["PEPE"] = state
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        failed = dispatch_trade_signals(
            snapshot,
            lambda _: (_ for _ in ()).throw(RuntimeError("K-line unavailable")),
            store,
            notifier,
        )
        loaded_symbols = []
        recovered = dispatch_trade_signals(
            {"complete": False, "comparisons": []},
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
            store,
            notifier,
        )

        self.assertEqual(failed.failures[0].canonical_symbol, "PEPE")
        self.assertEqual(store.states["PEPE"].binance_symbol, "PEPEUSDT")
        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(recovered.events, (EXIT_LONG,))

    def test_dispatch_backfills_binance_symbol_before_a_replay_gap(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        gap_candles = list(candles)
        gap_candles[-1] = replace(
            gap_candles[-1],
            close_time=(
                gap_candles[-1].close_time + FIFTEEN_MINUTES_MILLISECONDS
            ),
        )
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=candles[-2].close_time,
        )
        store.states["PEPE"] = state
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        failed = dispatch_trade_signals(
            snapshot, lambda _: gap_candles, store, notifier
        )

        self.assertIn("trailing replay gap", failed.failures[0].message)
        self.assertEqual(
            store.states["PEPE"],
            replace(state, binance_symbol="PEPEUSDT"),
        )

        loaded_symbols = []
        recovered = dispatch_trade_signals(
            {"complete": False, "comparisons": []},
            lambda symbol: loaded_symbols.append(symbol) or candles,
            store,
            notifier,
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(recovered.events, (EXIT_LONG,))

    def test_dispatch_stops_active_long_when_oi_is_blocked_during_a_replay_gap(
        self,
    ) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        gap_candles = list(candles)
        gap_candles[-1] = replace(
            gap_candles[-1],
            close_time=(
                gap_candles[-1].close_time + FIFTEEN_MINUTES_MILLISECONDS
            ),
        )
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=candles[-2].close_time,
            binance_symbol="PEPEUSDT",
        )
        store.states["PEPE"] = state
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: gap_candles, store, notifier
        )

        self.assertEqual(result.events, (STOP_LONG,))
        self.assertIn("trailing replay gap", result.failures[0].message)
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons,
            ("oi_to_market_cap_in_ambush_zone",),
        )
        self.assertEqual(store.states["PEPE"], replace(state, status=NO_ADD))
        self.assertEqual(notifier.stop_longs, [])

    def test_incomplete_snapshot_backfills_active_cooldown_binance_symbol(
        self,
    ) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        candles = _long_setup_candles()
        state = TradeSignalState(
            status=REENTRY_COOLDOWN,
            cooldown_until_candle_close_time=(
                candles[-1].close_time + 4 * FIFTEEN_MINUTES_MILLISECONDS
            ),
        )
        store.states["PEPE"] = state
        snapshot = {
            "complete": False,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }
        loaded_symbols = []

        result = dispatch_trade_signals(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or candles,
            store,
            notifier,
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("reentry_cooldown_active",))
        self.assertEqual(
            store.states["PEPE"],
            replace(state, binance_symbol="PEPEUSDT"),
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
            [(("AKE", "BULLA"), (), False)],
        )
        self.assertEqual(store.state.can_long, ("AKE", "BULLA"))
        self.assertEqual(store.state.stop_long, ("ESPORTS", "ON"))
        self.assertEqual(store.state.exit_long, ())
        self.assertEqual(store.state.last_sent_at, now)

    def test_does_not_send_list_when_only_stop_long_symbols_appear(self) -> None:
        now = datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc)
        last_sent_at = now - timedelta(minutes=10)
        store = ConditionListStore(
            SimpleNamespace(
                can_long=("AKE",),
                stop_long=("ON",),
                exit_long=(),
                last_sent_at=last_sent_at,
            )
        )
        notifier = ConditionListNotifier()

        result = dispatch_trade_condition_list(
            (
                _condition_scan("AKE", CAN_LONG),
                _condition_scan("ON", STOP_LONG),
                _condition_scan("ESPORTS", STOP_LONG),
            ),
            store,
            notifier,
            now,
        )

        self.assertIsNone(result)
        self.assertEqual(notifier.lists, [])
        self.assertEqual(store.state.last_sent_at, last_sent_at)

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
        self.assertEqual(notifier.lists, [((), ("KOMA",), False)])
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
        self.assertEqual(notifier.lists, [(("AKE",), (), True)])
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

    def test_delivered_condition_list_is_not_repeated_when_state_write_retries(
        self,
    ) -> None:
        now = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
        previous_state = SimpleNamespace(
            can_long=("AKE",),
            stop_long=("ON",),
            exit_long=(),
            last_sent_at=now - timedelta(minutes=5),
        )
        store = FailFirstConditionListStateWriteStore(previous_state)
        notifier = ConditionListNotifier()
        scans = (
            _condition_scan("AKE", CAN_LONG),
            _condition_scan("BULLA", CAN_LONG),
            _condition_scan("ON", STOP_LONG),
        )

        with self.assertRaisesRegex(RuntimeError, "list state write failed"):
            dispatch_trade_condition_list(scans, store, notifier, now)
        retried = dispatch_trade_condition_list(
            scans, store, notifier, now + timedelta(minutes=2)
        )

        self.assertEqual(retried, "updated")
        self.assertEqual(len(notifier.lists), 1)
        self.assertEqual(store.state.can_long, ("AKE", "BULLA"))

    def test_exits_if_protection_is_breached_after_high_rsi_continues_long(self) -> None:
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
            snapshot, lambda _: _rsi_above_60_candles(), store, notifier
        )
        reentered = dispatch_trade_signals(
            snapshot, lambda _: _second_long_setup_candles(), store, notifier
        )

        self.assertEqual(first.events, ("long",))
        self.assertEqual(first.details[0].event_type, "long")
        self.assertEqual(first.details[0].canonical_symbol, "PEPE")
        self.assertEqual(first.details[0].candle_close_time, 900_899_999)
        self.assertEqual(first.details[0].reasons, ())
        self.assertEqual(repeated.events, ())
        self.assertEqual(stopped.events, ())
        self.assertEqual(reentered.events, (EXIT_LONG,))
        self.assertEqual(reentered.details[0].event_type, EXIT_LONG)
        self.assertIn("close_below_ema200_exit_buffer", reentered.details[0].reasons)
        self.assertEqual(
            notifier.signals,
            [("long", "PEPE", 90)],
        )
        self.assertEqual(notifier.stop_longs, [])

    def test_failed_resume_notification_keeps_no_add_state(self) -> None:
        store = MemoryStore()
        candles = _second_long_setup_candles()
        previous_state = TradeSignalState(
            status="no_add",
            entry_price=100.5,
            stop_loss=90,
            entry_atr=1,
            highest_close=100.5,
            last_processed_candle_close_time=candles[-1].close_time,
        )
        store.states["PEPE"] = previous_state
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

        result = dispatch_trade_signals(
            snapshot,
            lambda _: candles,
            store,
            FailingTradeSignalNotifier(),
        )

        persisted = store.get_trade_signal_state("PEPE")
        self.assertEqual(persisted.status, NO_ADD)
        self.assertEqual(persisted.entry_price, previous_state.entry_price)
        self.assertEqual(persisted.binance_symbol, "PEPEUSDT")
        self.assertEqual(result.events, ())
        self.assertIn("WeCom failed", result.failures[0].message)

    def test_requires_oi_to_market_cap_strictly_above_90_percent(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.9,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(snapshot, lambda _: _long_setup_candles(), store, notifier)

        self.assertEqual(result.events, ())
        self.assertEqual(notifier.signals, [])

    def test_does_not_enter_long_in_ambush_zone(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        loaded_symbols = []
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 2.01,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
            store,
            notifier,
        )

        self.assertEqual(result.events, ())
        self.assertEqual(notifier.signals, [])
        self.assertEqual(loaded_symbols, [])
        self.assertEqual(store.get_trade_signal_state("PEPE"), None)
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons,
            ("oi_to_market_cap_in_ambush_zone",),
        )

    def test_allows_entry_at_exact_two_times_market_cap(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 2,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )

        self.assertEqual(result.events, ("long",))
        self.assertEqual(store.get_trade_signal_state("PEPE").status, "long")

    def test_mandatory_exit_takes_priority_over_ambush_zone_stop(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=899_999_999,
        )
        notifier = RecordingNotifier()
        candles = _atr_exit_candles()
        loaded_symbols = []
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or candles,
            store,
            notifier,
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.events, (EXIT_LONG,))
        self.assertIn("atr_stop_loss", result.details[0].reasons)
        self.assertEqual(notifier.stop_longs, [])
        self.assertEqual(result.scans[0].status, EXIT_LONG)

    def test_requires_price_adjusted_aggregate_oi_to_increase(self) -> None:
        indicators = TradeIndicators(
            candle_close_time=1,
            previous_close=100,
            close=110,
            rsi=40,
            previous_rsi=39,
            ema200=100,
            previous_ema200=100,
            ema200_slope_reference=99,
            atr=2,
            quote_volume=201,
            previous_quote_volume=100,
            average_quote_volume=100,
            ema200_breakout_candles_ago=0,
        )

        reasons = _aggregate_oi_entry_reasons(
            {
                "canonical_symbol": "PEPE",
                "total_oi_usd": 110,
            },
            {"PEPE": 100},
            indicators,
        )

        self.assertEqual(
            reasons,
            ("aggregate_oi_not_increasing_after_price_adjustment",),
        )

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
            result.scans[0].reasons,
            ("aggregate_oi_not_increasing_after_price_adjustment",),
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
        self.assertEqual(result.scans[0].status, "can_long")
        self.assertEqual(result.scans[0].reasons, ())

    def test_incomplete_snapshot_pauses_additions_for_healthy_active_long(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = TradeSignalState("long", 100.5, 90)
        snapshot = {
            "complete": False,
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
            None,
            lambda _: _post_entry_candles(),
            store,
            RecordingNotifier(),
        )

        self.assertEqual(result.events, ())
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("data_source_incomplete",))

    def test_does_not_scan_assets_at_exactly_90_percent(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.9,
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
            else _rsi_above_60_candles(),
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
                        "quote_volume_not_above_average",
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

    def test_scans_ambush_zone_without_loading_klines(self) -> None:
        loaded_symbols = []
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = scan_trade_conditions(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
        )

        self.assertEqual(loaded_symbols, [])
        self.assertEqual(result.failures, ())
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons,
            ("oi_to_market_cap_in_ambush_zone",),
        )

    def test_scans_active_ambush_position_for_trailing_exit(self) -> None:
        loaded_symbols = []
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=899_999_999,
            binance_symbol="PEPEUSDT",
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = scan_trade_conditions(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
            {"PEPE": state},
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.failures, ())
        self.assertEqual(result.scans[0].status, EXIT_LONG)
        self.assertEqual(result.state_updates, ())
        self.assertIn("trailing_take_profit", result.scans[0].reasons)

    def test_scan_does_not_advance_past_unnotified_historical_exit(self) -> None:
        candles = _long_setup_candles()
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=96,
            entry_atr=2,
            highest_close=100,
            last_processed_candle_close_time=candles[-1].close_time,
            binance_symbol="PEPEUSDT",
        )
        for close in (108, 100, 106):
            previous = candles[-1]
            candles.append(
                Candle(
                    close_time=(
                        previous.close_time + FIFTEEN_MINUTES_MILLISECONDS
                    ),
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    quote_volume=200,
                )
            )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        first = scan_trade_conditions(snapshot, lambda _: candles, {"PEPE": state})
        persisted = {"PEPE": state}
        persisted.update(first.state_updates)
        second = scan_trade_conditions(snapshot, lambda _: candles, persisted)

        self.assertEqual(first.state_updates, ())
        self.assertEqual(first.scans[0].status, EXIT_LONG)
        self.assertEqual(second.scans[0].status, EXIT_LONG)
        self.assertEqual(
            second.scans[0].candle_close_time,
            first.scans[0].candle_close_time,
        )

    def test_scan_respects_active_reentry_cooldown(self) -> None:
        candles = _long_setup_candles()
        state = TradeSignalState(
            status=REENTRY_COOLDOWN,
            cooldown_until_candle_close_time=(
                candles[-1].close_time + 4 * FIFTEEN_MINUTES_MILLISECONDS
            ),
            binance_symbol="PEPEUSDT",
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        result = scan_trade_conditions(snapshot, lambda _: candles, {"PEPE": state})

        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("reentry_cooldown_active",))

    def test_scan_persists_no_add_when_active_long_enters_ambush_zone(self) -> None:
        candles = _long_setup_candles()
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=96,
            entry_atr=2,
            highest_close=100,
            last_processed_candle_close_time=candles[-1].close_time,
            binance_symbol="PEPEUSDT",
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        result = scan_trade_conditions(snapshot, lambda _: candles, {"PEPE": state})

        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.state_updates[0][0], "PEPE")
        self.assertEqual(result.state_updates[0][1].status, NO_ADD)

    def test_scan_never_requires_exit_without_an_active_position(self) -> None:
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        result = scan_trade_conditions(
            snapshot, lambda _: _close_below_ema200_candles()
        )

        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertNotEqual(result.scans[0].status, EXIT_LONG)

    def test_scan_backfills_binance_symbol_into_migrated_active_state(self) -> None:
        candles = _long_setup_candles()
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=96,
            entry_atr=2,
            highest_close=100,
            last_processed_candle_close_time=candles[-1].close_time,
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        result = scan_trade_conditions(snapshot, lambda _: candles, {"PEPE": state})

        self.assertEqual(result.state_updates[0][0], "PEPE")
        self.assertEqual(result.state_updates[0][1].binance_symbol, "PEPEUSDT")

    def test_scan_backfills_binance_symbol_before_a_kline_failure(self) -> None:
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=899_999_999,
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        failed = scan_trade_conditions(
            snapshot,
            lambda _: (_ for _ in ()).throw(RuntimeError("K-line unavailable")),
            {"PEPE": state},
        )
        persisted = {"PEPE": state}
        persisted.update(failed.state_updates)
        loaded_symbols = []
        recovered = scan_trade_conditions(
            {"complete": False, "comparisons": []},
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
            persisted,
        )

        self.assertEqual(failed.failures[0].canonical_symbol, "PEPE")
        self.assertEqual(
            failed.state_updates,
            (("PEPE", replace(state, binance_symbol="PEPEUSDT")),),
        )
        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(recovered.scans[0].status, EXIT_LONG)

    def test_scan_stops_active_long_for_oi_threshold_when_kline_load_fails(
        self,
    ) -> None:
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            binance_symbol="PEPEUSDT",
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        result = scan_trade_conditions(
            snapshot,
            lambda _: (_ for _ in ()).throw(RuntimeError("K-line unavailable")),
            {"PEPE": state},
        )

        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons,
            ("oi_to_market_cap_in_ambush_zone",),
        )
        self.assertEqual(
            result.state_updates,
            (("PEPE", replace(state, status=NO_ADD)),),
        )

    def test_scan_stops_active_long_when_comparison_and_klines_are_unavailable(
        self,
    ) -> None:
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            binance_symbol="PEPEUSDT",
        )

        result = scan_trade_conditions(
            {"complete": True, "comparisons": []},
            lambda _: (_ for _ in ()).throw(RuntimeError("K-line unavailable")),
            {"PEPE": state},
        )

        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("data_source_incomplete",))
        self.assertEqual(
            result.state_updates,
            (("PEPE", replace(state, status=NO_ADD)),),
        )

    def test_scan_backfills_binance_symbol_before_a_replay_gap(self) -> None:
        candles = _long_setup_candles()
        gap_candles = list(candles)
        gap_candles[-1] = replace(
            gap_candles[-1],
            close_time=(
                gap_candles[-1].close_time + FIFTEEN_MINUTES_MILLISECONDS
            ),
        )
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=candles[-2].close_time,
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.2,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        failed = scan_trade_conditions(
            snapshot, lambda _: gap_candles, {"PEPE": state}
        )

        self.assertIn("trailing replay gap", failed.failures[0].message)
        self.assertEqual(
            failed.state_updates,
            (("PEPE", replace(state, binance_symbol="PEPEUSDT")),),
        )

        persisted = {"PEPE": state}
        persisted.update(failed.state_updates)
        loaded_symbols = []
        recovered = scan_trade_conditions(
            {"complete": False, "comparisons": []},
            lambda symbol: loaded_symbols.append(symbol) or candles,
            persisted,
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(recovered.scans[0].status, EXIT_LONG)

    def test_scan_stops_active_long_when_oi_is_blocked_during_a_replay_gap(
        self,
    ) -> None:
        candles = _long_setup_candles()
        gap_candles = list(candles)
        gap_candles[-1] = replace(
            gap_candles[-1],
            close_time=(
                gap_candles[-1].close_time + FIFTEEN_MINUTES_MILLISECONDS
            ),
        )
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            last_processed_candle_close_time=candles[-2].close_time,
            binance_symbol="PEPEUSDT",
        )
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                }
            ],
        }

        result = scan_trade_conditions(
            snapshot, lambda _: gap_candles, {"PEPE": state}
        )

        self.assertIn("trailing replay gap", result.failures[0].message)
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons,
            ("oi_to_market_cap_in_ambush_zone",),
        )
        self.assertEqual(
            result.state_updates,
            (("PEPE", replace(state, status=NO_ADD)),),
        )

    def test_scans_missing_active_position_during_incomplete_snapshot(self) -> None:
        loaded_symbols = []
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=899_999_999,
            binance_symbol="PEPEUSDT",
        )
        snapshot = {"complete": False, "comparisons": []}

        result = scan_trade_conditions(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
            {"PEPE": state},
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.failures, ())
        self.assertEqual(result.scans[0].canonical_symbol, "PEPE")
        self.assertEqual(result.scans[0].status, EXIT_LONG)
        self.assertIn("trailing_take_profit", result.scans[0].reasons)

    def test_scans_active_position_with_persisted_binance_symbol(self) -> None:
        loaded_symbols = []
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=101,
            entry_atr=1,
            highest_close=102,
            last_processed_candle_close_time=899_999_999,
            binance_symbol="PEPEUSDT",
        )
        snapshot = {
            "complete": False,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "total_oi_usd": 100,
                    "oi_to_market_cap": 2.01,
                    "contracts": [{"venue": "OKX", "symbol": "PEPE-USDT-SWAP"}],
                }
            ],
        }

        result = scan_trade_conditions(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
            {"PEPE": state},
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.failures, ())
        self.assertEqual(result.scans[0].status, EXIT_LONG)

    def test_scan_isolates_missing_binance_contract_to_its_symbol(self) -> None:
        loaded_symbols = []
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "BAD",
                    "oi_to_market_cap": 1.6,
                    "contracts": [{"venue": "OKX", "symbol": "BAD-USDT-SWAP"}],
                },
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.6,
                    "contracts": [
                        {"venue": "Binance", "symbol": "PEPEUSDT"}
                    ],
                },
            ],
        }

        result = scan_trade_conditions(
            snapshot,
            lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
        )

        self.assertEqual(loaded_symbols, ["PEPEUSDT"])
        self.assertEqual(result.failures[0].canonical_symbol, "BAD")
        self.assertEqual(
            {scan.canonical_symbol: scan.status for scan in result.scans},
            {"BAD": "kline_error", "PEPE": CAN_LONG},
        )

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

    def test_stops_active_long_at_or_below_90_percent_before_loading_rsi(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.9,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _rsi_above_60_candles(), store, notifier
        )

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(
            result.details[0].reasons,
            ("oi_to_market_cap_not_above_90",),
        )
        self.assertEqual(notifier.stop_longs, [])
        self.assertEqual(store.states["PEPE"].status, "no_add")

    def test_stops_active_long_when_oi_to_market_cap_is_below_90_percent(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.89,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(
            result.details[0].reasons, ("oi_to_market_cap_not_above_90",)
        )
        self.assertEqual(notifier.stop_longs, [])
        self.assertEqual(store.states["PEPE"].status, "no_add")
        self.assertEqual(len(result.scans), 1)
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons,
            ("oi_to_market_cap_not_above_90",),
        )

    def test_stops_active_long_for_oi_threshold_when_kline_load_fails(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.89,
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
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Binance timed out", result.failures[0].message)
        self.assertEqual(
            result.details[0].reasons, ("oi_to_market_cap_not_above_90",)
        )
        self.assertIsNone(result.details[0].candle_close_time)
        self.assertEqual(notifier.stop_longs, [])
        self.assertEqual(store.states["PEPE"].status, "no_add")

    def test_stops_active_long_for_oi_threshold_without_a_binance_contract(
        self,
    ) -> None:
        store = MemoryStore()
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
        )
        store.states["PEPE"] = state
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 2.01,
                    "contracts": [
                        {"venue": "OKX", "symbol": "PEPE-USDT-SWAP"}
                    ],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot,
            lambda _: self.fail("K-line loader must not receive a guessed symbol"),
            store,
            RecordingNotifier(),
        )

        self.assertEqual(result.events, (STOP_LONG,))
        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(
            result.scans[0].reasons,
            ("oi_to_market_cap_in_ambush_zone",),
        )
        self.assertEqual(store.states["PEPE"], replace(state, status=NO_ADD))

    def test_stops_active_long_when_comparison_and_klines_are_unavailable(
        self,
    ) -> None:
        store = MemoryStore()
        state = TradeSignalState(
            status="long",
            entry_price=100,
            stop_loss=98,
            entry_atr=1,
            highest_close=100,
            binance_symbol="PEPEUSDT",
        )
        store.states["PEPE"] = state

        result = dispatch_trade_signals(
            {"complete": True, "comparisons": []},
            lambda _: (_ for _ in ()).throw(RuntimeError("K-line unavailable")),
            store,
            RecordingNotifier(),
        )

        self.assertEqual(result.events, (STOP_LONG,))
        self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
        self.assertEqual(result.scans[0].status, STOP_LONG)
        self.assertEqual(result.scans[0].reasons, ("data_source_incomplete",))
        self.assertEqual(store.states["PEPE"], replace(state, status=NO_ADD))

    def test_loads_risk_candles_before_sending_oi_threshold_stop(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 0.9,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                },
                {
                    "canonical_symbol": "DOGE",
                    "oi_to_market_cap": 1.4,
                    "contracts": [{"venue": "Binance", "symbol": "DOGEUSDT"}],
                },
            ],
        }

        loaded_symbols = []

        def load(symbol: str):
            loaded_symbols.append(symbol)
            return _rsi_above_60_candles()

        result = dispatch_trade_signals(snapshot, load, store, notifier)

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(result.failures, ())
        self.assertCountEqual(loaded_symbols, ["PEPEUSDT", "DOGEUSDT"])

    def test_keeps_active_long_when_rsi_is_above_60(self) -> None:
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
        candles = _rsi_above_60_candles()

        result = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

        self.assertEqual(result.events, ())
        self.assertEqual(notifier.signals, [])
        self.assertEqual(notifier.stop_longs, [])
        self.assertEqual(store.states["PEPE"].status, "long")

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
        self.assertEqual(notifier.stop_longs, [])
        self.assertLess(result.details[0].rsi, 50)
        self.assertLess(result.details[0].close, result.details[0].ema200)
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
