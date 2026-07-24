import unittest

from crypto_oi_monitor.sources import (
    BINANCE_EXCHANGE_INFO_URL,
    BINANCE_TICKERS_URL,
    HYPERLIQUID_INFO_URL,
    KUCOIN_CONTRACTS_URL,
    MEXC_CONTRACTS_URL,
    MEXC_TICKERS_URL,
    fetch_binance_universe,
    fetch_hyperliquid_open_interest,
    fetch_kucoin_open_interest,
    fetch_mexc_open_interest,
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


class KucoinMexcClient:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict[str, str] | None]] = []

    def get_json(self, url: str, params: dict[str, str] | None = None):
        self.get_calls.append((url, params))
        if url == KUCOIN_CONTRACTS_URL:
            return {
                "data": [
                    {
                        "symbol": "ETHUSDTM",
                        "baseCurrency": "ETH",
                        "quoteCurrency": "USDT",
                        "settleCurrency": "USDT",
                        "status": "Open",
                        "isInverse": False,
                        "openInterest": "4",
                        "multiplier": "0.1",
                        "markPrice": "3000",
                    }
                ]
            }
        if url == MEXC_CONTRACTS_URL:
            return {
                "data": [
                    {
                        "symbol": "ETH_USDT",
                        "baseCoin": "ETH",
                        "quoteCoin": "USDT",
                        "settleCoin": "USDT",
                        "contractSize": "0.1",
                        "state": 0,
                    }
                ]
            }
        if url == MEXC_TICKERS_URL:
            return {
                "data": [
                    {"symbol": "ETH_USDT", "holdVol": "5", "fairPrice": "3000"}
                ]
            }
        raise AssertionError(f"Unexpected GET: {url}")


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

    def test_fetches_kucoin_contracts_from_one_public_endpoint(self) -> None:
        client = KucoinMexcClient()

        result = fetch_kucoin_open_interest(client, {"ETH"})

        self.assertEqual(result[0].oi_usd, 1_200)
        self.assertEqual(client.get_calls, [(KUCOIN_CONTRACTS_URL, None)])

    def test_fetches_mexc_contracts_and_tickers_from_public_endpoints(self) -> None:
        client = KucoinMexcClient()

        result = fetch_mexc_open_interest(client, {"ETH"})

        self.assertEqual(result[0].oi_usd, 1_500)
        self.assertEqual(
            client.get_calls,
            [(MEXC_CONTRACTS_URL, None), (MEXC_TICKERS_URL, None)],
        )


if __name__ == "__main__":
    unittest.main()
