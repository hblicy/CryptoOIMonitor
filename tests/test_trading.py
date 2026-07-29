import unittest

from crypto_oi_monitor.trading import (
    BINANCE_KLINES_URL,
    LONG,
    Candle,
    evaluate_trade_setup,
    fetch_binance_closed_candles,
)


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


class TradeSetupTests(unittest.TestCase):
    def test_fetches_1002_candles_and_discards_unclosed_binance_candle(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def get_json(self, url, params):
                self.calls.append((url, params))
                return [
                    [index, "0", "12", "8", "10", "0", index + 1]
                    for index in range(1002)
                ]

        client = FakeClient()
        candles = fetch_binance_closed_candles(client, "PEPEUSDT")

        self.assertEqual(len(candles), 1001)
        self.assertEqual(candles[0], Candle(1, 12, 8, 10))
        self.assertEqual(candles[-1], Candle(1001, 12, 8, 10))
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
                    [index, "0", "12", "8", "10", "0", index + 1]
                    for index in range(202)
                ]

        with self.assertRaisesRegex(ValueError, "1001 closed candles"):
            fetch_binance_closed_candles(FakeClient(), "PEPEUSDT")

    def test_emits_long_when_rsi_is_below_50_above_ema200_without_20_cross(self) -> None:
        candles = _candles(
            [100 + index for index in range(200)]
            + [298 - index for index in range(60)]
            + [246, 247]
        )

        signal = evaluate_trade_setup(candles)

        self.assertEqual(signal.side, LONG)
        self.assertEqual(signal.entry_price, 247)
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertGreater(signal.rsi, 20)
        self.assertGreater(signal.previous_rsi, 20)
        self.assertLess(signal.rsi, 50)
        self.assertGreater(signal.entry_price, signal.ema200)

    def test_does_not_emit_short_when_rsi_crosses_down_80_below_ema200(self) -> None:
        candles = _candles(
            [300 - index for index in range(200)]
            + [102 + index for index in range(60)]
            + [154]
        )

        self.assertIsNone(evaluate_trade_setup(candles))

    def test_does_not_emit_entry_after_rsi_reaches_50(self) -> None:
        long_candles = _candles(
            [100 + index for index in range(200)]
            + [298 - index for index in range(60)]
            + [260]
        )
        short_candles = _candles(
            [300 - index for index in range(200)]
            + [102 + index for index in range(60)]
            + [145]
        )

        self.assertIsNone(evaluate_trade_setup(long_candles))
        self.assertIsNone(evaluate_trade_setup(short_candles))

    def test_rejects_unclosed_or_insufficient_candles(self) -> None:
        with self.assertRaisesRegex(ValueError, "201 closed candles"):
            evaluate_trade_setup(_candles([100] * 200))


if __name__ == "__main__":
    unittest.main()
