import unittest

from crypto_oi_monitor.alerts import ENTERED_HIGH_RISK, RECOVERED
from crypto_oi_monitor.notifier import WeComNotifier


class RecordingClient:
    def __init__(self) -> None:
        self.sent = []

    def post_json(self, url, payload):
        self.sent.append((url, payload))
        return {"errcode": 0}


class WeComNotifierTests(unittest.TestCase):
    def test_sends_ambush_candidate_with_ratio_and_venue_breakdown(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send(
            ENTERED_HIGH_RISK,
            {
                "canonical_symbol": "PEPE",
                "market_cap_usd": 100,
                "total_oi_usd": 250,
                "oi_to_market_cap": 2.5,
                "contracts": [
                    {"venue": "Binance", "oi_usd": 200},
                    {"venue": "OKX", "oi_usd": 50},
                ],
            },
        )

        self.assertEqual(client.sent[0][0], "https://wecom.example/webhook")
        payload = client.sent[0][1]
        self.assertEqual(payload["msgtype"], "text")
        content = payload["text"]["content"]
        self.assertIn("OI 埋伏候选", content)
        self.assertIn("PEPE", content)
        self.assertIn("250.00 USD", content)
        self.assertIn("250.00%", content)
        self.assertIn("Binance", content)
        self.assertNotIn("<font", content)
        self.assertNotIn("**", content)

    def test_sends_ambush_candidate_exit_reminder(self) -> None:
        client = RecordingClient()
        notifier = WeComNotifier("https://wecom.example/webhook", client)

        notifier.send(
            RECOVERED,
            {
                "canonical_symbol": "PEPE",
                "market_cap_usd": 100,
                "total_oi_usd": 150,
                "oi_to_market_cap": 1.5,
                "contracts": [],
            },
        )

        payload = client.sent[0][1]
        self.assertEqual(payload["msgtype"], "text")
        content = payload["text"]["content"]
        self.assertIn("退出 OI 埋伏候选", content)
        self.assertNotIn("<font", content)
        self.assertNotIn("**", content)


if __name__ == "__main__":
    unittest.main()
