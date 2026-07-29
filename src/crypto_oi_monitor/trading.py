from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


RSI_PERIOD = 14
ATR_PERIOD = 14
EMA_PERIOD = 200
EMA_WARMUP_CANDLES = 1000
REQUIRED_CLOSED_CANDLES = EMA_WARMUP_CANDLES + 1
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


@dataclass(frozen=True)
class TradeSetup:
    side: str
    candle_close_time: int
    entry_price: float
    stop_loss: float
    rsi: float
    previous_rsi: float
    ema200: float
    atr: float


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
    if len(candles) < REQUIRED_CLOSED_CANDLES:
        raise ValueError(
            f"Trade signals require at least {REQUIRED_CLOSED_CANDLES} closed candles"
        )
    closes = [candle.close for candle in candles]
    previous_rsi, rsi = _rsi_values(closes, RSI_PERIOD)[-2:]
    ema200 = _ema(closes, EMA_PERIOD)
    atr = _atr(candles, ATR_PERIOD)
    current = candles[-1]

    if current.close > ema200 and rsi < 50:
        return TradeSetup(
            LONG,
            current.close_time,
            current.close,
            current.close - 2 * atr,
            rsi,
            previous_rsi,
            ema200,
            atr,
        )
    return None


def current_rsi(candles: list[Candle]) -> float:
    return _rsi_values([candle.close for candle in candles], RSI_PERIOD)[-1]


def current_ema200(candles: list[Candle]) -> float:
    return _ema([candle.close for candle in candles], EMA_PERIOD)


def _ema(values: list[float], period: int) -> float:
    average = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        average = (value - average) * multiplier + average
    return average


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
