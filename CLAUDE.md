# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个 Python 实现的本地 AI 代理运行时系统，支持与本地部署的大语言模型（如 vLLM、Ollama）进行交互式对话。核心功能包括代理编排、工具执行、会话管理、插件系统和上下文压缩。

**代码规模**: ~22,700 行 Python 代码  
**核心模块**: 38+ 个运行时组件

## 运行与测试

### 启动交互式对话

```bash
# 基础启动（使用默认配置）
python -m src.main

# 指定模型和 API 端点
python -m src.main \
  --model "Qwen/Qwen3-Coder-30B-A3B-Instruct" \
  --base-url "http://127.0.0.1:8000/v1" \
  --api-key "local-token"

# 启用文件写入和 Shell 命令权限
python -m src.main --allow-write --allow-shell

# 恢复已有会话
python -m src.main --resume-session-id <session_id>

# 显示完整对话记录
python -m src.main --show-transcript
```

### 环境变量配置

可通过环境变量设置默认值：
- `OPENAI_MODEL`: 默认模型名称
- `OPENAI_BASE_URL`: API 端点地址
- `OPENAI_API_KEY`: API 密钥

## 核心架构

### 三层架构设计

1. **入口层** ([main.py](src/main.py))
   - 命令行参数解析
   - 配置构建与验证
   - REPL 对话循环

2. **运行时层** ([agent_runtime.py](src/agent_runtime.py))
   - 对话循环编排：`run()` 创建新会话，`resume()` 恢复已有会话
   - 模型调用与流式输出
   - 工具执行与结果序列化
   - 上下文压缩与 token 预算管理

3. **子系统层**
   - **工具系统** ([agent_tools.py](src/agent_tools.py)): 工具注册、执行、流式输出
   - **插件系统** ([plugin_runtime.py](src/plugin_runtime.py)): 基于清单的插件加载、钩子、工具别名
   - **会话管理** ([session_store.py](src/session_store.py)): 会话持久化、恢复、快照追踪
   - **上下文压缩** ([compact.py](src/compact.py), [microcompact.py](src/microcompact.py)): 自动压缩、响应式压缩
   - **MCP 集成** ([mcp_runtime.py](src/mcp_runtime.py)): Model Context Protocol 传输层
   - **LSP 集成** ([lsp_runtime.py](src/lsp_runtime.py)): Language Server Protocol 支持

### 关键数据流

```
用户输入 → 斜杠命令预处理 → 系统提示构建 → 模型调用 → 工具执行 → 结果序列化 → 上下文压缩 → 会话持久化
```

### 核心类型定义

所有类型定义集中在 [agent_types.py](src/agent_types.py)：
- `ModelConfig`: 模型连接配置（模型名、API 地址、温度、超时、定价）
- `AgentRuntimeConfig`: 运行时配置（工作目录、权限、预算、压缩策略）
- `AgentRunResult`: 单轮执行结果（输出、token 用量、费用、会话 ID）
- `UsageStats`: Token 使用统计（输入、输出、缓存、推理 token）
- `BudgetConfig`: 预算限制（token 上限、费用上限、工具调用次数）

## 开发约定

### 会话管理

- 会话存储在 `.port_sessions/agent/` 目录
- 每个会话包含完整的对话历史和元数据
- 使用 `load_agent_session()` 和 `save_agent_session()` 进行持久化
- 会话 ID 使用 UUID 格式

### 上下文压缩策略

- **自动裁剪**: 当上下文超过 `auto_snip_threshold_tokens` 时触发
- **自动压缩**: 当上下文超过 `auto_compact_threshold_tokens` 时触发
- **保留消息**: 通过 `compact_preserve_messages` 控制保留最近 N 条消息
- 压缩后的消息会保留关键信息摘要

### 工具执行

- 所有工具必须在 `agent_tools.py` 中注册
- 工具执行支持流式输出（通过 `execute_tool_streaming`）
- 工具结果通过 `serialize_tool_result` 序列化为标准格式
- 工具权限通过 `AgentPermissions` 控制（文件写入、Shell 命令、危险操作）

### 插件开发

- 插件清单使用 JSON 格式
- 插件目录：`~/.claude/plugins` 或项目级 `./.claude/plugins`
- 插件可提供：工具别名、钩子、虚拟工具
- 通过 `PluginRuntime` 加载和管理插件

### 代理定义

- 代理定义使用 Markdown 格式（带 frontmatter）
- 代理目录：`~/.claude/agents` 或项目级 `./.claude/agents`
- 通过 `Agent` 工具调用子代理
- 支持嵌套代理委派和依赖感知批处理

## 重要注意事项

### Token 预算管理

系统支持多维度预算限制：
- `max_total_tokens`: 总 token 上限
- `max_input_tokens`: 输入 token 上限
- `max_output_tokens`: 输出 token 上限
- `max_total_cost_usd`: 总费用上限（美元）
- `max_tool_calls`: 工具调用次数上限
- `max_model_calls`: 模型调用次数上限
- `max_session_turns`: 会话轮次上限

预算检查在每次模型调用前执行，超限时会停止执行并返回 `stop_reason`。

### 安全与权限

- 默认禁用文件写入和 Shell 命令执行
- 通过 `--allow-write` 启用文件写入
- 通过 `--allow-shell` 启用 Shell 命令
- 通过 `--unsafe` 启用危险操作（如 `rm -rf`）
- 钩子策略通过 `hook_policy.py` 管理

### 模型兼容性

系统通过 OpenAI 兼容层 ([openai_compat.py](src/openai_compat.py)) 支持多种模型后端：
- vLLM 本地推理
- Ollama 本地部署
- LiteLLM 代理路由
- OpenRouter 云端网关
- 任何兼容 OpenAI API 的服务

确保模型支持工具调用（function calling）功能。

### 对话循环逻辑

- 首轮对话使用 `agent.run(prompt)` 创建新会话
- 后续轮次自动使用 `agent.resume(prompt, stored_session)` 携带历史上下文
- 会话 ID 在首轮执行后生成并持久化
- 输入 `/exit` 或 `/quit` 退出对话循环
- 支持 Ctrl+C 中断和 EOF 退出

### 修改核心逻辑时

- 修改 `agent_runtime.py` 时需同步更新 `agent_types.py` 中的类型定义
- 修改工具系统时需更新 `agent_tools.py` 中的工具注册表
- 修改会话格式时需确保向后兼容性（通过 `session_store.py`）
- 修改压缩策略时需测试不同 token 阈值下的行为
