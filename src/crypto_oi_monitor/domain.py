from __future__ import annotations

from dataclasses import dataclass
import math


BINANCE_MIN_TURNOVER_USD = 5_000_000
FOCUS_OI_TO_MARKET_CAP_RATIO = 1.1
TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO = 0.9
AMBUSH_OI_TO_MARKET_CAP_RATIO = 2
NORMAL = "normal"
WARNING = "warning"
HIGH_RISK = "high_risk"


@dataclass(frozen=True)
class ContractOpenInterest:
    venue: str
    symbol: str
    oi_usd: float
    canonical_symbol: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.oi_usd) or self.oi_usd < 0:
            raise ValueError("Contract OI must be finite non-negative")


@dataclass(frozen=True)
class AssetComparison:
    canonical_symbol: str
    market_cap_id: str
    market_cap_usd: float
    total_oi_usd: float
    oi_to_market_cap: float
    status: str
    covered_venues: tuple[str, ...]
    contracts: tuple[ContractOpenInterest, ...]


def risk_status(oi_to_market_cap: float) -> str:
    if oi_to_market_cap > AMBUSH_OI_TO_MARKET_CAP_RATIO:
        return HIGH_RISK
    if oi_to_market_cap > FOCUS_OI_TO_MARKET_CAP_RATIO:
        return WARNING
    return NORMAL


def is_binance_universe_member(contract_type: str, quote_volume_usd: float) -> bool:
    return contract_type == "PERPETUAL" and quote_volume_usd >= BINANCE_MIN_TURNOVER_USD


def aggregate_asset(
    canonical_symbol: str,
    market_cap_id: str,
    market_cap_usd: float,
    contracts: tuple[ContractOpenInterest, ...],
) -> AssetComparison:
    total_oi_usd = sum(contract.oi_usd for contract in contracts)
    if not math.isfinite(total_oi_usd):
        raise ValueError("Aggregate OI must be finite")
    if not math.isfinite(market_cap_usd) or market_cap_usd <= 0:
        raise ValueError("Market cap must be finite positive")
    oi_to_market_cap = total_oi_usd / market_cap_usd
    if not math.isfinite(oi_to_market_cap):
        raise ValueError("OI to market cap ratio must be finite")
    return AssetComparison(
        canonical_symbol=canonical_symbol,
        market_cap_id=market_cap_id,
        market_cap_usd=market_cap_usd,
        total_oi_usd=total_oi_usd,
        oi_to_market_cap=oi_to_market_cap,
        status=risk_status(oi_to_market_cap),
        covered_venues=tuple(dict.fromkeys(contract.venue for contract in contracts)),
        contracts=contracts,
    )
