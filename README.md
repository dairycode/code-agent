# Code Agent

> 基于 Claude API 的 AI 代码助手 — 支持工具调用、会话管理、插件扩展

---

## 项目简介

Code Agent 是一个 Python 实现的 AI 代理运行时系统，通过 Claude Messages API 与 Anthropic 模型进行交互式对话，完成代码编写、重构、调试等任务。

**核心特性**：
- 🚀 直接调用 Claude API（支持自定义 base_url）
- 🔧 完整的工具执行系统（文件操作、Shell 命令）
- 💾 会话持久化与恢复
- 🔌 可扩展的插件系统
- 📊 Token 预算与费用追踪
- 🗜️ 智能上下文压缩
- 🔒 细粒度权限控制

**代码规模**: ~22,700 行 Python 代码  
**核心模块**: 38+ 个运行时组件

---

## 🚀 快速开始

### 前置要求

- Python 3.10+
- Anthropic API 密钥（从 https://console.anthropic.com 获取）

### 安装

```bash
# 克隆仓库
git clone <repository-url>
cd code-agent

# 安装依赖（如果有 requirements.txt）
pip install -r requirements.txt
```

### 配置环境变量

```bash
# 设置 API 密钥
export ANTHROPIC_API_KEY="sk-ant-..."

# 可选：自定义端点和模型
export ANTHROPIC_BASE_URL="https://api.anthropic.com"
export ANTHROPIC_MODEL="claude-sonnet-4-20250514"
```

### 启动交互式对话

#### 方式一：使用启动脚本（推荐）

```bash
# Linux/macOS
./start.sh

# Windows
start.bat

# 带初始提示词启动
./start.sh "帮我写一个快速排序函数"
```

#### 方式二：直接使用 Python

```bash
# 基础启动（使用默认配置）
python -m src.main

# 指定模型和 API 端点
python -m src.main \
  --model "claude-sonnet-4-20250514" \
  --base-url "https://api.anthropic.com" \
  --api-key "sk-ant-..."

# 启用文件写入和 Shell 命令权限
python -m src.main --allow-write --allow-shell

# 恢复已有会话
python -m src.main --resume-session-id <session_id>
```

#### 方式三：使用环境变量

```bash
export ANTHROPIC_MODEL="claude-sonnet-4-20250514"
export ANTHROPIC_BASE_URL="https://api.anthropic.com"
export ANTHROPIC_API_KEY="sk-ant-..."

python -m src.main
```

---

## 🏗️ 核心架构

### 三层架构设计

```
┌─────────────────────────────────────────┐
│          入口层 (main.py)               │
│  命令行解析 / 配置构建 / REPL 循环      │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│      运行时层 (agent_runtime.py)        │
│  对话编排 / 模型调用 / 工具执行         │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│            子系统层                      │
│  工具 / 插件 / 会话 / 压缩 / MCP / LSP  │
└─────────────────────────────────────────┘
```

### 关键数据流

```
用户输入 → 斜杠命令预处理 → 系统提示构建 → 模型调用 
  → 工具执行 → 结果序列化 → 上下文压缩 → 会话持久化
```

### 核心模块

#### 代理运行时 (Agent Runtime)

| 模块 | 功能 |
|------|------|
| `agent_runtime.py` | 核心代理运行时 — 对话循环、模型调用、工具执行编排 |
| `agent_session.py` | 会话状态管理 — 对话历史、上下文追踪 |
| `agent_manager.py` | 代理管理器 — 血统追踪、分组管理、嵌套代理批处理 |
| `agent_registry.py` | 代理注册表 — 从 `~/.claude/agents` 和 `./.claude/agents` 发现本地代理定义 |
| `agent_types.py` | 类型定义 — 数据类、配置、统计信息 |
| `agent_context.py` | 上下文构建 — 系统提示、环境信息 |
| `agent_context_usage.py` | Token 使用统计 — 估算、收集、预算追踪 |
| `agent_prompting.py` | 提示工程 — 系统提示构建、上下文注入 |

#### 工具系统 (Tool System)

| 模块 | 功能 |
|------|------|
| `agent_tools.py` | 工具注册表 — 默认工具集、工具执行、流式输出 |
| `agent_slash_commands.py` | 斜杠命令 — `/exit`、`/search` 等命令预处理 |

#### 插件与扩展 (Plugins & Extensions)

| 模块 | 功能 |
|------|------|
| `plugin_runtime.py` | 插件运行时 — 基于清单的插件系统、钩子、工具别名 |
| `agent_plugin_cache.py` | 插件缓存 — 插件元数据缓存 |
| `mcp_runtime.py` | MCP 传输 — stdio MCP 协议、资源/工具列表与调用 |
| `lsp_runtime.py` | LSP 运行时 — 语言服务器协议集成 |

