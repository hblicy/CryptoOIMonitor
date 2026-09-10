import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TradeConditionPanel } from "./App";

describe("TradeConditionPanel", () => {
  it("describes the same-bar breakout and 2x volume rules without an OI cap", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={true} scans={[]} />,
    );

    expect(html).toContain("扫描 OI / 市值 &gt; 90% 的标的");
    expect(html).not.toContain("开多仅限不超过 200%");
    expect(html).toContain("当根上穿 EMA200");
    expect(html).toContain("前20根均量的2倍");
    expect(html).toContain("RSI 回升");
    expect(html).not.toContain("OI / 市值 &gt; 90% 且不超过 200%");
    expect(html).not.toContain("RSI &lt; 60");
    expect(html).not.toContain("最近3根内上穿 EMA200");
    expect(html).not.toContain("前20根均量的1.1倍");
  });

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

  it("shows unavailable legacy-scan indicators as dashes instead of zeroes", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel
        complete={true}
        scans={[{
          status: "stop_long",
          canonical_symbol: "PEPE",
          candle_close_time: null,
          rsi: null,
          close: null,
          ema200: null,
          oi_to_market_cap: 2.01,
          reasons: ["oi_to_market_cap_in_ambush_zone"],
        }]}
      />,
    );

    expect(html).toContain("<dt>RSI(14)</dt><dd>—</dd>");
    expect(html).toContain("<dt>收盘价</dt><dd>—</dd>");
    expect(html).toContain("<dt>EMA200</dt><dd>—</dd>");
    expect(html).not.toContain("<dt>RSI(14)</dt><dd>0.00</dd>");
  });

  it("shows only one incomplete-source notice when no scans are available", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={false} scans={[]} />,
    );

    expect(html.match(/\u6570\u636e\u6e90\u4e0d\u5b8c\u6574/g)).toHaveLength(1);
  });
});
