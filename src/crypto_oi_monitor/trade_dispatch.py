from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .http_client import DataSourceRequestError
from .trading import (
    EMA_PERIOD,
    LONG,
    SHORT,
    Candle,
    TradeSetup,
    current_rsi,
    evaluate_trade_setup,
)


MAX_KLINE_WORKERS = 8


@dataclass(frozen=True)
class TradeSignalDispatchFailure:
    canonical_symbol: str
    message: str


@dataclass(frozen=True)
class TradeSignalDispatchResult:
    events: tuple[str, ...]
    failures: tuple[TradeSignalDispatchFailure, ...]


class TradeSignalStateStore(Protocol):
    def get_trade_signal_state(self, canonical_symbol: str) -> str | None: ...

    def set_trade_signal_state(self, canonical_symbol: str, side: str) -> None: ...

    def clear_trade_signal_state(self, canonical_symbol: str) -> None: ...


class TradeSignalNotifier(Protocol):
    def send_trade_signal(
        self, signal: TradeSetup, comparison: dict[str, Any]
    ) -> None: ...


def dispatch_trade_signals(
    snapshot: dict[str, Any],
    kline_loader: Callable[[str], list[Candle]],
    store: TradeSignalStateStore,
    notifier: TradeSignalNotifier,
) -> TradeSignalDispatchResult:
    if not snapshot["complete"]:
        return TradeSignalDispatchResult((), ())

    candidates = []
    for comparison in snapshot["comparisons"]:
        state = store.get_trade_signal_state(comparison["canonical_symbol"])
        if comparison["oi_to_market_cap"] > 1 or state is not None:
            candidates.append((comparison, state))

    candles_by_symbol: dict[str, list[Candle]] = {}
    failures: list[TradeSignalDispatchFailure] = []
    if candidates:
        with ThreadPoolExecutor(
            max_workers=min(MAX_KLINE_WORKERS, len(candidates))
        ) as executor:
            futures = [
                (
                    comparison,
                    state,
                    executor.submit(kline_loader, _binance_symbol(comparison)),
                )
                for comparison, state in candidates
            ]
            for comparison, _state, future in futures:
                canonical_symbol = comparison["canonical_symbol"]
                try:
                    candles = future.result()
                except DataSourceRequestError as error:
                    failures.append(
                        TradeSignalDispatchFailure(
                            canonical_symbol,
                            f"{type(error).__name__}: {error}",
                        )
                    )
                    continue
                if len(candles) < EMA_PERIOD + 1:
                    failures.append(
                        TradeSignalDispatchFailure(
                            canonical_symbol,
                            f"仅获取到 {len(candles)} 根已收盘 15m K 线，需要至少 201 根",
                        )
                    )
                    continue
                candles_by_symbol[canonical_symbol] = candles

    dispatched: list[str] = []
    for comparison, state in candidates:
        canonical_symbol = comparison["canonical_symbol"]
        candles = candles_by_symbol.get(canonical_symbol)
        if candles is None:
            continue
        if state is not None:
            if _take_profit_reached(state, current_rsi(candles)):
                store.clear_trade_signal_state(canonical_symbol)
            else:
                continue
        if comparison["oi_to_market_cap"] <= 1:
            continue
        signal = evaluate_trade_setup(candles)
        if signal is None:
            continue
        notifier.send_trade_signal(signal, comparison)
        store.set_trade_signal_state(canonical_symbol, signal.side)
        dispatched.append(signal.side)
    return TradeSignalDispatchResult(tuple(dispatched), tuple(failures))


def _binance_symbol(comparison: dict[str, Any]) -> str:
    for contract in comparison["contracts"]:
        if contract["venue"] == "Binance":
            return contract["symbol"]
    raise ValueError(
        f"Binance contract is missing for {comparison['canonical_symbol']}"
    )


def _take_profit_reached(side: str, rsi: float) -> bool:
    if side == LONG:
        return rsi >= 50
    if side == SHORT:
        return rsi <= 50
    raise ValueError(f"Unsupported trade signal side: {side}")
