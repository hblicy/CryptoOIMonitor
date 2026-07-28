from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class DataSourceRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_payload = response_payload


SENSITIVE_QUERY_FIELDS = {
    "access_token",
    "api_key",
    "apikey",
    "key",
    "secret",
    "signature",
    "token",
}


def _safe_request_url(url: str) -> str:
    parsed = urlsplit(url)
    query = urlencode(
        [
            (name, "REDACTED" if name.lower() in SENSITIVE_QUERY_FIELDS else value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _redact_query_values(message: str, url: str) -> str:
    for name, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if name.lower() in SENSITIVE_QUERY_FIELDS and value:
            message = message.replace(value, "REDACTED")
    return message


class HttpJsonClient:
    def __init__(
        self,
        default_headers: dict[str, str] | None = None,
        timeout_seconds: float = 20,
    ) -> None:
        self.default_headers = {
            "Accept": "application/json",
            "User-Agent": "CryptoOIMonitor/1.0",
            **(default_headers or {}),
        }
        self.timeout_seconds = timeout_seconds

    def get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        request_url = url if not params else f"{url}?{urlencode(params)}"
        return self._request_json(Request(request_url, headers=self.default_headers))

    def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={**self.default_headers, "Content-Type": "application/json"},
            method="POST",
        )
        return self._request_json(request)

    def _request_json(self, request: Request) -> Any:
        safe_url = _safe_request_url(request.full_url)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            response_text = error.read().decode("utf-8", errors="replace")
            try:
                response_payload = json.loads(response_text)
            except json.JSONDecodeError:
                response_payload = None
            message = f"{safe_url} returned HTTP {error.code}"
            if isinstance(response_payload, dict):
                status = response_payload.get("status")
                if isinstance(status, dict) and status.get("error_message"):
                    message = f"{message}: {status['error_message']}"
            raise DataSourceRequestError(
                _redact_query_values(message, request.full_url),
                status_code=error.code,
                response_payload=response_payload,
            ) from error
        except URLError as error:
            raise DataSourceRequestError(
                _redact_query_values(
                    f"{safe_url} request failed: {error.reason}", request.full_url
                )
            ) from error
