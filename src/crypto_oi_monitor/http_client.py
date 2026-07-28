from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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


class HttpJsonClient:
    def __init__(self, default_headers: dict[str, str] | None = None) -> None:
        self.default_headers = {
            "Accept": "application/json",
            "User-Agent": "CryptoOIMonitor/1.0",
            **(default_headers or {}),
        }

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
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            response_text = error.read().decode("utf-8", errors="replace")
            try:
                response_payload = json.loads(response_text)
            except json.JSONDecodeError:
                response_payload = None
            message = f"{request.full_url} returned HTTP {error.code}"
            if isinstance(response_payload, dict):
                status = response_payload.get("status")
                if isinstance(status, dict) and status.get("error_message"):
                    message = f"{message}: {status['error_message']}"
            raise DataSourceRequestError(
                message,
                status_code=error.code,
                response_payload=response_payload,
            ) from error
        except URLError as error:
            raise DataSourceRequestError(
                f"{request.full_url} request failed: {error.reason}"
            ) from error
