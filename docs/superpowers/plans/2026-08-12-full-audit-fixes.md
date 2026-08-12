# 全仓库审计问题修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复审计发现的退出漏报、状态丢失、单币故障扩散、刷新滥用、CMC 额外请求、非法数值、日志膨胀及展示问题。

**Architecture:** 保留现有刷新、快照和交易状态机结构，在各自边界内补齐历史回放、状态保护、资源控制和数据校验。所有异常继续显式进入日志和快照，不复用陈旧实时数据冒充成功。

**Tech Stack:** Python 3 标准库、SQLite、React 18、Vite、Vitest、Bash。

---

### Task 1: 历史退出回放与单币故障隔离

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Test: `tests/test_trade_dispatch.py`

- [ ] 写测试证明中途 K 线触及移动保护价会退出、通知失败可重试、一个回放缺口不阻断其他币种。
- [ ] 运行对应测试并确认按旧实现失败。
- [ ] 按每根未处理 K 线计算指标、移动保护价和退出原因；把回放缺口转为该币种失败。
- [ ] 运行交易调度测试并确认通过。

### Task 2: 保留跌出币种池的活跃风控状态

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

- [ ] 写测试证明 `long`/`no_add` 状态加入状态清理保护集合。
- [ ] 运行测试并确认旧实现失败。
- [ ] 清理前读取活跃交易状态并加入保留集合。
- [ ] 运行应用测试并确认通过。

### Task 3: 手动刷新限频

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

- [ ] 写测试证明刷新占用和最小间隔都会立即拒绝手动刷新。
- [ ] 运行测试并确认旧实现失败。
- [ ] 增加非阻塞手动刷新入口和 429 响应，不改变定时刷新。
- [ ] 运行应用测试并确认通过。

### Task 4: CMC 缓存与外部数值校验

**Files:**
- Modify: `src/crypto_oi_monitor/domain.py`
- Modify: `src/crypto_oi_monitor/market_caps.py`
- Modify: `src/crypto_oi_monitor/trading.py`
- Modify: `src/crypto_oi_monitor/storage.py`
- Modify: `app.py`
- Test: `tests/test_domain.py`
- Test: `tests/test_market_caps.py`
- Test: `tests/test_trading.py`

- [ ] 写测试覆盖币种池变化不提前刷新 CMC、非有限/非法 OI、市值、K 线被拒绝、JSON 禁止非标准数值。
- [ ] 运行对应测试并确认旧实现失败。
- [ ] 在模型边界做有限数和范围校验，缓存有效期内构造当前币种池视图。
- [ ] 运行对应测试并确认通过。

### Task 5: 日志、配置、OKX 与前端提示

**Files:**
- Modify: `app.py`
- Modify: `scripts/start.sh`
- Modify: `.env.example`
- Modify: `src/crypto_oi_monitor/sources.py`
- Modify: `frontend/src/App.jsx`
- Modify: `README.md`
- Test: `tests/test_app.py`
- Test: `tests/test_sources.py`
- Test: `frontend/src/trade-condition-panel.test.jsx`

- [ ] 写测试覆盖空 CMC Key、滚动日志配置、OKX 排除非 USDT 永续和单条异常提示。
- [ ] 运行测试并确认旧实现失败。
- [ ] 实现滚动日志、配置校验、OKX 过滤和页面条件分支。
- [ ] 运行对应测试并确认通过。

### Task 6: 开发依赖与全量验证

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

- [ ] 升级 Vite/Vitest 到已修复且兼容部署 Node 版本的发行版。
- [ ] 运行后端全部测试与编译检查。
- [ ] 运行前端全部测试、生产构建及依赖审计。
- [ ] 运行 Bash 语法检查和 `git diff --check`，复核只包含本任务文件。
