import unittest
from threading import Barrier

from crypto_oi_monitor.http_client import DataSourceRequestError
from crypto_oi_monitor.trade_dispatch import (
    dispatch_trade_signals,
    scan_trade_conditions,
)
from crypto_oi_monitor.trading import Candle


def _candles(closes: list[float]) -> list[Candle]:
    return [
        Candle(
            close_time=index,
            high=close + 1,
            low=close - 1,
            close=close,
        )
        for index, close in enumerate(closes)
    ]


def _warm_candles(closes: list[float], warmup_close: float = 100) -> list[Candle]:
    return _candles([warmup_close] * 1000 + closes)


def _long_setup_candles() -> list[Candle]:
    return _warm_candles(
        [100 + index for index in range(200)]
        + [298 - index for index in range(60)]
        + [246]
    )


def _rsi_above_50_candles() -> list[Candle]:
    return _warm_candles(
        [100 + index for index in range(200)]
        + [298 - index for index in range(60)]
        + [260]
    )


def _close_below_ema200_candles() -> list[Candle]:
    return _warm_candles([300 - index for index in range(201)], warmup_close=300)


class MemoryStore:
    def __init__(self) -> None:
        self.states: dict[str, str] = {}

    def get_trade_signal_state(self, symbol: str) -> str | None:
        return self.states.get(symbol)

    def set_trade_signal_state(self, symbol: str, side: str) -> None:
        self.states[symbol] = side

    def clear_trade_signal_state(self, symbol: str) -> None:
        self.states.pop(symbol, None)


class RecordingNotifier:
    def __init__(self) -> None:
        self.signals = []
        self.stop_longs = []

    def send_trade_signal(self, signal, comparison) -> None:
        self.signals.append((signal.side, comparison["canonical_symbol"]))

    def send_stop_long(self, comparison, rsi, close, ema200) -> None:
        self.stop_longs.append((comparison["canonical_symbol"], rsi, close, ema200))


class TradeDispatchTests(unittest.TestCase):
    def test_sends_one_long_signal_until_rsi_returns_to_50(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.2,
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
        self.assertEqual(first.details[0].candle_close_time, 1260)
        self.assertEqual(first.details[0].reasons, ())
        self.assertEqual(repeated.events, ())
        self.assertEqual(stopped.events, ("stop_long",))
        self.assertEqual(stopped.details[0].event_type, "stop_long")
        self.assertEqual(stopped.details[0].reasons, ("rsi_above_50",))
        self.assertEqual(reentered.events, ("long",))
        self.assertEqual(notifier.signals, [("long", "PEPE"), ("long", "PEPE")])
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertGreater(notifier.stop_longs[0][1], 50)

    def test_requires_oi_to_market_cap_strictly_above_110_percent(self) -> None:
        store = MemoryStore()
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

        result = dispatch_trade_signals(snapshot, lambda _: _long_setup_candles(), store, notifier)

        self.assertEqual(result.events, ())
        self.assertEqual(notifier.signals, [])

    def test_does_not_stop_active_long_at_exactly_110_percent(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
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
        self.assertEqual(store.states["PEPE"], "long")

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
                ("DOGE", "stop_long", ("rsi_not_below_50",)),
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

    def test_stops_active_long_below_110_percent_before_loading_rsi(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.09,
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
            ("oi_to_market_cap_below_110",),
        )
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertNotIn("PEPE", store.states)

    def test_stops_active_long_when_oi_to_market_cap_is_below_110_percent(self) -> None:
        store = MemoryStore()
        store.states["PEPE"] = "long"
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [
                {
                    "canonical_symbol": "PEPE",
                    "oi_to_market_cap": 1.09,
                    "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
                }
            ],
        }

        result = dispatch_trade_signals(
            snapshot, lambda _: _long_setup_candles(), store, notifier
        )

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(
            result.details[0].reasons, ("oi_to_market_cap_below_110",)
        )
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertNotIn("PEPE", store.states)

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
            result.details[0].reasons, ("oi_to_market_cap_below_110",)
        )
        self.assertIsNone(result.details[0].candle_close_time)
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertNotIn("PEPE", store.states)

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
                    "oi_to_market_cap": 1.2,
                    "contracts": [{"venue": "Binance", "symbol": "DOGEUSDT"}],
                },
            ],
        }

        def load(symbol: str):
            self.assertEqual(notifier.stop_longs, [("PEPE", None, None, None)])
            self.assertNotIn("PEPE", store.states)
            self.assertEqual(symbol, "DOGEUSDT")
            return _candles([100] * 1001)

        result = dispatch_trade_signals(snapshot, load, store, notifier)

        self.assertEqual(result.events, ("stop_long",))
        self.assertEqual(result.failures, ())

    def test_does_not_stop_active_long_when_rsi_is_exactly_50(self) -> None:
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

        self.assertEqual(result.events, ())
        self.assertEqual(notifier.signals, [])
        self.assertEqual(notifier.stop_longs, [])
        self.assertEqual(store.states["PEPE"], "long")

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
        self.assertEqual(result.details[0].reasons, ("close_below_ema200",))
        self.assertEqual(notifier.stop_longs[0][0], "PEPE")
        self.assertLess(notifier.stop_longs[0][1], 50)
        self.assertLess(notifier.stop_longs[0][2], notifier.stop_longs[0][3])
        self.assertNotIn("PEPE", store.states)

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
        self.assertEqual(notifier.signals, [("long", "PEPE")])
        self.assertEqual(result.scans[0].status, "can_long")
        self.assertEqual(store.states["PEPE"], "long")

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
        self.assertEqual(notifier.signals, [("long", "PEPE")])
        self.assertEqual(result.failures[0].canonical_symbol, "NEW")
        self.assertIn("1001 根", result.failures[0].message)

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
        self.assertEqual(notifier.signals, [("long", "PEPE")])
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
        self.assertEqual(notifier.signals, [("long", "PEPE")])
        self.assertEqual(result.failures[0].canonical_symbol, "BAD")
        self.assertIn("malformed Binance kline", result.failures[0].message)


if __name__ == "__main__":
    unittest.main()
