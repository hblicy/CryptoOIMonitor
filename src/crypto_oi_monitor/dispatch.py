from __future__ import annotations

from typing import Any, Protocol

from .alerts import alert_transition


class AlertStateStore(Protocol):
    def get_alert_status(self, canonical_symbol: str) -> str | None: ...

    def set_alert_status(self, canonical_symbol: str, status: str) -> None: ...


class AlertNotifier(Protocol):
    def send(self, event: str, comparison: dict[str, Any]) -> None: ...


def dispatch_alerts(
    snapshot: dict[str, Any], store: AlertStateStore, notifier: AlertNotifier
) -> list[str]:
    if not snapshot["complete"]:
        return []
    dispatched: list[str] = []
    for comparison in snapshot["comparisons"]:
        previous = store.get_alert_status(comparison["canonical_symbol"])
        event = alert_transition(
            previous,
            comparison["oi_to_market_cap"],
            snapshot_complete=True,
        )
        if event is not None:
            notifier.send(event, comparison)
            dispatched.append(event)
            store.set_alert_status(comparison["canonical_symbol"], comparison["status"])
    return dispatched
