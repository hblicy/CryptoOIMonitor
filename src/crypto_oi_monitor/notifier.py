from __future__ import annotations

from typing import Any, Protocol

from .alerts import ENTERED_HIGH_RISK, RECOVERED
from .domain import FOCUS_OI_TO_MARKET_CAP_RATIO
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

    def send_stop_long(
        self,
        comparison: dict[str, Any],
        rsi: float | None,
        close: float | None,
        ema200: float | None,
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {"content": _stop_long_message(comparison, rsi, close, ema200)},
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
        f"停止开多条件：OI / 市值低于 110%、RSI(14) 超过 50 或 15m 收盘价低于 EMA200\n"
        f"杠杆参考：2-3倍\n"
        f"RSI(14)：{signal.rsi:.2f}\n"
        f"EMA200：{signal.ema200:.8f}\n"
        f"ATR(14)：{signal.atr:.8f}\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )


def _stop_long_message(
    comparison: dict[str, Any],
    rsi: float | None,
    close: float | None,
    ema200: float | None,
) -> str:
    reasons = []
    if comparison["oi_to_market_cap"] < FOCUS_OI_TO_MARKET_CAP_RATIO:
        reasons.append("OI / 市值已低于 110%")
    if rsi is not None and rsi > 50:
        reasons.append("RSI(14) 已超过 50")
    if close is not None and ema200 is not None and close < ema200:
        reasons.append("15m 收盘价已低于 EMA200")
    if not reasons:
        raise ValueError("Stop-long message requires a stop condition")
    kline_metrics = ""
    if rsi is not None and close is not None and ema200 is not None:
        period = "周期：15m（已收盘）\n"
        kline_metrics = (
            f"RSI(14)：{rsi:.2f}\n"
            f"收盘价：{close:.8f}\n"
            f"EMA200：{ema200:.8f}\n"
        )
    else:
        period = "周期：不适用（按 OI / 市值触发）\n"
        kline_metrics = "15m K 线：本轮未获取，已按 OI / 市值条件停止开多。\n"
    return (
        "【交易信号：停止开多】\n"
        f"币种：{comparison['canonical_symbol']}\n"
        f"{period}"
        f"{kline_metrics}"
        f"原因：{'；'.join(reasons)}，请勿继续开多。\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )
