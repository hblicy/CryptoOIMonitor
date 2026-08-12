import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TradeConditionPanel } from "./App";

describe("TradeConditionPanel", () => {
  it("shows active risk results while new entries are paused", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel
        complete={false}
        scans={[{
          status: "exit_long",
          canonical_symbol: "PEPE",
          candle_close_time: 1_722_269_700_000,
          rsi: 40,
          close: 97,
          ema200: 100,
          oi_to_market_cap: null,
          reasons: ["atr_stop_loss"],
        }]}
      />,
    );

    expect(html).toContain("\u5df2\u6682\u505c\u65b0\u5f00\u4ed3");
    expect(html).toContain("\u5df2\u6709\u4ea4\u6613\u72b6\u6001\u7684 15m \u98ce\u63a7\u4ecd\u5728\u6267\u884c");
    expect(html).toContain("PEPE");
    expect(html).toContain("trade-signal-exit_long");
  });

  it("shows only one incomplete-source notice when no scans are available", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={false} scans={[]} />,
    );

    expect(html.match(/\u6570\u636e\u6e90\u4e0d\u5b8c\u6574/g)).toHaveLength(1);
  });
});
