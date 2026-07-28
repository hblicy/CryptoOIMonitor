import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from crypto_oi_monitor.http_client import DataSourceRequestError, HttpJsonClient


class HttpJsonClientTests(unittest.TestCase):
    def test_exposes_cmc_error_payload_on_http_error(self) -> None:
        error = HTTPError(
            "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map",
            400,
            "Bad Request",
            None,
            io.BytesIO(
                b'{"status":{"error_message":"Invalid values for \\\"symbol\\\": \\\"DODOX\\\""}}'
            ),
        )

        with patch("crypto_oi_monitor.http_client.urlopen", side_effect=error):
            with self.assertRaises(DataSourceRequestError) as raised:
                HttpJsonClient().get_json("https://pro-api.coinmarketcap.com/map")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.response_payload["status"]["error_message"],
            'Invalid values for "symbol": "DODOX"',
        )
        self.assertIn("Invalid values", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
