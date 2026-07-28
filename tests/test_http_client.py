import io
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from crypto_oi_monitor.http_client import DataSourceRequestError, HttpJsonClient


class HttpJsonClientTests(unittest.TestCase):
    def test_uses_configured_request_timeout(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"

        with patch(
            "crypto_oi_monitor.http_client.urlopen", return_value=response
        ) as urlopen:
            HttpJsonClient(timeout_seconds=12).get_json("https://example.com/data")

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 12)

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

    def test_redacts_wecom_webhook_key_from_request_errors(self) -> None:
        webhook_url = (
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-webhook-key"
        )
        error = HTTPError(
            webhook_url,
            500,
            "Server Error",
            None,
            io.BytesIO(b"{}"),
        )

        with patch("crypto_oi_monitor.http_client.urlopen", side_effect=error):
            with self.assertRaises(DataSourceRequestError) as raised:
                HttpJsonClient().post_json(webhook_url, {"msgtype": "text"})

        self.assertNotIn("secret-webhook-key", str(raised.exception))
        self.assertIn("key=REDACTED", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
