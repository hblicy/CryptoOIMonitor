import unittest

from crypto_oi_monitor.trading import (
    BINANCE_KLINES_URL,
    LONG,
    Candle,
    TradeIndicators,
    entry_reasons,
    evaluate_trade_setup,
    fetch_binance_closed_candles,
    stop_long_reasons,
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


class TradeSetupTests(unittest.TestCase):
    @staticmethod
    def _valid_indicators(**changes) -> TradeIndicators:
        values = {
            "candle_close_time": 1,
            "previous_close": 99,
            "close": 101,
            "rsi": 40,
            "previous_rsi": 39,
            "ema200": 100,
            "previous_ema200": 100,
            "atr": 2,
            "quote_volume": 120,
            "previous_quote_volume": 100,
        }
        values.update(changes)
        return TradeIndicators(**values)

    def test_requires_rsi_to_recover_instead_of_only_staying_below_50(self) -> None:
        indicators = self._valid_indicators(previous_rsi=42)

        self.assertEqual(entry_reasons(indicators), ("rsi_not_rising",))

    def test_requires_fresh_close_crossover_above_ema200(self) -> None:
        indicators = self._valid_indicators(previous_close=101)

        self.assertEqual(entry_reasons(indicators), ("ema200_not_crossed_up",))

    def test_requires_current_quote_volume_to_exceed_previous_candle(self) -> None:
        indicators = self._valid_indicators(quote_volume=100)

        self.assertEqual(entry_reasons(indicators), ("quote_volume_not_increasing",))

    def test_stop_long_ignores_one_shot_entry_confirmations(self) -> None:
        indicators = self._valid_indicators(
            previous_close=101,
            previous_rsi=42,
            quote_volume=80,
        )

        self.assertEqual(stop_long_reasons(indicators), ())

    def test_stop_long_when_close_is_not_above_ema200(self) -> None:
        indicators = self._valid_indicators(close=100)

        self.assertEqual(stop_long_reasons(indicators), ("close_not_above_ema200",))

    def test_fetches_1002_candles_and_discards_unclosed_binance_candle(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def get_json(self, url, params):
                self.calls.append((url, params))
                return [
                    [index, "0", "12", "8", "10", "0", index + 1, "250"]
                    for index in range(1002)
                ]

        client = FakeClient()
        candles = fetch_binance_closed_candles(client, "PEPEUSDT")

        self.assertEqual(len(candles), 1001)
        self.assertEqual(candles[0], Candle(1, 12, 8, 10, 250))
        self.assertEqual(candles[-1], Candle(1001, 12, 8, 10, 250))
        self.assertEqual(
            client.calls,
            [
                (
                    BINANCE_KLINES_URL,
                    {"symbol": "PEPEUSDT", "interval": "15m", "limit": "1002"},
                )
            ],
        )

    def test_rejects_insufficient_history_for_ema200_warmup(self) -> None:
        class FakeClient:
            def get_json(self, url, params):
                return [
                    [index, "0", "12", "8", "10", "0", index + 1, "250"]
                    for index in range(202)
                ]

        with self.assertRaisesRegex(ValueError, "1001 closed candles"):
            fetch_binance_closed_candles(FakeClient(), "PEPEUSDT")

    def test_emits_long_when_breakout_volume_and_rsi_conditions_are_met(self) -> None:
        closes = (
            [100] * 950
            + [100 + (index % 2) * 2 for index in range(49)]
            + [95, 100.5]
        )
        candles = _candles(closes, [100] * 1000 + [120])

        signal = evaluate_trade_setup(candles)

        self.assertEqual(signal.side, LONG)
        self.assertEqual(signal.entry_price, 100.5)
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertGreaterEqual(signal.rsi, 35)
        self.assertGreater(signal.rsi, signal.previous_rsi)
        self.assertLess(signal.rsi, 50)
        self.assertGreater(signal.entry_price, signal.ema200)

    def test_does_not_emit_short_when_rsi_crosses_down_80_below_ema200(self) -> None:
        candles = _warm_candles(
            [300 - index for index in range(200)]
            + [102 + index for index in range(60)]
            + [154],
            warmup_close=300,
        )

        self.assertIsNone(evaluate_trade_setup(candles))

    def test_does_not_emit_entry_after_rsi_reaches_50(self) -> None:
        long_candles = _warm_candles(
            [100 + index for index in range(200)]
            + [298 - index for index in range(60)]
            + [260]
        )
        short_candles = _warm_candles(
            [300 - index for index in range(200)]
            + [102 + index for index in range(60)]
            + [145],
            warmup_close=300,
        )

        self.assertIsNone(evaluate_trade_setup(long_candles))
        self.assertIsNone(evaluate_trade_setup(short_candles))

    def test_rejects_unclosed_or_insufficient_candles(self) -> None:
        with self.assertRaisesRegex(ValueError, "1001 closed candles"):
            evaluate_trade_setup(_candles([100] * 1000))


if __name__ == "__main__":
    unittest.main()
