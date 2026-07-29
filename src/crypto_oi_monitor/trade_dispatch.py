from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .trading import (
    LONG,
    Candle,
    REQUIRED_CLOSED_CANDLES,
    TradeSetup,
    current_ema200,
    current_rsi,
    evaluate_trade_setup,
)


MAX_KLINE_WORKERS = 8
LEGACY_SHORT_STATE = "short"
STOP_LONG = "stop_long"
CAN_LONG = "can_long"
KLINE_ERROR = "kline_error"


@dataclass(frozen=True)
class TradeSignalDispatchFailure:
    canonical_symbol: str
    message: str


@dataclass(frozen=True)
class TradeSignalEvent:
    event_type: str
    canonical_symbol: str
    candle_close_time: int | None
    rsi: float | None
    close: float | None
    ema200: float | None
    oi_to_market_cap: float
    entry_price: float | None = None
    stop_loss: float | None = None
    atr: float | None = None
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "canonical_symbol": self.canonical_symbol,
            "candle_close_time": self.candle_close_time,
            "rsi": self.rsi,
            "close": self.close,
            "ema200": self.ema200,
            "oi_to_market_cap": self.oi_to_market_cap,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "atr": self.atr,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TradeConditionScan:
    status: str
    canonical_symbol: str
    candle_close_time: int | None
    rsi: float | None
    close: float | None
    ema200: float | None
    oi_to_market_cap: float
    reasons: tuple[str, ...] = ()
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "canonical_symbol": self.canonical_symbol,
            "candle_close_time": self.candle_close_time,
            "rsi": self.rsi,
            "close": self.close,
            "ema200": self.ema200,
            "oi_to_market_cap": self.oi_to_market_cap,
            "reasons": list(self.reasons),
            "error": self.error,
        }


@dataclass(frozen=True)
class TradeSignalDispatchResult:
    events: tuple[str, ...]
    failures: tuple[TradeSignalDispatchFailure, ...]
    details: tuple[TradeSignalEvent, ...] = ()
    scans: tuple[TradeConditionScan, ...] = ()


@dataclass(frozen=True)
class TradeConditionScanResult:
    scans: tuple[TradeConditionScan, ...]
    failures: tuple[TradeSignalDispatchFailure, ...]


class TradeSignalStateStore(Protocol):
    def get_trade_signal_state(self, canonical_symbol: str) -> str | None: ...

    def set_trade_signal_state(self, canonical_symbol: str, side: str) -> None: ...

    def clear_trade_signal_state(self, canonical_symbol: str) -> None: ...


