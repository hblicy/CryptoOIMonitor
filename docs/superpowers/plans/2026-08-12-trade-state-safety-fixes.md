# Trade State Safety Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep active-position risk signals operational during market-data outages and make Web/WeCom condition lists exactly reflect the persisted trade state machine.

**Architecture:** Persist the Binance contract symbol with each active trade state, enumerate persisted states independently from the current market-cap comparison set, and separate active-position risk evaluation from new-entry eligibility. Build generic entry scans only from complete data, then overlay state transitions and cooldown status after the state machine has run.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, React/Vite, Vitest.

---

### Task 1: Lock the four regressions with tests

**Files:**
- Modify: `tests/test_trade_dispatch.py`
- Modify: `tests/test_app.py`

- [ ] Add a test proving an incomplete snapshot still evaluates a persisted active state and emits `exit_long` from Binance closed K-lines.
- [ ] Add a test proving a simultaneous OI-threshold stop and ATR stop emits `exit_long`, with the K-line loader called.
- [ ] Add a test proving a failed `resume_long` notification leaves the `no_add` state unchanged.
- [ ] Add tests proving cooldown scans are `stop_long`, trailing exits are `exit_long`, and an OI-threshold stop remains in the stop list.
- [ ] Run `python -m unittest discover -s tests -p test_trade_dispatch.py -v` and verify the new tests fail for the audited reasons.

### Task 2: Persist enough state for outage-safe risk checks

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `src/crypto_oi_monitor/storage.py`
- Modify: `tests/test_storage.py`

- [ ] Add `binance_symbol` to `TradeSignalState` and the SQLite schema using an additive migration.
- [ ] Add `list_trade_signal_states()` to the state-store protocol and SQLite/ memory implementations.
- [ ] Store the exact Binance contract symbol on new and resumed entries; enrich legacy active states on the next complete comparison.
- [ ] For active `long`/`no_add` states absent from comparisons, use the persisted Binance symbol and an explicit unavailable OI ratio rather than inventing market data.
- [ ] Fail observably when a legacy active state has no recoverable Binance symbol.
- [ ] Run focused storage and dispatcher tests until green.

### Task 3: Make transition order and scans state-aware

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Modify: `src/crypto_oi_monitor/notifier.py`
- Modify: `tests/test_trade_dispatch.py`
- Modify: `tests/test_notifier.py`

- [ ] Evaluate active ATR/trailing/EMA mandatory exits before OI/market-cap stop transitions whenever K-lines are available.
- [ ] If K-lines fail, retain the OI-threshold stop but return the K-line failure so the missed mandatory-exit check is observable.
- [ ] Do not clear `no_add` before a successful resume notification; replace it with the new long state only after success.
- [ ] Overlay cooldown, direct stop, and mandatory-exit results onto the generic scan map before returning it.
- [ ] Render unavailable OI ratios honestly in stop/exit messages.
- [ ] Run focused dispatcher/notifier tests until green.

### Task 4: Keep active risk management running in the application

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`
- Modify: `frontend/src/App.jsx`
- Add: `frontend/src/trade-condition-panel.test.jsx`
- Modify: `frontend/src/format.js`
- Modify: `frontend/src/format.test.js`

- [ ] Call the trade dispatcher when a notifier is configured even if the snapshot is incomplete.
- [ ] Suppress new entries and condition-list pushes while retaining individual stop/exit events and failures in the API payload.
- [ ] Let the page show returned active-state scans during incomplete data, with an explicit “仅执行已有状态风控” notice.
- [ ] Run backend application tests and frontend unit tests until green.

### Task 5: Documentation and verification

**Files:**
- Modify: `README.md`

- [ ] Document that data-source outages block entry but do not block persisted-position K-line exits.
- [ ] Run the complete Python suite, Python compilation, frontend tests, and Vite production build.
- [ ] Run `git diff --check` and confirm unrelated untracked files remain untouched.
- [ ] Leave the verified working-tree changes uncommitted until the user explicitly requests a commit or push.
