import unittest

from crypto_oi_monitor.alerts import ENTERED_HIGH_RISK, RECOVERED
from crypto_oi_monitor.notifier import WeComNotifier
from crypto_oi_monitor.trade_dispatch import TradeSignalState
from crypto_oi_monitor.trading import LONG, TradeIndicators, TradeSetup


class RecordingClient:
    def __init__(self) -> None:
        self.sent = []

    def post_json(self, url, payload):
        self.sent.append((url, payload))
        return {"errcode": 0}


def _indicators(rsi: float = 45, close: float = 101, ema200: float = 100) -> TradeIndicators:
    return TradeIndicators(
        candle_close_time=1_000,
        previous_close=99,
        close=close,
        rsi=rsi,
        previous_rsi=rsi - 1,
        ema200=ema200,
        previous_ema200=ema200 - 0.1,
        atr=3,
        quote_volume=120,
        previous_quote_volume=100,
    )


class WeComNotifierTests(unittest.TestCase):
    def test_sends_trade_condition_lists_with_symbols_only(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_trade_condition_list(
            ("AKE", "BULLA"), ("ESPORTS", "ON"), ("KOMA",), periodic=False
        )

        payload = client.sent[0][1]
        content = payload["text"]["content"]
        self.assertEqual(payload["msgtype"], "text")
        self.assertIn("交易条件列表更新", content)
        self.assertIn("AKE、BULLA", content)
        self.assertIn("ESPORTS、ON", content)
        self.assertIn("必须退出", content)
        self.assertIn("KOMA", content)
        self.assertNotIn("RSI", content)
        self.assertNotIn("OI / 市值", content)

    def test_sends_hourly_trade_condition_lists_with_periodic_title(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_trade_condition_list((), (), (), periodic=True)

        content = client.sent[0][1]["text"]["content"]
        self.assertIn("交易条件列表定时播报", content)
        self.assertIn("暂无", content)

    def test_sends_ambush_candidate_with_aggregate_metrics_only(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send(
            ENTERED_HIGH_RISK,
            {
                "canonical_symbol": "PEPE",
                "market_cap_usd": 100,
                "total_oi_usd": 250,
                "oi_to_market_cap": 2.5,
                "contracts": [
                    {"venue": "Binance", "oi_usd": 200},
                    {"venue": "OKX", "oi_usd": 50},
                ],
            },
        )

        self.assertEqual(client.sent[0][0], "https://wecom.example/webhook")
        payload = client.sent[0][1]
        self.assertEqual(payload["msgtype"], "text")
        content = payload["text"]["content"]
        self.assertIn("OI 埋伏候选", content)
        self.assertIn("PEPE", content)
        self.assertIn("250.00 USD", content)
        self.assertIn("250.00%", content)
        self.assertNotIn("交易所明细", content)
        self.assertNotIn("Bybit", content)
        self.assertNotIn("<font", content)
        self.assertNotIn("**", content)

    def test_sends_ambush_candidate_exit_reminder(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send(
            RECOVERED,
            {
                "canonical_symbol": "PEPE",
                "market_cap_usd": 100,
                "total_oi_usd": 150,
                "oi_to_market_cap": 1.5,
                "contracts": [],
            },
        )

        payload = client.sent[0][1]
        self.assertEqual(payload["msgtype"], "text")
        content = payload["text"]["content"]
        self.assertIn("退出 OI 埋伏候选", content)
        self.assertNotIn("交易所明细", content)
        self.assertNotIn("<font", content)
        self.assertNotIn("**", content)

    def test_sends_long_trade_signal_with_entry_and_stop(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_trade_signal(
            TradeSetup(
                side=LONG,
                candle_close_time=1_000,
                entry_price=246,
                stop_loss=240,
                rsi=35,
                previous_rsi=10,
                previous_close=228,
                ema200=229,
                previous_ema200=228.5,
                atr=3,
                quote_volume=1_200_000,
                previous_quote_volume=1_000_000,
            ),
            {
                "canonical_symbol": "PEPE",
                "oi_to_market_cap": 1.2,
                "total_oi_usd": 120,
                "market_cap_usd": 100,
                "contracts": [],
            },
            110,
        )

        payload = client.sent[0][1]
        content = payload["text"]["content"]
        self.assertEqual(payload["msgtype"], "text")
        self.assertIn("交易信号：做多", content)
        self.assertIn("PEPE", content)
        self.assertIn("参考入场：246.00000000", content)
        self.assertIn("止损：240.00000000", content)
        self.assertIn("做多条件", content)
        self.assertIn("OI / 市值 > 100%", content)
        self.assertIn("15分钟前聚合 OI：110.00 USD", content)
        self.assertIn("当前聚合 OI：120.00 USD", content)
        self.assertIn("上一根收盘价：228.00000000", content)
        self.assertIn("当前收盘价：246.00000000", content)
        self.assertIn("上一根15m成交额：1,000,000.00 USD", content)
        self.assertIn("当前15m成交额：1,200,000.00 USD", content)
        self.assertNotIn("1h", content)
        self.assertNotIn("0.25", content)
        self.assertIn("亏损不补仓", content)
        self.assertIn("杠杆参考：2-3倍", content)
        self.assertIn("OI / 市值：120.00%", content)
        self.assertNotIn("<font", content)
        self.assertNotIn("**", content)

    def test_sends_stop_long_message_after_rsi_exceeds_50(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_stop_long(
            {"canonical_symbol": "PEPE", "oi_to_market_cap": 1.2},
            _indicators(52.5, 101, 100),
            ("rsi_not_below_50",),
        )

        payload = client.sent[0][1]
        content = payload["text"]["content"]
        self.assertEqual(payload["msgtype"], "text")
        self.assertIn("交易信号：停止开多", content)
        self.assertIn("PEPE", content)
        self.assertIn("RSI(14)：52.50", content)
        self.assertIn("请勿继续开多", content)
        self.assertIn("OI / 市值：120.00%", content)
        self.assertNotIn("<font", content)
        self.assertNotIn("**", content)

    def test_sends_stop_long_message_when_close_is_below_ema200(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_stop_long(
            {"canonical_symbol": "PEPE", "oi_to_market_cap": 1.2},
            _indicators(45, 99, 100),
            ("close_not_above_ema200",),
        )

        content = client.sent[0][1]["text"]["content"]
        self.assertIn("收盘价：99.00000000", content)
        self.assertIn("EMA200：100.00000000", content)
        self.assertIn("15m 收盘价未高于 EMA200", content)

    def test_sends_stop_long_message_when_oi_to_market_cap_is_not_above_100(
        self,
    ) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_stop_long(
            {"canonical_symbol": "PEPE", "oi_to_market_cap": 1.0},
            None,
            ("oi_to_market_cap_not_above_100",),
        )

        content = client.sent[0][1]["text"]["content"]
        self.assertIn("OI / 市值未高于 100%", content)
        self.assertIn("OI / 市值：100.00%", content)

    def test_sends_oi_threshold_stop_message_without_kline_metrics(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_stop_long(
            {"canonical_symbol": "PEPE", "oi_to_market_cap": 0.99},
            None,
            ("oi_to_market_cap_not_above_100",),
        )

        content = client.sent[0][1]["text"]["content"]
        self.assertIn("本轮未获取", content)
        self.assertIn("周期：不适用（按 OI / 市值触发）", content)
        self.assertNotIn("周期：15m（已收盘）", content)
        self.assertIn("OI / 市值：99.00%", content)

    def test_sends_must_exit_message_with_stop_and_cooldown(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send_exit_long(
            {"canonical_symbol": "PEPE", "oi_to_market_cap": 1.2},
            _indicators(40, 97, 100),
            TradeSignalState("long", 100, 98),
            ("atr_stop_loss",),
        )

        content = client.sent[0][1]["text"]["content"]
        self.assertIn("交易信号：必须退出", content)
        self.assertIn("止损：98.00000000", content)
        self.assertIn("请执行退出，不要补仓", content)
        self.assertIn("至少等待 4 根 15m K 线", content)

    def test_rejects_short_trade_signals(self) -> None:
        notifier = WeComNotifier("https://wecom.example/webhook", RecordingClient())

        with self.assertRaisesRegex(ValueError, "Unsupported trade signal side"):
            notifier.send_trade_signal(
                TradeSetup(
                    side="short",
                    candle_close_time=1_000,
                    entry_price=246,
                    stop_loss=250,
                    rsi=65,
                    previous_rsi=90,
                    previous_close=251,
                    ema200=250,
                    previous_ema200=250,
                    atr=3,
                    quote_volume=1_200_000,
                    previous_quote_volume=1_000_000,
                ),
                {"canonical_symbol": "PEPE", "oi_to_market_cap": 1.2},
                110,
            )


if __name__ == "__main__":
    unittest.main()
