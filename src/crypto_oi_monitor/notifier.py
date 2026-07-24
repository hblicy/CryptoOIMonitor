from __future__ import annotations

from typing import Any, Protocol

from .alerts import ENTERED_HIGH_RISK, RECOVERED


class WeComHttpClient(Protocol):
    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class WeComNotifier:
    def __init__(self, webhook_url: str, client: WeComHttpClient) -> None:
        self.webhook_url = webhook_url
        self.client = client

    def send(self, event: str, comparison: dict[str, Any]) -> None:
        response = self.client.post_json(
            self.webhook_url,
            {"msgtype": "markdown", "markdown": {"content": _message(event, comparison)}},
        )
        if response["errcode"] != 0:
            raise RuntimeError(f"WeCom webhook rejected message: {response}")


def _message(event: str, comparison: dict[str, Any]) -> str:
    if event == ENTERED_HIGH_RISK:
        title = "<font color=\"warning\">【OI 高风险】</font>"
    elif event == RECOVERED:
        title = "<font color=\"info\">【OI 高风险恢复】</font>"
    else:
        raise ValueError(f"Unsupported notification event: {event}")

    venues = "\n".join(
        f"- {contract['venue']}: {contract['oi_usd']:,.2f} USD"
        for contract in comparison["contracts"]
    )
    return (
        f"{title}\n"
        f"> 币种：**{comparison['canonical_symbol']}**\n"
        f"> 聚合 OI：**{comparison['total_oi_usd']:,.2f} USD**\n"
        f"> 市值：{comparison['market_cap_usd']:,.2f} USD\n"
        f"> OI / 市值：**{comparison['oi_to_market_cap'] * 100:.2f}%**\n"
        f"> 交易所明细：\n{venues}"
    )
