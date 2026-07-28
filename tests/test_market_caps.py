import unittest

from crypto_oi_monitor.http_client import DataSourceRequestError
from crypto_oi_monitor.market_caps import (
    CMC_ID_MAP_URL,
    CMC_QUOTES_URL,
    fetch_market_caps,
    parse_market_caps,
)


class CoinMarketCapMarketCapTests(unittest.TestCase):
    def test_reads_usd_market_cap_from_v3_quote_list(self) -> None:
        result = parse_market_caps(
            {"data": [{"id": 1, "symbol": "BTC"}]},
            {
                "data": [
                    {
                        "id": 1,
                        "quote": [
                            {"symbol": "EUR", "market_cap": 900},
                            {"symbol": "USD", "market_cap": 1_000},
                        ],
                    }
                ]
            },
            {"BTC"},
        )

        self.assertEqual(result.market_caps["BTC"].market_cap_usd, 1_000)

    def test_accepts_string_zero_error_code_as_success(self) -> None:
        result = parse_market_caps(
            {
                "status": {"error_code": "0", "error_message": ""},
                "data": [{"id": 1, "symbol": "BTC"}],
            },
            {
                "status": {"error_code": "0", "error_message": ""},
                "data": [{"id": 1, "quote": {"USD": {"market_cap": 1_000}}}],
            },
            {"BTC"},
        )

        self.assertEqual(result.market_caps["BTC"].market_cap_usd, 1_000)

    def test_uses_unique_cmc_id_mapping_and_usd_market_cap(self) -> None:
        result = parse_market_caps(
            {
                "data": [
                    {"id": 1027, "symbol": "ETH"},
                    {"id": 1, "symbol": "BTC"},
                ]
            },
            {
                "data": [
                    {"id": 1027, "quote": {"USD": {"market_cap": 300_000_000_000}}},
                    {"id": 1, "quote": {"USD": {"market_cap": 2_000_000_000_000}}},
                ]
            },
            {"ETH", "BTC"},
        )

        self.assertEqual(result.market_caps["ETH"].market_cap_id, "1027")
        self.assertEqual(result.market_caps["BTC"].market_cap_usd, 2_000_000_000_000)
        self.assertEqual(result.unmapped_assets, ())

    def test_leaves_ambiguous_symbol_unmapped(self) -> None:
        result = parse_market_caps(
            {"data": [{"id": 1, "symbol": "AAA"}, {"id": 2, "symbol": "AAA"}]},
            {"data": []},
            {"AAA"},
        )

        self.assertEqual(result.market_caps, {})
        self.assertEqual(result.unmapped_assets, ("AAA",))

    def test_fetches_mapping_then_quotes_by_cmc_id(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        client = FakeClient()
        result = fetch_market_caps(client, {"ETH"})

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(
            client.calls,
            [
                (CMC_ID_MAP_URL, {"symbol": "ETH"}),
                (CMC_QUOTES_URL, {"id": "1027", "convert": "USD", "skip_invalid": "true"}),
            ],
        )

    def test_skips_only_symbols_rejected_by_cmc_map(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    if "BROCCOLIF3B" in params["symbol"]:
                        error = DataSourceRequestError("CMC map rejected symbols")
                        error.status_code = 400
                        error.response_payload = {
                            "status": {
                                "error_message": (
                                    'Invalid values for "symbol": "BROCCOLIF3B,DODOX"'
                                )
                            }
                        }
                        raise error
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        client = FakeClient()
        with self.assertLogs("crypto_oi_monitor.market_caps", "WARNING") as logs:
            result = fetch_market_caps(client, {"BROCCOLIF3B", "DODOX", "ETH"})

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(result.unmapped_assets, ("BROCCOLIF3B", "DODOX"))
        self.assertIn("BROCCOLIF3B, DODOX", logs.output[0])
        self.assertEqual(
            client.calls,
            [
                (
                    CMC_ID_MAP_URL,
                    {"symbol": "BROCCOLIF3B,DODOX,ETH"},
                ),
                (CMC_ID_MAP_URL, {"symbol": "ETH"}),
                (
                    CMC_QUOTES_URL,
                    {"id": "1027", "convert": "USD", "skip_invalid": "true"},
                ),
            ],
        )

    def test_does_not_ignore_other_cmc_map_errors(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                error = DataSourceRequestError("CMC authentication failed")
                error.status_code = 401
                error.response_payload = {
                    "status": {"error_message": "API key is invalid"}
                }
                raise error

        with self.assertRaisesRegex(DataSourceRequestError, "authentication failed"):
            fetch_market_caps(FakeClient(), {"ETH"})


if __name__ == "__main__":
    unittest.main()
