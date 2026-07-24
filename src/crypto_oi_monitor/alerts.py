from __future__ import annotations

from .domain import HIGH_RISK


ENTERED_HIGH_RISK = "entered_high_risk"
RECOVERED = "recovered"


def alert_transition(
    previous_status: str | None,
    current_status: str,
    snapshot_complete: bool,
) -> str | None:
    if not snapshot_complete:
        return None
    if current_status == HIGH_RISK and previous_status != HIGH_RISK:
        return ENTERED_HIGH_RISK
    if previous_status == HIGH_RISK and current_status != HIGH_RISK:
        return RECOVERED
    return None
