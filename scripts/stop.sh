#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/monitor.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "未找到 PID 文件，服务未运行：$PID_FILE" >&2
  exit 1
fi

pid="$(tr -d '[:space:]' < "$PID_FILE")"
if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
  echo "PID 文件内容非法：$PID_FILE" >&2
  exit 1
fi

if ! kill -0 "$pid" 2>/dev/null; then
  echo "清理过期 PID 文件：$PID_FILE" >&2
  rm -f "$PID_FILE"
  exit 1
fi

command_line="$(ps -p "$pid" -o args=)"
if [[ "$command_line" != *"$ROOT/app.py"* ]]; then
  echo "PID 文件指向非本项目进程，拒绝停止：$pid" >&2
  exit 1
fi

kill -TERM "$pid"
for _ in {1..50}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "服务已停止，PID：$pid"
    exit 0
  fi
  sleep 0.2
done

echo "服务未在 10 秒内退出，发送 SIGKILL：$pid" >&2
kill -KILL "$pid"
sleep 0.2
if kill -0 "$pid" 2>/dev/null; then
  echo "无法停止服务，PID：$pid" >&2
  exit 1
fi

rm -f "$PID_FILE"
echo "服务已强制停止，PID：$pid"
