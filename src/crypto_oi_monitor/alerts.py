from __future__ import annotations

from .domain import HIGH_RISK


ENTERED_HIGH_RISK = "entered_high_risk"
RECOVERED = "recovered"
ALERT_ENTER_RATIO = 2.05
ALERT_EXIT_RATIO = 1.95


def alert_transition(
    previous_status: str | None,
    oi_to_market_cap: float,
    snapshot_complete: bool,
) -> str | None:
    if not snapshot_complete:
        return None
    if previous_status != HIGH_RISK and oi_to_market_cap > ALERT_ENTER_RATIO:
        return ENTERED_HIGH_RISK
    if previous_status == HIGH_RISK and oi_to_market_cap < ALERT_EXIT_RATIO:
        return RECOVERED
    return None
