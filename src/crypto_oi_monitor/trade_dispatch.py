from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from .domain import TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
from .trading import (
    LONG,
    Candle,
    FIFTEEN_MINUTES_MILLISECONDS,
    REQUIRED_CLOSED_CANDLES,
    TradeIndicators,
    TradeSetup,
    entry_reasons,
    evaluate_trade_setup,
    exit_reasons,
    stop_long_reasons,
    trade_indicators,
)


MAX_KLINE_WORKERS = 8
LEGACY_SHORT_STATE = "short"
STOP_LONG = "stop_long"
CAN_LONG = "can_long"
EXIT_LONG = "exit_long"
KLINE_ERROR = "kline_error"
NO_ADD = "no_add"
REENTRY_COOLDOWN = "reentry_cooldown"
REENTRY_COOLDOWN_CANDLES = 4
TRADE_CONDITION_LIST_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class TradeSignalDispatchFailure:
    canonical_symbol: str
    message: str


@dataclass(frozen=True)
class TradeSignalState:
    status: str
    entry_price: float | None = None
    stop_loss: float | None = None
    cooldown_until_candle_close_time: int | None = None


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
    previous_rsi: float | None = None
    previous_close: float | None = None
    previous_ema200: float | None = None
    quote_volume: float | None = None
    previous_quote_volume: float | None = None
    aggregate_oi_usd: float | None = None
    previous_aggregate_oi_usd: float | None = None

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
            "previous_rsi": self.previous_rsi,
            "previous_close": self.previous_close,
            "previous_ema200": self.previous_ema200,
            "quote_volume": self.quote_volume,
            "previous_quote_volume": self.previous_quote_volume,
            "aggregate_oi_usd": self.aggregate_oi_usd,
            "previous_aggregate_oi_usd": self.previous_aggregate_oi_usd,
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
    previous_rsi: float | None = None
    previous_close: float | None = None
    previous_ema200: float | None = None
    quote_volume: float | None = None
    previous_quote_volume: float | None = None
    aggregate_oi_usd: float | None = None
    previous_aggregate_oi_usd: float | None = None

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
            "previous_rsi": self.previous_rsi,
            "previous_close": self.previous_close,
            "previous_ema200": self.previous_ema200,
            "quote_volume": self.quote_volume,
            "previous_quote_volume": self.previous_quote_volume,
            "aggregate_oi_usd": self.aggregate_oi_usd,
            "previous_aggregate_oi_usd": self.previous_aggregate_oi_usd,
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


@dataclass(frozen=True)
class TradeConditionListState:
    can_long: tuple[str, ...]
    stop_long: tuple[str, ...]
    exit_long: tuple[str, ...]
    last_sent_at: datetime | None


class TradeConditionListStateStore(Protocol):
    def get_trade_condition_list_state(self) -> TradeConditionListState: ...

    def set_trade_condition_list_state(self, state: TradeConditionListState) -> None: ...


class TradeConditionListNotifier(Protocol):
    def send_trade_condition_list(
        self,
        can_long: tuple[str, ...],
        stop_long: tuple[str, ...],
        exit_long: tuple[str, ...],
        periodic: bool,
    ) -> None: ...


class TradeSignalStateStore(Protocol):
    def get_trade_signal_state(
        self, canonical_symbol: str
    ) -> TradeSignalState | None: ...

    def set_trade_signal_state(
        self, canonical_symbol: str, state: TradeSignalState
    ) -> None: ...

    def clear_trade_signal_state(self, canonical_symbol: str) -> None: ...


class TradeSignalNotifier(Protocol):
    def send_trade_signal(
        self,
        signal: TradeSetup,
        comparison: dict[str, Any],
        previous_aggregate_oi_usd: float,
    ) -> None: ...

    def send_stop_long(
        self,
        comparison: dict[str, Any],
        indicators: TradeIndicators | None,
        reasons: tuple[str, ...],
    ) -> None: ...

    def send_exit_long(
        self,
        comparison: dict[str, Any],
        indicators: TradeIndicators,
        state: TradeSignalState,
        reasons: tuple[str, ...],
    ) -> None: ...


def dispatch_trade_condition_list(
    scans: tuple[TradeConditionScan, ...],
    store: TradeConditionListStateStore,
    notifier: TradeConditionListNotifier,
    now: datetime,
) -> str | None:
    can_long = tuple(
        sorted(
            {
                scan.canonical_symbol
                for scan in scans
                if scan.status == CAN_LONG
            }
        )
    )
    stop_long = tuple(
        sorted(
            {
                scan.canonical_symbol
                for scan in scans
                if scan.status == STOP_LONG
            }
        )
    )
    exit_long = tuple(
        sorted(
            {
                scan.canonical_symbol
                for scan in scans
                if scan.status == EXIT_LONG
            }
        )
    )
    previous = store.get_trade_condition_list_state()
    has_new_symbols = bool(
        set(can_long) - set(previous.can_long)
        or set(stop_long) - set(previous.stop_long)
        or set(exit_long) - set(previous.exit_long)
    )
    periodic = (
        previous.last_sent_at is None
        or now - previous.last_sent_at >= TRADE_CONDITION_LIST_INTERVAL
    )
    event = "updated" if has_new_symbols else "periodic" if periodic else None
    if event is not None:
        notifier.send_trade_condition_list(
            can_long, stop_long, exit_long, event == "periodic"
        )
        store.set_trade_condition_list_state(
            TradeConditionListState(can_long, stop_long, exit_long, now)
        )
    return event


