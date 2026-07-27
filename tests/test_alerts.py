import unittest

from crypto_oi_monitor.alerts import ENTERED_HIGH_RISK, RECOVERED, alert_transition
from crypto_oi_monitor.domain import HIGH_RISK, NORMAL, WARNING


class AlertTransitionTests(unittest.TestCase):
    def test_sends_alert_when_ratio_strictly_exceeds_205_percent(self) -> None:
        self.assertEqual(
            alert_transition(NORMAL, 2.06, snapshot_complete=True),
            ENTERED_HIGH_RISK,
        )

    def test_does_not_repeat_alert_while_ratio_remains_above_enter_threshold(self) -> None:
        self.assertIsNone(
            alert_transition(HIGH_RISK, 2.10, snapshot_complete=True)
        )

    def test_sends_recovery_when_ratio_strictly_falls_below_195_percent(self) -> None:
        self.assertEqual(
            alert_transition(HIGH_RISK, 1.94, snapshot_complete=True),
            RECOVERED,
        )

    def test_keeps_candidate_active_inside_hysteresis_band(self) -> None:
        self.assertIsNone(
            alert_transition(HIGH_RISK, 2.00, snapshot_complete=True)
        )

    def test_does_not_enter_inside_hysteresis_band(self) -> None:
        self.assertIsNone(
            alert_transition(WARNING, 2.05, snapshot_complete=True)
        )

    def test_never_alerts_from_incomplete_snapshot(self) -> None:
        self.assertIsNone(
            alert_transition(NORMAL, 2.06, snapshot_complete=False)
        )


if __name__ == "__main__":
    unittest.main()
