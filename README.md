# Code Agent

一个 Python 实现的 AI 代理运行时系统，通过 Claude Messages API 驱动交互式编程对话。本项目是教学用途的完整 Agent 实现，涵盖工具调用、会话管理、上下文压缩、插件系统等核心概念。

---

## 目录

- [快速开始](#快速开始)
- [架构总览](#架构总览)
- [执行流程详解](#执行流程详解)
- [核心模块说明](#核心模块说明)
- [配置项参考](#配置项参考)
- [内置工具列表](#内置工具列表)
- [插件系统](#插件系统)
- [上下文压缩机制](#上下文压缩机制)
- [会话持久化](#会话持久化)

---

## 快速开始

### 前置要求

- Python 3.10+
- Anthropic API 密钥（或兼容 Claude Messages API 格式的网关）

### 启动

```bash
# 编辑 start.sh 顶部的配置区，填入你的 API 信息
vim start.sh

# 启动交互式对话
./start.sh

# 带初始提示词启动
./start.sh "帮我写一个快速排序函数"
```

或直接使用 Python：

```bash
python3 -m src.main \
  --model "claude-sonnet-4-20250514" \
  --base-url "https://api.anthropic.com" \
  --api-key "sk-ant-..." \
  --allow-write --allow-shell --stream
```

---

## 架构总览

### 三层架构

```
┌─────────────────────────────────────────────────┐
│              入口层 (main.py)                    │
│    命令行解析 → 配置构建 → REPL 对话循环         │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│          运行时层 (agent_runtime.py)             │
│  对话编排 → 模型调用 → 工具执行 → 预算管理       │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│                 子系统层                          │
│  claude_client │ agent_tools │ compact           │
│  plugin_runtime │ mcp_runtime │ session_store    │
│  search_runtime │ lsp_runtime │ worktree_runtime │
└─────────────────────────────────────────────────┘
```

### 模块依赖关系

```
main.py
└── LocalCodingAgent (agent_runtime.py)
    ├── ClaudeClient (claude_client.py)        ← API 调用
    ├── AgentSessionState (agent_session.py)   ← 会话状态
    ├── tool_registry (agent_tools.py)         ← 工具执行
    ├── AgentManager (agent_manager.py)        ← 子代理管理
    ├── PluginRuntime (plugin_runtime.py)      ← 插件系统
    ├── MCPRuntime (mcp_runtime.py)            ← MCP 协议
    ├── compact / microcompact                 ← 上下文压缩
    ├── SessionStore (session_store.py)        ← 会话持久化
    └── PromptContext (agent_prompting.py)     ← 系统提示构建
```

---

## 执行流程详解

### 1. 启动阶段

```
start.sh → python3 -m src.main → _build_agent() → _run_agent_chat_loop()
```

`main.py` 做三件事：
1. 解析命令行参数，构建 `ModelConfig` 和 `AgentRuntimeConfig`
2. 实例化 `LocalCodingAgent`（初始化所有子系统）
3. 进入 REPL 循环

### 2. REPL 循环

```python
while True:
    prompt = input("user> ")
    if prompt in ('/exit', '/quit'):
        break
    if active_session_id:
        result = agent.resume(prompt, stored_session)  # 恢复已有会话
    else:
        result = agent.run(prompt)                      # 创建新会话
    active_session_id = result.session_id
    print(result.final_output)
```

### 3. 单轮执行流程 (`_run_prompt`)

这是系统的核心，每次用户输入后执行：

```
用户输入
  │
  ▼
┌─────────────────────────────┐
│ 1. 斜杠命令预处理            │  /exit, /help, /search 等
│    如果已处理 → 直接返回     │
└──────────────┬──────────────┘
               │
  ▼
┌─────────────────────────────┐
│ 2. 插件 Hook 处理            │  before_prompt, on_resume
└──────────────┬──────────────┘
               │
  ▼
┌─────────────────────────────┐
│ 3. 构建/复用 Session         │  system prompt + 历史消息
│    追加用户消息到消息列表     │
└──────────────┬──────────────┘
               │
  ▼
┌─────────────────────────────┐
│ 4. 预算检查                  │  token/费用/调用次数
│    超限 → 立即返回           │
└──────────────┬──────────────┘
               │
  ▼
┌═══════════════════════════════════════════════════┐
║ 5. Agent 循环 (最多 max_turns 轮)                ║
║                                                   ║
║  ┌───────────────────────────────────────┐       ║
║  │ a) 上下文压缩（三级递进）              │       ║
║  │    microcompact → snip → compact      │       ║
║  └───────────────────┬───────────────────┘       ║
║                      │                            ║
║  ┌───────────────────▼───────────────────┐       ║
║  │ b) 调用 Claude API                    │       ║
║  │    session.to_openai_messages()       │       ║
║  │    → ClaudeClient.stream/complete()   │       ║
║  │    → AssistantTurn                    │       ║
║  └───────────────────┬───────────────────┘       ║
║                      │                            ║
║            ┌─────────┴─────────┐                  ║
║            │                   │                  ║
║     无工具调用            有工具调用               ║
║            │                   │                  ║
║  ┌─────────▼─────────┐  ┌─────▼──────────────┐  ║
║  │ 检查是否被截断     │  │ c) 逐个执行工具     │  ║
║  │ 是 → 注入续写提示  │  │    权限检查         │  ║
║  │ 否 → 返回最终输出  │  │    execute_tool()   │  ║
║  └────────────────────┘  │    结果序列化       │  ║
║                          │    追加到 session   │  ║
║                          └─────────┬──────────┘  ║
║                                    │              ║
║                          回到循环顶部 ────────────╯║
╚═══════════════════════════════════════════════════╝
```

### 4. Claude API 调用流程

```
session.to_openai_messages()          ← OpenAI 格式消息列表
        │
        ▼
ClaudeClient._convert_messages()      ← 转换为 Claude 格式
  • system 消息 → 顶层 system 字段
  • assistant.tool_calls → tool_use content blocks
  • tool results → user 角色的 tool_result blocks
  • 合并连续同角色消息（Claude 要求严格交替）
        │
        ▼
ClaudeClient._convert_tools()         ← 工具定义转换
  • parameters → input_schema
        │
        ▼
HTTP POST {base_url}/v1/messages      ← 原始 urllib 请求
  Headers: x-api-key, anthropic-version: 2023-06-01
  Body: { model, max_tokens, system, messages, tools, stream }
        │
        ▼
SSE 流式响应解析
  event: message_start      → 提取 input_tokens
  event: content_block_start → 识别 text / tool_use
  event: content_block_delta → 文本增量 / 工具参数增量
  event: message_delta      → stop_reason + output_tokens
        │
        ▼
StreamEvent → agent_runtime 处理
```

### 5. 工具执行流程

```
AssistantTurn.tool_calls
        │
        ▼
对每个 ToolCall:
  ├── 预算检查（tool_calls 计数）
  ├── 插件 preflight hook
  ├── 权限检查（write/shell/unsafe）
  ├── 分发执行：
  │   ├── Agent/delegate_agent → 创建子代理
  │   ├── Skill → 加载并执行技能
  │   └── 其他 → execute_tool_streaming()
  ├── 序列化结果 → ToolExecutionResult
  ├── 插件 result hook
  └── 追加 tool result 消息到 session
```

---

## 核心模块说明

### `src/main.py` — 入口点
- 命令行参数解析（argparse）
- 构建 `ModelConfig` 和 `AgentRuntimeConfig`
- REPL 对话循环
- 会话恢复逻辑

### `src/agent_runtime.py` — 代理运行时（核心）
- `LocalCodingAgent` 类：整个系统的编排中心
- `run(prompt)` → 创建新会话并执行
- `resume(prompt, stored_session)` → 恢复已有会话
- `_run_prompt()` → 核心 Agent 循环（模型调用 + 工具执行）
- `_query_model()` → 调用 Claude API
- `_check_budget()` → 多维度预算检查
- 上下文压缩调度

### `src/claude_client.py` — Claude API 客户端
- 原始 urllib HTTP 请求（无 SDK 依赖）
- OpenAI 格式 → Claude 格式的消息转换
- SSE 流式响应解析
- stop_reason 映射（`end_turn` → `stop`，`tool_use` → `tool_calls`）

### `src/agent_session.py` — 会话状态
- `AgentMessage`：单条消息（role, content, tool_calls, usage）
- `AgentSessionState`：完整会话（system prompt + 消息列表）
- 消息序列化/反序列化
- 流式消息增量更新

### `src/agent_tools.py` — 工具系统
- `AgentTool`：工具定义（name, description, parameters, handler）
- `ToolExecutionContext`：执行上下文（权限、超时、运行时引用）
- `execute_tool_streaming()`：流式工具执行
- 33 个内置工具

### `src/agent_types.py` — 类型定义
- 所有核心 dataclass 集中定义
- `ModelConfig`、`AgentRuntimeConfig`、`UsageStats` 等

### `src/agent_prompting.py` — 系统提示构建
- 分段构建系统提示词（intro, system, tasks, actions, tools, tone...）
- 动态注入环境信息、插件指引、MCP 指引等

### `src/compact.py` — 对话压缩
- 调用模型生成 9 段结构化摘要
- 替换历史消息为压缩版本
- 保留最近 N 条消息不压缩

### `src/microcompact.py` — 轻量级清理
- 基于时间间隔清理旧工具结果
- 不调用模型，纯规则驱动

### `src/plugin_runtime.py` — 插件系统
- 基于 JSON 清单的插件发现和加载
- 支持工具别名、虚拟工具、Hook 注入

### `src/mcp_runtime.py` — MCP 协议集成
- stdio 传输层
- 工具列表/调用、资源列表/读取

### `src/session_store.py` — 会话持久化
- JSON 格式存储在 `.port_sessions/agent/` 目录
- 支持会话恢复和历史回放

---

## 配置项参考

### 命令行参数完整列表

#### 模型配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--model` | 模型名称 | `claude-sonnet-4-20250514` |
| `--base-url` | API 端点 | `https://api.anthropic.com` |
| `--api-key` | API 密钥 | - |
| `--temperature` | 采样温度 | `0.0` |
| `--max-tokens` | 单次最大输出 token | `8192` |
| `--timeout-seconds` | HTTP 请求超时 | `120.0` |
| `--input-cost-per-million` | 输入定价（$/M tokens） | `0.0` |
| `--output-cost-per-million` | 输出定价（$/M tokens） | `0.0` |

#### 运行时配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--cwd` | 工作目录 | `.` |
| `--add-dir` | 额外工作目录（可重复） | - |
| `--max-turns` | Agent 循环最大轮数 | `12` |
| `--stream` | 启用流式输出 | `false` |
| `--disable-claude-md` | 禁用 CLAUDE.md 发现 | `false` |
| `--scratchpad-root` | Scratchpad 目录 | `.port_sessions/scratchpad` |

#### 权限控制

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--allow-write` | 允许文件写入（write_file, edit_file） | `false` |
| `--allow-shell` | 允许执行 shell 命令（bash 工具） | `false` |
| `--unsafe` | 允许破坏性命令（rm -rf 等） | `false` |

#### 预算限制

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--max-total-tokens` | 总 token 上限 | 无限制 |
| `--max-input-tokens` | 输入 token 上限 | 无限制 |
| `--max-output-tokens` | 输出 token 上限 | 无限制 |
| `--max-reasoning-tokens` | 推理 token 上限 | 无限制 |
| `--max-budget-usd` | 总费用上限（美元） | 无限制 |
| `--max-tool-calls` | 工具调用次数上限 | 无限制 |
| `--max-delegated-tasks` | 委派子任务上限 | 无限制 |
| `--max-model-calls` | 模型调用次数上限 | 无限制 |
| `--max-session-turns` | 会话总轮数上限 | 无限制 |

#### 上下文压缩

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--auto-snip-threshold` | Snip 触发阈值（token 数） | 无（不自动 snip） |
| `--auto-compact-threshold` | Compact 触发阈值（token 数） | 无（不自动 compact） |
| `--compact-preserve-messages` | 压缩时保留最近消息数 | `4` |

#### 会话管理

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--resume-session-id` | 恢复指定会话 | - |
| `--show-transcript` | 打印完整对话记录 | `false` |

#### 系统提示

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--system-prompt` | 自定义系统提示（替换默认） | - |
| `--append-system-prompt` | 追加到系统提示末尾 | - |
| `--override-system-prompt` | 完全覆盖系统提示 | - |

#### 结构化输出

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--response-schema-file` | JSON Schema 文件路径 | - |
| `--response-schema-name` | Schema 名称 | 文件名 stem |
| `--response-schema-strict` | 启用严格模式 | `false` |

### 环境变量

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_API_KEY` | API 密钥 |
| `ANTHROPIC_BASE_URL` | API 端点 |
| `ANTHROPIC_MODEL` | 默认模型名 |
| `CODE_AGENT_TOKENIZER_PATH` | 自定义 tokenizer 路径 |
| `CODE_AGENT_TOKENIZER_MODEL` | tokenizer 模型名 |
| `CODE_AGENT_SEARCH_PROVIDER` | 默认搜索提供商 |
| `CODE_AGENT_ASK_USER_INTERACTIVE` | 启用交互式提问（1/true） |

---

## 内置工具列表

系统通过 `agent_tools.py` 中的 `default_tool_registry()` 注册所有内置工具。每个工具是一个 `AgentTool` 实例，包含名称、描述、参数 JSON Schema 和执行处理函数。

### 文件操作

| 工具名 | 说明 |
|--------|------|
| `list_dir` | 列出工作区目录下的文件和子目录 |
| `read_file` | 读取工作区内 UTF-8 文本文件内容 |
| `write_file` | 在工作区内写入完整文件（需 `--allow-write`） |
| `edit_file` | 通过精确字符串匹配替换文件内容（需 `--allow-write`） |
| `notebook_edit` | 编辑 Jupyter Notebook (.ipynb) 单元格 |
| `glob_search` | 按 glob 模式搜索工作区文件 |
| `grep_search` | 按正则表达式搜索工作区文件内容 |

### Shell 与执行

| 工具名 | 说明 |
|--------|------|
| `bash` | 在工作区执行 shell 命令（需 `--allow-shell`） |
| `sleep` | 短暂暂停执行，用于有界等待场景 |

### 代码智能

| 工具名 | 说明 |
|--------|------|
| `LSP` | 本地 LSP 代码智能（定义跳转、引用查找、悬停、符号搜索、调用层次等） |

### Web 与搜索

| 工具名 | 说明 |
|--------|------|
| `web_fetch` | 从 http/https/file URL 获取文本资源 |
| `web_search` | 通过配置的搜索后端执行网络搜索 |
| `search_status` | 显示搜索运行时摘要或指定搜索提供商状态 |
| `search_list_providers` | 列出已配置的搜索提供商 |
| `search_activate_provider` | 设置活跃的搜索提供商 |
| `tool_search` | 按名称或描述搜索工具注册表 |

### 用户交互

| 工具名 | 说明 |
|--------|------|
| `ask_user_question` | 向用户请求输入（交互模式下） |

### 账户管理

| 工具名 | 说明 |
|--------|------|
| `account_status` | 显示账户运行时摘要 |
| `account_list_profiles` | 列出已配置的账户配置文件 |
| `account_login` | 激活账户配置文件或临时身份 |
| `account_logout` | 清除活跃的账户会话状态 |

### 配置管理

| 工具名 | 说明 |
|--------|------|
| `config_list` | 列出工作区配置键 |
| `config_get` | 按点分路径读取配置值 |
| `config_set` | 按点分路径写入配置值 |

### MCP 协议

| 工具名 | 说明 |
|--------|------|
| `mcp_list_resources` | 列出工作区 MCP 清单中发现的资源 |
| `mcp_read_resource` | 按 URI 读取 MCP 资源 |
| `mcp_list_tools` | 列出 MCP 服务器暴露的工具 |
| `mcp_call_tool` | 调用 MCP 服务器暴露的工具 |

### 远程连接

| 工具名 | 说明 |
|--------|------|
| `remote_status` | 显示远程运行时摘要 |
| `remote_list_profiles` | 列出已配置的远程配置文件 |
| `remote_connect` | 激活远程目标连接 |
| `remote_disconnect` | 断开活跃的远程连接 |
| `remote_trigger` | 管理远程触发器（列出/创建/运行） |

### Git Worktree

| 工具名 | 说明 |
|--------|------|
| `worktree_status` | 显示当前 worktree 会话状态 |
| `worktree_enter` | 创建隔离的 git worktree 并切换进入 |
| `worktree_exit` | 离开 worktree 会话，可选删除 |

### 工作流

| 工具名 | 说明 |
|--------|------|
| `workflow_list` | 列出工作区工作流定义 |
| `workflow_get` | 按名称显示工作流定义 |
| `workflow_run` | 记录并渲染工作流执行请求 |

### 计划与任务

| 工具名 | 说明 |
|--------|------|
| `plan_get` | 显示当前运行时计划 |
| `update_plan` | 替换当前计划为结构化多步骤计划 |
| `plan_clear` | 清除当前计划 |
| `todo_write` | 替换当前任务列表 |
| `task_next` | 显示下一个可执行任务 |
| `task_list` | 列出所有任务 |
| `task_get` | 按 ID 显示任务详情 |
| `task_create` | 创建新任务 |
| `task_update` | 更新任务 |
| `task_start` | 标记任务为进行中 |
| `task_complete` | 标记任务为已完成 |
| `task_block` | 标记任务为阻塞 |
| `task_cancel` | 取消任务 |

### 协作与团队

| 工具名 | 说明 |
|--------|------|
| `team_list` | 列出协作团队 |
| `team_get` | 按名称显示团队详情 |
| `team_create` | 创建协作团队 |
| `team_delete` | 删除团队及其消息记录 |
| `send_message` | 向团队或成员发送消息 |
| `team_messages` | 显示团队消息记录 |

### 代理与委派

| 工具名 | 说明 |
|--------|------|
| `Agent` | 启动子代理处理复杂任务（支持类型选择、模型覆盖、后台执行、worktree 隔离） |
| `delegate_agent` | （旧版）委派子任务给嵌套代理 |
| `Skill` | 在主对话中执行技能（技能提供专业化能力） |

### 模式控制

| 工具名 | 说明 |
|--------|------|
| `EnterPlanMode` | 进入计划模式（专注于探索和制定计划） |
| `ExitPlanMode` | 退出计划模式，返回正常执行 |
| `TaskOutput` | 获取后台任务的输出 |
| `TaskStop` | 停止运行中的后台任务 |

### 工具执行流程

```
工具调用请求
    │
    ▼
┌─────────────────────────────┐
│ 1. 预算检查                  │  tool_calls 计数是否超限
└──────────────┬──────────────┘
               │
    ▼
┌─────────────────────────────┐
│ 2. 插件 preflight hook       │  插件可注入前置指令或阻止执行
└──────────────┬──────────────┘
               │
    ▼
┌─────────────────────────────┐
│ 3. 权限检查                  │  write_file → allow_write
│                              │  bash → allow_shell
│                              │  rm -rf → unsafe
└──────────────┬──────────────┘
               │
    ▼
┌─────────────────────────────┐
│ 4. 分发执行                  │
│   ├── Agent → 创建子代理     │
│   ├── Skill → 加载技能       │
│   └── 其他 → execute_tool()  │
└──────────────┬──────────────┘
               │
    ▼
┌─────────────────────────────┐
│ 5. 结果序列化                │  serialize_tool_result()
│    → ToolExecutionResult     │
└──────────────┬──────────────┘
               │
    ▼
┌─────────────────────────────┐
│ 6. 插件 result hook          │  插件可修改或注释结果
└─────────────────────────────┘
```

---

## 插件系统

插件系统通过 `plugin_runtime.py` 实现，基于 JSON 清单文件进行插件发现、加载和管理。

### 插件发现

系统从工作目录向上逐级搜索以下路径：

```
.code-agent-plugin/plugin.json    ← 单插件目录
plugins/*/plugin.json              ← 多插件目录
```

### 清单格式

每个插件通过一个 `plugin.json` 文件描述：

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "示例插件",
  "tool_aliases": [
    {
      "original": "bash",
      "alias": "run_command",
      "description": "执行系统命令"
    }
  ],
  "virtual_tools": [
    {
      "name": "greet",
      "description": "生成问候语",
      "parameters": { "type": "object", "properties": { "name": { "type": "string" } } },
      "response_template": "Hello, {{name}}!"
    }
  ],
  "tool_hooks": [
    {
      "tool_name": "bash",
      "before_tool": "请确认命令安全性",
      "after_result": "检查输出是否包含错误"
    }
  ],
  "blocked_tools": ["write_file"],
  "before_prompt": "你是一个专注于代码审查的助手",
  "after_turn": "每轮结束后总结关键发现",
  "on_resume": "会话恢复时的初始化指令"
}
```

### 插件能力

| 能力 | 说明 |
|------|------|
| **工具别名** | 为已有工具创建替代名称和自定义描述 |
| **虚拟工具** | 创建合成工具，使用模板生成响应 |
| **工具钩子** | 拦截工具执行：前置指令、后置处理、阻止执行 |
| **工具屏蔽** | 阻止特定工具被调用 |
| **生命周期注入** | 在对话各阶段注入自定义指令 |

### 生命周期钩子

```
会话开始
    │
    ├── before_prompt      ← 注入到系统提示之前
    │
    ▼
每轮对话
    │
    ├── tool preflight     ← 工具执行前检查
    ├── tool result        ← 工具结果后处理
    ├── after_turn         ← 每轮结束后注入
    │
    ▼
会话恢复
    │
    ├── on_resume          ← 恢复时初始化
    │
    ▼
会话持久化
    │
    ├── before_persist     ← 持久化前处理
    │
    ▼
代理委派
    │
    ├── before_delegate    ← 委派前注入
    └── after_delegate     ← 委派后注入
```

### PluginRuntime 核心方法

```python
class PluginRuntime:
    @classmethod
    def from_workspace(cls, cwd: Path) -> PluginRuntime
        """从工作区发现并加载所有插件"""

    def instruction_blocks(self) -> list[str]
        """生成所有插件的指令块"""

    def blocked_tool_message(self, tool_name: str) -> str | None
        """检查工具是否被屏蔽，返回屏蔽消息"""

    def tool_preflight_injections(self, tool_name: str) -> list[str]
        """获取工具执行前的注入指令"""

    def tool_result_injections(self, tool_name: str) -> list[str]
        """获取工具结果后的注入指令"""

    def export_session_state(self) -> dict
        """导出插件运行时状态（用于会话持久化）"""

    def restore_session_state(self, state: dict) -> None
        """恢复插件运行时状态"""
```

---

## 上下文压缩机制

当对话历史过长时，系统通过三级递进策略管理上下文窗口，确保不超出模型的 token 限制。

### 三级压缩策略

```
Token 使用量增长
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Level 1: Microcompact（轻量清理）                         │
│                                                          │
│ 触发条件：距上次助手消息超过 60 分钟                       │
│ 原理：服务端 prompt cache 已过期，旧工具结果无缓存价值     │
│ 操作：清除旧的工具结果内容，替换为占位标记                 │
│ 成本：零（纯规则驱动，不调用模型）                        │
└──────────────────────────┬──────────────────────────────┘
                           │ 仍然超限
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Level 2: Snip（自动裁剪）                                │
│                                                          │
│ 触发条件：token 数超过 auto_snip_threshold               │
│ 操作：移除最早的消息对（保留最近 N 条）                   │
│ 成本：零（不调用模型）                                   │
└──────────────────────────┬──────────────────────────────┘
                           │ 仍然超限
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Level 3: Compact（完整压缩）                             │
│                                                          │
│ 触发条件：token 数超过 auto_compact_threshold            │
│ 操作：调用模型生成 9 段结构化摘要，替换历史消息           │
│ 成本：一次模型调用                                       │
└─────────────────────────────────────────────────────────┘
```

### Microcompact 详解

```python
# 可被清理的工具列表
COMPACTABLE_TOOLS = {
    'read_file', 'bash', 'grep_search', 'glob_search',
    'web_search', 'web_fetch', 'edit_file', 'write_file',
}

# 默认参数
DEFAULT_GAP_THRESHOLD_MINUTES = 60.0   # 触发间隔
DEFAULT_KEEP_RECENT = 3                 # 保留最近 N 个工具结果
```

执行逻辑：
1. 计算距上次助手消息的时间间隔
2. 若间隔 < 阈值，不触发
3. 收集所有可清理工具的结果（按时间排序）
4. 保留最近 3 个不动
5. 将更早的工具结果内容替换为 `'[Old tool result content cleared]'`

### Compact 详解

完整压缩调用模型生成结构化摘要，包含 9 个段落：

| 段落 | 内容 |
|------|------|
| 1. Primary Request and Intent | 用户的所有显式请求和意图 |
| 2. Key Technical Concepts | 讨论中涉及的技术概念 |
| 3. Files and Code Sections | 查看/修改/创建的文件及代码片段 |
| 4. Errors and fixes | 遇到的错误及修复方式 |
| 5. Problem Solving | 已解决的问题和进行中的排查 |
| 6. All user messages | 所有用户消息（用于理解反馈和意图变化） |
| 7. Pending Tasks | 待完成的任务 |
| 8. Current Work | 压缩前正在进行的工作 |
| 9. Optional Next Step | 下一步建议（附用户原始引用） |

压缩后的会话结构：

```
┌─────────────────────────────┐
│ [compact_boundary] 标记      │  ← 标识压缩边界
├─────────────────────────────┤
│ 结构化摘要消息               │  ← 9 段摘要替代所有历史
├─────────────────────────────┤
│ 保留的最近 N 条消息          │  ← compact_preserve_messages
└─────────────────────────────┘
```

### 容错机制

- **Prompt Too Long 重试**：若压缩请求本身超限，逐步丢弃最早的 API 轮次组（最多重试 3 次）
- **连续失败熔断**：连续 3 次压缩失败后停止尝试
- **Session Memory 优先**：优先使用免费的 session-memory 压缩，失败时回退到 LLM 压缩

---

## 会话持久化

会话系统通过 `session_store.py` 实现，将完整的对话状态序列化为 JSON 文件，支持跨进程恢复。

### 存储位置

```
.port_sessions/
└── agent/
    ├── a1b2c3d4-e5f6-7890-abcd-ef1234567890.json
    ├── f9e8d7c6-b5a4-3210-fedc-ba0987654321.json
    └── ...
```

每个会话一个 JSON 文件，文件名为会话 UUID。

### 会话数据结构

```python
@dataclass(frozen=True)
class StoredAgentSession:
    session_id: str                    # UUID 格式的唯一标识
    model_config: dict                 # 模型配置快照
    runtime_config: dict               # 运行时配置快照
    system_prompt_parts: tuple[str]    # 系统提示各段落
    user_context: dict[str, str]       # 用户上下文数据
    system_context: dict[str, str]     # 系统上下文数据
    messages: tuple[dict, ...]         # 完整对话消息列表
    turns: int                         # 对话轮次数
    tool_calls: int                    # 工具调用总次数
    usage: dict                        # Token 用量统计
    total_cost_usd: float              # 累计费用（美元）
    file_history: tuple[dict, ...]     # 文件编辑历史
    budget_state: dict                 # 预算跟踪状态
    plugin_state: dict                 # 插件运行时状态
    scratchpad_directory: str | None   # Scratchpad 目录路径
```

### 保存与恢复流程

```
保存流程：
agent.run(prompt)
    │
    ▼
执行完成 → AgentRunResult
    │
    ▼
serialize_model_config() + serialize_runtime_config()
    │
    ▼
构建 StoredAgentSession
    │
    ▼
save_agent_session(session, directory)
    │
    ▼
写入 {session_id}.json

恢复流程：
用户输入 --resume-session-id <id>
    │
    ▼
load_agent_session(session_id, directory)
    │
    ▼
解析 JSON → StoredAgentSession
    │
    ▼
agent.resume(prompt, stored_session)
    │
    ▼
重建会话状态 → 继续对话
```

### 序列化内容

| 组件 | 序列化内容 |
|------|-----------|
| **模型配置** | model, base_url, api_key, temperature, timeout_seconds, pricing |
| **运行时配置** | cwd, max_turns, permissions, stream, compression thresholds, budget |
| **消息列表** | 每条消息的 role, content, tool_calls, usage, timestamp |
| **预算状态** | 已消耗的 token、费用、调用次数 |
| **插件状态** | 插件运行时的内部状态快照 |
| **文件历史** | 所有文件操作的记录（路径、操作类型、时间戳） |

### 使用示例

```bash
# 首次对话（自动生成 session_id）
python3 -m src.main --model "claude-sonnet-4-20250514"
# 输出: session_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890

# 恢复对话（携带完整历史上下文）
python3 -m src.main --resume-session-id a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

恢复后的对话会自动加载之前的系统提示、消息历史、预算状态和插件状态，模型可以无缝继续之前的工作。