def dispatch_trade_signals(
    snapshot: dict[str, Any],
    reference_snapshot: dict[str, Any] | None,
    kline_loader: Callable[[str], list[Candle]],
    store: TradeSignalStateStore,
    notifier: TradeSignalNotifier,
) -> TradeSignalDispatchResult:
    if not snapshot["complete"]:
        return TradeSignalDispatchResult((), ())

    reference_oi = _reference_oi_by_symbol(reference_snapshot)

    candidates: list[tuple[dict[str, Any], TradeSignalState | None]] = []
    oi_threshold_stops: list[tuple[dict[str, Any], TradeSignalState]] = []
    for comparison in snapshot["comparisons"]:
        state = _as_trade_signal_state(
            store.get_trade_signal_state(comparison["canonical_symbol"])
        )
        if state is not None and state.status == LEGACY_SHORT_STATE:
            store.clear_trade_signal_state(comparison["canonical_symbol"])
            state = None
        if (
            state is not None
            and state.status == LONG
            and comparison["oi_to_market_cap"]
            <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
        ):
            oi_threshold_stops.append((comparison, state))
        elif (
            comparison["oi_to_market_cap"] > TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
            or state is not None
        ):
            candidates.append((comparison, state))

    dispatched: list[str] = []
    details: list[TradeSignalEvent] = []
    for comparison, state in oi_threshold_stops:
        canonical_symbol = comparison["canonical_symbol"]
        reasons = ("oi_to_market_cap_not_above_100",)
        notifier.send_stop_long(comparison, None, reasons)
        store.set_trade_signal_state(
            canonical_symbol,
            TradeSignalState(
                status=NO_ADD,
                entry_price=state.entry_price,
                stop_loss=state.stop_loss,
            ),
        )
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
        candidate_comparisons, reference_oi, candles_by_symbol, failures_by_symbol
    )
    for comparison, state in candidates:
        canonical_symbol = comparison["canonical_symbol"]
        candles = candles_by_symbol.get(canonical_symbol)
        if candles is None:
            continue
        indicators = trade_indicators(candles)
        if state is not None and state.status == REENTRY_COOLDOWN:
            if (
                state.cooldown_until_candle_close_time is not None
                and indicators.candle_close_time
                < state.cooldown_until_candle_close_time
            ):
                continue
            store.clear_trade_signal_state(canonical_symbol)
            state = None
        if state is not None:
            forced_exit_reasons = (
                exit_reasons(candles, indicators, state.stop_loss)
                if state.stop_loss is not None
                else ()
            )
            if forced_exit_reasons:
                notifier.send_exit_long(
                    comparison, indicators, state, forced_exit_reasons
                )
                store.set_trade_signal_state(
                    canonical_symbol,
                    TradeSignalState(
                        status=REENTRY_COOLDOWN,
                        cooldown_until_candle_close_time=(
                            indicators.candle_close_time
                            + REENTRY_COOLDOWN_CANDLES
                            * FIFTEEN_MINUTES_MILLISECONDS
                        ),
                    ),
                )
                dispatched.append(EXIT_LONG)
                details.append(
                    TradeSignalEvent(
                        event_type=EXIT_LONG,
                        canonical_symbol=canonical_symbol,
                        candle_close_time=indicators.candle_close_time,
                        rsi=indicators.rsi,
                        close=indicators.close,
                        ema200=indicators.ema200,
                        oi_to_market_cap=comparison["oi_to_market_cap"],
                        entry_price=state.entry_price,
                        stop_loss=state.stop_loss,
                        atr=indicators.atr,
                        reasons=forced_exit_reasons,
                    )
                )
                continue
            reasons = stop_long_reasons(indicators)
            if state.status == LONG and reasons:
                notifier.send_stop_long(comparison, indicators, reasons)
                store.set_trade_signal_state(
                    canonical_symbol,
                    TradeSignalState(
                        status=NO_ADD,
                        entry_price=state.entry_price,
                        stop_loss=state.stop_loss,
                    ),
                )
                dispatched.append(STOP_LONG)
                details.append(
                    TradeSignalEvent(
                        event_type=STOP_LONG,
                        canonical_symbol=canonical_symbol,
                        candle_close_time=indicators.candle_close_time,
                        rsi=indicators.rsi,
                        close=indicators.close,
                        ema200=indicators.ema200,
                        oi_to_market_cap=comparison["oi_to_market_cap"],
                        reasons=reasons,
                    )
                )
                continue
            if state.status == LONG:
                continue
            entry_blockers = _aggregate_oi_entry_reasons(comparison, reference_oi)
            entry_blockers += entry_reasons(indicators)
            if entry_blockers:
                continue
            store.clear_trade_signal_state(canonical_symbol)
        if comparison["oi_to_market_cap"] <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO:
            continue
        if _aggregate_oi_entry_reasons(comparison, reference_oi):
            continue
        signal = evaluate_trade_setup(candles)
        if signal is None:
            continue
        previous_aggregate_oi_usd = reference_oi[canonical_symbol]
        notifier.send_trade_signal(
            signal, comparison, previous_aggregate_oi_usd
        )
        store.set_trade_signal_state(
            canonical_symbol,
            TradeSignalState(
                status=signal.side,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
            ),
        )
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
                previous_rsi=signal.previous_rsi,
                previous_close=signal.previous_close,
                previous_ema200=signal.previous_ema200,
                quote_volume=signal.quote_volume,
                previous_quote_volume=signal.previous_quote_volume,
                aggregate_oi_usd=comparison["total_oi_usd"],
                previous_aggregate_oi_usd=previous_aggregate_oi_usd,
            )
        )
    return TradeSignalDispatchResult(
        tuple(dispatched), tuple(failures), tuple(details), tuple(scans)
    )


