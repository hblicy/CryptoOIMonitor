from __future__ import annotations

from typing import Any, Protocol

from .alerts import ENTERED_HIGH_RISK, RECOVERED
from .trading import LONG, TradeSetup


class WeComHttpClient(Protocol):
    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class WeComNotifier:
    def __init__(self, webhook_url: str, client: WeComHttpClient) -> None:
        self.webhook_url = webhook_url
        self.client = client

    def send(self, event: str, comparison: dict[str, Any]) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {"msgtype": "text", "text": {"content": _message(event, comparison)}},
        )
        if response["errcode"] != 0:
            raise RuntimeError(f"WeCom webhook rejected message: {response}")

    def send_trade_signal(
        self, signal: TradeSetup, comparison: dict[str, Any]
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {"content": _trade_message(signal, comparison)},
            },
        )
        if response["errcode"] != 0:
            raise RuntimeError(f"WeCom webhook rejected message: {response}")

    def send_stop_long(self, comparison: dict[str, Any], rsi: float) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {"content": _stop_long_message(comparison, rsi)},
            },
        )
        if response["errcode"] != 0:
            raise RuntimeError(f"WeCom webhook rejected message: {response}")


def _message(event: str, comparison: dict[str, Any]) -> str:
    if event == ENTERED_HIGH_RISK:
        title = "【OI 埋伏候选】"
    elif event == RECOVERED:
        title = "【退出 OI 埋伏候选】"
    else:
        raise ValueError(f"Unsupported notification event: {event}")

    return (
        f"{title}\n"
        f"币种：{comparison['canonical_symbol']}\n"
        f"聚合 OI：{comparison['total_oi_usd']:,.2f} USD\n"
        f"市值：{comparison['market_cap_usd']:,.2f} USD\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )


def _trade_message(signal: TradeSetup, comparison: dict[str, Any]) -> str:
    if signal.side != LONG:
        raise ValueError(f"Unsupported trade signal side: {signal.side}")
    title = "【交易信号：做多】"
    return (
        f"{title}\n"
        f"币种：{comparison['canonical_symbol']}\n"
        f"周期：15m（已收盘）\n"
        f"参考入场：{signal.entry_price:.8f}\n"
        f"止损：{signal.stop_loss:.8f}（2 × ATR(14)）\n"
        f"停止开多条件：RSI(14) 超过 50\n"
        f"杠杆参考：2-3倍\n"
        f"RSI(14)：{signal.rsi:.2f}\n"
        f"EMA200：{signal.ema200:.8f}\n"
        f"ATR(14)：{signal.atr:.8f}\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )


def _stop_long_message(comparison: dict[str, Any], rsi: float) -> str:
    return (
        "【交易信号：停止开多】\n"
        f"币种：{comparison['canonical_symbol']}\n"
        "周期：15m（已收盘）\n"
        f"RSI(14)：{rsi:.2f}\n"
        "原因：RSI(14) 已超过 50，请勿继续开多。\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )
