from __future__ import annotations

from typing import Any, Protocol

from .alerts import ENTERED_HIGH_RISK, RECOVERED
from .domain import TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
from .trade_dispatch import RESUME_LONG, TradeSignalState
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
        self,
        signal: TradeSetup,
        comparison: dict[str, Any],
        previous_aggregate_oi_usd: float,
        event_type: str = LONG,
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {
                    "content": _trade_message(
                        signal, comparison, previous_aggregate_oi_usd, event_type
                    )
                },
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
        cooldown_candles: int,
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {
                    "content": _exit_long_message(
                        comparison, indicators, state, reasons, cooldown_candles
                    )
                },
            },
        )
        if response["errcode"] != 0:
            raise RuntimeError(f"WeCom webhook rejected message: {response}")

    def send_trade_condition_list(
        self,
        can_long: tuple[str, ...],
        exit_long: tuple[str, ...],
        periodic: bool,
    ) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {
                    "content": _trade_condition_list_message(
                        can_long, exit_long, periodic
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


def _trade_message(
    signal: TradeSetup,
    comparison: dict[str, Any],
    previous_aggregate_oi_usd: float,
    event_type: str,
) -> str:
    if signal.side != LONG:
        raise ValueError(f"Unsupported trade signal side: {signal.side}")
    if event_type == LONG:
        title = "【交易信号：做多】"
        resume_warning = ""
    elif event_type == RESUME_LONG:
        title = "【交易信号：恢复做多】"
        resume_warning = (
            "恢复说明：仅适用于已经平仓后的重新开仓，不作为亏损仓位补仓依据。\n"
        )
    else:
        raise ValueError(f"Unsupported trade signal event: {event_type}")
    return (
        f"{title}\n"
        f"币种：{comparison['canonical_symbol']}\n"
        f"周期：15m（已收盘）\n"
        f"参考入场：{signal.entry_price:.8f}\n"
        f"止损：{signal.stop_loss:.8f}（2 × ATR(14)）\n"
        f"{resume_warning}"
        f"做多条件：OI / 市值 > {TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO * 100:.0f}% 且不超过 200%；"
        "当根已收盘 15m K 线上穿 EMA200、当前仍在其上方且 EMA200 向上；"
        "价格校正后的聚合 OI 较15分钟前增加；"
        "15m USDT 成交额突破前20根均量的2倍；"
        "RSI(14) 回升\n"
        "风险规则：亏损不补仓；触及止损或 EMA 结构退出条件时必须退出。\n"
        f"杠杆参考：2-3倍\n"
        f"RSI(14)：{signal.previous_rsi:.2f} → {signal.rsi:.2f}\n"
        f"上一根收盘价：{signal.previous_close:.8f}\n"
        f"上一根 EMA200：{signal.previous_ema200:.8f}\n"
        f"当前收盘价：{signal.entry_price:.8f}\n"
        f"当前 EMA200：{signal.ema200:.8f}\n"
        f"5根前 EMA200：{signal.ema200_slope_reference:.8f}\n"
        f"上一根15m成交额：{signal.previous_quote_volume:,.2f} USD\n"
        f"当前15m成交额：{signal.quote_volume:,.2f} USD\n"
        f"前20根15m平均成交额：{signal.average_quote_volume:,.2f} USD\n"
        f"15分钟前聚合 OI：{previous_aggregate_oi_usd:,.2f} USD\n"
        f"当前聚合 OI：{comparison['total_oi_usd']:,.2f} USD\n"
        "15分钟前价格校正 OI 指数："
        f"{previous_aggregate_oi_usd / signal.previous_close:.8f}\n"
        "当前价格校正 OI 指数："
        f"{comparison['total_oi_usd'] / signal.entry_price:.8f}\n"
        f"ATR(14)：{signal.atr:.8f}\n"
        f"OI / 市值：{comparison['oi_to_market_cap'] * 100:.2f}%"
    )


def _trade_condition_list_message(
    can_long: tuple[str, ...],
    exit_long: tuple[str, ...],
    periodic: bool,
) -> str:
    title = "【交易条件列表定时播报】" if periodic else "【交易条件列表更新】"
    can_long_content = "、".join(can_long) or "暂无"
    exit_long_content = "、".join(exit_long) or "暂无"
    return (
        f"{title}\n\n"
        f"可以做多\n{can_long_content}\n\n"
        f"必须退出\n{exit_long_content}"
    )


def _oi_to_market_cap_line(comparison: dict[str, Any]) -> str:
    ratio = comparison["oi_to_market_cap"]
    if ratio is None:
        return "OI / 市值：不可用（数据源不完整）"
    return f"OI / 市值：{ratio * 100:.2f}%"


def _exit_long_message(
    comparison: dict[str, Any],
    indicators: TradeIndicators,
    state: TradeSignalState,
    reasons: tuple[str, ...],
    cooldown_candles: int,
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
        f"重新开仓：等待 {cooldown_candles} 根 15m K 线，并重新满足完整做多条件。\n"
        + _oi_to_market_cap_line(comparison)
    )


def _reason_text(reason: str) -> str:
    labels = {
        "oi_to_market_cap_not_above_90": "OI / 市值未高于 90%",
        "oi_to_market_cap_in_ambush_zone": "OI / 市值已高于 200%，禁止开多",
        "aggregate_oi_history_unavailable": "缺少15分钟前聚合 OI",
        "aggregate_oi_not_increasing_after_price_adjustment": "价格校正后的聚合 OI 未较15分钟前增加",
        "ema200_not_crossed_up": "当根已收盘15m K线未上穿 EMA200，或当前已回到 EMA200 下方",
        "ema200_breakout_before_cooldown_end": "EMA200 突破发生在冷却结束前",
        "ema200_not_rising": "EMA200 未向上倾斜",
        "quote_volume_not_increasing": "15m USDT 成交额未较上一根增加",
        "quote_volume_not_above_average": "15m 成交额未突破前20根均量的2倍",
        "close_not_above_ema200": "15m 收盘价未高于 EMA200",
        "rsi_not_below_60": "RSI(14) 未低于 60",
        "rsi_not_rising": "RSI(14) 未回升",
        "atr_stop_loss": "已触及 2 × ATR 止损",
        "trailing_take_profit": "已触及移动止盈保护价",
        "close_below_ema200_exit_buffer": "15m 收盘价低于 EMA200 − 0.5 × ATR",
        "two_closes_below_ema200": "连续两根 15m 收盘价低于 EMA200",
    }
    try:
        return labels[reason]
    except KeyError as error:
        raise ValueError(f"Unsupported trade reason: {reason}") from error