def scan_trade_conditions(
    snapshot: dict[str, Any],
    reference_snapshot: dict[str, Any] | None,
    kline_loader: Callable[[str], list[Candle]],
) -> TradeConditionScanResult:
    if not snapshot["complete"]:
        return TradeConditionScanResult((), ())

    comparisons = [
        comparison
        for comparison in snapshot["comparisons"]
        if comparison["oi_to_market_cap"] > TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
    ]
    candles_by_symbol, failures, failures_by_symbol = _load_trade_candles(
        comparisons, kline_loader
    )
    scans = _build_trade_condition_scans(
        comparisons,
        _reference_oi_by_symbol(reference_snapshot),
        candles_by_symbol,
        failures_by_symbol,
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
                trade_indicators(candles)
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
    reference_oi: dict[str, float],
    candles_by_symbol: dict[str, list[Candle]],
    failures_by_symbol: dict[str, TradeSignalDispatchFailure],
) -> list[TradeConditionScan]:
    scans: list[TradeConditionScan] = []
    for comparison in comparisons:
        if comparison["oi_to_market_cap"] <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO:
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
                    aggregate_oi_usd=comparison["total_oi_usd"],
                    previous_aggregate_oi_usd=reference_oi.get(canonical_symbol),
                    error=failure.message,
                )
            )
            continue
        scans.append(_scan_trade_condition(comparison, reference_oi, candles))
    return scans


def _binance_symbol(comparison: dict[str, Any]) -> str:
    for contract in comparison["contracts"]:
        if contract["venue"] == "Binance":
            return contract["symbol"]
    raise ValueError(
        f"Binance contract is missing for {comparison['canonical_symbol']}"
    )


def _as_trade_signal_state(
    state: TradeSignalState | str | None,
) -> TradeSignalState | None:
    if state is None or isinstance(state, TradeSignalState):
        return state
    if isinstance(state, str):
        return TradeSignalState(status=state)
    raise TypeError(f"Unsupported trade signal state: {type(state).__name__}")


def _scan_trade_condition(
    comparison: dict[str, Any],
    reference_oi: dict[str, float],
    candles: list[Candle],
) -> TradeConditionScan:
    indicators = trade_indicators(candles)
    forced_exit_reasons = exit_reasons(candles, indicators, None)
    reasons = forced_exit_reasons or (
        _aggregate_oi_entry_reasons(comparison, reference_oi)
        + entry_reasons(indicators)
    )
    return TradeConditionScan(
        status=(
            EXIT_LONG
            if forced_exit_reasons
            else CAN_LONG
            if not reasons
            else STOP_LONG
        ),
        canonical_symbol=comparison["canonical_symbol"],
        candle_close_time=indicators.candle_close_time,
        rsi=indicators.rsi,
        close=indicators.close,
        ema200=indicators.ema200,
        oi_to_market_cap=comparison["oi_to_market_cap"],
        reasons=tuple(reasons),
        previous_rsi=indicators.previous_rsi,
        previous_close=indicators.previous_close,
        previous_ema200=indicators.previous_ema200,
        quote_volume=indicators.quote_volume,
        previous_quote_volume=indicators.previous_quote_volume,
        aggregate_oi_usd=comparison["total_oi_usd"],
        previous_aggregate_oi_usd=reference_oi.get(
            comparison["canonical_symbol"]
        ),
    )


def _reference_oi_by_symbol(
    reference_snapshot: dict[str, Any] | None,
) -> dict[str, float]:
    if reference_snapshot is None:
        return {}
    if reference_snapshot["complete"] is not True:
        return {}
    return {
        comparison["canonical_symbol"]: float(comparison["total_oi_usd"])
        for comparison in reference_snapshot["comparisons"]
    }


def _aggregate_oi_entry_reasons(
    comparison: dict[str, Any], reference_oi: dict[str, float]
) -> tuple[str, ...]:
    previous_oi = reference_oi.get(comparison["canonical_symbol"])
    if previous_oi is None:
        return ("aggregate_oi_history_unavailable",)
    if float(comparison["total_oi_usd"]) <= previous_oi:
        return ("aggregate_oi_not_increasing",)
    return ()
