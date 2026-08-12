# Risk-First Trade Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved 90% OI/market-cap entry threshold, EMA200 slope, RSI below 60, volume breakout, price-adjusted OI growth, resume-long signal, trailing profit protection, and ATR-adaptive cooldown.

**Architecture:** Extend the pure indicator functions in `trading.py`, then let `trade_dispatch.py` own the persisted signal state machine. Store entry ATR, the highest closed price, and the last processed closed-candle time in SQLite so trailing protection survives restarts and catches up missed candles; expose the new audit fields through the existing API model and render them with the existing dashboard components.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, React/Vite, Vitest.

---

### Task 1: Entry indicator filters

**Files:**
- Modify: `tests/test_trading.py`
- Modify: `src/crypto_oi_monitor/trading.py`

- [ ] **Step 1: Write failing indicator tests**

Add tests whose valid fixture uses `rsi=59`, an EMA200 value greater than the value five bars earlier, and volume greater than both the previous candle and `1.2 * average_quote_volume`. Assert the exact blockers:

```python
self.assertEqual(entry_reasons(replace(valid, rsi=60)), ("rsi_not_below_60",))
self.assertEqual(entry_reasons(replace(valid, ema200=100)), ("ema200_not_rising",))
self.assertEqual(
    entry_reasons(replace(valid, quote_volume=120, average_quote_volume=100)),
    ("quote_volume_not_above_average",),
)
```

Add candle-level tests that assert `ema200_slope_reference` is the EMA value five closed candles before the current candle and `average_quote_volume` excludes the current candle.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_trading -v
```

Expected: failures because the new fields and blockers do not exist.

- [ ] **Step 3: Implement the indicator fields and entry rules**

In `trading.py` define:

```python
EMA_SLOPE_LOOKBACK = 5
VOLUME_AVERAGE_PERIOD = 20
VOLUME_BREAKOUT_MULTIPLIER = 1.2
ENTRY_RSI_MAX = 60
```

Add `ema200_slope_reference` and `average_quote_volume` to `TradeIndicators` and `TradeSetup`. Remove the RSI minimum blocker. Entry must use strict comparisons:

```python
if indicators.ema200 <= indicators.ema200_slope_reference:
    reasons.append("ema200_not_rising")
if indicators.quote_volume <= indicators.previous_quote_volume:
    reasons.append("quote_volume_not_increasing")
if indicators.quote_volume <= VOLUME_BREAKOUT_MULTIPLIER * indicators.average_quote_volume:
    reasons.append("quote_volume_not_above_average")
if indicators.rsi >= ENTRY_RSI_MAX:
    reasons.append("rsi_not_below_60")
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run `python -m unittest tests.test_trading -v` and expect all tests in the module to pass.

### Task 2: Threshold and price-adjusted OI confirmation

**Files:**
- Modify: `tests/test_trade_dispatch.py`
- Modify: `tests/test_domain.py`
- Modify: `src/crypto_oi_monitor/domain.py`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`

- [ ] **Step 1: Write failing threshold and OI tests**

Add tests proving `0.90` is rejected and a value above `0.90` is considered. Add a regression test where raw OI USD rises only in proportion to price and must produce `aggregate_oi_not_increasing_after_price_adjustment`:

```python
comparison = {"canonical_symbol": "PEPE", "total_oi_usd": 110, "oi_to_market_cap": 0.91}
reference_oi = {"PEPE": 100}
indicators = replace(valid_indicators, previous_close=100, close=110)
self.assertEqual(
    _aggregate_oi_entry_reasons(comparison, reference_oi, indicators),
    ("aggregate_oi_not_increasing_after_price_adjustment",),
)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run `python -m unittest tests.test_domain tests.test_trade_dispatch -v`; expect failures on the old 100% threshold and old raw OI comparison.

- [ ] **Step 3: Implement the 90% threshold and adjusted comparison**

Set:

```python
TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO = 0.9
```

Change `_aggregate_oi_entry_reasons` to receive `TradeIndicators` and require:

```python
current_units = float(comparison["total_oi_usd"]) / indicators.close
previous_units = previous_oi / indicators.previous_close
if current_units <= previous_units:
    return ("aggregate_oi_not_increasing_after_price_adjustment",)
```

Keep missing-history failure explicit and do not fall back to raw OI.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run `python -m unittest tests.test_domain tests.test_trade_dispatch -v` and expect both modules to pass.

### Task 3: Trailing protection and adaptive cooldown

**Files:**
- Modify: `tests/test_trading.py`
- Modify: `tests/test_trade_dispatch.py`
- Modify: `src/crypto_oi_monitor/trading.py`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`

- [ ] **Step 1: Write failing pure-function tests**

Add tests for a helper that returns the next highest close and active protection price:

```python
self.assertEqual(update_trailing_stop(100, 2, 96, 100, 105), (105, 96))
self.assertEqual(update_trailing_stop(100, 2, 96, 105, 106), (106, 102))
self.assertEqual(update_trailing_stop(100, 2, 103, 106, 105), (106, 103))
```

Add dynamic cooldown tests for volatility ratios at `0.8`, between bands, and above `1.2`, expecting 3, 4, and 6 candles.

- [ ] **Step 2: Run focused tests and verify RED**

Run `python -m unittest tests.test_trading tests.test_trade_dispatch -v`; expect missing helper/state failures.

- [ ] **Step 3: Implement trailing and cooldown calculations**

Add pure functions to `trading.py`:

```python
def update_trailing_stop(entry_price, entry_atr, stop_loss, highest_close, close):
    next_high = max(highest_close, close)
    if next_high < entry_price + 3 * entry_atr:
        return next_high, stop_loss
    return next_high, max(stop_loss, entry_price + entry_atr, next_high - 2 * entry_atr)
