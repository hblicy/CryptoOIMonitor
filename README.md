# 合约 OI / 市值监控

汇总 CEX（Binance、OKX、Bybit、Bitget、Gate、KuCoin、BingX、MEXC）与 DEX（Hyperliquid、Aster、Lighter）的永续合约 OI，并按 CoinMarketCap 市值计算 `OI / MC`。

- 币种池：仅 Binance USDⓈ 永续合约，且 24 小时美元成交额不少于 **1,000 万 USD**。
- 黄色重点关注：`OI > 1.1 × MC`。
- 红色埋伏候选：`OI > 2 × MC`。
- 当任一交易所或 CoinMarketCap 本轮请求失败时，页面显示数据源错误并禁止企业微信阈值推送。
- CoinMarketCap 未收录的币种仅排除自身，不影响其余币种的市值计算。
- 数据库保存每轮快照和企业微信推送状态；不会复用旧快照冒充本轮数据。

## 启动

```powershell
cd D:\code-web3\06-trading-research\CryptoOIMonitor\frontend
npm install
npm run build

cd ..
$env:COINMARKETCAP_API_KEY = "你的 CoinMarketCap Pro API Key" # 必填
$env:WECOM_ROBOT_WEBHOOK_URL = "你的企业微信机器人 Webhook" # 启用提醒时必填
python app.py --port 8766
```

打开 <http://127.0.0.1:8766>。

页面服务启动后立即刷新，之后按固定 120 秒时间点刷新；若单轮耗时超过 120 秒，会在结束后立即补跑一轮，但不会并发重叠，随后从该轮完成时间重新计算 120 秒周期。Binance、BingX 和 Aster 需按交易对拉取 OI，首轮全量刷新通常需要约一分钟；BingX 单个请求超过 12 秒会将该数据源标记为异常并暂停提醒推送。页面会保留最近一次完整快照并显示其时间。
`REFRESH_SECONDS`、`CMC_REFRESH_SECONDS`、`SNAPSHOT_RETENTION_DAYS` 和 `MIN_FREE_DISK_GB` 必须是大于 0 的整数；配置为 `0` 或负数时服务会拒绝启动。OI 按 `REFRESH_SECONDS`（默认 120 秒）刷新；CoinMarketCap 市值按 `CMC_REFRESH_SECONDS`（默认 600 秒）独立刷新。

同一进程会缓存已确认的 CoinMarketCap 币种 ID，后续市值刷新只请求报价，不会重复请求映射接口；未映射或符号歧义的币种每小时会重新尝试映射一次。实际请求市值时会在日志中记录。

CoinMarketCap 健康状态会显示市值的实际更新时间；顶部时间仅表示 OI 快照更新时间。快照默认保留 `SNAPSHOT_RETENTION_DAYS=30` 天。若可用磁盘空间低于 `MIN_FREE_DISK_GB=2`，服务会清除历史快照、仅保留最新快照，并在空间足够时收缩数据库；空间仍不足会记录错误日志。

## Ubuntu VPS 启停

```bash
# 首次部署
cp .env.example .env
nano .env                    # 填写 API Key 和 Webhook

cd frontend
npm ci && npm run build
cd ..

# 启动
bash scripts/start.sh

# 查看日志
tail -f data/monitor.log

# 停止
bash scripts/stop.sh
```

可通过 `ENV_FILE` 环境变量指定其他配置文件：

```bash
ENV_FILE=/etc/crypto-oi-monitor.env bash scripts/start.sh
```


## 企业微信关注提醒

只在完整快照中触发，并使用告警滞回区间：

- `OI / 市值 > 205%` 时推送进入埋伏候选消息。
- `OI / 市值 < 195%` 时推送退出埋伏候选提醒。
- `195%～205%` 区间保持原有告警状态，不重复推送。

机器人 Webhook 仅从 `WECOM_ROBOT_WEBHOOK_URL` 环境变量读取，绝不写入代码或提交到仓库。

## 企业微信交易信号（仅做多）

仅推送信号，不会自动下单。信号使用 Binance USDⓈ 永续合约的已收盘 15 分钟 K 线：

- EMA200 使用最近 1001 根已收盘 K 线计算，其中 1000 根用于预热，以对齐 Binance 图表口径。
- 前置条件：完整快照且 `OI / 市值 > 110%`。
- 做多：收盘价高于 EMA200，且 RSI(14) 小于 50。
- 停止开多：已开多后，`OI / 市值 < 110%`、RSI(14) 超过 50，或 15 分钟收盘价低于 EMA200 时推送停止开多提示；OI / 市值条件会立即生效，不等待 K 线请求。恰好 110% 时不新开多，也不因比例本身停止已有开多。
- 杠杆参考：2-3 倍；信号不包含自动下单或仓位金额。
- 同一币种在停止开多条件触发前不重复推送；提示停止开多后，RSI(14) 回落至 50 以下且收盘价重回 EMA200 上方时可再次推送做多。
- Web 页面“交易条件扫描”会列出所有 `OI / 市值 > 110%` 的标的，分为可以做多、停止做多和 K 线异常，并展示 RSI、收盘价、EMA200、OI / 市值和停止原因。
- 完整快照中不再可比较的币种会清除关注提醒和交易信号状态；下次重新纳入比较时会按新币种重新判断。

## 验证

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
python -m compileall -q app.py src\crypto_oi_monitor

cd frontend
npm test
npm run build
```

## CoinMarketCap 重名币种映射

同一 ticker 在 CoinMarketCap 有多个活跃候选时，系统不会自动选择，避免取错市值。页面会列出候选的 CMC ID、名称、价格、市值，以及与 Binance 合约价格的价差；价差只用于人工判断，不会自动映射。

确认映射后，在 `.env` 中配置固定 ID 并重启服务：

```bash
CMC_ID_OVERRIDES=BTC:1,ETH:1027
```

若已缓存的 CMC ID 后续返回空市值，系统会使该缓存失效，并在一小时后重新查询映射。
