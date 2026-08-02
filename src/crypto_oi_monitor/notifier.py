from __future__ import annotations

from typing import Any, Protocol

from .alerts import ENTERED_HIGH_RISK, RECOVERED
from .trade_dispatch import TradeSignalState
from .trading import LONG, TradeIndicators, TradeSetup


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
        indicators: TradeIndicators | None,
        reasons: tuple[str, ...],
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {"content": _stop_long_message(comparison, indicators, reasons)},
            },
        )
        if response["errcode"] != 0:
            raise RuntimeError(f"WeCom webhook rejected message: {response}")

    def send_exit_long(
        self,
        comparison: dict[str, Any],
        indicators: TradeIndicators,
        state: TradeSignalState,
        reasons: tuple[str, ...],
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {
                    "content": _exit_long_message(
                        comparison, indicators, state, reasons
                    )
                },
            },
        )
        if response["errcode"] != 0:
            raise RuntimeError(f"WeCom webhook rejected message: {response}")

    def send_trade_condition_list(
        self,
        can_long: tuple[str, ...],
        stop_long: tuple[str, ...],
        exit_long: tuple[str, ...],
        periodic: bool,
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {
                    "content": _trade_condition_list_message(
                        can_long, stop_long, exit_long, periodic
                    )
                },
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
        "做多条件：OI / 市值 > 110%；1h 趋势向上；15m 收盘价高于 EMA200 + 0.25 × ATR；"
        "RSI(14) 在 35-50 且回升\n"
        "风险规则：亏损不补仓；触及止损或 EMA 结构退出条件时必须退出。\n"
        f"杠杆参考：2-3倍\n"
        f"RSI(14)：{signal.rsi:.2f}\n"
        f"EMA200：{signal.ema200:.8f}\n"
        f"1h EMA200：{signal.hourly_ema200:.8f}\n"
        f"ATR(14)：{signal.atr:.8f}\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )


def _trade_condition_list_message(
    can_long: tuple[str, ...],
    stop_long: tuple[str, ...],
    exit_long: tuple[str, ...],
    periodic: bool,
) -> str:
    title = "【交易条件列表定时播报】" if periodic else "【交易条件列表更新】"
    can_long_content = "、".join(can_long) or "暂无"
    stop_long_content = "、".join(stop_long) or "暂无"
    exit_long_content = "、".join(exit_long) or "暂无"
    return (
        f"{title}\n\n"
        f"可以做多\n{can_long_content}\n\n"
        f"停止做多\n{stop_long_content}\n\n"
        f"必须退出\n{exit_long_content}"
    )


def _stop_long_message(
    comparison: dict[str, Any],
    indicators: TradeIndicators | None,
    reasons: tuple[str, ...],
) -> str:
    if not reasons:
        raise ValueError("Stop-long message requires a stop condition")
    if indicators is not None:
        period = "周期：15m（已收盘）\n"
        kline_metrics = (
            f"RSI(14)：{indicators.rsi:.2f}\n"
            f"收盘价：{indicators.close:.8f}\n"
            f"EMA200：{indicators.ema200:.8f}\n"
        )
    else:
        period = "周期：不适用（按 OI / 市值触发）\n"
        kline_metrics = "15m K 线：本轮未获取，已按 OI / 市值条件停止开多。\n"
    return (
        "【交易信号：停止开多】\n"
        f"币种：{comparison['canonical_symbol']}\n"
        f"{period}"
        f"{kline_metrics}"
        f"原因：{'；'.join(_reason_text(reason) for reason in reasons)}，请勿继续开多或补仓。\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )


def _exit_long_message(
    comparison: dict[str, Any],
    indicators: TradeIndicators,
    state: TradeSignalState,
    reasons: tuple[str, ...],
) -> str:
    if state.entry_price is None or state.stop_loss is None:
        raise ValueError("Exit-long message requires entry and stop-loss prices")
    return (
        "【交易信号：必须退出】\n"
        f"币种：{comparison['canonical_symbol']}\n"
        "周期：15m（已收盘）\n"
        f"参考入场：{state.entry_price:.8f}\n"
        f"止损：{state.stop_loss:.8f}\n"
        f"收盘价：{indicators.close:.8f}\n"
        f"EMA200：{indicators.ema200:.8f}\n"
        f"原因：{'；'.join(_reason_text(reason) for reason in reasons)}。请执行退出，不要补仓。\n"
        "重新开仓：至少等待 4 根 15m K 线，并重新满足完整做多条件。\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )


def _reason_text(reason: str) -> str:
    labels = {
        "oi_to_market_cap_below_110": "OI / 市值已低于 110%",
        "hourly_close_not_above_ema200": "1h 收盘价未高于 EMA200",
        "hourly_ema200_not_rising": "1h EMA200 未上行",
        "close_not_above_ema200_buffer": "15m 收盘价未高于 EMA200 + 0.25 × ATR",
        "rsi_below_35": "RSI(14) 低于 35",
        "rsi_not_below_50": "RSI(14) 未低于 50",
        "rsi_not_rising": "RSI(14) 未回升",
        "atr_stop_loss": "已触及 2 × ATR 止损",
        "close_below_ema200_exit_buffer": "15m 收盘价低于 EMA200 − 0.5 × ATR",
        "two_closes_below_ema200": "连续两根 15m 收盘价低于 EMA200",
    }
    try:
        return labels[reason]
    except KeyError as error:
        raise ValueError(f"Unsupported trade reason: {reason}") from error
