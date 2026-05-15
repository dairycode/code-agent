#!/usr/bin/env bash

# Code Agent 启动脚本
# 用法: ./start.sh [prompt]

set -e

# 脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ═══════════════════════════════════════
# 配置区（直接修改以下值即可）
# ═══════════════════════════════════════

MODEL="claude-4.6-sonnet"
BASE_URL="http://llmapi.bilibili.co"
API_KEY="bsk-8eaff4b3a78334ea2a2380857166c036"
TEMPERATURE="0.0"
MAX_TOKENS="8192"
TIMEOUT_SECONDS="120.0"
MAX_TURNS="12"
CWD="."

# 权限
ALLOW_WRITE="true"
ALLOW_SHELL="true"
UNSAFE="false"

# 输出模式
STREAM="true"
SHOW_TRANSCRIPT="false"

# ═══════════════════════════════════════
# 参数校验
# ═══════════════════════════════════════

if [ -z "$MODEL" ] || [ -z "$BASE_URL" ] || [ -z "$API_KEY" ]; then
    echo "错误: MODEL、BASE_URL、API_KEY 均不能为空，请在配置区填写完整" >&2
    exit 1
fi

# ═══════════════════════════════════════
# 以下无需修改
# ═══════════════════════════════════════

ARGS=(
    --model "$MODEL"
    --base-url "$BASE_URL"
    --api-key "$API_KEY"
    --temperature "$TEMPERATURE"
    --max-tokens "$MAX_TOKENS"
    --timeout-seconds "$TIMEOUT_SECONDS"
    --max-turns "$MAX_TURNS"
    --cwd "$CWD"
)

if [ "$ALLOW_WRITE" = "true" ]; then
    ARGS+=(--allow-write)
fi

if [ "$ALLOW_SHELL" = "true" ]; then
    ARGS+=(--allow-shell)
fi

if [ "$UNSAFE" = "true" ]; then
    ARGS+=(--unsafe)
fi

if [ "$STREAM" = "true" ]; then
    ARGS+=(--stream)
fi

if [ "$SHOW_TRANSCRIPT" = "true" ]; then
    ARGS+=(--show-transcript)
fi

# 初始提示词（从命令行参数）
if [ $# -gt 0 ]; then
    ARGS+=("$*")
fi

# 打印配置信息
echo "================================"
echo "Code Agent"
echo "================================"
echo "模型: $MODEL"
echo "API: $BASE_URL"
echo "温度: $TEMPERATURE"
echo "max_tokens: $MAX_TOKENS"
echo "超时: ${TIMEOUT_SECONDS}s"
echo "最大轮次: $MAX_TURNS"
echo "工作目录: $CWD"
echo "文件写入: $ALLOW_WRITE"
echo "Shell 命令: $ALLOW_SHELL"
echo "危险操作: $UNSAFE"
echo "流式输出: $STREAM"
echo "================================"
echo

python3 -m src.main "${ARGS[@]}"
