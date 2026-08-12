#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
STATE_DIR="$ROOT/data"
PID_FILE="$STATE_DIR/monitor.pid"
LOG_FILE="$STATE_DIR/monitor.log"
STARTUP_LOG_FILE="$STATE_DIR/startup.log"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "配置文件不存在：$ENV_FILE。请从 .env.example 复制为 .env 并填写配置。" >&2
  exit 1
fi

if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "前端构建产物不存在：$ROOT/frontend/dist/index.html。请在 frontend 目录执行 npm ci && npm run build。" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8766}"
mkdir -p "$STATE_DIR"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(tr -d '[:space:]' < "$PID_FILE")"
  if [[ ! "$existing_pid" =~ ^[0-9]+$ ]]; then
    echo "PID 文件内容非法：$PID_FILE" >&2
    exit 1
  fi

  if kill -0 "$existing_pid" 2>/dev/null; then
    command_line="$(ps -p "$existing_pid" -o args=)"
    if [[ "$command_line" == *"$ROOT/app.py"* ]]; then
      echo "服务已在运行，PID：$existing_pid" >&2
      exit 1
    fi
    echo "PID 文件指向非本项目进程，拒绝启动：$existing_pid" >&2
    exit 1
  fi

  echo "清理过期 PID 文件：$PID_FILE" >&2
  rm -f "$PID_FILE"
fi

nohup python3 "$ROOT/app.py" \
  --host "$HOST" \
  --port "$PORT" \
  --log-file "$LOG_FILE" \
  --log-max-mb "${LOG_MAX_MB:-50}" \
  --log-backup-count "${LOG_BACKUP_COUNT:-5}" \
  >"$STARTUP_LOG_FILE" 2>&1 &
pid="$!"
printf '%s\n' "$pid" > "$PID_FILE"

sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "服务启动失败，启动输出：" >&2
  tail -n 40 "$STARTUP_LOG_FILE" >&2
  if [[ -f "$LOG_FILE" ]]; then
    echo "最近应用日志：" >&2
    tail -n 40 "$LOG_FILE" >&2
  fi
  exit 1
fi

echo "服务已启动，PID：$pid"
echo "访问地址：http://$HOST:$PORT"
echo "日志文件：$LOG_FILE"