#### 任务与计划 (Tasks & Plans)

| 模块 | 功能 |
|------|------|
| `task.py` | 任务定义 — 任务数据结构 |
| `task_runtime.py` | 任务运行时 — 持久化任务、依赖感知执行 |
| `plan_runtime.py` | 计划运行时 — 计划到任务的同步 |
| `workflow_runtime.py` | 工作流运行时 — 工作流编排 |

#### 上下文压缩 (Context Compression)

| 模块 | 功能 |
|------|------|
| `compact.py` | 对话压缩 — 自动压缩、响应式压缩 |
| `microcompact.py` | 微压缩 — 轻量级压缩策略 |
| `token_budget.py` | Token 预算 — 预算管理、限制检查 |

#### 配置与策略 (Config & Policy)

| 模块 | 功能 |
|------|------|
| `config_runtime.py` | 配置运行时 — 本地配置/设置变更 |
| `hook_policy.py` | 钩子策略 — 本地策略清单、信任报告、安全环境 |
| `account_runtime.py` | 账户运行时 — 账户配置文件、登录/登出状态 |

#### 远程与分布式 (Remote & Distributed)

| 模块 | 功能 |
|------|------|
| `remote_runtime.py` | 远程运行时 — 远程配置文件、连接/断开状态 |
| `remote_trigger_runtime.py` | 远程触发 — 远程 CLI/斜杠命令流程 |
| `team_runtime.py` | 团队运行时 — 团队协作功能 |

#### 其他子系统

| 模块 | 功能 |
|------|------|
| `ask_user_runtime.py` | 用户询问 — 排队或交互式询问流程、历史记录 |
| `search_runtime.py` | 搜索运行时 — 基于提供商的 `web_search`、本地清单 |
| `worktree_runtime.py` | 工作树运行时 — Git worktree 隔离、分支管理 |
| `openai_compat.py` | OpenAI 兼容层 — 备用 API 客户端（本地模型） |
| `claude_client.py` | Claude API 客户端 — Messages API 调用 |
| `session_store.py` | 会话存储 — 会话持久化与恢复 |
| `session_env_vars.py` | 环境变量 — 会话级环境变量管理 |
| `tokenizer_runtime.py` | 分词器 — Token 计数与估算 |
| `builtin_agents.py` | 内置代理 — 预定义的代理配置 |
| `main.py` | 入口点 — 交互式 REPL 实现 |

### 模块依赖关系

```
agent_runtime.py
├── agent_manager.py
├── agent_context.py
├── agent_session.py
├── agent_tools.py
├── plugin_runtime.py
├── mcp_runtime.py
├── compact.py
└── claude_client.py
```

---

## 🔧 核心特性

### 嵌套代理委派
- 将子任务委派给子代理
- 依赖感知的拓扑批处理
- 血统追踪与分组管理

### 上下文管理
- 自动裁剪与压缩
- 响应式压缩（提示过长时）
- Token 预算与费用追踪

### 工具执行
- 流式工具输出
- 工具别名与虚拟工具
- 工具屏蔽与权限控制

### 会话持久化
- 文件编辑历史回放
- 会话恢复与摘要
- 快照 ID 追踪

---

## 🔧 使用示例

### 基础对话

```bash
$ python -m src.main

# Agent Chat
Enter a prompt. Use '/exit' or '/quit' to stop.
user> 帮我写一个快速排序函数

[Agent 会生成代码并解释实现]

user> 优化一下时间复杂度

[Agent 会分析并提供优化建议]

user> /exit
chat_ended=user_exit
```

### 编程式使用

```python
from src.agent_runtime import LocalCodingAgent
from src.agent_types import AgentRuntimeConfig, ModelConfig, ModelPricing

# 创建模型配置
model_config = ModelConfig(
    model='claude-sonnet-4-20250514',
    base_url='https://api.anthropic.com',
    api_key='sk-ant-...',
    temperature=0.0,
    timeout_seconds=120.0,
    max_tokens=8192,
    pricing=ModelPricing(
        input_cost_per_million_tokens_usd=3.0,
        output_cost_per_million_tokens_usd=15.0,
    ),
)

# 创建运行时配置
runtime_config = AgentRuntimeConfig(
    cwd='/path/to/project',
    permissions=AgentPermissions(
        allow_file_write=True,
        allow_shell_commands=True,
    ),
)

# 初始化代理
agent = LocalCodingAgent(
    model_config=model_config,
    runtime_config=runtime_config,
)

# 运行对话
result = agent.run('帮我重构这个函数')
print(result.final_output)
print(f'Token 用量: {result.usage.total_tokens}')
print(f'费用: ${result.total_cost_usd:.6f}')
```

