from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import json
import logging
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
    dynamic_cooldown_candles,
    evaluate_trade_setup,
    exit_reasons,
    stop_long_reasons,
    trade_indicators,
    update_trailing_stop,
)


MAX_KLINE_WORKERS = 8
LEGACY_SHORT_STATE = "short"
STOP_LONG = "stop_long"
CAN_LONG = "can_long"
EXIT_LONG = "exit_long"
RESUME_LONG = "resume_long"
KLINE_ERROR = "kline_error"
NO_ADD = "no_add"
REENTRY_COOLDOWN = "reentry_cooldown"
TRADE_CONDITION_LIST_INTERVAL = timedelta(hours=1)
LOGGER = logging.getLogger(__name__)


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
    entry_atr: float | None = None
    highest_close: float | None = None
    last_processed_candle_close_time: int | None = None
    binance_symbol: str | None = None
    position_id: str | None = None


@dataclass(frozen=True)
class TradeSignalEvent:
    event_type: str
    canonical_symbol: str
    candle_close_time: int | None
    rsi: float | None
    close: float | None
    ema200: float | None
    oi_to_market_cap: float | None
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
    ema200_slope_reference: float | None = None
    average_quote_volume: float | None = None
    adjusted_aggregate_oi: float | None = None
    previous_adjusted_aggregate_oi: float | None = None
    cooldown_candles: int | None = None

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
            "ema200_slope_reference": self.ema200_slope_reference,
            "average_quote_volume": self.average_quote_volume,
            "adjusted_aggregate_oi": self.adjusted_aggregate_oi,
            "previous_adjusted_aggregate_oi": self.previous_adjusted_aggregate_oi,
            "cooldown_candles": self.cooldown_candles,
        }


@dataclass(frozen=True)
class TradeConditionScan:
    status: str
    canonical_symbol: str
    candle_close_time: int | None
    rsi: float | None
    close: float | None
    ema200: float | None
    oi_to_market_cap: float | None
    reasons: tuple[str, ...] = ()
    error: str | None = None
    previous_rsi: float | None = None
    previous_close: float | None = None
    previous_ema200: float | None = None
    quote_volume: float | None = None
    previous_quote_volume: float | None = None
    aggregate_oi_usd: float | None = None
    previous_aggregate_oi_usd: float | None = None
    ema200_slope_reference: float | None = None
    average_quote_volume: float | None = None
    adjusted_aggregate_oi: float | None = None
    previous_adjusted_aggregate_oi: float | None = None

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
            "ema200_slope_reference": self.ema200_slope_reference,
            "average_quote_volume": self.average_quote_volume,
            "adjusted_aggregate_oi": self.adjusted_aggregate_oi,
            "previous_adjusted_aggregate_oi": self.previous_adjusted_aggregate_oi,
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

    def notification_was_delivered(self, event_id: str) -> bool: ...

    def mark_notification_delivered(self, event_id: str) -> None: ...


class TradeConditionListNotifier(Protocol):
    def send_trade_condition_list(
        self,
        can_long: tuple[str, ...],
        stop_long: tuple[str, ...],
        exit_long: tuple[str, ...],
        periodic: bool,
    ) -> None: ...


class TradeSignalStateStore(Protocol):
    def apply_pending_trade_signal_states(self) -> None: ...

    def list_trade_signal_states(self) -> dict[str, TradeSignalState]: ...

    def get_trade_signal_state(
        self, canonical_symbol: str
    ) -> TradeSignalState | None: ...

    def set_trade_signal_state(
        self, canonical_symbol: str, state: TradeSignalState
    ) -> None: ...

    def clear_trade_signal_state(self, canonical_symbol: str) -> None: ...

    def notification_was_delivered(self, event_id: str) -> bool: ...

    def mark_notification_delivered(self, event_id: str) -> None: ...

    def mark_trade_notification_delivered(
        self, event_id: str, canonical_symbol: str, state: TradeSignalState
    ) -> None: ...

    def mark_trade_notification_state_applied(self, event_id: str) -> None: ...


class TradeSignalNotifier(Protocol):
    def send_trade_signal(
        self,
        signal: TradeSetup,
        comparison: dict[str, Any],
        previous_aggregate_oi_usd: float,
        event_type: str,
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
        cooldown_candles: int,
    ) -> None: ...


