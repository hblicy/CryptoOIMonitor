import unittest

from crypto_oi_monitor.alerts import ENTERED_HIGH_RISK
from crypto_oi_monitor.notifier import WeComNotifier


class RecordingClient:
    def __init__(self) -> None:
        self.sent = []

    def post_json(self, url, payload):
        self.sent.append((url, payload))
        return {"errcode": 0}


class WeComNotifierTests(unittest.TestCase):
    def test_sends_high_risk_asset_with_ratio_and_venue_breakdown(self) -> None:
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
        content = client.sent[0][1]["markdown"]["content"]
        self.assertIn("PEPE", content)
        self.assertIn("250.00 USD", content)
        self.assertIn("250.00%", content)
        self.assertIn("Binance", content)


if __name__ == "__main__":
    unittest.main()
