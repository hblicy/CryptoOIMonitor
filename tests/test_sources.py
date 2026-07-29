import unittest

from crypto_oi_monitor.sources import (
    BINGX_CONTRACTS_URL,
    BINGX_OI_BATCH_SIZE,
    BINGX_OPEN_INTEREST_URL,
    LIGHTER_ORDER_BOOK_DETAILS_URL,
    MAX_BINGX_OI_WORKERS,
    fetch_bingx_open_interest,
    fetch_lighter_open_interest,
    parse_aster_open_interest,
    parse_binance_open_interest,
    parse_binance_universe,
    parse_bingx_open_interest,
    parse_bitget_open_interest,
    parse_bybit_open_interest,
    parse_gate_open_interest,
    parse_hyperliquid_open_interest,
    parse_kucoin_open_interest,
    parse_lighter_open_interest,
    parse_mexc_open_interest,
    parse_okx_open_interest,
)


class BinanceParserTests(unittest.TestCase):
    def test_builds_universe_from_usdt_perpetual_and_ten_million_turnover(self) -> None:
        universe = parse_binance_universe(
            {
                "symbols": [
                    {
                        "symbol": "ETHUSDT",
                        "baseAsset": "ETH",
                        "contractType": "PERPETUAL",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                    },
                    {
                        "symbol": "LOWUSDT",
                        "baseAsset": "LOW",
                        "contractType": "PERPETUAL",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                    },
                    {
                        "symbol": "BTCUSDT_260925",
                        "baseAsset": "BTC",
                        "contractType": "CURRENT_QUARTER",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                    },
                ]
            },
            [
                {"symbol": "ETHUSDT", "quoteVolume": "10000000", "lastPrice": "3000"},
                {"symbol": "LOWUSDT", "quoteVolume": "9999999.99"},
                {"symbol": "BTCUSDT_260925", "quoteVolume": "999999999"},
            ],
        )

        self.assertEqual(tuple(universe), ("ETH",))
        self.assertEqual(universe["ETH"].symbol, "ETHUSDT")
        self.assertEqual(universe["ETH"].last_price, 3_000)

    def test_converts_binance_base_oi_with_mark_price(self) -> None:
        oi = parse_binance_open_interest(
            "ETH", "ETHUSDT", {"openInterest": "20"}, {"ETHUSDT": "3000"}
        )

        self.assertEqual(oi.oi_usd, 60_000)


