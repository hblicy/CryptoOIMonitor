import unittest

from crypto_oi_monitor.sources import (
    BINANCE_EXCHANGE_INFO_URL,
    BINANCE_TICKERS_URL,
    HYPERLIQUID_INFO_URL,
    fetch_binance_universe,
    fetch_hyperliquid_open_interest,
)


class FakeHttpClient:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict[str, str] | None]] = []
        self.post_calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, url: str, params: dict[str, str] | None = None):
        self.get_calls.append((url, params))
        if url == BINANCE_EXCHANGE_INFO_URL:
            return {
                "symbols": [
                    {
                        "symbol": "ETHUSDT",
                        "baseAsset": "ETH",
                        "contractType": "PERPETUAL",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                    }
                ]
            }
        if url == BINANCE_TICKERS_URL:
            return [{"symbol": "ETHUSDT", "quoteVolume": "10000000"}]
        raise AssertionError(f"Unexpected GET: {url}")

    def post_json(self, url: str, payload: dict[str, str]):
        self.post_calls.append((url, payload))
        if url == HYPERLIQUID_INFO_URL:
            return [
                {"universe": [{"name": "ETH"}]},
                [{"openInterest": "5", "markPx": "3000"}],
            ]
        raise AssertionError(f"Unexpected POST: {url}")


class FetcherTests(unittest.TestCase):
    def test_fetches_binance_universe_from_the_two_public_market_endpoints(self) -> None:
        client = FakeHttpClient()

        universe = fetch_binance_universe(client)

        self.assertEqual(tuple(universe), ("ETH",))
        self.assertEqual(
            client.get_calls,
            [(BINANCE_EXCHANGE_INFO_URL, None), (BINANCE_TICKERS_URL, None)],
        )

    def test_fetches_hyperliquid_asset_contexts_with_meta_request(self) -> None:
        client = FakeHttpClient()

        result = fetch_hyperliquid_open_interest(client, {"ETH"})

        self.assertEqual(result[0].oi_usd, 15_000)
        self.assertEqual(client.post_calls, [(HYPERLIQUID_INFO_URL, {"type": "metaAndAssetCtxs"})])


if __name__ == "__main__":
    unittest.main()
