import { describe, expect, it } from "vitest";

import { manualRefreshRequestOptions } from "./App";

describe("manual refresh authentication", () => {
  it("sends the operator token only in the refresh request header", () => {
    expect(manualRefreshRequestOptions("test-token")).toEqual({
      method: "POST",
      headers: { "X-Manual-Refresh-Token": "test-token" },
    });
  });
});
