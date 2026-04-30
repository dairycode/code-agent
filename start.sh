#!/usr/bin/env bash

# Code Agent 启动脚本
# 用法: ./start.sh [prompt]

set -e

# 脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 加载环境变量
if [ -f .env ]; then
    echo "加载 .env 配置..."
    export $(grep -v '^#' .env | xargs)
fi

# 默认配置
MODEL="${OPENAI_MODEL:-Qwen/Qwen3-Coder-30B-A3B-Instruct}"
BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:8000/v1}"
API_KEY="${OPENAI_API_KEY:-local-token}"
TEMPERATURE="${TEMPERATURE:-0.0}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-120.0}"
MAX_TURNS="${MAX_TURNS:-12}"
CWD="${CWD:-.}"

# 构建命令参数
ARGS=(
    --model "$MODEL"
    --base-url "$BASE_URL"
    --api-key "$API_KEY"
    --temperature "$TEMPERATURE"
    --timeout-seconds "$TIMEOUT_SECONDS"
    --max-turns "$MAX_TURNS"
    --cwd "$CWD"
)

# 权限配置
if [ "${ALLOW_WRITE:-false}" = "true" ]; then
    ARGS+=(--allow-write)
fi

if [ "${ALLOW_SHELL:-false}" = "true" ]; then
    ARGS+=(--allow-shell)
fi

if [ "${UNSAFE:-false}" = "true" ]; then
    ARGS+=(--unsafe)
fi

# 流式输出
if [ "${STREAM:-false}" = "true" ]; then
    ARGS+=(--stream)
fi

# 显示完整对话记录
if [ "${SHOW_TRANSCRIPT:-false}" = "true" ]; then
    ARGS+=(--show-transcript)
fi

# Token 定价
if [ -n "$INPUT_COST_PER_MILLION" ]; then
    ARGS+=(--input-cost-per-million "$INPUT_COST_PER_MILLION")
fi

if [ -n "$OUTPUT_COST_PER_MILLION" ]; then
    ARGS+=(--output-cost-per-million "$OUTPUT_COST_PER_MILLION")
fi

# Token 预算限制
if [ -n "$MAX_TOTAL_TOKENS" ]; then
    ARGS+=(--max-total-tokens "$MAX_TOTAL_TOKENS")
fi

if [ -n "$MAX_INPUT_TOKENS" ]; then
    ARGS+=(--max-input-tokens "$MAX_INPUT_TOKENS")
fi

if [ -n "$MAX_OUTPUT_TOKENS" ]; then
    ARGS+=(--max-output-tokens "$MAX_OUTPUT_TOKENS")
fi

if [ -n "$MAX_BUDGET_USD" ]; then
    ARGS+=(--max-budget-usd "$MAX_BUDGET_USD")
fi

if [ -n "$MAX_TOOL_CALLS" ]; then
    ARGS+=(--max-tool-calls "$MAX_TOOL_CALLS")
fi

if [ -n "$MAX_MODEL_CALLS" ]; then
    ARGS+=(--max-model-calls "$MAX_MODEL_CALLS")
fi

# 上下文压缩
if [ -n "$AUTO_SNIP_THRESHOLD" ]; then
    ARGS+=(--auto-snip-threshold "$AUTO_SNIP_THRESHOLD")
fi

if [ -n "$AUTO_COMPACT_THRESHOLD" ]; then
    ARGS+=(--auto-compact-threshold "$AUTO_COMPACT_THRESHOLD")
fi

if [ -n "$COMPACT_PRESERVE_MESSAGES" ]; then
    ARGS+=(--compact-preserve-messages "$COMPACT_PRESERVE_MESSAGES")
fi

# 恢复会话
if [ -n "$RESUME_SESSION_ID" ]; then
    ARGS+=(--resume-session-id "$RESUME_SESSION_ID")
fi

# 初始提示词（从命令行参数）
if [ $# -gt 0 ]; then
    ARGS+=("$*")
fi

# 打印配置信息
echo "================================"
echo "Code Agent 启动配置"
echo "================================"
echo "模型: $MODEL"
echo "API 地址: $BASE_URL"
echo "温度: $TEMPERATURE"
echo "最大轮次: $MAX_TURNS"
echo "工作目录: $CWD"
echo "文件写入: ${ALLOW_WRITE:-false}"
echo "Shell 命令: ${ALLOW_SHELL:-false}"
echo "================================"
echo

# 启动 Agent
python -m src.main "${ARGS[@]}"
