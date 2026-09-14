# Restore Three-Candle EMA200 Breakout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 允许最近 3 根已收盘 15m K 线内发生的 EMA200 向上突破，在当前仍位于 EMA200 上方且其余确认条件当根满足时触发做多信号。

**Architecture:** 保留现有 `TradeIndicators.ema200_breakout_candles_ago` 数据结构和判断链，只把突破回看窗口从 1 恢复为 3。同步后端推送与前端条件说明，避免运行规则和展示文案不一致。

**Tech Stack:** Python 3.12、`unittest`、React 18、Vitest

---

### Task 1: Add regression coverage

**Files:**
- Modify: `tests/test_trading.py`
- Modify: `tests/test_notifier.py`
- Modify: `frontend/src/sources.test.js`
- Modify: `frontend/src/trade-condition-panel.test.jsx`

- [x] **Step 1: Change the direct entry test to accept a prior-candle breakout**

```python
def test_accepts_ema200_breakout_from_the_previous_closed_candle(self) -> None:
    indicators = self._valid_indicators(ema200_breakout_candles_ago=1)
    self.assertEqual(entry_reasons(indicators), ())
```

- [x] **Step 2: Change the candle-history regression to accept a breakout from two candles ago**

```python
signal = evaluate_trade_setup(candles)
self.assertIsNotNone(signal)
self.assertEqual(trade_indicators(candles).ema200_breakout_candles_ago, 2)
```

- [x] **Step 3: Update notifier and frontend assertions to require “最近3根” wording**

- [x] **Step 4: Run the focused tests and verify they fail for the intended old behavior**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_trading tests.test_notifier -v
npm test -- --run src/sources.test.js src/trade-condition-panel.test.jsx
```

Expected: failures show the previous/two-candle breakout is rejected and UI/notifier still say “当根”.

### Task 2: Restore the three-candle rule and align wording

**Files:**
- Modify: `src/crypto_oi_monitor/trading.py`
- Modify: `src/crypto_oi_monitor/notifier.py`
- Modify: `frontend/src/App.jsx`
- Modify: `README.md`

- [x] **Step 1: Restore the breakout lookback constant**

```python
EMA_BREAKOUT_LOOKBACK = 3
```

- [x] **Step 2: Describe the rule consistently as a breakout within the latest three closed candles while the current close remains above EMA200**

- [x] **Step 3: Keep all other entry gates unchanged**

The volume multiplier remains `2.0`; EMA200 slope, RSI rise, OI/market-cap, and price-adjusted aggregate OI checks remain untouched.

- [x] **Step 4: Re-run the focused tests and verify they pass**

### Task 3: Verify the complete change

**Files:**
- Verify only: all modified files

- [x] **Step 1: Run all backend tests**

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests -v
```

Expected: 238 tests plus the renamed assertions pass with no failures.

- [x] **Step 2: Run all frontend tests and build**

```powershell
npm test
npm run build
```

Expected: 4 test files pass and Vite production build succeeds.

- [x] **Step 3: Compile the Python sources**

```powershell
$env:PYTHONPATH='src'; python -m compileall -q src app.py
```

Expected: exit code 0.

- [x] **Step 4: Inspect the final diff and confirm only the approved rule, tests, and matching wording changed**