```

Compute the current normalized ATR and the median of the preceding 96 normalized ATR values. Return 3, 4, or 6 candles using the approved inclusive boundaries. Extend `TradeSignalState` with `entry_atr`, `highest_close`, and `last_processed_candle_close_time`; replay every fetched closed candle after the stored time before evaluating active-stop exits. Emit `trailing_take_profit` when the active protection price is above entry.

- [ ] **Step 4: Verify state-machine behavior**

Test that the protection price never falls, repeated refreshes are idempotent, closed candles missed during service downtime are replayed, incomplete replay history fails explicitly, a trailing exit enters the computed cooldown, and the cooldown end still requires a full new entry signal.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run `python -m unittest tests.test_trading tests.test_trade_dispatch -v` and expect all tests to pass.

### Task 4: Persist and migrate trailing state

**Files:**
- Modify: `tests/test_storage.py`
- Modify: `src/crypto_oi_monitor/storage.py`

- [ ] **Step 1: Write failing persistence tests**

Add a round-trip assertion:

```python
state = TradeSignalState("long", 100, 96, None, 2, 106, 1234)
store.set_trade_signal_state("ETH", state)
self.assertEqual(store.get_trade_signal_state("ETH"), state)
```

Create a legacy table without `entry_atr`, `highest_close`, and `last_processed_candle_close_time`, insert a valid old long state, initialize `SnapshotStore`, and assert migration derives `entry_atr == 2`, initializes `highest_close == 100`, and leaves the unknown last-processed time null. Add an invalid legacy stop test that clears the state and logs a warning.

- [ ] **Step 2: Run storage tests and verify RED**

Run `python -m unittest tests.test_storage -v`; expect schema and dataclass failures.

- [ ] **Step 3: Implement additive SQLite migration**

Add nullable REAL columns `entry_atr` and `highest_close`, plus nullable INTEGER `last_processed_candle_close_time`. Include them in SELECT, INSERT, and conflict UPDATE statements. For old active states derive `(entry_price - stop_loss) / 2`; reject non-positive values, initialize `highest_close` from entry price, leave the unknowable last-processed time null, persist the migrated values, and log the migration without sensitive data.

- [ ] **Step 4: Run storage tests and verify GREEN**

Run `python -m unittest tests.test_storage -v` and expect all tests to pass.

### Task 5: Resume-long notification and audit output

**Files:**
- Modify: `tests/test_trade_dispatch.py`
- Modify: `tests/test_notifier.py`
- Modify: `tests/test_app.py`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `src/crypto_oi_monitor/notifier.py`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/format.test.js`

- [ ] **Step 1: Write failing notification and API tests**

Assert that a `no_add` state followed by a new complete setup emits one `resume_long` event, sends the “恢复做多” title and warning, and becomes `long`. Assert that the event/scan JSON contains `ema200_slope_reference`, `average_quote_volume`, adjusted OI values, active protection and dynamic cooldown where applicable.

- [ ] **Step 2: Run backend tests and verify RED**

Run `python -m unittest tests.test_notifier tests.test_trade_dispatch tests.test_app -v`; expect the new event and fields to be absent.

- [ ] **Step 3: Implement notifier and API fields**

Define `RESUME_LONG = "resume_long"`. Pass an explicit resume flag/event to the notifier rather than inferring it from text. Add Chinese reason mappings:

```python
"ema200_not_rising": "EMA200 未向上倾斜"
"quote_volume_not_above_average": "15m 成交额未突破前20根均量的1.2倍"
"aggregate_oi_not_increasing_after_price_adjustment": "价格校正后的聚合 OI 未增加"
"rsi_not_below_60": "RSI(14) 未低于 60"
"trailing_take_profit": "已触及移动止盈保护价"
```

Include the dynamic cooldown candle count in must-exit messages.

- [ ] **Step 4: Update frontend labels and tests**

Add `resume_long` and all new reason labels to `App.jsx`; update the scan heading from 100%/35-50 to 90%/RSI below 60 and display the slope reference and 20-candle average in the compact card note. Keep the main table’s 110% and 200% labels unchanged.

- [ ] **Step 5: Verify backend and frontend GREEN**

Run:

```powershell
python -m unittest tests.test_notifier tests.test_trade_dispatch tests.test_app -v
npm test -- --run
npm run build
```

Run the npm commands from `frontend`; expect zero failures and a successful Vite build.

### Task 6: Documentation and full verification

**Files:**
- Modify: `README.md`
- Verify: `docs/superpowers/specs/2026-08-12-risk-first-trade-signal-design.md`

- [ ] **Step 1: Update operating documentation**

Replace the README trading rules with the exact 90% threshold, EMA200 five-candle slope, RSI below 60, price-adjusted OI, 20-candle volume breakout, resume-long semantics, trailing protection formula, and 3/4/6 candle cooldown. Preserve the warning that signals do not place exchange stop orders.

- [ ] **Step 2: Run the complete backend suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass with zero failures/errors.

- [ ] **Step 3: Run syntax and frontend verification**

Run:

```powershell
python -m py_compile app.py src/crypto_oi_monitor/*.py
npm test -- --run
npm run build
```

Expected: Python compilation, Vitest, and Vite build all exit 0.

- [ ] **Step 4: Inspect the final diff**

Run `git diff --check`, `git status --short`, and `git diff --stat`. Confirm only the intended strategy, tests, frontend, README, specification, and plan files changed; preserve unrelated untracked files.

- [ ] **Step 5: Commit the implementation**

Stage only the intended files and commit with the Chinese message:

```text
优化交易信号风控与移动止盈
```

Do not push unless the user explicitly asks.