def _deliver_notification_once(
    store: TradeConditionListStateStore | TradeSignalStateStore,
    event_id: str,
    sender: Callable[[], None],
) -> bool:
    if store.notification_was_delivered(event_id):
        LOGGER.info("Skipping previously delivered notification %s", event_id)
        return False
    sender()
    store.mark_notification_delivered(event_id)
    return True


def _deliver_trade_notification_once(
    store: TradeSignalStateStore,
    event_id: str,
    canonical_symbol: str,
    next_state: TradeSignalState,
    sender: Callable[[], None],
) -> bool:
    if store.notification_was_delivered(event_id):
        LOGGER.info("Skipping previously delivered notification %s", event_id)
        return False
    sender()
    store.mark_trade_notification_delivered(
        event_id, canonical_symbol, next_state
    )
    return True


def _position_lifecycle_id(
    canonical_symbol: str, state: TradeSignalState
) -> str:
    if state.position_id is not None:
        return state.position_id
    return ":".join(
        (
            "legacy",
            canonical_symbol,
            repr(state.entry_price),
            repr(state.entry_atr),
        )
    )


def _trade_notification_event_id(
    event_type: str,
    canonical_symbol: str,
    *,
    state: TradeSignalState | None = None,
    candle_close_time: int | None = None,
) -> str:
    lifecycle = (
        _position_lifecycle_id(canonical_symbol, state)
        if state is not None
        else str(candle_close_time)
    )
    return f"trade:{event_type}:{canonical_symbol}:{lifecycle}"


