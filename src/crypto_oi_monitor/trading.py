from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


RSI_PERIOD = 14
ATR_PERIOD = 14
EMA_PERIOD = 200
EMA_WARMUP_CANDLES = 1000
REQUIRED_CLOSED_CANDLES = EMA_WARMUP_CANDLES + 1
FIFTEEN_MINUTES_MILLISECONDS = 15 * 60 * 1000
ENTRY_RSI_MIN = 35
ENTRY_RSI_MAX = 50
EXIT_EMA_ATR_BUFFER = 0.5
LONG = "long"
BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


class BinanceKlineHttpClient(Protocol):
    def get_json(self, url: str, params: dict[str, str]) -> Any: ...


@dataclass(frozen=True)
class Candle:
    close_time: int
    high: float
    low: float
    close: float
    quote_volume: float


@dataclass(frozen=True)
class TradeSetup:
    side: str
    candle_close_time: int
    entry_price: float
    stop_loss: float
    rsi: float
    previous_rsi: float
    previous_close: float
    ema200: float
    previous_ema200: float
    atr: float
    quote_volume: float
    previous_quote_volume: float


@dataclass(frozen=True)
class TradeIndicators:
    candle_close_time: int
    previous_close: float
    close: float
    rsi: float
    previous_rsi: float
    ema200: float
    previous_ema200: float
    atr: float
    quote_volume: float
    previous_quote_volume: float


def fetch_binance_closed_candles(
    client: BinanceKlineHttpClient, symbol: str
) -> list[Candle]:
    payload = client.get_json(
        BINANCE_KLINES_URL,
        {
            "symbol": symbol,
            "interval": "15m",
            "limit": str(REQUIRED_CLOSED_CANDLES + 1),
        },
    )
    candles = [
        Candle(
            close_time=int(candle[6]),
            high=float(candle[2]),
            low=float(candle[3]),
            close=float(candle[4]),
            quote_volume=float(candle[7]),
        )
        for candle in payload[:-1]
    ]
    if len(candles) < REQUIRED_CLOSED_CANDLES:
        raise ValueError(
            "Binance returned only "
            f"{len(candles)} closed candles; EMA200 requires at least "
            f"{REQUIRED_CLOSED_CANDLES} closed candles"
        )
    return candles


def evaluate_trade_setup(candles: list[Candle]) -> TradeSetup | None:
    indicators = trade_indicators(candles)
    if entry_reasons(indicators):
        return None
    return TradeSetup(
        LONG,
        indicators.candle_close_time,
        indicators.close,
        indicators.close - 2 * indicators.atr,
        indicators.rsi,
        indicators.previous_rsi,
        indicators.previous_close,
        indicators.ema200,
        indicators.previous_ema200,
        indicators.atr,
        indicators.quote_volume,
        indicators.previous_quote_volume,
    )


def trade_indicators(candles: list[Candle]) -> TradeIndicators:
    if len(candles) < REQUIRED_CLOSED_CANDLES:
        raise ValueError(
            f"Trade signals require at least {REQUIRED_CLOSED_CANDLES} closed candles"
        )
    closes = [candle.close for candle in candles]
    previous_rsi, rsi = _rsi_values(closes, RSI_PERIOD)[-2:]
    previous_ema200, ema200 = _ema_values(closes, EMA_PERIOD)[-2:]
    atr = _atr(candles, ATR_PERIOD)
    previous = candles[-2]
    current = candles[-1]
    return TradeIndicators(
        current.close_time,
        previous.close,
        current.close,
        rsi,
        previous_rsi,
        ema200,
        previous_ema200,
        atr,
        current.quote_volume,
        previous.quote_volume,
    )


def entry_reasons(indicators: TradeIndicators) -> tuple[str, ...]:
    reasons = []
    if not (
        indicators.previous_close <= indicators.previous_ema200
        and indicators.close > indicators.ema200
    ):
        reasons.append("ema200_not_crossed_up")
    if indicators.quote_volume <= indicators.previous_quote_volume:
        reasons.append("quote_volume_not_increasing")
    if indicators.rsi < ENTRY_RSI_MIN:
        reasons.append("rsi_below_35")
    if indicators.rsi >= ENTRY_RSI_MAX:
        reasons.append("rsi_not_below_50")
    if indicators.rsi <= indicators.previous_rsi:
        reasons.append("rsi_not_rising")
    return tuple(reasons)


def stop_long_reasons(indicators: TradeIndicators) -> tuple[str, ...]:
    reasons = []
    if indicators.rsi >= ENTRY_RSI_MAX:
        reasons.append("rsi_not_below_50")
    if indicators.close <= indicators.ema200:
        reasons.append("close_not_above_ema200")
    return tuple(reasons)


def exit_reasons(
    candles: list[Candle], indicators: TradeIndicators, stop_loss: float | None
) -> tuple[str, ...]:
    reasons = []
    if stop_loss is not None and indicators.close <= stop_loss:
        reasons.append("atr_stop_loss")
    if indicators.close <= indicators.ema200 - EXIT_EMA_ATR_BUFFER * indicators.atr:
        reasons.append("close_below_ema200_exit_buffer")
    if (
        candles[-2].close <= indicators.previous_ema200
        and indicators.close <= indicators.ema200
    ):
        reasons.append("two_closes_below_ema200")
    return tuple(reasons)


def current_rsi(candles: list[Candle]) -> float:
    return _rsi_values([candle.close for candle in candles], RSI_PERIOD)[-1]


def current_ema200(candles: list[Candle]) -> float:
    return _ema([candle.close for candle in candles], EMA_PERIOD)


def _ema(values: list[float], period: int) -> float:
    return _ema_values(values, period)[-1]


def _ema_values(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise ValueError(f"EMA{period} requires at least {period} values")
    average = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    result = [average]
    for value in values[period:]:
        average = (value - average) * multiplier + average
        result.append(average)
    return result


def _rsi_values(closes: list[float], period: int) -> list[float]:
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    if len(changes) < period + 1:
        raise ValueError("Trade signals require RSI history")
    average_gain = sum(max(change, 0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0) for change in changes[:period]) / period
    values = [_rsi(average_gain, average_loss)]
    for change in changes[period:]:
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period
        values.append(_rsi(average_gain, average_loss))
    return values


def _rsi(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100 if average_gain else 50
    return 100 - 100 / (1 + average_gain / average_loss)


def _atr(candles: list[Candle], period: int) -> float:
    ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(candles, candles[1:])
    ]
    if len(ranges) < period:
        raise ValueError("Trade signals require ATR history")
    average = sum(ranges[:period]) / period
    for value in ranges[period:]:
        average = (average * (period - 1) + value) / period
    return average
