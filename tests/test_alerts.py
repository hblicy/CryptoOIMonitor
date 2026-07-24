import unittest

from crypto_oi_monitor.alerts import ENTERED_HIGH_RISK, RECOVERED, alert_transition
from crypto_oi_monitor.domain import HIGH_RISK, NORMAL, WARNING


class AlertTransitionTests(unittest.TestCase):
    def test_sends_alert_when_asset_enters_high_risk(self) -> None:
        self.assertEqual(
            alert_transition(NORMAL, HIGH_RISK, snapshot_complete=True),
            ENTERED_HIGH_RISK,
        )

    def test_does_not_repeat_alert_while_asset_remains_high_risk(self) -> None:
        self.assertIsNone(
            alert_transition(HIGH_RISK, HIGH_RISK, snapshot_complete=True)
        )

    def test_sends_recovery_when_asset_leaves_high_risk(self) -> None:
        self.assertEqual(
            alert_transition(HIGH_RISK, WARNING, snapshot_complete=True),
            RECOVERED,
        )

    def test_never_alerts_from_incomplete_snapshot(self) -> None:
        self.assertIsNone(
            alert_transition(NORMAL, HIGH_RISK, snapshot_complete=False)
        )


if __name__ == "__main__":
    unittest.main()
