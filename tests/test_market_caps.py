import unittest

from crypto_oi_monitor.market_caps import COINGECKO_MARKETS_URL, fetch_market_caps, parse_market_caps


class CoinGeckoMarketCapTests(unittest.TestCase):
    def test_uses_unique_matching_symbol(self) -> None:
        result = parse_market_caps(
            [
                {"id": "ethereum", "symbol": "eth", "market_cap": 300_000_000_000},
                {"id": "bitcoin", "symbol": "btc", "market_cap": 2_000_000_000_000},
            ],
            {"ETH", "BTC"},
        )

        self.assertEqual(result.market_caps["ETH"].coingecko_id, "ethereum")
        self.assertEqual(result.market_caps["BTC"].market_cap_usd, 2_000_000_000_000)
        self.assertEqual(result.unmapped_assets, ())

    def test_leaves_ambiguous_symbol_unmapped(self) -> None:
        result = parse_market_caps(
            [
                {"id": "coin-a", "symbol": "aaa", "market_cap": 10},
                {"id": "coin-b", "symbol": "aaa", "market_cap": 9},
            ],
            {"AAA"},
        )

        self.assertEqual(result.market_caps, {})
        self.assertEqual(result.unmapped_assets, ("AAA",))

    def test_fetches_market_caps_in_symbol_batches(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                return [{"id": "ethereum", "symbol": "eth", "market_cap": 300}]

        client = FakeClient()
        result = fetch_market_caps(client, {"ETH"})

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(
            client.calls,
            [
                (
                    COINGECKO_MARKETS_URL,
                    {
                        "vs_currency": "usd",
                        "symbols": "eth",
                        "order": "market_cap_desc",
                        "per_page": "250",
                        "page": "1",
                        "sparkline": "false",
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
