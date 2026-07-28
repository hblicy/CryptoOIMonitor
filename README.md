# 合约 OI / 市值监控

汇总 Binance、OKX、Bybit、Bitget、Gate、KuCoin、MEXC、Hyperliquid、Aster 的永续合约 OI，并按 CoinMarketCap 市值计算 `OI / MC`。

- 币种池：仅 Binance USDⓈ 永续合约，且 24 小时美元成交额不少于 **1,000 万 USD**。
- 黄色重点关注：`OI > MC`。
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

页面服务启动后立即刷新，之后默认每 120 秒刷新一次。Binance 和 Aster 需按交易对拉取 OI，首轮全量刷新通常需要约一分钟；页面会保留最近一次完整快照并显示其时间。

CoinMarketCap 的市值查询按每 100 个返回币种计 1 个 Call Credit；默认刷新频率下请确认套餐额度充足。

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

## 企业微信交易信号

仅推送信号，不会自动下单。信号使用 Binance USDⓈ 永续合约的已收盘 15 分钟 K 线：

- 前置条件：完整快照且 `OI / 市值 > 100%`。
- 做多：收盘价高于 EMA200，RSI(14) 从下向上突破 20。
- 做空：收盘价低于 EMA200，RSI(14) 从上向下跌破 80。
- 止损：入场参考价的 `2 × ATR(14)`；止盈条件为 RSI(14) 回到 50。
- 杠杆参考：2-3 倍；信号不包含自动下单或仓位金额。
- 同一币种在 RSI 回到 50 前不重复推送同方向信号。

## 验证

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
python -m compileall -q app.py src\crypto_oi_monitor

cd frontend
npm test
npm run build
```
