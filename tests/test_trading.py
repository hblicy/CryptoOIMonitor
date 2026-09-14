import unittest

from crypto_oi_monitor.trading import (
    BINANCE_KLINES_URL,
    LONG,
    Candle,
    TradeIndicators,
    entry_reasons,
    evaluate_trade_setup,
    fetch_binance_closed_candles,
    cooldown_candles_for_volatility_ratio,
    stop_long_reasons,
    trade_indicators,
    update_trailing_stop,
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
    def test_rejects_invalid_candle_numbers(self) -> None:
        invalid_candles = (
            (float("nan"), 99, 100, 10),
            (101, 99, 0, 10),
            (101, 99, 102, 10),
            (101, 99, 100, -1),
        )
        for high, low, close, quote_volume in invalid_candles:
            with self.subTest(
                high=high, low=low, close=close, quote_volume=quote_volume
            ):
                with self.assertRaises(ValueError):
                    Candle(1, high, low, close, quote_volume)
    def test_raises_trailing_stop_only_after_three_entry_atr_profit(self) -> None:
        self.assertEqual(
            update_trailing_stop(100, 2, 96, 100, 105),
            (105, 96),
        )
        self.assertEqual(
            update_trailing_stop(100, 2, 96, 105, 106),
            (106, 102),
        )

    def test_never_lowers_an_existing_trailing_stop(self) -> None:
        self.assertEqual(
            update_trailing_stop(100, 2, 103, 106, 105),
            (106, 103),
        )

    def test_maps_normalized_atr_ratio_to_dynamic_cooldown(self) -> None:
        self.assertEqual(cooldown_candles_for_volatility_ratio(0.8), 3)
        self.assertEqual(cooldown_candles_for_volatility_ratio(0.800001), 4)
        self.assertEqual(cooldown_candles_for_volatility_ratio(1.2), 4)
        self.assertEqual(cooldown_candles_for_volatility_ratio(1.200001), 6)

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
            "ema200_slope_reference": 99,
            "atr": 2,
            "quote_volume": 200,
            "previous_quote_volume": 100,
            "average_quote_volume": 90,
            "ema200_breakout_candles_ago": 0,
        }
        values.update(changes)
        return TradeIndicators(**values)

    def test_requires_rsi_to_recover_instead_of_only_staying_below_60(self) -> None:
        indicators = self._valid_indicators(previous_rsi=42)

        self.assertEqual(entry_reasons(indicators), ("rsi_not_rising",))

    def test_does_not_require_current_quote_volume_to_exceed_previous_candle(
        self,
    ) -> None:
        indicators = self._valid_indicators(
            quote_volume=201,
            previous_quote_volume=400,
            average_quote_volume=100,
        )

        self.assertEqual(entry_reasons(indicators), ())

    def test_requires_ema200_crossover_within_three_closed_candles(self) -> None:
        indicators = self._valid_indicators(ema200_breakout_candles_ago=None)

        self.assertEqual(entry_reasons(indicators), ("ema200_not_crossed_up",))

    def test_accepts_ema200_breakout_from_the_previous_closed_candle(self) -> None:
        indicators = self._valid_indicators(ema200_breakout_candles_ago=1)

        self.assertEqual(entry_reasons(indicators), ())

    def test_requires_current_close_to_remain_above_ema200(self) -> None:
        indicators = self._valid_indicators(
            close=99,
            ema200_breakout_candles_ago=1,
        )

        self.assertEqual(entry_reasons(indicators), ("ema200_not_crossed_up",))

    def test_requires_ema200_to_rise_over_five_closed_candles(self) -> None:
        indicators = self._valid_indicators(ema200_slope_reference=100)

        self.assertEqual(entry_reasons(indicators), ("ema200_not_rising",))

    def test_requires_quote_volume_to_break_twenty_candle_average(self) -> None:
        indicators = self._valid_indicators(
            quote_volume=200,
            previous_quote_volume=100,
            average_quote_volume=100,
        )

        self.assertEqual(
            entry_reasons(indicators),
            ("quote_volume_not_above_average",),
        )

    def test_accepts_ema200_breakout_from_two_closed_candles_ago(self) -> None:
        closes = (
            [100] * 950
            + [100 + (index % 2) * 2 for index in range(45)]
            + [97, 98, 102, 102, 100, 100.5, 100.7, 100.9]
        )
        candles = _candles(closes, [100] * (len(closes) - 1) + [201])

        signal = evaluate_trade_setup(candles)

        self.assertIsNotNone(signal)
        self.assertEqual(trade_indicators(candles).ema200_breakout_candles_ago, 2)

    def test_rejects_ema200_breakout_older_than_three_closed_candles(self) -> None:
        closes = (
            [100] * 950
            + [100 + (index % 2) * 2 for index in range(45)]
            + [97, 98, 102, 102, 100, 100.5, 100.7, 100.8, 100.9]
        )
        candles = _candles(closes, [100] * (len(closes) - 1) + [201])

        self.assertIsNone(evaluate_trade_setup(candles))

    def test_accepts_high_rsi_when_rsi_is_rising(self) -> None:
        self.assertEqual(
            entry_reasons(self._valid_indicators(rsi=78, previous_rsi=77)),
            (),
        )

    def test_rejects_entry_when_rsi_is_not_rising(self) -> None:
        self.assertEqual(
            entry_reasons(self._valid_indicators(rsi=78, previous_rsi=78)),
            ("rsi_not_rising",),
        )

    def test_exposes_slope_reference_and_average_volume_from_closed_history(
        self,
    ) -> None:
        candles = _candles(
            [100] * 995 + [99, 100, 100, 100, 100, 101],
            [50] * 980 + list(range(1, 21)) + [100],
        )

        indicators = trade_indicators(candles)

        self.assertLess(indicators.ema200_slope_reference, indicators.ema200)
        self.assertEqual(indicators.average_quote_volume, sum(range(1, 21)) / 20)

    def test_stop_long_ignores_one_shot_entry_confirmations(self) -> None:
        indicators = self._valid_indicators(
            previous_close=101,
            previous_rsi=42,
            quote_volume=80,
        )

        self.assertEqual(stop_long_reasons(indicators), ())

    def test_stop_long_does_not_use_high_rsi(self) -> None:
        indicators = self._valid_indicators(rsi=78, previous_rsi=77)

        self.assertEqual(stop_long_reasons(indicators), ())

    def test_stop_long_when_close_is_not_above_ema200(self) -> None:
        indicators = self._valid_indicators(close=100)

        self.assertEqual(stop_long_reasons(indicators), ("close_not_above_ema200",))

    def test_fetches_1500_candles_and_discards_unclosed_binance_candle(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def get_json(self, url, params):
                self.calls.append((url, params))
                return [
                    [index, "0", "12", "8", "10", "0", index + 1, "250"]
                    for index in range(1500)
                ]

        client = FakeClient()
        candles = fetch_binance_closed_candles(client, "PEPEUSDT")

        self.assertEqual(len(candles), 1499)
        self.assertEqual(candles[0], Candle(1, 12, 8, 10, 250))
        self.assertEqual(candles[-1], Candle(1499, 12, 8, 10, 250))
        self.assertEqual(
            client.calls,
            [
                (
                    BINANCE_KLINES_URL,
                    {"symbol": "PEPEUSDT", "interval": "15m", "limit": "1500"},
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
            + [100 + (index % 2) * 2 for index in range(45)]
            + [97, 98, 102, 102, 100, 100.5]
        )
        candles = _candles(closes, [100] * 1000 + [201])

        signal = evaluate_trade_setup(candles)

        self.assertEqual(signal.side, LONG)
        self.assertEqual(signal.entry_price, 100.5)
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertGreater(signal.rsi, signal.previous_rsi)
        self.assertLess(signal.rsi, 60)
        self.assertGreater(signal.entry_price, signal.ema200)

    def test_does_not_emit_short_when_rsi_crosses_down_80_below_ema200(self) -> None:
        candles = _warm_candles(
            [300 - index for index in range(200)]
            + [102 + index for index in range(60)]
            + [154],
            warmup_close=300,
        )

        self.assertIsNone(evaluate_trade_setup(candles))

    def test_does_not_emit_entry_after_rsi_reaches_60(self) -> None:
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
