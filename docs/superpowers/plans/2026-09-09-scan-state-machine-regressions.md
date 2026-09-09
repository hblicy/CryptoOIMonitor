# Scan State Machine Regression Fixes Implementation Plan

> **For agentic workers:** Execute inline with test-driven development. Do not create commits unless the user requests one.

**Goal:** Make scan-only and dispatch-fallback decisions preserve the same cooldown, stop, exit, and Binance-symbol safety invariants as normal signal dispatch.

**Architecture:** Keep `TradeConditionScanResult.state_updates` as the application boundary. Normalize the persisted Binance symbol before replay, return only safe state progress, and mirror normal dispatch state transitions inside `_scan_active_trade_condition`. Do not add a new database status or alter notifier behavior.

**Tech Stack:** Python 3.12, frozen dataclasses, `unittest`, SQLite application state.

---

### Task 1: Lock the five regressions with tests

**Files:**
- Modify: `tests/test_trade_dispatch.py`
- Modify: `tests/test_app.py`

- [x] Add a two-refresh test proving an unnotified historical `EXIT_LONG` remains `EXIT_LONG` and does not persist a checkpoint past the trigger candle.
- [x] Add a scan-only cooldown test expecting `STOP_LONG` with `reentry_cooldown_active` before the deadline.
- [x] Add a complete-snapshot stop test expecting a `LONG` state update to `NO_ADD`.
- [x] Add a no-position EMA-exit test expecting `STOP_LONG`, never `EXIT_LONG`.
- [x] Add a migrated-state test expecting the current Binance contract symbol in `state_updates`.
- [x] Run the five tests and confirm each fails for the reviewed behavior.

### Task 2: Align scan-only state progression

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `app.py`

- [x] Include `REENTRY_COOLDOWN` in the state maps passed from `app.py` and retained by `scan_trade_conditions`.
- [x] Normalize `state.binance_symbol` from the selected comparison before replay.
- [x] For forced exits, return the normalized pre-replay state so the trigger candle remains replayable until normal dispatch confirms the exit.
- [x] Implement cooldown deadline and post-cooldown entry checks using the existing dispatch rules.
- [x] On complete-snapshot OI/data/EMA stop conditions, return `replace(state, status=NO_ADD)` for `LONG`.
- [x] For candidates without state, evaluate only entry blockers and return `CAN_LONG` or `STOP_LONG`.
- [x] Run the targeted tests and the trade-dispatch module tests.
- [x] Persist a migrated cooldown state's current Binance symbol before the normal dispatcher returns early for an active cooldown.

### Task 3: Document and verify

**Files:**
- Modify: `docs/2026-09-07-entry-rule-tightening.md`

- [x] Update the implementation notes and verified test counts.
- [x] Run backend discovery, focused trading/dispatch/notifier tests, Python compilation, frontend Vitest, and the Vite production build.
- [x] Review the final changed paths without committing or altering unrelated files.

### Task 4: Preserve Binance symbol across a K-line failure

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `tests/test_trade_dispatch.py`
- Modify: `docs/2026-09-07-entry-rule-tightening.md`

- [x] Add a normal-dispatch regression test proving a migrated active state saves the current Binance symbol even when the K-line request fails, then uses it for risk checks on the next incomplete refresh.
- [x] Add the equivalent scan-only regression test using `state_updates` across two refreshes.
- [x] Normalize active-state Binance symbols before loading K-lines and preserve the safe mapping update on failure paths.
- [x] Run targeted tests, backend discovery, Python compilation, frontend Vitest, and the production build.

### Task 5: Preserve Binance symbol across replay failures

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `tests/test_trade_dispatch.py`
- Modify: `docs/2026-09-07-entry-rule-tightening.md`

- [x] Add dispatch and scan-only cross-refresh tests proving a replay-gap failure preserves only the current Binance symbol and permits risk checks after the comparison disappears.
- [x] Add a test proving an incomplete snapshot retains and backfills a `reentry_cooldown` state.
- [x] Persist the normalized pre-replay state from dispatch and scan exception paths without advancing trailing fields or checkpoints.
- [x] Include `REENTRY_COOLDOWN` in incomplete-snapshot and missing-comparison candidate selection.
- [x] Run targeted tests, backend discovery, Python compilation, frontend Vitest, and the production build.

### Task 6: Preserve conservative stop state when K-lines are unavailable

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `tests/test_trade_dispatch.py`
- Modify: `docs/2026-09-07-entry-rule-tightening.md`

- [x] Add dispatch and scan-only tests for an active `long` whose OI ratio is outside the entry range while the Binance contract or K-line request is unavailable.
- [x] Add dispatch and scan-only tests for a complete snapshot that no longer contains the active symbol while its K-line request also fails.
- [x] Centralize the stop reasons that can be determined without K-lines and apply `long → no_add` in both early-return paths while retaining the data-source failure.
- [x] Run targeted tests, backend discovery, Python compilation, frontend Vitest, and the production build.

### Task 7: Preserve conservative stop state across missing mappings and replay gaps

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `tests/test_trade_dispatch.py`
- Modify: `docs/2026-09-07-entry-rule-tightening.md`

- [x] Add a dispatch test for an active `long` missing both its current comparison and persisted Binance symbol.
- [x] Add dispatch and scan-only tests for blocked OI during a trailing replay gap.
- [x] Keep missing-symbol active states in the dispatch candidate flow without issuing a duplicate K-line failure.
- [x] Apply only `long → no_add` on replay errors when an OI/data stop reason is independently available, without advancing trailing fields or checkpoints.
- [x] Run targeted tests, backend discovery, Python compilation, frontend Vitest, and the production build.

### Task 8: Preserve a newly discovered Binance mapping when exit notification fails

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `tests/test_trade_dispatch.py`
- Modify: `docs/2026-09-07-entry-rule-tightening.md`

- [x] Add a two-refresh regression test where a migrated active state discovers its Binance symbol, hits a forced exit, and the first exit notification fails.
- [x] Persist only the normalized pre-replay state before attempting the exit notification, leaving trailing fields and the trigger checkpoint replayable.
- [x] Run targeted tests, backend discovery, Python compilation, frontend Vitest, and the production build.