def _condition_list_notification_event_id(
    event: str,
    previous: TradeConditionListState,
    can_long: tuple[str, ...],
    stop_long: tuple[str, ...],
    exit_long: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {
            "event": event,
            "previous_sent_at": (
                None
                if previous.last_sent_at is None
                else previous.last_sent_at.isoformat()
            ),
            "can_long": can_long,
            "stop_long": stop_long,
            "exit_long": exit_long,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"trade-condition-list:{digest}"

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
        event_id = _condition_list_notification_event_id(
            event, previous, can_long, stop_long, exit_long
        )
        _deliver_notification_once(
            store,
            event_id,
            lambda: notifier.send_trade_condition_list(
                can_long, stop_long, exit_long, event == "periodic"
            ),
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
    store.apply_pending_trade_signal_states()
    complete = snapshot["complete"] is True
    reference_oi = _reference_oi_by_symbol(reference_snapshot)
    persisted_states = {
        symbol: _as_trade_signal_state(state)
        for symbol, state in store.list_trade_signal_states().items()
    }
    comparisons_by_symbol = {
        comparison["canonical_symbol"]: comparison
        for comparison in snapshot["comparisons"]
    }
    state_failures: list[TradeSignalDispatchFailure] = []
    candidates: list[tuple[dict[str, Any], TradeSignalState | None]] = []
    for comparison in snapshot["comparisons"]:
        canonical_symbol = comparison["canonical_symbol"]
        state = persisted_states.get(canonical_symbol)
        if state is not None and state.status == LEGACY_SHORT_STATE:
            store.clear_trade_signal_state(canonical_symbol)
            persisted_states.pop(canonical_symbol, None)
            state = None
        ratio = comparison.get("oi_to_market_cap")
        if complete and (
            (ratio is not None and ratio > TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO)
            or state is not None
        ):
            candidates.append((comparison, state))
        elif not complete and state is not None and state.status in {LONG, NO_ADD}:
            candidates.append((comparison, state))

    for canonical_symbol, state in persisted_states.items():
        if (
            canonical_symbol in comparisons_by_symbol
            or state is None
            or state.status not in {LONG, NO_ADD}
        ):
            continue
        if state.binance_symbol is None:
            failure = TradeSignalDispatchFailure(
                canonical_symbol,
                "Persisted Binance symbol is missing for active risk checks",
            )
            LOGGER.error("%s: %s", canonical_symbol, failure.message)
            state_failures.append(failure)
            continue
        candidates.append(
            (
                {
                    "canonical_symbol": canonical_symbol,
                    "total_oi_usd": None,
                    "oi_to_market_cap": None,
                    "contracts": [
                        {"venue": "Binance", "symbol": state.binance_symbol}
                    ],
                },
                state,
            )
        )

    candidate_comparisons = [comparison for comparison, _state in candidates]
    candles_by_symbol, failures, failures_by_symbol = _load_trade_candles(
        candidate_comparisons, kline_loader
    )
    failures = state_failures + failures
    scan_by_symbol: dict[str, TradeConditionScan] = {}
    scan_order: list[str] = []

    def set_scan(scan: TradeConditionScan) -> None:
        if scan.canonical_symbol not in scan_by_symbol:
            scan_order.append(scan.canonical_symbol)
        scan_by_symbol[scan.canonical_symbol] = scan

    def record_candidate_failure(
        canonical_symbol: str, error: Exception
    ) -> TradeSignalDispatchFailure:
        failure = TradeSignalDispatchFailure(
            canonical_symbol, f"{type(error).__name__}: {error}"
        )
        failures.append(failure)
        failures_by_symbol[canonical_symbol] = failure
        LOGGER.exception("Trade signal action failed for %s", canonical_symbol)
        return failure

    if complete:
        eligible_comparisons = [
            comparison
            for comparison in snapshot["comparisons"]
            if comparison.get("oi_to_market_cap") is not None
            and comparison["oi_to_market_cap"]
            > TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
        ]
        for scan in _build_trade_condition_scans(
            eligible_comparisons,
            reference_oi,
            candles_by_symbol,
            failures_by_symbol,
        ):
            set_scan(scan)

    dispatched: list[str] = []
    details: list[TradeSignalEvent] = []
    for comparison, state in candidates:
        canonical_symbol = comparison["canonical_symbol"]
        ratio = comparison.get("oi_to_market_cap")
        binance_symbol = _binance_symbol(comparison)
        replay_candles = candles_by_symbol.get(canonical_symbol)
        if replay_candles is None:
            if (
                complete
                and state is not None
                and state.status == LONG
                and ratio is not None
                and ratio <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
            ):
                reasons = ("oi_to_market_cap_not_above_90",)
                set_scan(
                    _state_condition_scan(
                        comparison, None, STOP_LONG, reasons
                    )
                )
                try:
                    next_state = replace(
                        state, status=NO_ADD, binance_symbol=binance_symbol
                    )
                    event_id = _trade_notification_event_id(
                        STOP_LONG, canonical_symbol, state=state
                    )
                    _deliver_trade_notification_once(
                        store,
                        event_id,
                        canonical_symbol,
                        next_state,
                        lambda: notifier.send_stop_long(comparison, None, reasons),
                    )
                    store.set_trade_signal_state(
                        canonical_symbol,
                        next_state,
                    )
                    store.mark_trade_notification_state_applied(event_id)
                except Exception as error:
                    record_candidate_failure(canonical_symbol, error)
                    continue
                dispatched.append(STOP_LONG)
                details.append(
                    TradeSignalEvent(
                        STOP_LONG,
                        canonical_symbol,
                        None,
                        None,
                        None,
                        None,
                        ratio,
                        reasons=reasons,
                    )
                )
            elif state is not None and canonical_symbol in failures_by_symbol:
                set_scan(
                    TradeConditionScan(
                        KLINE_ERROR,
                        canonical_symbol,
                        None,
                        None,
                        None,
                        None,
                        ratio,
                        aggregate_oi_usd=comparison.get("total_oi_usd"),
                        error=failures_by_symbol[canonical_symbol].message,
                    )
                )
            continue
        candles = replay_candles[-REQUIRED_CLOSED_CANDLES:]
        indicators = trade_indicators(candles)
        cooldown_entry_floor: int | None = None
        if state is not None and state.status == REENTRY_COOLDOWN:
            if (
                state.cooldown_until_candle_close_time is not None
                and indicators.candle_close_time
                < state.cooldown_until_candle_close_time
            ):
                set_scan(
                    _state_condition_scan(
                        comparison,
                        indicators,
                        STOP_LONG,
                        ("reentry_cooldown_active",),
                    )
                )
                continue
            cooldown_entry_floor = state.cooldown_until_candle_close_time
        if state is not None:
            original_state = state
            if state.binance_symbol != binance_symbol:
                state = replace(state, binance_symbol=binance_symbol)
            if (
                state.entry_price is not None
                and state.stop_loss is not None
                and state.entry_atr is not None
                and state.highest_close is not None
            ):
                try:
                    (
                        state,
                        exit_indicators,
                        forced_exit_reasons,
                        cooldown_candles,
                    ) = _replay_active_position(
                        canonical_symbol, replay_candles, state
                    )
                except Exception as error:
                    failure = TradeSignalDispatchFailure(
                        canonical_symbol, f"{type(error).__name__}: {error}"
                    )
                    failures.append(failure)
                    failures_by_symbol[canonical_symbol] = failure
                    set_scan(
                        TradeConditionScan(
                            KLINE_ERROR,
                            canonical_symbol,
                            None,
                            None,
                            None,
                            None,
                            ratio,
                            aggregate_oi_usd=comparison.get("total_oi_usd"),
                            error=failure.message,
                        )
                    )
                    continue
                if state.stop_loss > original_state.stop_loss:
                    LOGGER.info(
                        "Raised %s active protection from %.8f to %.8f at closed candle %s",
                        canonical_symbol,
                        original_state.stop_loss,
                        state.stop_loss,
                        state.last_processed_candle_close_time,
                    )
            else:
                exit_indicators = indicators
                forced_exit_reasons = (
                    exit_reasons(
                        candles,
                        indicators,
                        state.stop_loss,
                        state.entry_price,
                    )
                    if state.stop_loss is not None
                    else ()
                )
                cooldown_candles = (
                    dynamic_cooldown_candles(candles)
                    if forced_exit_reasons
                    else None
                )
            if forced_exit_reasons:
                assert exit_indicators is not None
                assert cooldown_candles is not None
                LOGGER.info(
                    "Selected %s closed 15m candles for %s dynamic cooldown",
                    cooldown_candles,
                    canonical_symbol,
                )
                set_scan(
                    _state_condition_scan(
                        comparison,
                        exit_indicators,
                        EXIT_LONG,
                        forced_exit_reasons,
                    )
                )
                try:
                    next_state = TradeSignalState(
                        status=REENTRY_COOLDOWN,
                        cooldown_until_candle_close_time=(
                            exit_indicators.candle_close_time
                            + cooldown_candles
                            * FIFTEEN_MINUTES_MILLISECONDS
                        ),
                        binance_symbol=binance_symbol,
                        position_id=_position_lifecycle_id(
                            canonical_symbol, state
                        ),
                    )
                    event_id = _trade_notification_event_id(
                        EXIT_LONG, canonical_symbol, state=state
                    )
                    _deliver_trade_notification_once(
                        store,
                        event_id,
                        canonical_symbol,
                        next_state,
                        lambda: notifier.send_exit_long(
                            comparison,
                            exit_indicators,
                            state,
                            forced_exit_reasons,
                            cooldown_candles,
                        ),
                    )
                    store.set_trade_signal_state(
                        canonical_symbol,
                        next_state,
                    )
                    store.mark_trade_notification_state_applied(event_id)
                except Exception as error:
                    record_candidate_failure(canonical_symbol, error)
                    continue
                dispatched.append(EXIT_LONG)
                details.append(
                    TradeSignalEvent(
                        event_type=EXIT_LONG,
                        canonical_symbol=canonical_symbol,
                        candle_close_time=exit_indicators.candle_close_time,
                        rsi=exit_indicators.rsi,
                        close=exit_indicators.close,
                        ema200=exit_indicators.ema200,
                        oi_to_market_cap=comparison["oi_to_market_cap"],
                        entry_price=state.entry_price,
                        stop_loss=state.stop_loss,
                        atr=exit_indicators.atr,
                        reasons=forced_exit_reasons,
                        cooldown_candles=cooldown_candles,
                    )
                )
                continue
            if state != original_state:
                try:
                    store.set_trade_signal_state(canonical_symbol, state)
                except Exception as error:
                    record_candidate_failure(canonical_symbol, error)
                    continue
            if complete and (
                ratio is None or comparison.get("total_oi_usd") is None
            ):
                reasons = ("data_source_incomplete",)
                set_scan(
                    _state_condition_scan(
                        comparison, indicators, STOP_LONG, reasons
                    )
                )
                if state.status == LONG:
                    try:
                        next_state = replace(state, status=NO_ADD)
                        event_id = _trade_notification_event_id(
                            STOP_LONG, canonical_symbol, state=state
                        )
                        _deliver_trade_notification_once(
                            store,
                            event_id,
                            canonical_symbol,
                            next_state,
                            lambda: notifier.send_stop_long(
                                comparison, indicators, reasons
                            ),
                        )
                        store.set_trade_signal_state(
                            canonical_symbol, next_state
                        )
                        store.mark_trade_notification_state_applied(event_id)
                    except Exception as error:
                        record_candidate_failure(canonical_symbol, error)
                        continue
                    dispatched.append(STOP_LONG)
                    details.append(
                        TradeSignalEvent(
                            STOP_LONG,
                            canonical_symbol,
                            indicators.candle_close_time,
                            indicators.rsi,
                            indicators.close,
                            indicators.ema200,
                            ratio,
                            reasons=reasons,
                        )
                    )
                continue
            if (
                complete
                and ratio is not None
                and ratio <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
            ):
                reasons = ("oi_to_market_cap_not_above_90",)
                set_scan(
                    _state_condition_scan(
                        comparison, indicators, STOP_LONG, reasons
                    )
                )
                if state.status == LONG:
                    try:
                        next_state = replace(state, status=NO_ADD)
                        event_id = _trade_notification_event_id(
                            STOP_LONG, canonical_symbol, state=state
                        )
                        _deliver_trade_notification_once(
                            store,
                            event_id,
                            canonical_symbol,
                            next_state,
                            lambda: notifier.send_stop_long(
                                comparison, indicators, reasons
                            ),
                        )
                        store.set_trade_signal_state(
                            canonical_symbol,
                            next_state,
                        )
                        store.mark_trade_notification_state_applied(event_id)
                    except Exception as error:
                        record_candidate_failure(canonical_symbol, error)
                        continue
                    dispatched.append(STOP_LONG)
                    details.append(
                        TradeSignalEvent(
                            STOP_LONG,
                            canonical_symbol,
                            indicators.candle_close_time,
                            indicators.rsi,
                            indicators.close,
                            indicators.ema200,
                            ratio,
                            reasons=reasons,
                        )
                    )
                continue
            reasons = stop_long_reasons(indicators)
            if state.status == LONG and reasons:
                set_scan(
                    _state_condition_scan(
                        comparison, indicators, STOP_LONG, reasons
                    )
                )
                try:
                    next_state = replace(state, status=NO_ADD)
                    event_id = _trade_notification_event_id(
                        STOP_LONG, canonical_symbol, state=state
                    )
                    _deliver_trade_notification_once(
                        store,
                        event_id,
                        canonical_symbol,
                        next_state,
                        lambda: notifier.send_stop_long(
                            comparison, indicators, reasons
                        ),
                    )
                    store.set_trade_signal_state(
                        canonical_symbol,
                        next_state,
                    )
                    store.mark_trade_notification_state_applied(event_id)
                except Exception as error:
                    record_candidate_failure(canonical_symbol, error)
                    continue
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
                if complete:
                    set_scan(
                        _state_condition_scan(
                            comparison, indicators, CAN_LONG, ()
                        )
                    )
                else:
                    set_scan(
                        _state_condition_scan(
                            comparison,
                            indicators,
                            STOP_LONG,
                            ("data_source_incomplete",),
                        )
                    )
                continue
            if not complete:
                set_scan(
                    _state_condition_scan(
                        comparison,
                        indicators,
                        STOP_LONG,
                        ("data_source_incomplete",),
                    )
                )
                continue
            if ratio is None or comparison.get("total_oi_usd") is None:
                set_scan(
                    _state_condition_scan(
                        comparison,
                        indicators,
                        STOP_LONG,
                        ("data_source_incomplete",),
                    )
                )
                continue
            entry_blockers = _aggregate_oi_entry_reasons(
                comparison, reference_oi, indicators
            )
            entry_blockers += entry_reasons(indicators)
            if (
                cooldown_entry_floor is not None
                and indicators.ema200_breakout_candles_ago is not None
                and indicators.candle_close_time
                - indicators.ema200_breakout_candles_ago
                * FIFTEEN_MINUTES_MILLISECONDS
                < cooldown_entry_floor
            ):
                entry_blockers += ("ema200_breakout_before_cooldown_end",)
            if entry_blockers:
                set_scan(
                    _state_condition_scan(
                        comparison, indicators, STOP_LONG, entry_blockers
                    )
                )
                continue
            signal_event_type = RESUME_LONG
        else:
            if not complete:
                continue
            signal_event_type = LONG
        if ratio is None or ratio <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO:
            continue
        if _aggregate_oi_entry_reasons(comparison, reference_oi, indicators):
            continue
        signal = evaluate_trade_setup(candles)
        if signal is None:
            continue
        previous_aggregate_oi_usd = reference_oi[canonical_symbol]
        set_scan(
            _state_condition_scan(comparison, indicators, CAN_LONG, ())
        )
        try:
            next_state = TradeSignalState(
                status=signal.side,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                entry_atr=signal.atr,
                highest_close=signal.entry_price,
                last_processed_candle_close_time=signal.candle_close_time,
                binance_symbol=binance_symbol,
                position_id=f"{canonical_symbol}:{signal.candle_close_time}",
            )
            event_id = _trade_notification_event_id(
                signal_event_type,
                canonical_symbol,
                candle_close_time=signal.candle_close_time,
            )
            _deliver_trade_notification_once(
                store,
                event_id,
                canonical_symbol,
                next_state,
                lambda: notifier.send_trade_signal(
                    signal,
                    comparison,
                    previous_aggregate_oi_usd,
                    signal_event_type,
                ),
            )
            store.set_trade_signal_state(
                canonical_symbol,
                next_state,
            )
            store.mark_trade_notification_state_applied(event_id)
        except Exception as error:
            record_candidate_failure(canonical_symbol, error)
            continue
        if signal_event_type == RESUME_LONG:
            LOGGER.info(
                "Resumed long signal for %s at closed candle %s",
                canonical_symbol,
                signal.candle_close_time,
            )
        dispatched.append(signal_event_type)
        details.append(
            TradeSignalEvent(
                event_type=signal_event_type,
                canonical_symbol=canonical_symbol,
                candle_close_time=signal.candle_close_time,
                rsi=signal.rsi,
                close=signal.entry_price,
                ema200=signal.ema200,
                oi_to_market_cap=ratio,
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
                ema200_slope_reference=signal.ema200_slope_reference,
                average_quote_volume=signal.average_quote_volume,
                adjusted_aggregate_oi=(
                    comparison["total_oi_usd"] / signal.entry_price
                ),
                previous_adjusted_aggregate_oi=(
                    previous_aggregate_oi_usd / signal.previous_close
                ),
            )
        )
    scans = [scan_by_symbol[symbol] for symbol in scan_order]
    return TradeSignalDispatchResult(
        tuple(dispatched), tuple(failures), tuple(details), tuple(scans)
    )


def _replay_active_position(
    canonical_symbol: str,
    candles: list[Candle],
    state: TradeSignalState,
) -> tuple[
    TradeSignalState,
    TradeIndicators | None,
    tuple[str, ...],
    int | None,
]:
    if (
        state.entry_price is None
        or state.stop_loss is None
        or state.entry_atr is None
        or state.highest_close is None
    ):
        raise ValueError(f"Active trade state is incomplete for {canonical_symbol}")
    latest_close_time = candles[-1].close_time
    if state.last_processed_candle_close_time is None:
        LOGGER.warning(
            "Trailing history start is unknown for migrated state %s; continuing from candle %s",
            canonical_symbol,
            latest_close_time,
        )
        unprocessed_indexes = [len(candles) - 1]
    else:
        if state.last_processed_candle_close_time > latest_close_time:
            raise ValueError(
                "Stored trailing candle time is newer than Binance closed K-line "
                f"for {canonical_symbol}"
            )
        unprocessed_indexes = [
            index
            for index, candle in enumerate(candles)
            if candle.close_time > state.last_processed_candle_close_time
        ]
        if unprocessed_indexes:
            first = candles[unprocessed_indexes[0]]
            expected_first_close_time = (
                state.last_processed_candle_close_time
                + FIFTEEN_MINUTES_MILLISECONDS
            )
            has_gap = first.close_time != expected_first_close_time or any(
                candles[current].close_time - candles[previous].close_time
                != FIFTEEN_MINUTES_MILLISECONDS
                for previous, current in zip(
                    unprocessed_indexes, unprocessed_indexes[1:]
                )
            )
            if has_gap:
                raise ValueError(
                    "Binance closed K-line history does not cover the "
                    f"trailing replay gap for {canonical_symbol}"
                )

    replayed_state = state
    if not unprocessed_indexes:
        indicators = trade_indicators(candles[-REQUIRED_CLOSED_CANDLES:])
        reasons = exit_reasons(
            candles,
            indicators,
            replayed_state.stop_loss,
            replayed_state.entry_price,
        )
        return (
            replayed_state,
            indicators if reasons else None,
            reasons,
            dynamic_cooldown_candles(candles) if reasons else None,
        )

    for index in unprocessed_indexes:
        history_start = index - REQUIRED_CLOSED_CANDLES + 1
        if history_start < 0:
            raise ValueError(
                "Binance closed K-line history does not provide EMA200 warmup "
                f"for trailing replay of {canonical_symbol}"
            )
        history = candles[history_start : index + 1]
        candle = candles[index]
        next_highest_close, next_stop_loss = update_trailing_stop(
            replayed_state.entry_price,
            replayed_state.entry_atr,
            replayed_state.stop_loss,
            replayed_state.highest_close,
            candle.close,
        )
        replayed_state = replace(
            replayed_state,
            stop_loss=next_stop_loss,
            highest_close=next_highest_close,
            last_processed_candle_close_time=candle.close_time,
        )
        indicators = trade_indicators(history)
        reasons = exit_reasons(
            history,
            indicators,
            replayed_state.stop_loss,
            replayed_state.entry_price,
        )
        if reasons:
            return (
                replayed_state,
                indicators,
                reasons,
                dynamic_cooldown_candles(history),
            )
    return replayed_state, None, (), None


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
                trade_indicators(candles[-REQUIRED_CLOSED_CANDLES:])
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
    candles = candles[-REQUIRED_CLOSED_CANDLES:]
    indicators = trade_indicators(candles)
    forced_exit_reasons = exit_reasons(candles, indicators, None)
    reasons = forced_exit_reasons or (
        _aggregate_oi_entry_reasons(comparison, reference_oi, indicators)
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
        ema200_slope_reference=indicators.ema200_slope_reference,
        average_quote_volume=indicators.average_quote_volume,
        adjusted_aggregate_oi=(
            float(comparison["total_oi_usd"]) / indicators.close
        ),
        previous_adjusted_aggregate_oi=(
            None
            if reference_oi.get(comparison["canonical_symbol"]) is None
            else reference_oi[comparison["canonical_symbol"]]
            / indicators.previous_close
        ),
    )


def _state_condition_scan(
    comparison: dict[str, Any],
    indicators: TradeIndicators | None,
    status: str,
    reasons: tuple[str, ...],
) -> TradeConditionScan:
    ratio = comparison.get("oi_to_market_cap")
    aggregate_oi_usd = comparison.get("total_oi_usd")
    if indicators is None:
        return TradeConditionScan(
            status=status,
            canonical_symbol=comparison["canonical_symbol"],
            candle_close_time=None,
            rsi=None,
            close=None,
            ema200=None,
            oi_to_market_cap=ratio,
            reasons=tuple(reasons),
            aggregate_oi_usd=aggregate_oi_usd,
        )
    return TradeConditionScan(
        status=status,
        canonical_symbol=comparison["canonical_symbol"],
        candle_close_time=indicators.candle_close_time,
        rsi=indicators.rsi,
        close=indicators.close,
        ema200=indicators.ema200,
        oi_to_market_cap=ratio,
        reasons=tuple(reasons),
        previous_rsi=indicators.previous_rsi,
        previous_close=indicators.previous_close,
        previous_ema200=indicators.previous_ema200,
        quote_volume=indicators.quote_volume,
        previous_quote_volume=indicators.previous_quote_volume,
        aggregate_oi_usd=aggregate_oi_usd,
        ema200_slope_reference=indicators.ema200_slope_reference,
        average_quote_volume=indicators.average_quote_volume,
        adjusted_aggregate_oi=(
            None
            if aggregate_oi_usd is None
            else float(aggregate_oi_usd) / indicators.close
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
    comparison: dict[str, Any],
    reference_oi: dict[str, float],
    indicators: TradeIndicators,
) -> tuple[str, ...]:
    previous_oi = reference_oi.get(comparison["canonical_symbol"])
    if previous_oi is None:
        return ("aggregate_oi_history_unavailable",)
    current_units = float(comparison["total_oi_usd"]) / indicators.close
    previous_units = previous_oi / indicators.previous_close
    if current_units <= previous_units:
        return ("aggregate_oi_not_increasing_after_price_adjustment",)
    return ()