class VenueParserTests(unittest.TestCase):
    def test_bingx_submits_a_full_batch_concurrently(self) -> None:
        self.assertEqual(MAX_BINGX_OI_WORKERS, BINGX_OI_BATCH_SIZE)

    def test_reads_okx_usd_oi_directly(self) -> None:
        result = parse_okx_open_interest(
            {"data": [{"instId": "ETH-USDT-SWAP", "oiUsd": "120000"}]}, {"ETH"}
        )
        self.assertEqual(result[0].oi_usd, 120_000)

    def test_reads_bybit_open_interest_value_directly(self) -> None:
        result = parse_bybit_open_interest(
            {
                "result": {
                    "list": [
                        {"symbol": "ETHUSDT", "openInterestValue": "140000"},
                        {"symbol": "XRPUSDT", "openInterestValue": "20"},
                    ]
                }
            },
            {"ETH"},
        )
        self.assertEqual(result[0].oi_usd, 140_000)

    def test_calculates_bitget_oi_from_base_open_interest_and_mark_price(self) -> None:
        result = parse_bitget_open_interest(
            {"data": {"list": [{"symbol": "ETHUSDT", "openInterest": "5"}]}},
            {"data": [{"symbol": "ETHUSDT", "markPrice": "3000"}]},
            {
                "data": [
                    {"symbol": "ETHUSDT", "baseCoin": "ETH", "sizeMultiplier": "0.1"}
                ]
            },
            {"ETH"},
        )
        self.assertEqual(result[0].oi_usd, 15_000)

    def test_ignores_bitget_open_interest_without_an_active_contract_definition(self) -> None:
        result = parse_bitget_open_interest(
            {"data": {"list": [{"symbol": "PLAYUSDT", "openInterest": "5"}]}},
            {"data": []},
            {"data": []},
            {"PLAY"},
        )
        self.assertEqual(result, [])

    def test_calculates_gate_oi_from_contract_multiplier_and_mark_price(self) -> None:
        result = parse_gate_open_interest(
            [
                {
                    "contract": "ETH_USDT",
                    "total_size": "5",
                    "mark_price": "3000",
                }
            ],
            [{"name": "ETH_USDT", "quanto_multiplier": "0.1"}],
            {"ETH"},
        )
        self.assertEqual(result[0].oi_usd, 1500)

    def test_calculates_hyperliquid_oi_from_size_and_mark_price(self) -> None:
        result = parse_hyperliquid_open_interest(
            [
                {"universe": [{"name": "ETH"}]},
                [{"openInterest": "5", "markPx": "3000"}],
            ],
            {"ETH"},
        )
        self.assertEqual(result[0].oi_usd, 15_000)

    def test_calculates_aster_base_oi_with_mark_price(self) -> None:
        oi = parse_aster_open_interest(
            "ETH", "ETHUSDT", {"openInterest": "20"}, {"ETHUSDT": "3000"}
        )
        self.assertEqual(oi.oi_usd, 60_000)

    def test_reads_bingx_usdt_open_interest(self) -> None:
        result = parse_bingx_open_interest(
            {
                "data": [
                    {"symbol": "ETH-USDT", "asset": "ETH", "currency": "USDT", "status": 1},
                    {"symbol": "ETH-USD", "asset": "ETH", "currency": "USD", "status": 1},
                ]
            },
            {"ETH-USDT": {"openInterest": "150000"}},
            {"ETH"},
        )

        self.assertEqual(result[0].venue, "BingX")
        self.assertEqual(result[0].canonical_symbol, "ETH")
        self.assertEqual(result[0].oi_usd, 150_000)
        self.assertEqual(len(result), 1)

    def test_calculates_lighter_oi_from_base_size_and_mark_price(self) -> None:
        result = parse_lighter_open_interest(
            {
                "order_book_details": [
                    {
                        "symbol": "1000PEPE",
                        "market_type": "perp",
                        "status": "active",
                        "open_interest": "5000",
                        "mark_price": "0.01",
                    },
                    {
                        "symbol": "PEPE/USDC",
                        "market_type": "spot",
                        "status": "active",
                        "open_interest": "1000",
                        "mark_price": "0.01",
                    },
                ]
            },
            {"PEPE"},
        )

        self.assertEqual(result[0].venue, "Lighter")
        self.assertEqual(result[0].canonical_symbol, "PEPE")
        self.assertEqual(result[0].oi_usd, 50)
        self.assertEqual(len(result), 1)

    def test_fetches_bingx_open_interest_for_selected_usdt_contracts(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def get_json(self, url, params=None):
                self.calls.append((url, params))
                if url == BINGX_CONTRACTS_URL:
                    return {
                        "data": [
                            {
                                "symbol": "ETH-USDT",
                                "asset": "ETH",
                                "currency": "USDT",
                                "status": 1,
                            }
                        ]
                    }
                return {"data": {"symbol": "ETH-USDT", "openInterest": "150000"}}

        client = FakeClient()
        result = fetch_bingx_open_interest(client, {"ETH"})

        self.assertEqual(result[0].oi_usd, 150_000)
        self.assertEqual(
            client.calls,
            [
                (BINGX_CONTRACTS_URL, None),
                (BINGX_OPEN_INTEREST_URL, {"symbol": "ETH-USDT"}),
            ],
        )

    def test_fetches_lighter_market_details_once(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def get_json(self, url, params=None):
                self.calls.append((url, params))
                return {
                    "order_book_details": [
                        {
                            "symbol": "ETH",
                            "market_type": "perp",
                            "status": "active",
                            "open_interest": "5",
                            "mark_price": "3000",
                        }
                    ]
                }

        client = FakeClient()
        result = fetch_lighter_open_interest(client, {"ETH"})

        self.assertEqual(result[0].oi_usd, 15_000)
        self.assertEqual(client.calls, [(LIGHTER_ORDER_BOOK_DETAILS_URL, None)])

    def test_calculates_kucoin_usdt_linear_oi_and_maps_xbt_to_btc(self) -> None:
        result = parse_kucoin_open_interest(
            {
                "data": [
                    {
                        "symbol": "XBTUSDTM",
                        "baseCurrency": "XBT",
                        "quoteCurrency": "USDT",
                        "settleCurrency": "USDT",
                        "status": "Open",
                        "isInverse": False,
                        "openInterest": "20",
                        "multiplier": "0.001",
                        "markPrice": "60000",
                    },
                    {
                        "symbol": "BTCUSDCM",
                        "baseCurrency": "XBT",
                        "quoteCurrency": "USDC",
                        "settleCurrency": "USDC",
                        "status": "Open",
                        "isInverse": False,
                        "openInterest": "20",
                        "multiplier": "0.001",
                        "markPrice": "60000",
                    },
                ]
            },
            {"BTC"},
        )

        self.assertEqual(result[0].venue, "KuCoin")
        self.assertEqual(result[0].canonical_symbol, "BTC")
        self.assertEqual(result[0].oi_usd, 1_200)
        self.assertEqual(len(result), 1)

    def test_calculates_mexc_usdt_linear_oi_from_contract_size_and_fair_price(self) -> None:
        result = parse_mexc_open_interest(
            {
                "data": [
                    {
                        "symbol": "ETH_USDT",
                        "baseCoin": "ETH",
                        "quoteCoin": "USDT",
                        "settleCoin": "USDT",
                        "contractSize": "0.1",
                        "state": 0,
                    },
                    {
                        "symbol": "ETH_USDT_OLD",
                        "baseCoin": "ETH",
                        "quoteCoin": "USDT",
                        "settleCoin": "USDT",
                        "contractSize": "0.1",
                        "state": 3,
                    },
                ]
            },
            {"data": [{"symbol": "ETH_USDT", "holdVol": "5", "fairPrice": "3000"}]},
            {"ETH"},
        )

        self.assertEqual(result[0].venue, "MEXC")
        self.assertEqual(result[0].oi_usd, 1_500)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