class TradeSignalNotifier(Protocol):
    def send_trade_signal(
        self, signal: TradeSetup, comparison: dict[str, Any]
    ) -> None: ...

    def send_stop_long(
        self,
        comparison: dict[str, Any],
        rsi: float | None,
        close: float | None,
        ema200: float | None,
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
    oi_threshold_stops = []
    for comparison in snapshot["comparisons"]:
        state = store.get_trade_signal_state(comparison["canonical_symbol"])
        if state == LEGACY_SHORT_STATE:
            store.clear_trade_signal_state(comparison["canonical_symbol"])
            state = None
        if state == LONG and comparison["oi_to_market_cap"] <= 1:
            oi_threshold_stops.append((comparison, state))
        elif comparison["oi_to_market_cap"] > 1 or state == LONG:
            candidates.append((comparison, state))

    dispatched: list[str] = []
    details: list[TradeSignalEvent] = []
    for comparison, state in oi_threshold_stops:
        canonical_symbol = comparison["canonical_symbol"]
        reasons = _stop_long_reasons(
            state, comparison["oi_to_market_cap"], None, None, None
        )
        notifier.send_stop_long(comparison, None, None, None)
        store.clear_trade_signal_state(canonical_symbol)
        dispatched.append(STOP_LONG)
        details.append(
            TradeSignalEvent(
                event_type=STOP_LONG,
                canonical_symbol=canonical_symbol,
                candle_close_time=None,
                rsi=None,
                close=None,
                ema200=None,
                oi_to_market_cap=comparison["oi_to_market_cap"],
                reasons=reasons,
            )
        )
    candidate_comparisons = [comparison for comparison, _state in candidates]
    candles_by_symbol, failures, failures_by_symbol = _load_trade_candles(
        candidate_comparisons, kline_loader
    )
    scans = _build_trade_condition_scans(
        candidate_comparisons, candles_by_symbol, failures_by_symbol
    )
    for comparison, state in candidates:
        canonical_symbol = comparison["canonical_symbol"]
        candles = candles_by_symbol.get(canonical_symbol)
        if candles is None:
            continue
        if state is not None:
            rsi = current_rsi(candles)
            close = candles[-1].close
            ema200 = current_ema200(candles)
            reasons = _stop_long_reasons(
                state, comparison["oi_to_market_cap"], rsi, close, ema200
            )
            if reasons:
                notifier.send_stop_long(comparison, rsi, close, ema200)
                store.clear_trade_signal_state(canonical_symbol)
                dispatched.append(STOP_LONG)
                details.append(
                    TradeSignalEvent(
                        event_type=STOP_LONG,
                        canonical_symbol=canonical_symbol,
                        candle_close_time=candles[-1].close_time,
                        rsi=rsi,
                        close=close,
                        ema200=ema200,
                        oi_to_market_cap=comparison["oi_to_market_cap"],
                        reasons=reasons,
                    )
                )
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
        details.append(
            TradeSignalEvent(
                event_type=signal.side,
                canonical_symbol=canonical_symbol,
                candle_close_time=signal.candle_close_time,
                rsi=signal.rsi,
                close=signal.entry_price,
                ema200=signal.ema200,
                oi_to_market_cap=comparison["oi_to_market_cap"],
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                atr=signal.atr,
            )
        )
    return TradeSignalDispatchResult(
        tuple(dispatched), tuple(failures), tuple(details), tuple(scans)
    )


def scan_trade_conditions(
    snapshot: dict[str, Any], kline_loader: Callable[[str], list[Candle]]
) -> TradeConditionScanResult:
    if not snapshot["complete"]:
        return TradeConditionScanResult((), ())

    comparisons = [
        comparison
        for comparison in snapshot["comparisons"]
        if comparison["oi_to_market_cap"] > 1
    ]
    candles_by_symbol, failures, failures_by_symbol = _load_trade_candles(
        comparisons, kline_loader
    )
    scans = _build_trade_condition_scans(
        comparisons, candles_by_symbol, failures_by_symbol
    )
    return TradeConditionScanResult(tuple(scans), tuple(failures))


def _load_trade_candles(
    comparisons: list[dict[str, Any]], kline_loader: Callable[[str], list[Candle]]
) -> tuple[
    dict[str, list[Candle]],
    list[TradeSignalDispatchFailure],
    dict[str, TradeSignalDispatchFailure],
]:
    candles_by_symbol: dict[str, list[Candle]] = {}
    failures: list[TradeSignalDispatchFailure] = []
    failures_by_symbol: dict[str, TradeSignalDispatchFailure] = {}
    if not comparisons:
        return candles_by_symbol, failures, failures_by_symbol

    with ThreadPoolExecutor(
        max_workers=min(MAX_KLINE_WORKERS, len(comparisons))
    ) as executor:
        futures = [
            (comparison, executor.submit(kline_loader, _binance_symbol(comparison)))
            for comparison in comparisons
        ]
        for comparison, future in futures:
            canonical_symbol = comparison["canonical_symbol"]
            try:
                candles = future.result()
            except Exception as error:
                failure = TradeSignalDispatchFailure(
                    canonical_symbol,
                    f"{type(error).__name__}: {error}",
                )
                failures.append(failure)
                failures_by_symbol[canonical_symbol] = failure
                continue
            if len(candles) < REQUIRED_CLOSED_CANDLES:
                failure = TradeSignalDispatchFailure(
                    canonical_symbol,
                    f"仅获取到 {len(candles)} 根已收盘 15m K 线，需要至少 {REQUIRED_CLOSED_CANDLES} 根",
                )
                failures.append(failure)
                failures_by_symbol[canonical_symbol] = failure
                continue
            candles_by_symbol[canonical_symbol] = candles
    return candles_by_symbol, failures, failures_by_symbol


def _build_trade_condition_scans(
    comparisons: list[dict[str, Any]],
    candles_by_symbol: dict[str, list[Candle]],
    failures_by_symbol: dict[str, TradeSignalDispatchFailure],
) -> list[TradeConditionScan]:
    scans: list[TradeConditionScan] = []
    for comparison in comparisons:
        if comparison["oi_to_market_cap"] <= 1:
            continue
        canonical_symbol = comparison["canonical_symbol"]
        candles = candles_by_symbol.get(canonical_symbol)
        if candles is None:
            failure = failures_by_symbol[canonical_symbol]
            scans.append(
                TradeConditionScan(
                    status=KLINE_ERROR,
                    canonical_symbol=canonical_symbol,
                    candle_close_time=None,
                    rsi=None,
                    close=None,
                    ema200=None,
                    oi_to_market_cap=comparison["oi_to_market_cap"],
                    error=failure.message,
                )
            )
            continue
        scans.append(_scan_trade_condition(comparison, candles))
    return scans


def _binance_symbol(comparison: dict[str, Any]) -> str:
    for contract in comparison["contracts"]:
        if contract["venue"] == "Binance":
            return contract["symbol"]
    raise ValueError(
        f"Binance contract is missing for {comparison['canonical_symbol']}"
    )


def _stop_long_reasons(
    side: str,
    oi_to_market_cap: float,
    rsi: float | None,
    close: float | None,
    ema200: float | None,
) -> tuple[str, ...]:
    if side == LONG:
        reasons = []
        if oi_to_market_cap <= 1:
            reasons.append("oi_to_market_cap_not_above_100")
        if rsi is not None and rsi > 50:
            reasons.append("rsi_above_50")
        if close is not None and ema200 is not None and close < ema200:
            reasons.append("close_below_ema200")
        return tuple(reasons)
    raise ValueError(f"Unsupported trade signal side: {side}")


def _scan_trade_condition(
    comparison: dict[str, Any], candles: list[Candle]
) -> TradeConditionScan:
    rsi = current_rsi(candles)
    close = candles[-1].close
    ema200 = current_ema200(candles)
    reasons = []
    if rsi >= 50:
        reasons.append("rsi_not_below_50")
    if close <= ema200:
        reasons.append("close_not_above_ema200")
    return TradeConditionScan(
        status=CAN_LONG if not reasons else STOP_LONG,
        canonical_symbol=comparison["canonical_symbol"],
        candle_close_time=candles[-1].close_time,
        rsi=rsi,
        close=close,
        ema200=ema200,
        oi_to_market_cap=comparison["oi_to_market_cap"],
        reasons=tuple(reasons),
    )