---

## ⚙️ 配置选项

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--model` | 模型名称 | `claude-sonnet-4-20250514` |
| `--base-url` | API 端点地址 | `https://api.anthropic.com` |
| `--api-key` | API 密钥 | - |
| `--temperature` | 采样温度 | `0.0` |
| `--max-tokens` | 最大输出 token 数 | `8192` |
| `--timeout-seconds` | 请求超时时间 | `120.0` |
| `--cwd` | 工作目录 | `.` |
| `--allow-write` | 启用文件写入 | `False` |
| `--allow-shell` | 启用 Shell 命令 | `False` |
| `--unsafe` | 启用危险操作 | `False` |
| `--stream` | 启用流式输出 | `False` |
| `--max-turns` | 最大对话轮次 | `12` |
| `--resume-session-id` | 恢复会话 ID | - |
| `--show-transcript` | 显示完整对话记录 | `False` |

### Token 预算限制

```bash
python -m src.main \
  --max-total-tokens 100000 \
  --max-input-tokens 80000 \
  --max-output-tokens 20000 \
  --max-budget-usd 1.0 \
  --max-tool-calls 50 \
  --max-model-calls 20
```

### 上下文压缩

```bash
python -m src.main \
  --auto-snip-threshold 50000 \
  --auto-compact-threshold 80000 \
  --compact-preserve-messages 4
```

---

## 🔌 插件系统

### 插件目录

- 全局插件：`~/.claude/plugins`
- 项目插件：`./.claude/plugins`

### 插件清单示例

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "tools": {
    "custom_tool": {
      "command": "python /path/to/tool.py",
      "description": "自定义工具"
    }
  },
  "hooks": {
    "pre_model_call": "python /path/to/hook.py"
  }
}
```

---

## 🤖 代理定义

### 代理目录

- 全局代理：`~/.claude/agents`
- 项目代理：`./.claude/agents`

### 代理定义示例

```markdown
---
name: code-reviewer
description: 代码审查专家
model: Qwen/Qwen3-Coder-30B-A3B-Instruct
---

你是一位经验丰富的代码审查专家，专注于：
- 代码质量与可读性
- 潜在的 bug 和安全问题
- 性能优化建议
- 最佳实践遵循
```

---

## 📊 性能与监控

### Token 使用统计

每次对话结束后会显示：
- `total_tokens`: 总 token 数
- `input_tokens`: 输入 token 数
- `output_tokens`: 输出 token 数
- `total_cost_usd`: 总费用（美元）

### 会话管理

- 会话存储在 `.port_sessions/agent/` 目录
- 每个会话包含完整的对话历史和元数据
- 支持跨进程恢复会话

---

## 🔒 安全特性

### 权限控制

- **默认禁用**：文件写入、Shell 命令、危险操作
- **显式启用**：通过命令行参数启用所需权限
- **细粒度控制**：通过 `AgentPermissions` 精确控制

### 钩子策略

- 本地策略清单
- 信任报告
- 安全环境隔离

---

## 模型兼容性

通过 Claude Messages API 调用 Anthropic 模型：

| 模型 | 说明 |
|------|------|
| **claude-sonnet-4-20250514** | 默认模型，平衡性能与成本 |
| **claude-opus-4-20250514** | 最强推理能力 |
| **claude-haiku-3-5-20241022** | 最快响应速度 |

支持自定义 `base_url`，可接入兼容 Claude API 格式的代理网关。

**要求**：模型必须支持工具调用（tool use）功能。

---

## 📝 开发指南

### 添加新工具

1. 在 `src/agent_tools.py` 中注册工具
2. 实现工具执行逻辑
3. 添加工具文档字符串

### 创建自定义代理

1. 在 `~/.claude/agents` 或 `./.claude/agents` 创建 Markdown 文件
2. 定义代理元数据（frontmatter）
3. 通过 `Agent` 工具调用

### 扩展插件系统

1. 创建插件清单 JSON
2. 实现钩子或工具别名
3. 放置在 `~/.claude/plugins`

详细开发文档请参考 [CLAUDE.md](CLAUDE.md) 和 [src/README.md](src/README.md)。

---

## 🤝 贡献指南

欢迎贡献！请遵循以下准则：

1. 保持代码简洁（KISS 原则）
2. 遵循单一职责原则
3. 添加必要的类型注解
4. 编写清晰的文档字符串
5. 确保向后兼容性

---

## 📚 相关文档

- [CLAUDE.md](CLAUDE.md) - Claude Code 开发指南

---

## 📄 许可证

本项目遵循开源许可证。详见 LICENSE 文件。

---

<p align="center">
  <em>由 Code Agent 团队维护</em>
</p>
