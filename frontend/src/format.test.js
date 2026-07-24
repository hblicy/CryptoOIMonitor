import { describe, expect, it } from "vitest";

import { formatRatio, formatUsd, riskLabel } from "./format";

describe("dashboard formatting", () => {
  it("formats large USD values compactly", () => {
    expect(formatUsd(1_250_000_000)).toBe("$1.25B");
  });

  it("formats ratio and strict risk labels", () => {
    expect(formatRatio(2.5)).toBe("250.00%");
    expect(riskLabel("high_risk")).toBe("高危（>200%）");
  });
});
