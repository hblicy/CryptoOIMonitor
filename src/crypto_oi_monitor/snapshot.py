from __future__ import annotations

from dataclasses import dataclass

from .domain import ContractOpenInterest, aggregate_asset
from .market_caps import MarketCapLookup


@dataclass(frozen=True)
class SourceHealth:
    status: str
    count: int | None = None
    message: str | None = None

    @classmethod
    def ok(cls, count: int) -> "SourceHealth":
        return cls(status="ok", count=count)

    @classmethod
    def failed(cls, message: str) -> "SourceHealth":
        return cls(status="error", message=message)

    def as_dict(self) -> dict[str, int | str | None]:
        return {"status": self.status, "count": self.count, "message": self.message}


def build_snapshot(
    captured_at: str,
    selected_assets: set[str],
    market_cap_lookup: MarketCapLookup,
    contracts_by_venue: dict[str, list[ContractOpenInterest]],
    health: dict[str, SourceHealth],
) -> dict[str, object]:
    contracts_by_asset: dict[str, list[ContractOpenInterest]] = {
        asset: [] for asset in selected_assets
    }
    for contracts in contracts_by_venue.values():
        for contract in contracts:
            canonical = _canonical_from_contract(contract)
            if canonical in contracts_by_asset:
                contracts_by_asset[canonical].append(contract)

    comparisons = []
    for asset in selected_assets:
        market_cap = market_cap_lookup.market_caps.get(asset)
        if market_cap is None:
            continue
        comparison = aggregate_asset(
            asset,
            market_cap.coingecko_id,
            market_cap.market_cap_usd,
            tuple(contracts_by_asset[asset]),
        )
        comparisons.append(
            {
                "canonical_symbol": comparison.canonical_symbol,
                "coingecko_id": comparison.coingecko_id,
                "market_cap_usd": comparison.market_cap_usd,
                "total_oi_usd": comparison.total_oi_usd,
                "oi_to_market_cap": comparison.oi_to_market_cap,
                "status": comparison.status,
                "covered_venues": list(comparison.covered_venues),
                "contracts": [
                    {
                        "venue": contract.venue,
                        "symbol": contract.symbol,
                        "oi_usd": contract.oi_usd,
                        "canonical_symbol": contract.canonical_symbol,
                    }
                    for contract in comparison.contracts
                ],
            }
        )
    comparisons.sort(key=lambda comparison: comparison["oi_to_market_cap"], reverse=True)
    return {
        "captured_at": captured_at,
        "complete": all(state.status == "ok" for state in health.values()),
        "selected_asset_count": len(selected_assets),
        "comparisons": comparisons,
        "unmapped_assets": list(market_cap_lookup.unmapped_assets),
        "sources": {name: state.as_dict() for name, state in health.items()},
    }


def _canonical_from_contract(contract: ContractOpenInterest) -> str:
    if contract.canonical_symbol is not None:
        return contract.canonical_symbol
    symbol = contract.symbol.replace("-", "_")
    if "_" in symbol:
        return symbol.split("_")[0]
    return symbol.removesuffix("USDT")
