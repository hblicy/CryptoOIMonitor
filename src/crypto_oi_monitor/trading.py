from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Any, Protocol


RSI_PERIOD = 14
ATR_PERIOD = 14
EMA_PERIOD = 200
EMA_WARMUP_CANDLES = 1000
REQUIRED_CLOSED_CANDLES = EMA_WARMUP_CANDLES + 1
BINANCE_KLINE_FETCH_LIMIT = 1500
FIFTEEN_MINUTES_MILLISECONDS = 15 * 60 * 1000
EMA_SLOPE_LOOKBACK = 5
EMA_BREAKOUT_LOOKBACK = 3
VOLUME_AVERAGE_PERIOD = 20
VOLUME_BREAKOUT_MULTIPLIER = 1.1
ENTRY_RSI_MAX = 60
EXIT_EMA_ATR_BUFFER = 0.5
TRAILING_ACTIVATION_ATR = 3
TRAILING_PROFIT_FLOOR_ATR = 1
TRAILING_DISTANCE_ATR = 2
COOLDOWN_BASELINE_CANDLES = 96
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

    def __post_init__(self) -> None:
        prices = (self.high, self.low, self.close)
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError("Candle prices must be finite positive")
        if self.high < self.low:
            raise ValueError("Candle high must not be below low")
        if not self.low <= self.close <= self.high:
            raise ValueError("Candle close must be within the high-low range")
        if not math.isfinite(self.quote_volume) or self.quote_volume < 0:
            raise ValueError("Candle quote volume must be finite non-negative")


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
    ema200_slope_reference: float
    atr: float
    quote_volume: float
    previous_quote_volume: float
    average_quote_volume: float


@dataclass(frozen=True)
class TradeIndicators:
    candle_close_time: int
    previous_close: float
    close: float
    rsi: float
    previous_rsi: float
    ema200: float
    previous_ema200: float
    ema200_slope_reference: float
    atr: float
    quote_volume: float
    previous_quote_volume: float
    average_quote_volume: float
    ema200_breakout_candles_ago: int | None


def fetch_binance_closed_candles(
    client: BinanceKlineHttpClient, symbol: str
) -> list[Candle]:
    payload = client.get_json(
        BINANCE_KLINES_URL,
        {
            "symbol": symbol,
            "interval": "15m",
            "limit": str(BINANCE_KLINE_FETCH_LIMIT),
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
        indicators.ema200_slope_reference,
        indicators.atr,
        indicators.quote_volume,
        indicators.previous_quote_volume,
        indicators.average_quote_volume,
    )


def trade_indicators(candles: list[Candle]) -> TradeIndicators:
    if len(candles) < REQUIRED_CLOSED_CANDLES:
        raise ValueError(
            f"Trade signals require at least {REQUIRED_CLOSED_CANDLES} closed candles"
        )
    closes = [candle.close for candle in candles]
    previous_rsi, rsi = _rsi_values(closes, RSI_PERIOD)[-2:]
    ema200_values = _ema_values(closes, EMA_PERIOD)
    previous_ema200, ema200 = ema200_values[-2:]
    ema200_slope_reference = ema200_values[-(EMA_SLOPE_LOOKBACK + 1)]
    aligned_closes = closes[EMA_PERIOD - 1 :]
    ema200_breakout_candles_ago = next(
        (
            candles_ago
            for candles_ago in range(EMA_BREAKOUT_LOOKBACK)
            if aligned_closes[-(candles_ago + 2)]
            <= ema200_values[-(candles_ago + 2)]
            and aligned_closes[-(candles_ago + 1)]
            > ema200_values[-(candles_ago + 1)]
        ),
        None,
    )
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
        ema200_slope_reference,
        atr,
        current.quote_volume,
        previous.quote_volume,
        sum(candle.quote_volume for candle in candles[-(VOLUME_AVERAGE_PERIOD + 1) : -1])
        / VOLUME_AVERAGE_PERIOD,
        ema200_breakout_candles_ago,
    )


def entry_reasons(indicators: TradeIndicators) -> tuple[str, ...]:
    reasons = []
    if (
        indicators.ema200_breakout_candles_ago is None
        or indicators.close <= indicators.ema200
    ):
        reasons.append("ema200_not_crossed_up")
    if indicators.ema200 <= indicators.ema200_slope_reference:
        reasons.append("ema200_not_rising")
    if (
        indicators.quote_volume
        <= VOLUME_BREAKOUT_MULTIPLIER * indicators.average_quote_volume
    ):
        reasons.append("quote_volume_not_above_average")
    if indicators.rsi >= ENTRY_RSI_MAX:
        reasons.append("rsi_not_below_60")
    if indicators.rsi <= indicators.previous_rsi:
        reasons.append("rsi_not_rising")
    return tuple(reasons)


def stop_long_reasons(indicators: TradeIndicators) -> tuple[str, ...]:
    reasons = []
    if indicators.rsi >= ENTRY_RSI_MAX:
        reasons.append("rsi_not_below_60")
    if indicators.close <= indicators.ema200:
        reasons.append("close_not_above_ema200")
    return tuple(reasons)


def update_trailing_stop(
    entry_price: float,
    entry_atr: float,
    stop_loss: float,
    highest_close: float,
    close: float,
) -> tuple[float, float]:
    if entry_atr <= 0:
        raise ValueError("Entry ATR must be positive")
    next_highest_close = max(highest_close, close)
    if next_highest_close < entry_price + TRAILING_ACTIVATION_ATR * entry_atr:
        return next_highest_close, stop_loss
    return next_highest_close, max(
        stop_loss,
        entry_price + TRAILING_PROFIT_FLOOR_ATR * entry_atr,
        next_highest_close - TRAILING_DISTANCE_ATR * entry_atr,
    )


def cooldown_candles_for_volatility_ratio(volatility_ratio: float) -> int:
    if volatility_ratio <= 0:
        raise ValueError("Volatility ratio must be positive")
    if volatility_ratio <= 0.8:
        return 3
    if volatility_ratio <= 1.2:
        return 4
    return 6


def dynamic_cooldown_candles(candles: list[Candle]) -> int:
    atr_values = _atr_values(candles, ATR_PERIOD)
    normalized_atr_values = [
        atr / candle.close
        for atr, candle in zip(atr_values, candles[ATR_PERIOD:])
    ]
    if len(normalized_atr_values) < COOLDOWN_BASELINE_CANDLES + 1:
        raise ValueError("Dynamic cooldown requires 96 prior normalized ATR values")
    baseline = median(
        normalized_atr_values[-(COOLDOWN_BASELINE_CANDLES + 1) : -1]
    )
    if baseline <= 0:
        raise ValueError("Dynamic cooldown ATR baseline must be positive")
    return cooldown_candles_for_volatility_ratio(
        normalized_atr_values[-1] / baseline
    )


def exit_reasons(
    candles: list[Candle],
    indicators: TradeIndicators,
    stop_loss: float | None,
    entry_price: float | None = None,
) -> tuple[str, ...]:
    reasons = []
    if stop_loss is not None and indicators.close <= stop_loss:
        reasons.append(
            "trailing_take_profit"
            if entry_price is not None and stop_loss > entry_price
            else "atr_stop_loss"
        )
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
    return _atr_values(candles, period)[-1]


def _atr_values(candles: list[Candle], period: int) -> list[float]:
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
    values = [average]
    for value in ranges[period:]:
        average = (average * (period - 1) + value) / period
        values.append(average)
    return values
