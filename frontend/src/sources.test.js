import { describe, expect, it } from "vitest";

import { SOURCE_GROUPS, TRADE_VENUES, sourceHealthSummary } from "./App";

describe("data source groups", () => {
  it("separates CEX, DEX, and market-cap sources", () => {
    expect(SOURCE_GROUPS).toEqual([
      {
        label: "CEX",
        sources: ["Binance", "OKX", "Bybit", "Bitget", "Gate", "KuCoin", "BingX", "MEXC"],
      },
      { label: "DEX", sources: ["Hyperliquid", "Aster", "Lighter"] },
      { label: "MC", sources: ["CoinMarketCap"] },
    ]);
  });

  it("uses only OI venues to calculate coverage", () => {
    expect(TRADE_VENUES).toHaveLength(11);
    expect(TRADE_VENUES).not.toContain("CoinMarketCap");
  });

  it("keeps the health denominator at all configured sources", () => {
    expect(sourceHealthSummary({ Binance: { status: "error" } })).toEqual({
      healthy: 0,
      total: 12,
    });
  });
});
