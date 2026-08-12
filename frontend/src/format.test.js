import { describe, expect, it } from "vitest";

import { formatRatio, formatUsd, riskLabel } from "./format";

describe("dashboard formatting", () => {
  it("formats large USD values compactly", () => {
    expect(formatUsd(1_250_000_000)).toBe("$1.25B");
  });

  it("labels elevated OI ratios as attention signals", () => {
    expect(formatRatio(2.5)).toBe("250.00%");
    expect(riskLabel("high_risk")).toBe("埋伏候选区（>200%）");
    expect(riskLabel("warning")).toBe("重点关注（>110%）");
    expect(riskLabel("normal")).toBe("常规");
  });

  it("does not render a missing OI ratio as zero", () => {
    expect(formatRatio(null)).toBe("\u2014");
  });
});
