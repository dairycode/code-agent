from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .account_runtime import AccountRuntime
from .agent_manager import AgentManager
from .agent_context import clear_context_caches
from .agent_context_usage import collect_context_usage, estimate_tokens
from .compact import compact_conversation
from .ask_user_runtime import AskUserRuntime
from .agent_registry import (
    find_agent_definition,
    load_agent_registry,
)
from .config_runtime import ConfigRuntime
from .hook_policy import HookPolicyRuntime
from .lsp_runtime import LSPRuntime
from .mcp_runtime import MCPRuntime
from .agent_prompting import (
    build_prompt_context,
    build_system_prompt_parts,
)
from .agent_session import AgentSessionState
from .agent_slash_commands import preprocess_slash_command
from .agent_tools import (
    AgentTool,
    build_tool_context,
    default_tool_registry,
    execute_tool_streaming,
    serialize_tool_result,
)
from .agent_types import (
    AgentRunResult,
    AgentPermissions,
    AgentRuntimeConfig,
    AssistantTurn,
    BudgetConfig,
    ModelConfig,
    StreamEvent,
    ToolCall,
    ToolExecutionResult,
    UsageStats,
)
from .claude_client import ClaudeClient, ClaudeAPIError
from .plan_runtime import PlanRuntime
from .plugin_runtime import PluginRuntime
from .remote_runtime import RemoteRuntime
from .remote_trigger_runtime import RemoteTriggerRuntime
from .search_runtime import SearchRuntime
from .task_runtime import TaskRuntime
from .team_runtime import TeamRuntime
from .workflow_runtime import WorkflowRuntime
from .worktree_runtime import WorktreeRuntime
from .session_store import (
    StoredAgentSession,
    load_agent_session,
    save_agent_session,
    serialize_model_config,
    serialize_runtime_config,
    usage_from_payload,
)
from .token_budget import calculate_token_budget
from .builtin_agents import (
    AgentDefinition,
    ALL_AGENT_DISALLOWED_TOOLS,
    GENERAL_PURPOSE_AGENT,
)
from .microcompact import microcompact_messages as _microcompact_messages


@dataclass(frozen=True)
class BudgetDecision:
    exceeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class PromptPreflightResult:
    usage_increment: UsageStats = field(default_factory=UsageStats)
    model_calls_increment: int = 0
    stop_reason: str | None = None
    reason: str | None = None


@dataclass
class LocalCodingAgent:
    model_config: ModelConfig
    runtime_config: AgentRuntimeConfig
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    override_system_prompt: str | None = None
    tool_registry: dict[str, AgentTool] | None = None
    agent_manager: AgentManager | None = None
    parent_agent_id: str | None = None
    managed_group_id: str | None = None
    managed_child_index: int | None = None
    managed_label: str | None = None
    plugin_runtime: PluginRuntime | None = None
    hook_policy_runtime: HookPolicyRuntime | None = None
    mcp_runtime: MCPRuntime | None = None
    remote_runtime: RemoteRuntime | None = None
    remote_trigger_runtime: RemoteTriggerRuntime | None = None
    search_runtime: SearchRuntime | None = None
    account_runtime: AccountRuntime | None = None
    ask_user_runtime: AskUserRuntime | None = None
    config_runtime: ConfigRuntime | None = None
    lsp_runtime: LSPRuntime | None = None
    plan_runtime: PlanRuntime | None = None
    task_runtime: TaskRuntime | None = None
    team_runtime: TeamRuntime | None = None
    workflow_runtime: WorkflowRuntime | None = None
    worktree_runtime: WorktreeRuntime | None = None
    last_session: AgentSessionState | None = field(default=None, init=False, repr=False)
    last_run_result: AgentRunResult | None = field(default=None, init=False, repr=False)
    cumulative_usage: UsageStats = field(default_factory=UsageStats, init=False, repr=False)
    cumulative_cost_usd: float = field(default=0.0, init=False, repr=False)
    _compact_consecutive_failures: int = field(default=0, init=False, repr=False)
    active_session_id: str | None = field(default=None, init=False, repr=False)
    last_session_path: str | None = field(default=None, init=False, repr=False)
    managed_agent_id: str | None = field(default=None, init=False, repr=False)
    resume_source_session_id: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """初始化所有 runtime 子系统（插件、MCP、远程、搜索等），注册工具，创建 API 客户端。"""
        if self.tool_registry is None:
            self.tool_registry = default_tool_registry()
        if self.agent_manager is None:
            self.agent_manager = AgentManager()
        if self.plugin_runtime is None:
            self.plugin_runtime = PluginRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.hook_policy_runtime is None:
            self.hook_policy_runtime = HookPolicyRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.mcp_runtime is None:
            self.mcp_runtime = MCPRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.remote_runtime is None:
            self.remote_runtime = RemoteRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.remote_trigger_runtime is None:
            self.remote_trigger_runtime = RemoteTriggerRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.search_runtime is None:
            self.search_runtime = SearchRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.account_runtime is None:
            self.account_runtime = AccountRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.ask_user_runtime is None:
            self.ask_user_runtime = AskUserRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.config_runtime is None:
            self.config_runtime = ConfigRuntime.from_workspace(self.runtime_config.cwd)
        if self.lsp_runtime is None:
            self.lsp_runtime = LSPRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.plan_runtime is None:
            self.plan_runtime = PlanRuntime.from_workspace(self.runtime_config.cwd)
        if self.task_runtime is None:
            self.task_runtime = TaskRuntime.from_workspace(self.runtime_config.cwd)
        if self.team_runtime is None:
            self.team_runtime = TeamRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.workflow_runtime is None:
            self.workflow_runtime = WorkflowRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.worktree_runtime is None:
            self.worktree_runtime = WorktreeRuntime.from_workspace(self.runtime_config.cwd)
        self.runtime_config = self._apply_hook_policy_budget_overrides(self.runtime_config)
        registry = dict(self.tool_registry)
        plugin_tools = self.plugin_runtime.register_tool_aliases(registry)
        if plugin_tools:
            registry = {**registry, **plugin_tools}
        virtual_tools = self.plugin_runtime.register_virtual_tools(registry)
        if virtual_tools:
            registry = {**registry, **virtual_tools}
        self.tool_registry = registry
        self.client = ClaudeClient(self.model_config)
        self.tool_context = build_tool_context(
            self.runtime_config,
            tool_registry=self.tool_registry,
            extra_env=(
                self.hook_policy_runtime.safe_env()
                if self.hook_policy_runtime is not None
                else None
            ),
            search_runtime=self.search_runtime,
            account_runtime=self.account_runtime,
            ask_user_runtime=self.ask_user_runtime,
            config_runtime=self.config_runtime,
            lsp_runtime=self.lsp_runtime,
            mcp_runtime=self.mcp_runtime,
            remote_runtime=self.remote_runtime,
            remote_trigger_runtime=self.remote_trigger_runtime,
            plan_runtime=self.plan_runtime,
            task_runtime=self.task_runtime,
            team_runtime=self.team_runtime,
            workflow_runtime=self.workflow_runtime,
            worktree_runtime=self.worktree_runtime,
        )

    def build_prompt_context(self, scratchpad_directory: Path | None = None):
        """构建 system prompt 所需的上下文信息（环境、git 状态、用户配置等）。"""
        return build_prompt_context(
            self.runtime_config,
            self.model_config,
            scratchpad_directory=scratchpad_directory,
        )

    def build_system_prompt_parts(self, prompt_context=None) -> list[str]:
        """组装完整的 system prompt 各段落（角色说明、工具文档、agent 列表等）。"""
        if prompt_context is None:
            prompt_context = self.build_prompt_context()
        return build_system_prompt_parts(
            prompt_context=prompt_context,
            runtime_config=self.runtime_config,
            tools=self.tool_registry,
            available_agents=self.available_agents(),
            custom_system_prompt=self.custom_system_prompt,
            append_system_prompt=self.append_system_prompt,
            override_system_prompt=self.override_system_prompt,
        )

    def load_agent_registry(self):
        """从工作目录加载 agent 注册表（内置 + 用户自定义 agent 定义）。"""
        return load_agent_registry(self.runtime_config.cwd)

    def available_agents(self) -> tuple[AgentDefinition, ...]:
        """返回当前可用的所有 agent 定义（用于 Agent 工具的子类型选择）。"""
        return self.load_agent_registry().active_agents

    def build_session(
        self,
        user_prompt: str | None = None,
        *,
        scratchpad_directory: Path | None = None,
    ) -> AgentSessionState:
        """构建一个全新的会话状态：生成 system prompt，初始化空消息列表。"""
        prompt_context = self.build_prompt_context(scratchpad_directory)
        system_prompt_parts = self.build_system_prompt_parts(prompt_context)
        return AgentSessionState.create(
            system_prompt_parts,
            user_prompt,
            user_context=prompt_context.user_context,
            system_context=prompt_context.system_context,
        )

    def _apply_hook_policy_budget_overrides(
        self,
        runtime_config: AgentRuntimeConfig,
    ) -> AgentRuntimeConfig:
        """如果 hook 策略文件定义了预算限制，将其合并到 runtime 配置中。"""
        if self.hook_policy_runtime is None or not self.hook_policy_runtime.manifests:
            return runtime_config
        overrides = self.hook_policy_runtime.budget_overrides()
        if not overrides:
            return runtime_config
        budget = runtime_config.budget_config
        return replace(
            runtime_config,
            budget_config=BudgetConfig(
                max_total_tokens=(
                    budget.max_total_tokens
                    if budget.max_total_tokens is not None
                    else _optional_policy_int(overrides.get('max_total_tokens'))
                ),
                max_input_tokens=(
                    budget.max_input_tokens
                    if budget.max_input_tokens is not None
                    else _optional_policy_int(overrides.get('max_input_tokens'))
                ),
                max_output_tokens=(
                    budget.max_output_tokens
                    if budget.max_output_tokens is not None
                    else _optional_policy_int(overrides.get('max_output_tokens'))
                ),
                max_reasoning_tokens=(
                    budget.max_reasoning_tokens
                    if budget.max_reasoning_tokens is not None
                    else _optional_policy_int(overrides.get('max_reasoning_tokens'))
                ),
                max_total_cost_usd=(
                    budget.max_total_cost_usd
                    if budget.max_total_cost_usd is not None
                    else _optional_policy_float(overrides.get('max_total_cost_usd'))
                ),
                max_tool_calls=(
                    budget.max_tool_calls
                    if budget.max_tool_calls is not None
                    else _optional_policy_int(overrides.get('max_tool_calls'))
                ),
                max_delegated_tasks=(
                    budget.max_delegated_tasks
                    if budget.max_delegated_tasks is not None
                    else _optional_policy_int(overrides.get('max_delegated_tasks'))
                ),
                max_model_calls=(
                    budget.max_model_calls
                    if budget.max_model_calls is not None
                    else _optional_policy_int(overrides.get('max_model_calls'))
                ),
                max_session_turns=(
                    budget.max_session_turns
                    if budget.max_session_turns is not None
                    else _optional_policy_int(overrides.get('max_session_turns'))
                ),
            ),
        )

    def run(self, prompt: str) -> AgentRunResult:
        """
        全新会话入口：重置状态 → 生成新 session_id → 创建 scratchpad → 进入 _run_prompt 循环。
        """
        self.managed_agent_id = None
        self.resume_source_session_id = None
        if self.plugin_runtime is not None:
            self.plugin_runtime.restore_session_state({})  # 清空插件会话状态
        session_id = uuid4().hex  # 每次 run 生成全新的会话 ID
        scratchpad_directory = self._ensure_scratchpad_directory(session_id)
        result = self._run_prompt(
            prompt,
            base_session=None,  # None 表示从零开始，不携带历史
            session_id=session_id,
            scratchpad_directory=scratchpad_directory,
            existing_file_history=(),
        )
        self._accumulate_usage(result)
        self._finalize_managed_agent(result)
        return result

    def resume(self, prompt: str, stored_session: StoredAgentSession) -> AgentRunResult:
        """
        恢复已有会话入口：从磁盘反序列化历史消息 → 回放文件操作和压缩记录 → 恢复插件状态 → 进入 _run_prompt 循环。
        """
        self.managed_agent_id = None
        self.resume_source_session_id = stored_session.session_id
        # 从序列化数据重建会话状态（system prompt + 历史消息）
        session = AgentSessionState.from_persisted(
            system_prompt_parts=stored_session.system_prompt_parts,
            user_context=stored_session.user_context,
            system_context=stored_session.system_context,
            messages=stored_session.messages,
        )
        # 回放文件操作历史，让 Agent 知道之前修改过哪些文件
        self._append_file_history_replay_if_needed(
            session,
            stored_session.file_history,
        )
        # 如果历史消息曾被压缩过，追加压缩标记以保持一致性
        self._append_compaction_replay_if_needed(session)
        self.active_session_id = stored_session.session_id
        self.last_session = session
        self.last_session_path = str(
            self.runtime_config.session_directory / f'{stored_session.session_id}.json'
        )
        if self.plugin_runtime is not None:
            self.plugin_runtime.restore_session_state(stored_session.plugin_state)  # 恢复插件状态
        scratchpad_directory = (
            Path(stored_session.scratchpad_directory)
            if stored_session.scratchpad_directory
            else self._ensure_scratchpad_directory(stored_session.session_id)
        )
        result = self._run_prompt(
            prompt,
            base_session=session,  # 非 None，携带完整历史上下文进入循环
            session_id=stored_session.session_id,
            scratchpad_directory=scratchpad_directory,
            existing_file_history=stored_session.file_history,
        )
        self._accumulate_usage(result)
        self._finalize_managed_agent(result)
        return result

    def _run_prompt(
        self,
        prompt: str,
        *,
        base_session: AgentSessionState | None,
        session_id: str,
        scratchpad_directory: Path | None,
        existing_file_history: tuple[dict[str, object], ...],
    ) -> AgentRunResult:
        """
        Agent 核心循环：预处理 prompt → 构建/复用 session → 多轮"调模型→执行工具"循环。
        每轮开始前执行上下文压缩（microcompact/snip/compact），防止撑爆上下文窗口。
        当模型返回纯文本（无 tool_calls）时结束，或触及预算/轮数上限时强制停止。
        """
        # ── 阶段 1：斜杠命令预处理（/exit、/clear 等无需调模型的命令）──
        slash_result = preprocess_slash_command(self, prompt)
        if slash_result.handled and not slash_result.should_query:
            return AgentRunResult(
                final_output=slash_result.output,
                turns=0,
                tool_calls=0,
                transcript=slash_result.transcript,
                session_id=self.active_session_id,
                session_path=self.last_session_path,
                scratchpad_directory=(
                    str(scratchpad_directory) if scratchpad_directory is not None else None
                ),
            )

        # ── 阶段 2：Hook 链处理 prompt（插件/策略可修改或增强 prompt）──
        effective_prompt = self._apply_hook_policy_before_prompt_hooks(
            slash_result.prompt or prompt
        )
        effective_prompt = self._apply_plugin_before_prompt_hooks(effective_prompt)
        effective_prompt = self._apply_plugin_resume_hooks(
            effective_prompt,
            resumed=base_session is not None,
        )
        # ── 阶段 3：向 AgentManager 注册本次运行 ──
        self.managed_agent_id = self.agent_manager.start_agent(
            prompt=effective_prompt,
            parent_agent_id=self.parent_agent_id,
            group_id=self.managed_group_id,
            child_index=self.managed_child_index,
            label=self.managed_label or ('root' if base_session is None else 'resume'),
            resumed_from_session_id=self.resume_source_session_id,
        )
        # ── 阶段 4：构建或复用 session，追加用户消息 ──
        session = (
            base_session  # resume 路径：复用已有 session（含历史消息）
            if base_session is not None
            else self.build_session(  # run 路径：构建全新 session（仅含 system prompt）
                None,
                scratchpad_directory=scratchpad_directory,
            )
        )
        session.append_user(effective_prompt)  # 将本轮用户 prompt 追加到消息列表
        self.last_session = session
        self.active_session_id = session_id
        tool_specs = [tool.to_openai_tool() for tool in self.tool_registry.values()]  # 将注册的工具转为 OpenAI function calling 格式

        # ── 阶段 5：初始化计数器（resume 时从上次会话的统计继续累加）──
        starting_usage = UsageStats()
        starting_cost_usd = 0.0
        starting_tool_calls = 0
        starting_session_turns = 0
        starting_model_calls = 0
        if base_session is not None and self.resume_source_session_id:
            try:
                stored_resume_state = load_agent_session(
                    self.resume_source_session_id,
                    directory=self.runtime_config.session_directory,
                )
            except OSError:
                stored_resume_state = None
            if stored_resume_state is not None:
                starting_usage = usage_from_payload(stored_resume_state.usage)
                starting_cost_usd = stored_resume_state.total_cost_usd
                starting_tool_calls = stored_resume_state.tool_calls
                starting_session_turns = stored_resume_state.turns
                budget_state = (
                    stored_resume_state.budget_state
                    if isinstance(stored_resume_state.budget_state, dict)
                    else {}
                )
                starting_model_calls = int(budget_state.get('model_calls', 0)) if isinstance(budget_state.get('model_calls', 0), int) else 0
        tool_calls = starting_tool_calls
        last_content = ''
        total_usage = starting_usage
        total_cost_usd = starting_cost_usd
        file_history = list(existing_file_history)
        stream_events: list[dict[str, object]] = []
        assistant_response_segments: list[str] = []
        delegated_tasks = sum(
            1 for entry in file_history if entry.get('action') in ('delegate_agent', 'Agent')
        )
        model_calls = starting_model_calls

        # ── 阶段 6：首次预算检查，如果初始状态已超限则直接返回 ──
        initial_budget = self._check_budget(
            total_usage,
            total_cost_usd,
            tool_calls=tool_calls,
            delegated_tasks=delegated_tasks,
            model_calls=model_calls,
            session_turns=starting_session_turns,
        )
        if initial_budget.exceeded:
            result = AgentRunResult(
                final_output=initial_budget.reason or 'Stopped before the first model call.',
                turns=0,
                tool_calls=0,
                transcript=session.transcript(),
                session_id=session_id,
                usage=total_usage,
                total_cost_usd=total_cost_usd,
                stop_reason='budget_exceeded',
                file_history=tuple(file_history),
                scratchpad_directory=(
                    str(scratchpad_directory) if scratchpad_directory is not None else None
                ),
            )
            result = self._persist_session(session, result)
            self.last_run_result = result
            return result

        # ── 阶段 7：核心 Agent 循环（最多 max_turns 轮）──
        for turn_index in range(1, self.runtime_config.max_turns + 1):
            # 三级递进的上下文压缩，防止消息列表超出模型上下文窗口
            self._microcompact_session_if_needed(  # 第 1 级：轻量清理，移除冗余元数据
                session,
                stream_events,
                turn_index=turn_index,
            )
            self._snip_session_if_needed(  # 第 2 级：截断早期工具输出中的长结果
                session,
                stream_events,
                turn_index=turn_index,
            )
            self._compact_session_if_needed(  # 第 3 级：用模型对历史消息做摘要压缩
                session,
                stream_events,
                turn_index=turn_index,
            )
            # Preflight：检查 prompt 总长度是否超出模型上下文窗口
            preflight = self._preflight_prompt_length(
                session,
                stream_events,
                turn_index=turn_index,
            )
            if preflight.usage_increment.total_tokens or preflight.model_calls_increment:
                total_usage = total_usage + preflight.usage_increment
                total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
                model_calls += preflight.model_calls_increment
                budget_after_preflight = self._check_budget(
                    total_usage,
                    total_cost_usd,
                    tool_calls=tool_calls,
                    delegated_tasks=delegated_tasks,
                    model_calls=model_calls,
                    session_turns=starting_session_turns + turn_index,
                )
                if budget_after_preflight.exceeded:
                    result = AgentRunResult(
                        final_output=(
                            budget_after_preflight.reason
                            or 'Stopped because the runtime budget was exceeded.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='budget_exceeded',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    result = self._persist_session(session, result)
                    self.last_run_result = result
                    return result
            if preflight.stop_reason is not None:
                result = AgentRunResult(
                    final_output=preflight.reason or 'Stopped before the next model call.',
                    turns=max(turn_index - 1, 0),
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason=preflight.stop_reason,
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_runtime_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=max(turn_index - 1, 0),
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result
            # ── 调用模型：发送 session 消息 + 工具定义到 LLM API ──
            try:
                turn, turn_events = self._query_model(session, tool_specs)
            except ClaudeAPIError as exc:
                # 如果是 prompt 过长错误，尝试紧急压缩后重试一次
                if self._is_prompt_too_long_error(exc) and self._reactive_compact_session(
                    session,
                    stream_events,
                    turn_index=turn_index,
                ):
                    try:
                        turn, turn_events = self._query_model(session, tool_specs)
                    except ClaudeAPIError as retry_exc:
                        exc = retry_exc
                    else:
                        stream_events.extend(
                            {
                                'type': 'reactive_compact_retry',
                                'turn_index': turn_index,
                            }
                            for _ in [0]
                        )
                        stream_events.extend(event.to_dict() for event in turn_events)
                        model_calls += 1
                        total_usage = total_usage + turn.usage
                        total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
                        last_content = turn.content

                        budget_after_model = self._check_budget(
                            total_usage,
                            total_cost_usd,
                            tool_calls=tool_calls,
                            delegated_tasks=delegated_tasks,
                            model_calls=model_calls,
                            session_turns=starting_session_turns + turn_index,
                        )
                        if budget_after_model.exceeded:
                            result = AgentRunResult(
                                final_output=(
                                    budget_after_model.reason
                                    or 'Stopped because the runtime budget was exceeded.'
                                ),
                                turns=turn_index,
                                tool_calls=tool_calls,
                                transcript=session.transcript(),
                                events=tuple(stream_events),
                                usage=total_usage,
                                total_cost_usd=total_cost_usd,
                                stop_reason='budget_exceeded',
                                file_history=tuple(file_history),
                                session_id=session_id,
                                scratchpad_directory=(
                                    str(scratchpad_directory) if scratchpad_directory is not None else None
                                ),
                            )
                            result = self._persist_session(session, result)
                            self.last_run_result = result
                            return result

                        if not turn.tool_calls:
                            assistant_response_segments.append(turn.content)
                            if self._should_continue_response(turn):
                                session.append_user(
                                    self._build_continuation_prompt(),
                                    metadata={
                                        'kind': 'continuation_request',
                                        'continuation_index': len(assistant_response_segments),
                                    },
                                    message_id=f'continuation_{turn_index}',
                                )
                                stream_events.append(
                                    {
                                        'type': 'continuation_request',
                                        'reason': turn.finish_reason,
                                        'continuation_index': len(assistant_response_segments),
                                    }
                                )
                                last_content = ''.join(assistant_response_segments)
                                continue
                            result = AgentRunResult(
                                final_output=''.join(assistant_response_segments),
                                turns=turn_index,
                                tool_calls=tool_calls,
                                transcript=session.transcript(),
                                events=tuple(stream_events),
                                usage=total_usage,
                                total_cost_usd=total_cost_usd,
                                stop_reason=turn.finish_reason,
                                file_history=tuple(file_history),
                                session_id=session_id,
                                scratchpad_directory=(
                                    str(scratchpad_directory) if scratchpad_directory is not None else None
                                ),
                            )
                            result = self._persist_session(session, result)
                            self.last_run_result = result
                            return result
                        # fall through to the normal tool-call branch below
                # normal error path if not recovered
                result = AgentRunResult(
                    final_output=str(exc),
                    turns=max(turn_index - 1, 0),
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason='backend_error',
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_runtime_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=turn_index,
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            stream_events.extend(event.to_dict() for event in turn_events)
            model_calls += 1
            total_usage = total_usage + turn.usage
            total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
            last_content = turn.content

            budget_after_model = self._check_budget(
                total_usage,
                total_cost_usd,
                tool_calls=tool_calls,
                delegated_tasks=delegated_tasks,
                model_calls=model_calls,
                session_turns=starting_session_turns + turn_index,
            )
            if budget_after_model.exceeded:
                result = AgentRunResult(
                    final_output=(
                        budget_after_model.reason
                        or 'Stopped because the runtime budget was exceeded.'
                    ),
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason='budget_exceeded',
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            # ── 判断模型是否请求工具调用 ──
            if not turn.tool_calls:
                # 模型返回纯文本，无工具调用
                assistant_response_segments.append(turn.content)
                if self._should_continue_response(turn):
                    # 模型输出被截断（如 max_tokens），注入 continuation prompt 让模型继续
                    session.append_user(
                        self._build_continuation_prompt(),
                        metadata={
                            'kind': 'continuation_request',
                            'continuation_index': len(assistant_response_segments),
                        },
                        message_id=f'continuation_{turn_index}',
                    )
                    stream_events.append(
                        {
                            'type': 'continuation_request',
                            'reason': turn.finish_reason,
                            'continuation_index': len(assistant_response_segments),
                        }
                    )
                    last_content = ''.join(assistant_response_segments)
                    continue  # 继续下一轮让模型接着输出
                # 模型正常结束，拼接所有响应段落作为最终输出
                result = AgentRunResult(
                    final_output=''.join(assistant_response_segments),
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason=turn.finish_reason,
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_runtime_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=turn_index,
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            # ── 模型请求了工具调用，逐个执行 ──
            for tool_call in turn.tool_calls:
                assistant_response_segments.clear()
                tool_calls += 1
                if tool_call.name in ('Agent', 'delegate_agent'):
                    delegated_tasks += self._delegated_task_units(tool_call.arguments)
                # 每次工具调用前检查预算是否超限
                budget_after_tool_request = self._check_budget(
                    total_usage,
                    total_cost_usd,
                    tool_calls=tool_calls,
                    delegated_tasks=delegated_tasks,
                    model_calls=model_calls,
                    session_turns=starting_session_turns + turn_index,
                )
                if budget_after_tool_request.exceeded:
                    stream_events.append(
                        {
                            'type': 'task_budget_exceeded',
                            'turn_index': turn_index,
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'reason': budget_after_tool_request.reason,
                        }
                    )
                    result = AgentRunResult(
                        final_output=(
                            budget_after_tool_request.reason
                            or 'Stopped because the runtime budget was exceeded.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='budget_exceeded',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    result = self._persist_session(session, result)
                    self.last_run_result = result
                    return result
                tool_result = None
                tool_message_index = session.start_tool(
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                    message_id=f'tool_{len(session.messages)}',
                    metadata={'phase': 'starting'},
                )
                stream_events.append(
                    {
                        'type': 'tool_start',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                    }
                )
                if self.plugin_runtime is not None:
                    self.plugin_runtime.record_tool_attempt(tool_call.name, blocked=False)
                plugin_preflight_messages = self._plugin_tool_preflight_messages(tool_call.name)
                policy_preflight_messages = self._hook_policy_tool_preflight_messages(
                    tool_call.name
                )
                if plugin_preflight_messages:
                    stream_events.append(
                        {
                            'type': 'plugin_tool_preflight',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message_count': len(plugin_preflight_messages),
                        }
                    )
                if policy_preflight_messages:
                    stream_events.append(
                        {
                            'type': 'hook_policy_tool_preflight',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message_count': len(policy_preflight_messages),
                        }
                    )
                plugin_block_message = self._plugin_block_message(tool_call.name)
                policy_block_message = self._hook_policy_block_message(tool_call.name)
                if plugin_block_message is not None:
                    if self.plugin_runtime is not None:
                        blocked_attempts = int(
                            self.plugin_runtime.session_state.get('blocked_tool_attempts', 0)
                        )
                        self.plugin_runtime.session_state['blocked_tool_attempts'] = (
                            blocked_attempts + 1
                        )
                    tool_result = ToolExecutionResult(
                        name=tool_call.name,
                        ok=False,
                        content=plugin_block_message,
                        metadata={
                            'action': 'plugin_block',
                            'plugin_blocked': True,
                            'plugin_block_message': plugin_block_message,
                        },
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_block',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message': plugin_block_message,
                        }
                    )
                if policy_block_message is not None:
                    tool_result = ToolExecutionResult(
                        name=tool_call.name,
                        ok=False,
                        content=policy_block_message,
                        metadata={
                            'action': 'hook_policy_block',
                            'hook_policy_blocked': True,
                            'hook_policy_block_message': policy_block_message,
                            'error_kind': 'permission_denied',
                        },
                    )
                    stream_events.append(
                        {
                            'type': 'hook_policy_tool_block',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message': policy_block_message,
                        }
                    )
                # 根据工具类型分发执行
                if tool_call.name in ('Agent', 'delegate_agent'):
                    if tool_result is None:
                        tool_result = self._execute_delegate_agent(tool_call.arguments)  # 委派子 Agent
                elif tool_call.name == 'Skill':
                    if tool_result is None:
                        tool_result = self._execute_skill(tool_call.arguments)  # 执行 Skill
                elif tool_result is None:
                    # 普通工具：通过流式执行器运行（Read/Edit/Bash 等）
                    for update in execute_tool_streaming(
                        self.tool_registry,
                        tool_call.name,
                        tool_call.arguments,
                        self.tool_context,
                    ):
                        if update.kind == 'delta':
                            session.append_tool_delta(
                                tool_message_index,
                                update.content,
                                metadata={'last_stream': update.stream or 'tool'},
                            )
                            stream_events.append(
                                {
                                    'type': 'tool_delta',
                                    'tool_name': tool_call.name,
                                    'tool_call_id': tool_call.id,
                                    'message_id': session.messages[tool_message_index].message_id,
                                    'stream': update.stream,
                                    'delta': update.content,
                                }
                            )
                            continue
                        tool_result = update.result
                if tool_result is None:
                    raise RuntimeError(f'Tool executor returned no final result for {tool_call.name}')
                if self.plugin_runtime is not None:
                    self.plugin_runtime.record_tool_result(
                        tool_call.name,
                        ok=tool_result.ok,
                        metadata=tool_result.metadata,
                    )
                plugin_messages = self._plugin_tool_result_messages(tool_call.name)
                policy_messages = self._hook_policy_tool_result_messages(tool_call.name)
                if plugin_messages:
                    merged_metadata = dict(tool_result.metadata)
                    merged_metadata['plugin_messages'] = list(plugin_messages)
                    tool_result = ToolExecutionResult(
                        name=tool_result.name,
                        ok=tool_result.ok,
                        content=tool_result.content,
                        metadata=merged_metadata,
                    )
                    for message in plugin_messages:
                        stream_events.append(
                            {
                                'type': 'plugin_tool_hook',
                                'tool_name': tool_call.name,
                                'tool_call_id': tool_call.id,
                                'message_id': session.messages[tool_message_index].message_id,
                                'message': message,
                            }
                        )
                if policy_messages:
                    merged_metadata = dict(tool_result.metadata)
                    merged_metadata['hook_policy_messages'] = list(policy_messages)
                    tool_result = ToolExecutionResult(
                        name=tool_result.name,
                        ok=tool_result.ok,
                        content=tool_result.content,
                        metadata=merged_metadata,
                    )
                    for message in policy_messages:
                        stream_events.append(
                            {
                                'type': 'hook_policy_tool_hook',
                                'tool_name': tool_call.name,
                                'tool_call_id': tool_call.id,
                                'message_id': session.messages[tool_message_index].message_id,
                                'message': message,
                            }
                        )
                if tool_result.metadata.get('error_kind') == 'permission_denied':
                    stream_events.append(
                        {
                            'type': 'tool_permission_denial',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'reason': tool_result.content,
                            'source': (
                                'hook_policy'
                                if tool_result.metadata.get('action') == 'hook_policy_block'
                                else 'tool_runtime'
                            ),
                        }
                    )
                # 将工具执行结果写回 session 消息列表，供下一轮模型调用时读取
                session.finalize_tool(
                    tool_message_index,
                    content=serialize_tool_result(tool_result),
                    metadata={
                        'phase': 'completed',
                        'plugin_preflight_messages': list(plugin_preflight_messages),
                        'hook_policy_preflight_messages': list(policy_preflight_messages),
                        **dict(tool_result.metadata),
                    },
                    stop_reason='tool_completed',
                )
                stream_events.append(
                    {
                        'type': 'tool_result',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                        'ok': tool_result.ok,
                        'metadata': dict(tool_result.metadata),
                    }
                )
                self._append_runtime_tool_followup_events(
                    stream_events,
                    tool_call=tool_call,
                    tool_result=tool_result,
                )
                plugin_runtime_message = self._build_plugin_tool_runtime_message(
                    tool_name=tool_call.name,
                    preflight_messages=plugin_preflight_messages,
                    block_message=plugin_block_message,
                    plugin_messages=plugin_messages,
                    hook_policy_preflight_messages=policy_preflight_messages,
                    hook_policy_block_message=policy_block_message,
                    hook_policy_messages=policy_messages,
                    delegate_preflight_messages=tuple(
                        message
                        for message in tool_result.metadata.get(
                            'plugin_delegate_preflight_messages',
                            [],
                        )
                        if isinstance(message, str) and message
                    ),
                    delegate_after_messages=tuple(
                        message
                        for message in tool_result.metadata.get(
                            'plugin_delegate_after_messages',
                            [],
                        )
                        if isinstance(message, str) and message
                    ),
                )
                if plugin_runtime_message is not None:
                    session.append_user(
                        plugin_runtime_message,
                        metadata={
                            'kind': 'plugin_tool_runtime',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'plugin_blocked': plugin_block_message is not None,
                            'plugin_message_count': len(plugin_messages),
                            'plugin_preflight_count': len(plugin_preflight_messages),
                        },
                        message_id=f'plugin_tool_runtime_{tool_call.id}',
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_context',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': f'plugin_tool_runtime_{tool_call.id}',
                            'blocked': plugin_block_message is not None,
                            'message_count': len(plugin_messages),
                            'preflight_count': len(plugin_preflight_messages),
                        }
                    )
                self._refresh_runtime_views_for_tool_result(tool_call.name, tool_result)
                # 记录文件操作历史（用于 resume 时回放）
                history_entry = self._build_file_history_entry(
                    tool_call=tool_call,
                    tool_result=tool_result,
                    turn_index=turn_index,
                )
                if history_entry is not None:
                    file_history.append(history_entry)
            # 工具执行完毕，继续下一轮循环让模型看到结果

        # ── 阶段 8：循环耗尽 max_turns，强制结束 ──
        result = AgentRunResult(
            final_output=(
                last_content
                or 'Stopped: max turns reached before the model produced a final answer.'
            ),
            turns=self.runtime_config.max_turns,
            tool_calls=tool_calls,
            transcript=session.transcript(),
            events=tuple(stream_events),
            usage=total_usage,
            total_cost_usd=total_cost_usd,
            stop_reason='max_turns',
            file_history=tuple(file_history),
            session_id=session_id,
            scratchpad_directory=(
                str(scratchpad_directory) if scratchpad_directory is not None else None
            ),
        )
        result = self._append_runtime_after_turn_events(
            result,
            prompt=effective_prompt,
            turn_index=self.runtime_config.max_turns,
        )
        result = self._persist_session(session, result)
        self.last_run_result = result
        return result

    def _query_model(
        self,
        session: AgentSessionState,
        tool_specs: list[dict[str, object]],
    ) -> tuple[AssistantTurn, tuple[StreamEvent, ...]]:
        """调用 Claude Messages API，将内部 OpenAI 格式消息转换后发送，返回模型响应和流式事件。"""
        if not self.runtime_config.stream_model_responses:
            turn = self.client.complete(
                session.to_openai_messages(),
                tool_specs,
                output_schema=self.runtime_config.output_schema,
            )
            assistant_tool_calls = tuple(
                {
                    'id': tool_call.id,
                    'type': 'function',
                    'function': {
                        'name': tool_call.name,
                        'arguments': json.dumps(
                            tool_call.arguments,
                            ensure_ascii=True,
                        ),
                    },
                }
                for tool_call in turn.tool_calls
            )
            session.append_assistant(
                turn.content,
                assistant_tool_calls,
                message_id=f'assistant_{len(session.messages)}',
                stop_reason=turn.finish_reason,
                usage=turn.usage,
            )
            return turn, ()

        assistant_index = session.start_assistant(
            message_id=f'assistant_{len(session.messages)}'
        )
        usage = UsageStats()
        finish_reason: str | None = None
        events: list[StreamEvent] = []
        for event in self.client.stream(
            session.to_openai_messages(),
            tool_specs,
            output_schema=self.runtime_config.output_schema,
        ):
            events.append(event)
            if event.type == 'content_delta':
                session.append_assistant_delta(assistant_index, event.delta)
            elif event.type == 'tool_call_delta':
                session.merge_assistant_tool_call_delta(
                    assistant_index,
                    tool_call_index=event.tool_call_index or 0,
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    arguments_delta=event.arguments_delta,
                )
            elif event.type == 'usage':
                usage = usage + event.usage
            elif event.type == 'message_stop':
                finish_reason = event.finish_reason

        session.finalize_assistant(
            assistant_index,
            finish_reason=finish_reason,
            usage=usage,
        )
        assistant_message = session.messages[assistant_index]
        turn = AssistantTurn(
            content=assistant_message.content,
            tool_calls=self._tool_calls_from_message(assistant_message.tool_calls),
            finish_reason=finish_reason,
            raw_message=assistant_message.to_openai_message(),
            usage=usage,
        )
        return turn, tuple(events)

    def _tool_calls_from_message(
        self,
        tool_calls: tuple[dict[str, object], ...],
    ) -> tuple[ToolCall, ...]:
        """将模型返回的原始 tool_calls JSON 解析为结构化的 ToolCall 对象列表。"""
        parsed: list[ToolCall] = []
        for index, raw_tool_call in enumerate(tool_calls):
            function_block = raw_tool_call.get('function')
            if not isinstance(function_block, dict):
                continue
            name = function_block.get('name')
            if not isinstance(name, str) or not name:
                continue
            raw_arguments = function_block.get('arguments', '')
            if isinstance(raw_arguments, str) and raw_arguments.strip():
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ClaudeAPIError(
                        f'Tool arguments must decode to an object, got {type(arguments).__name__}'
                    )
            else:
                arguments = {}
            call_id = raw_tool_call.get('id')
            if not isinstance(call_id, str) or not call_id:
                call_id = f'call_{index}'
            parsed.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                )
            )
        return tuple(parsed)

    def _should_continue_response(self, turn: AssistantTurn) -> bool:
        """判断模型是否因 max_tokens 截断了输出，需要发送 continuation prompt 继续。"""
        return turn.finish_reason in {'length', 'max_tokens'}

    def _build_continuation_prompt(self) -> str:
        """构建让模型接着上次截断位置继续输出的 system-reminder 提示。"""
        return (
            '<system-reminder>\n'
            'Your previous answer was truncated because the model stopped early. '
            'Continue exactly where you left off. Do not repeat completed text.\n'
            '</system-reminder>'
        )

    def _check_budget(
        self,
        usage: UsageStats,
        total_cost_usd: float,
        *,
        tool_calls: int,
        delegated_tasks: int,
        model_calls: int,
        session_turns: int,
    ) -> BudgetDecision:
        """综合检查所有预算维度（token/费用/工具调用次数/轮数），返回是否超限。"""
        budget = self.runtime_config.budget_config
        token_reason = self._check_token_budget(usage, budget)
        if token_reason is not None:
            return BudgetDecision(exceeded=True, reason=token_reason)
        if (
            budget.max_total_cost_usd is not None
            and total_cost_usd > budget.max_total_cost_usd
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the total estimated cost '
                    f'(${total_cost_usd:.6f}) exceeded the configured budget '
                    f'(${budget.max_total_cost_usd:.6f}).'
                ),
            )
        if (
            budget.max_tool_calls is not None
            and tool_calls > budget.max_tool_calls
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the tool-call budget was exceeded '
                    f'({tool_calls} > {budget.max_tool_calls}).'
                ),
            )
        if (
            budget.max_delegated_tasks is not None
            and delegated_tasks > budget.max_delegated_tasks
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the delegated-task budget was exceeded '
                    f'({delegated_tasks} > {budget.max_delegated_tasks}).'
                ),
            )
        if (
            budget.max_model_calls is not None
            and model_calls > budget.max_model_calls
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the model-call budget was exceeded '
                    f'({model_calls} > {budget.max_model_calls}).'
                ),
            )
        if (
            budget.max_session_turns is not None
            and session_turns > budget.max_session_turns
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the session-turn budget was exceeded '
                    f'({session_turns} > {budget.max_session_turns}).'
                ),
            )
        return BudgetDecision(exceeded=False)

    def _preflight_prompt_length(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> PromptPreflightResult:
        """在调用模型前检查 prompt 总 token 数是否超限，必要时触发紧急压缩。"""
        snapshot = calculate_token_budget(
            session=session,
            model=self.model_config.model,
            budget_config=self.runtime_config.budget_config,
            output_schema=self.runtime_config.output_schema,
        )
        if not snapshot.exceeds_soft_limit and not snapshot.exceeds_hard_limit:
            return PromptPreflightResult()

        stream_events.append(
            {
                'type': 'prompt_length_check',
                'turn_index': turn_index,
                'projected_input_tokens': snapshot.projected_input_tokens,
                'soft_input_limit_tokens': snapshot.soft_input_limit_tokens,
                'hard_input_limit_tokens': snapshot.hard_input_limit_tokens,
                'soft_overflow_tokens': snapshot.soft_overflow_tokens,
                'overflow_tokens': snapshot.overflow_tokens,
                'exceeds_hard_limit': snapshot.exceeds_hard_limit,
            }
        )

        target_tokens = snapshot.soft_input_limit_tokens
        if snapshot.exceeds_hard_limit:
            target_tokens = snapshot.hard_input_limit_tokens
        if target_tokens < 0:
            target_tokens = 0

        if self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=target_tokens,
            allow_compaction=True,
        ):
            recovered = calculate_token_budget(
                session=session,
                model=self.model_config.model,
                budget_config=self.runtime_config.budget_config,
                output_schema=self.runtime_config.output_schema,
            )
            stream_events.append(
                {
                    'type': 'prompt_length_recovery',
                    'turn_index': turn_index,
                    'strategy': 'heuristic',
                    'projected_input_tokens': recovered.projected_input_tokens,
                    'soft_input_limit_tokens': recovered.soft_input_limit_tokens,
                    'hard_input_limit_tokens': recovered.hard_input_limit_tokens,
                    'exceeds_hard_limit': recovered.exceeds_hard_limit,
                    'exceeds_soft_limit': recovered.exceeds_soft_limit,
                }
            )
            if not recovered.exceeds_soft_limit and not recovered.exceeds_hard_limit:
                return PromptPreflightResult()
            snapshot = recovered

        # Circuit-breaker: skip auto-compact after MAX_COMPACT_FAILURES consecutive failures
        from .compact import MAX_COMPACT_FAILURES
        if self._compact_consecutive_failures >= MAX_COMPACT_FAILURES:
            stream_events.append(
                {
                    'type': 'auto_compact_circuit_breaker',
                    'turn_index': turn_index,
                    'consecutive_failures': self._compact_consecutive_failures,
                }
            )
        elif self._can_auto_compact_with_summary(session):
            compact_result = compact_conversation(
                self,
                custom_instructions=(
                    'Automatically collapse earlier conversation context to fit the next model '
                    'turn. Preserve the active task, recent file changes, failures, pending work, '
                    'and exact next step.'
                ),
            )
            if compact_result.error is None:
                self._compact_consecutive_failures = 0  # Reset on success
                recovered = calculate_token_budget(
                    session=session,
                    model=self.model_config.model,
                    budget_config=self.runtime_config.budget_config,
                    output_schema=self.runtime_config.output_schema,
                )
                stream_events.append(
                    {
                        'type': 'auto_compact_summary',
                        'turn_index': turn_index,
                        'pre_compact_token_count': compact_result.pre_compact_token_count,
                        'post_compact_token_count': compact_result.post_compact_token_count,
                        'true_post_compact_token_count': compact_result.true_post_compact_token_count,
                        'summary_usage_tokens': compact_result.usage.total_tokens,
                        'ptl_retries': compact_result.ptl_retries,
                        'projected_input_tokens': recovered.projected_input_tokens,
                        'soft_input_limit_tokens': recovered.soft_input_limit_tokens,
                        'hard_input_limit_tokens': recovered.hard_input_limit_tokens,
                        'exceeds_hard_limit': recovered.exceeds_hard_limit,
                        'exceeds_soft_limit': recovered.exceeds_soft_limit,
                    }
                )
                if not recovered.exceeds_soft_limit and not recovered.exceeds_hard_limit:
                    return PromptPreflightResult(
                        usage_increment=compact_result.usage,
                        model_calls_increment=1,
                    )
                snapshot = recovered
                if compact_result.usage.total_tokens:
                    return PromptPreflightResult(
                        usage_increment=compact_result.usage,
                        model_calls_increment=1,
                        stop_reason=(
                            'prompt_too_long'
                            if recovered.exceeds_hard_limit
                            else None
                        ),
                        reason=(
                            self._build_prompt_length_error(recovered)
                            if recovered.exceeds_hard_limit
                            else None
                        ),
                    )
            else:
                self._compact_consecutive_failures += 1
                stream_events.append(
                    {
                        'type': 'auto_compact_failed',
                        'turn_index': turn_index,
                        'reason': compact_result.error,
                        'consecutive_failures': self._compact_consecutive_failures,
                    }
                )

        if snapshot.exceeds_hard_limit:
            return PromptPreflightResult(
                stop_reason='prompt_too_long',
                reason=self._build_prompt_length_error(snapshot),
            )

        stream_events.append(
            {
                'type': 'prompt_length_warning',
                'turn_index': turn_index,
                'projected_input_tokens': snapshot.projected_input_tokens,
                'soft_input_limit_tokens': snapshot.soft_input_limit_tokens,
                'hard_input_limit_tokens': snapshot.hard_input_limit_tokens,
                'soft_overflow_tokens': snapshot.soft_overflow_tokens,
            }
        )
        return PromptPreflightResult()

    def _can_auto_compact_with_summary(self, session: AgentSessionState) -> bool:
        """判断当前会话是否满足自动摘要压缩的条件（消息数量足够多）。"""
        prefix_count = self._compact_prefix_count(session)
        preserve_count = max(self.runtime_config.compact_preserve_messages, 1)
        return len(session.messages) - prefix_count > preserve_count

    def _build_prompt_length_error(self, snapshot) -> str:
        """构建 prompt 超长时的错误提示信息，包含 token 统计快照。"""
        return (
            'Stopped before the next model call because the prompt would exceed the '
            'effective input budget. '
            f'Projected prompt tokens: {snapshot.projected_input_tokens:,}; '
            f'hard input limit: {snapshot.hard_input_limit_tokens:,}; '
            f'soft input limit: {snapshot.soft_input_limit_tokens:,}.'
        )

    def _microcompact_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        """Run time-based microcompaction to clear old tool results.

        Fires when the gap since the last assistant message exceeds the
        threshold (default 60 minutes), indicating the server-side cache
        has expired and the full prefix will be rewritten anyway.
        """
        if not session.messages:
            return
        result = _microcompact_messages(
            session.messages,
            model=self.model_config.model,
        )
        if not result.triggered:
            return
        session.messages = result.messages
        stream_events.append(
            {
                'type': 'microcompact',
                'turn_index': turn_index,
                'cleared_tool_count': result.cleared_tool_count,
                'kept_tool_count': result.kept_tool_count,
                'estimated_tokens_saved': result.estimated_tokens_saved,
                'gap_minutes': round(result.gap_minutes, 1),
            }
        )

    def _snip_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        """第 2 级上下文压缩：将早期消息中的长工具输出替换为摘要占位符。"""
        threshold = self.runtime_config.auto_snip_threshold_tokens
        if threshold is None or threshold <= 0:
            return
        self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=threshold,
            allow_compaction=False,
        )

    def _compact_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        """第 3 级上下文压缩：用模型对历史消息做摘要，大幅缩减上下文长度。"""
        threshold = self.runtime_config.auto_compact_threshold_tokens
        if threshold is None or threshold <= 0:
            return
        self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=threshold,
            allow_compaction=True,
        )

    def _reactive_compact_session(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> bool:
        """紧急压缩：在 prompt-too-long 错误后尝试强制压缩以恢复可用状态。"""
        return self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=0,
            allow_compaction=True,
            reactive=True,
        )

    def _reduce_context_pressure(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        target_tokens: int,
        allow_compaction: bool,
        reactive: bool = False,
    ) -> bool:
        """依次尝试 snip → compact 来降低上下文压力，返回是否成功减少了 token。"""
        changed = False
        for _ in range(6):
            usage_report = collect_context_usage(
                session=session,
                model=self.model_config.model,
                strategy='reactive_compact' if reactive else 'context_pressure',
            )
            if usage_report.total_tokens <= target_tokens:
                break
            if self._snip_session_pass(
                session,
                stream_events,
                turn_index=turn_index,
                target_tokens=target_tokens,
                current_total=usage_report.total_tokens,
                reactive=reactive,
            ):
                changed = True
                continue
            if allow_compaction and self._compact_session_pass(
                session,
                stream_events,
                turn_index=turn_index,
                usage_total=usage_report.total_tokens,
                reactive=reactive,
            ):
                changed = True
                if reactive:
                    continue
                break
            break
        return changed

    def _snip_session_pass(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        target_tokens: int,
        current_total: int,
        reactive: bool,
    ) -> bool:
        """执行一轮 snip 操作：遍历消息列表，将超长工具输出替换为截断摘要。"""
        prefix_count = self._compact_prefix_count(session)
        tail_count = min(
            max(self.runtime_config.compact_preserve_messages, 0),
            max(len(session.messages) - prefix_count, 0),
        )
        candidate_indexes = [
            index
            for index in range(prefix_count, max(len(session.messages) - tail_count, prefix_count))
            if self._message_can_be_snipped(session.messages[index])
        ]
        if not candidate_indexes:
            return False
        snipped_count = 0
        tokens_removed = 0
        snipped_message_ids: list[str] = []
        for index in candidate_indexes:
            if current_total <= target_tokens and not reactive:
                break
            message = session.messages[index]
            original_tokens = estimate_tokens(message.content, self.model_config.model)
            replacement = self._build_snipped_message_content(message)
            replacement_tokens = estimate_tokens(replacement, self.model_config.model)
            if replacement_tokens >= original_tokens:
                continue
            session.tombstone_message(
                index,
                summary=replacement,
                stop_reason='snipped_for_context',
                mutation_kind='snip_tombstone',
                metadata={
                    'kind': 'snipped_message',
                    'original_token_estimate': original_tokens,
                    'replacement_token_estimate': replacement_tokens,
                    'snipped_turn_index': turn_index,
                    'snipped_from_role': message.role,
                    'snipped_from_message_id': message.message_id,
                    'snipped_from_kind': message.metadata.get('kind'),
                    'snipped_from_lineage_id': message.metadata.get('lineage_id'),
                    'snipped_from_revision': message.metadata.get('revision'),
                },
            )
            delta = original_tokens - replacement_tokens
            current_total -= delta
            tokens_removed += delta
            snipped_count += 1
            if session.messages[index].message_id:
                snipped_message_ids.append(session.messages[index].message_id)
            if reactive and snipped_count >= 3:
                break
        if not snipped_count:
            return False
        stream_events.append(
            {
                'type': 'reactive_snip_boundary' if reactive else 'snip_boundary',
                'turn_index': turn_index,
                'snipped_message_count': snipped_count,
                'estimated_tokens_removed': tokens_removed,
                'snipped_message_ids': snipped_message_ids,
            }
        )
        return True

    def _compact_session_pass(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        usage_total: int,
        reactive: bool,
    ) -> bool:
        """执行一轮 compact 操作：调用模型生成历史消息的摘要，替换原始消息。"""
        prefix_count = self._compact_prefix_count(session)
        preserve_messages = max(self.runtime_config.compact_preserve_messages, 0)
        if reactive:
            preserve_messages = max(preserve_messages // 2, 1)
        tail_count = min(
            preserve_messages,
            max(len(session.messages) - prefix_count, 0),
        )
        compact_end = len(session.messages) - tail_count
        if compact_end <= prefix_count:
            return False
        candidates = session.messages[prefix_count:compact_end]
        preserved_tail = list(session.messages[compact_end:])
        if not candidates:
            return False
        compacted_tokens = sum(
            usage.tokens
            for usage in (
                collect_context_usage(
                    session=AgentSessionState(
                        system_prompt_parts=session.system_prompt_parts,
                        user_context=session.user_context,
                        system_context=session.system_context,
                        messages=list(candidates),
                    ),
                    model=self.model_config.model,
                    strategy='compacted_segment',
                ).categories
            )
            if usage.name != 'Free space'
        )
        compact_message = self._build_compact_boundary_message(
            candidates,
            turn_index=turn_index,
            estimated_tokens_before=usage_total,
            estimated_tokens_removed=compacted_tokens,
            preserved_tail_count=tail_count,
            preserved_tail=preserved_tail,
        )
        session.messages = (
            session.messages[:prefix_count]
            + [compact_message]
            + session.messages[compact_end:]
        )
        stream_events.append(
            {
                'type': 'reactive_compact_boundary' if reactive else 'compact_boundary',
                'turn_index': turn_index,
                'compacted_message_count': len(candidates),
                'estimated_tokens_before': usage_total,
                'estimated_tokens_removed': compacted_tokens,
                'preserved_tail_count': tail_count,
                'preserved_tail_ids': [
                    message.message_id for message in preserved_tail if message.message_id
                ],
                'compaction_depth': compact_message.metadata.get('compaction_depth'),
                'nested_compaction_count': compact_message.metadata.get('nested_compaction_count'),
                'compacted_message_ids': [
                    message.message_id for message in candidates if message.message_id
                ],
            }
        )
        return True

    def _check_token_budget(
        self,
        usage: UsageStats,
        budget: BudgetConfig,
    ) -> str | None:
        """检查 token 用量是否超出配置的各项 token 上限。"""
        if budget.max_total_tokens is not None and usage.total_tokens > budget.max_total_tokens:
            return (
                'Stopped because the total token budget was exceeded '
                f'({usage.total_tokens} > {budget.max_total_tokens}).'
            )
        if budget.max_input_tokens is not None and usage.input_tokens > budget.max_input_tokens:
            return (
                'Stopped because the input token budget was exceeded '
                f'({usage.input_tokens} > {budget.max_input_tokens}).'
            )
        if budget.max_output_tokens is not None and usage.output_tokens > budget.max_output_tokens:
            return (
                'Stopped because the output token budget was exceeded '
                f'({usage.output_tokens} > {budget.max_output_tokens}).'
            )
        if (
            budget.max_reasoning_tokens is not None
            and usage.reasoning_tokens > budget.max_reasoning_tokens
        ):
            return (
                'Stopped because the reasoning token budget was exceeded '
                f'({usage.reasoning_tokens} > {budget.max_reasoning_tokens}).'
            )
        return None

    def _build_file_history_entry(
        self,
        *,
        tool_call: ToolCall,
        tool_result,
        turn_index: int,
    ) -> dict[str, object] | None:
        """根据工具调用和执行结果构建文件操作历史条目（用于 resume 时回放）。"""
        if not tool_result.metadata:
            return None
        if (
            'path' not in tool_result.metadata
            and 'command' not in tool_result.metadata
            and tool_result.metadata.get('action') not in ('delegate_agent', 'Agent')
        ):
            return None
        metadata = dict(tool_result.metadata)
        entry: dict[str, object] = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'turn_index': turn_index,
            'tool_call_id': tool_call.id,
            'tool_name': tool_call.name,
            'ok': tool_result.ok,
            'history_entry_id': f'{turn_index}:{tool_call.id}:{tool_call.name}',
            'result_preview': self._preview_text(tool_result.content, 220),
            **metadata,
        }
        action = metadata.get('action')
        path = metadata.get('path')
        if isinstance(path, str) and path:
            entry['history_kind'] = 'file_change'
            entry['changed_paths'] = [path]
            before_sha256 = metadata.get('before_sha256')
            if isinstance(before_sha256, str) and before_sha256:
                entry['before_snapshot_id'] = f'{path}:{before_sha256[:12]}'
            after_sha256 = metadata.get('after_sha256')
            if isinstance(after_sha256, str) and after_sha256:
                entry['after_snapshot_id'] = f'{path}:{after_sha256[:12]}'
        elif isinstance(metadata.get('command'), str):
            entry['history_kind'] = 'shell'
        elif action in ('delegate_agent', 'Agent'):
            entry['history_kind'] = 'delegation'
            delegate_batches = metadata.get('delegate_batches')
            if isinstance(delegate_batches, list):
                entry['delegate_batch_count'] = len(delegate_batches)
            dependency_skips = metadata.get('dependency_skips')
            if isinstance(dependency_skips, int) and not isinstance(dependency_skips, bool):
                entry['dependency_skips'] = dependency_skips
        else:
            entry['history_kind'] = 'tool'
        return entry

    def _compact_prefix_count(self, session: AgentSessionState) -> int:
        """计算 compact 时需要保留的前缀消息数量（system prompt 等不可压缩的部分）。"""
        prefix_count = 0
        for message in session.messages:
            if prefix_count == 0 and message.role == 'system':
                prefix_count += 1
                continue
            if (
                prefix_count == 1
                and message.role == 'user'
                and message.content.startswith('<system-reminder>')
            ):
                prefix_count += 1
                continue
            break
        return prefix_count

    def _message_can_be_snipped(self, message) -> bool:
        """判断一条消息是否可以被 snip（只有工具结果消息且长度超阈值才可以）。"""
        if message.metadata.get('kind') in {
            'compact_boundary',
            'snipped_message',
            'file_history_replay',
        }:
            return False
        if message.role == 'tool':
            return True
        if message.role == 'assistant' and (message.tool_calls or len(message.content) > 600):
            return True
        if (
            message.role == 'user'
            and message.metadata.get('kind') in {'continuation_request', 'file_history_replay'}
        ):
            return True
        return False

    def _build_snipped_message_content(self, message) -> str:
        """为被 snip 的消息构建截断后的替代内容（保留开头和结尾）。"""
        preview = ' '.join(message.content.split())
        if len(preview) > 120:
            preview = preview[:117] + '...'
        if message.role == 'tool':
            label = f'tool result ({message.name or "tool"})'
        elif message.role == 'assistant':
            label = 'assistant message with tool calls'
        else:
            label = message.role
        return (
            '<system-reminder>\n'
            f'Older {label} was snipped to save context.\n'
            f'Message id: {message.message_id or "(none)"}\n'
            f'Preview: {preview or "(empty)"}\n'
            '</system-reminder>'
        )

    def _build_compact_boundary_message(
        self,
        messages,
        *,
        turn_index: int,
        estimated_tokens_before: int,
        estimated_tokens_removed: int,
        preserved_tail_count: int,
        preserved_tail,
    ):
        """构建 compact 操作后的边界消息，标记历史消息已被摘要替换。"""
        summary_lines = [
            '<system-reminder>',
            'Earlier conversation history was compacted to keep the session within the context budget.',
            '',
            'Compacted summary:',
        ]
        remaining = 24
        for message in messages:
            if remaining <= 0:
                break
            label = message.role
            if message.role == 'tool' and message.name:
                label = f'tool:{message.name}'
            snippet = ' '.join(message.content.split())
            if len(snippet) > 160:
                snippet = snippet[:157] + '...'
            if not snippet:
                snippet = '(empty)'
            summary_lines.append(f'- {label}: {snippet}')
            remaining -= 1
        if len(messages) > 24:
            summary_lines.append(f'- ... plus {len(messages) - 24} older messages')
        summary_lines.extend(
            [
                '',
                'Keep using the preserved recent tail as the active working set.',
                '</system-reminder>',
            ]
        )
        from .agent_session import AgentMessage

        nested_compaction_count = sum(
            1 for message in messages if message.metadata.get('kind') == 'compact_boundary'
        )
        prior_depths = [
            int(message.metadata.get('compaction_depth', 0))
            for message in messages
            if isinstance(message.metadata.get('compaction_depth', 0), int)
        ]
        compaction_depth = (max(prior_depths) if prior_depths else 0) + 1
        compacted_kinds: dict[str, int] = {}
        source_mutation_totals: dict[str, int] = {}
        compacted_lineage_ids: list[str] = []
        preserved_tail_lineage_ids = [
            lineage_id
            for lineage_id in (
                message.metadata.get('lineage_id') for message in preserved_tail
            )
            if isinstance(lineage_id, str) and lineage_id
        ]
        max_source_revision = 0
        max_source_mutation_serial = 0
        compacted_revision_total = 0
        for message in messages:
            kind = message.metadata.get('kind')
            label = str(kind) if isinstance(kind, str) and kind else message.role
            compacted_kinds[label] = compacted_kinds.get(label, 0) + 1
            lineage_id = message.metadata.get('lineage_id')
            if isinstance(lineage_id, str) and lineage_id:
                compacted_lineage_ids.append(lineage_id)
            revision = message.metadata.get('revision')
            if isinstance(revision, int) and not isinstance(revision, bool):
                max_source_revision = max(max_source_revision, revision)
                compacted_revision_total += revision
            max_mutation_serial = message.metadata.get('max_mutation_serial')
            if isinstance(max_mutation_serial, int) and not isinstance(max_mutation_serial, bool):
                max_source_mutation_serial = max(
                    max_source_mutation_serial,
                    max_mutation_serial,
                )
            mutation_totals = message.metadata.get('mutation_totals')
            if isinstance(mutation_totals, dict):
                for mutation_kind, count in mutation_totals.items():
                    if (
                        not isinstance(mutation_kind, str)
                        or not mutation_kind
                        or isinstance(count, bool)
                        or not isinstance(count, int)
                        or count <= 0
                    ):
                        continue
                    source_mutation_totals[mutation_kind] = (
                        source_mutation_totals.get(mutation_kind, 0) + count
                    )

        compact_boundary_id = f'compact_boundary_{turn_index}_{len(messages)}'

        return AgentMessage(
            role='system',
            content='\n'.join(summary_lines),
            message_id=compact_boundary_id,
            metadata={
                'kind': 'compact_boundary',
                'lineage_id': compact_boundary_id,
                'revision': 0,
                'revision_count': 1,
                'message_role': 'system',
                'turn_index': turn_index,
                'compacted_message_count': len(messages),
                'estimated_tokens_before': estimated_tokens_before,
                'estimated_tokens_removed': estimated_tokens_removed,
                'preserved_tail_count': preserved_tail_count,
                'preserved_tail_ids': [
                    message.message_id for message in preserved_tail if message.message_id
                ],
                'compaction_depth': compaction_depth,
                'nested_compaction_count': nested_compaction_count,
                'compacted_kinds': compacted_kinds,
                'compacted_lineage_ids': compacted_lineage_ids,
                'preserved_tail_lineage_ids': preserved_tail_lineage_ids,
                'max_source_revision': max_source_revision,
                'max_source_mutation_serial': max_source_mutation_serial,
                'source_mutation_totals': source_mutation_totals,
                'compacted_revision_total': compacted_revision_total,
                'compacted_message_ids': [
                    message.message_id for message in messages if message.message_id
                ],
            },
        )

    def _is_prompt_too_long_error(self, exc: Exception) -> bool:
        """判断 API 错误是否是 prompt 超长导致的（用于触发紧急压缩重试）。"""
        text = str(exc).lower()
        patterns = (
            'prompt is too long',
            'maximum context length',
            'context length exceeded',
            'too many tokens',
            'input too long',
            'context window',
        )
        return any(pattern in text for pattern in patterns)

    def _execute_skill(
        self,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute a skill through the Skill tool.

        Checks bundled skills first, then falls back to slash commands.
        """
        from .agent_slash_commands import find_slash_command, get_slash_command_specs
        from .bundled_skills import find_bundled_skill, get_bundled_skills

        skill_name = arguments.get('skill')
        if not isinstance(skill_name, str) or not skill_name.strip():
            return ToolExecutionResult(
                name='Skill',
                ok=False,
                content='skill must be a non-empty string',
            )

        # Normalize: strip leading '/' if present
        skill_name = skill_name.strip().lstrip('/')
        args = arguments.get('args', '')
        if not isinstance(args, str):
            args = str(args) if args is not None else ''

        # 1. Check bundled skills first
        bundled = find_bundled_skill(skill_name)
        if bundled is not None:
            prompt = bundled.get_prompt(self, args.strip())
            return ToolExecutionResult(
                name='Skill',
                ok=True,
                content=prompt,
                metadata={
                    'action': 'skill',
                    'skill_name': skill_name,
                    'source': 'bundled',
                    'should_query': True,
                },
            )

        # 2. Fall back to slash commands
        spec = find_slash_command(skill_name)
        if spec is None:
            available_cmds = sorted(
                name
                for s in get_slash_command_specs()
                for name in s.names
            )
            available_skills = [sk.name for sk in get_bundled_skills()]
            all_available = sorted(set(available_cmds + available_skills))
            return ToolExecutionResult(
                name='Skill',
                ok=False,
                content=(
                    f'Unknown skill: {skill_name}. '
                    f'Available skills: {", ".join(all_available[:30])}'
                ),
                metadata={'action': 'skill_not_found', 'skill_name': skill_name},
            )

        # Invoke the slash command handler
        input_text = f'/{skill_name} {args}'.strip()
        result = spec.handler(self, args.strip(), input_text)

        if result.output:
            content = result.output
        elif result.prompt:
            content = result.prompt
        else:
            content = f'Skill /{skill_name} completed.'

        return ToolExecutionResult(
            name='Skill',
            ok=True,
            content=content,
            metadata={
                'action': 'skill',
                'skill_name': skill_name,
                'command_name': spec.names[0],
                'handled': result.handled,
                'should_query': result.should_query,
            },
        )

    def _resolve_agent_definition(
        self,
        arguments: dict[str, object],
    ) -> AgentDefinition:
        """Resolve the agent definition from subagent_type or default to general-purpose."""
        subagent_type = arguments.get('subagent_type')
        if isinstance(subagent_type, str) and subagent_type:
            agent_def = find_agent_definition(self.runtime_config.cwd, subagent_type)
            if agent_def is not None:
                return agent_def
        return GENERAL_PURPOSE_AGENT

    def _resolve_child_model_config(
        self,
        arguments: dict[str, object],
        agent_def: AgentDefinition,
    ) -> ModelConfig:
        """Resolve model config for a child agent based on explicit override or agent definition."""
        model_override = arguments.get('model')
        agent_model = agent_def.model

        # Explicit model param in arguments takes priority
        if isinstance(model_override, str) and model_override.strip():
            return replace(self.model_config, model=model_override.strip())

        # Agent definition model
        if agent_model and agent_model != 'inherit':
            return replace(self.model_config, model=agent_model)

        return self.model_config

    def _filter_tools_for_agent(
        self,
        agent_def: AgentDefinition,
    ) -> dict[str, AgentTool]:
        """Build the tool registry for a child agent based on its definition."""
        # Start from parent tools, remove Agent/delegate_agent to prevent recursive spawning
        base_tools = {
            name: tool
            for name, tool in self.tool_registry.items()
            if name not in ('delegate_agent', 'Agent')
        }

        # Apply agent-specific tool allow-list
        if agent_def.tools is not None:
            allowed = set(agent_def.tools)
            base_tools = {
                name: tool
                for name, tool in base_tools.items()
                if name in allowed
            }

        # Apply agent-specific disallowed tools
        if agent_def.disallowed_tools:
            denied = set(agent_def.disallowed_tools)
            base_tools = {
                name: tool
                for name, tool in base_tools.items()
                if name not in denied
            }

        # Apply universal agent disallowed tools
        base_tools = {
            name: tool
            for name, tool in base_tools.items()
            if name not in ALL_AGENT_DISALLOWED_TOOLS
        }

        return base_tools

    def _execute_delegate_agent(
        self,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """执行 Agent/delegate_agent 工具调用：创建子 agent 实例并运行。"""
        tool_name = 'Agent'
        agent_def = self._resolve_agent_definition(arguments)
        max_turns = arguments.get('max_turns')
        if max_turns is not None and (isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1):
            return ToolExecutionResult(
                name=tool_name,
                ok=False,
                content='max_turns must be an integer >= 1',
            )
        subtasks = self._normalize_delegate_subtasks(arguments)
        if not subtasks:
            return ToolExecutionResult(
                name=tool_name,
                ok=False,
                content='prompt must be a non-empty string or subtasks must contain at least one prompt',
            )

        # Resolve child permissions — read-only agents get no write/shell
        if agent_def.disallowed_tools and (
            'edit_file' in agent_def.disallowed_tools
            or 'write_file' in agent_def.disallowed_tools
        ):
            # Read-only agent (Explore, Plan, verification)
            child_permissions = AgentPermissions(
                allow_file_write=False,
                allow_shell_commands=self.runtime_config.permissions.allow_shell_commands,
                allow_destructive_shell_commands=False,
            )
        else:
            child_permissions = AgentPermissions(
                allow_file_write=(
                    self.runtime_config.permissions.allow_file_write
                    and bool(arguments.get('allow_write', False))
                ),
                allow_shell_commands=(
                    self.runtime_config.permissions.allow_shell_commands
                    and bool(arguments.get('allow_shell', False))
                ),
                allow_destructive_shell_commands=False,
            )

        # Resolve max_turns — agent definition or explicit param
        effective_max_turns = max_turns or agent_def.max_turns or min(self.runtime_config.max_turns, 6)

        child_runtime_config = replace(
            self.runtime_config,
            max_turns=effective_max_turns,
            permissions=child_permissions,
            auto_compact_threshold_tokens=self.runtime_config.auto_compact_threshold_tokens,
        )

        child_model_config = self._resolve_child_model_config(arguments, agent_def)
        child_tools = self._filter_tools_for_agent(agent_def)
        include_parent_context = bool(arguments.get('include_parent_context', True))
        continue_on_error = bool(arguments.get('continue_on_error', True))
        max_failures = arguments.get('max_failures')
        if isinstance(max_failures, bool) or (max_failures is not None and not isinstance(max_failures, int)):
            max_failures = None
        if isinstance(max_failures, int) and max_failures < 0:
            max_failures = None
        strategy = self._normalize_delegate_strategy(arguments.get('strategy'))
        child_summaries: list[dict[str, object]] = []
        child_session_ids: list[str] = []
        prior_results: list[dict[str, str]] = []
        completed_labels: set[str] = set()
        failed_labels: set[str] = set()
        delegate_preflight_messages = (
            self.plugin_runtime.before_delegate_injections()
            if self.plugin_runtime is not None
            else ()
        )
        delegate_after_messages: tuple[str, ...] = ()
        group_id: str | None = None
        if self.agent_manager is not None and len(subtasks) > 1:
            group_id = self.agent_manager.start_group(
                label=str(arguments.get('label') or 'delegated_group'),
                parent_agent_id=self.managed_agent_id,
                strategy=strategy,
            )
        planned_batches = self._plan_delegate_batches(subtasks, strategy)
        batch_summaries: list[dict[str, object]] = []
        failed_children = 0
        dependency_skips = 0
        child_result = None
        stop_processing = False
        for batch_index, batch in enumerate(planned_batches, start=1):
            if stop_processing:
                break
            batch_completed = 0
            batch_failed = 0
            batch_skipped = 0
            batch_labels: list[str] = []
            for subtask in batch:
                index = int(subtask.get('_delegate_index', len(child_summaries) + 1))
                subtask_label = str(subtask.get('label') or f'subtask_{index}')
                batch_labels.append(subtask_label)
                dependencies = tuple(
                    item
                    for item in subtask.get('depends_on', ())
                    if isinstance(item, str) and item
                )
                unmet_dependencies = [
                    dependency
                    for dependency in dependencies
                    if dependency not in completed_labels
                ]
                blocked_dependencies = [
                    dependency
                    for dependency in dependencies
                    if dependency in failed_labels
                ]
                if unmet_dependencies:
                    skip_reason = (
                        'skipped_dependency'
                        if blocked_dependencies
                        else 'pending_dependency'
                    )
                    child_result = AgentRunResult(
                        final_output=(
                            'Skipped delegated subtask because dependencies were not satisfied: '
                            + ', '.join(unmet_dependencies)
                        ),
                        turns=0,
                        tool_calls=0,
                        transcript=(),
                        stop_reason=skip_reason,
                    )
                    summary = {
                        'index': index,
                        'label': subtask_label,
                        'session_id': '',
                        'turns': child_result.turns,
                        'tool_calls': child_result.tool_calls,
                        'stop_reason': skip_reason,
                        'output_preview': self._preview_text(child_result.final_output, 220),
                        'resume_used': False,
                        'resumed_from_session_id': '',
                        'depends_on': list(dependencies),
                        'batch_index': batch_index,
                    }
                    child_summaries.append(summary)
                    failed_children += 1
                    batch_failed += 1
                    batch_skipped += 1
                    dependency_skips += 1
                    failed_labels.add(subtask_label)
                    if isinstance(max_failures, int) and failed_children > max_failures:
                        stop_processing = True
                        break
                    if not continue_on_error:
                        stop_processing = True
                        break
                    continue
                # Use agent definition's system prompt if available
                child_system_prompt = agent_def.system_prompt or self.custom_system_prompt
                child_override_prompt = None
                if agent_def.system_prompt:
                    child_override_prompt = agent_def.system_prompt
                else:
                    child_override_prompt = self.override_system_prompt

                # Inject critical system reminder if agent definition has one
                child_append_prompt = self.append_system_prompt
                if agent_def.critical_system_reminder:
                    reminder = f'\n\n<system-reminder>\n{agent_def.critical_system_reminder}\n</system-reminder>'
                    child_append_prompt = (
                        (child_append_prompt or '') + reminder
                    )

                child_agent = LocalCodingAgent(
                    model_config=child_model_config,
                    runtime_config=replace(
                        child_runtime_config,
                        max_turns=subtask.get('max_turns', child_runtime_config.max_turns),
                        disable_claude_md_discovery=agent_def.omit_claude_md,
                    ),
                    custom_system_prompt=child_system_prompt if not child_override_prompt else None,
                    append_system_prompt=child_append_prompt,
                    override_system_prompt=child_override_prompt,
                    tool_registry=child_tools,
                    agent_manager=self.agent_manager,
                    parent_agent_id=self.managed_agent_id,
                    managed_group_id=group_id,
                    managed_child_index=index,
                    managed_label=subtask_label,
                )
                if group_id is not None and child_agent.managed_agent_id is not None:
                    self.agent_manager.register_group_child(
                        group_id,
                        child_agent.managed_agent_id,
                        child_index=index,
                    )
                resume_session_id = subtask.get('resume_session_id')
                child_prompt = str(subtask['prompt'])
                if agent_def.initial_prompt and not (
                    isinstance(resume_session_id, str) and resume_session_id
                ):
                    child_prompt = f'{agent_def.initial_prompt.strip()}\n\n{child_prompt}'.strip()
                if delegate_preflight_messages:
                    child_prompt = self._prepend_plugin_delegate_context(
                        child_prompt,
                        delegate_preflight_messages,
                    )
                if include_parent_context and prior_results:
                    child_prompt = self._prepend_delegate_context(child_prompt, prior_results)
                resume_used = False
                if isinstance(resume_session_id, str) and resume_session_id:
                    try:
                        stored_child_session = load_agent_session(
                            resume_session_id,
                            directory=child_runtime_config.session_directory,
                        )
                    except OSError:
                        child_result = AgentRunResult(
                            final_output=f'Unable to load delegated session {resume_session_id}.',
                            turns=0,
                            tool_calls=0,
                            transcript=(),
                            stop_reason='resume_load_error',
                            session_id=resume_session_id,
                        )
                        failed_children += 1
                        batch_failed += 1
                        summary = {
                            'index': index,
                            'label': subtask_label,
                            'session_id': resume_session_id,
                            'turns': child_result.turns,
                            'tool_calls': child_result.tool_calls,
                            'stop_reason': child_result.stop_reason or 'resume_load_error',
                            'output_preview': self._preview_text(child_result.final_output, 220),
                            'resume_used': True,
                            'resumed_from_session_id': resume_session_id,
                            'depends_on': list(dependencies),
                            'batch_index': batch_index,
                        }
                        child_summaries.append(summary)
                        prior_results.append(
                            {
                                'label': summary['label'],
                                'output_preview': str(summary['output_preview']),
                            }
                        )
                        failed_labels.add(subtask_label)
                        if isinstance(max_failures, int) and failed_children > max_failures:
                            stop_processing = True
                            break
                        if not continue_on_error:
                            stop_processing = True
                            break
                        continue
                    child_result = child_agent.resume(child_prompt, stored_child_session)
                    resume_used = True
                else:
                    child_result = child_agent.run(child_prompt)
                if group_id is not None and child_agent.managed_agent_id is not None:
                    self.agent_manager.register_group_child(
                        group_id,
                        child_agent.managed_agent_id,
                        child_index=index,
                    )
                summary = {
                    'index': index,
                    'label': subtask_label,
                    'session_id': child_result.session_id or '',
                    'turns': child_result.turns,
                    'tool_calls': child_result.tool_calls,
                    'stop_reason': child_result.stop_reason or 'stop',
                    'output_preview': self._preview_text(child_result.final_output, 220),
                    'resume_used': resume_used,
                    'resumed_from_session_id': (
                        str(resume_session_id)
                        if isinstance(resume_session_id, str) and resume_session_id
                        else ''
                    ),
                    'depends_on': list(dependencies),
                    'batch_index': batch_index,
                }
                child_summaries.append(summary)
                if child_result.session_id:
                    child_session_ids.append(child_result.session_id)
                prior_results.append(
                    {
                        'label': summary['label'],
                        'output_preview': str(summary['output_preview']),
                    }
                )
                if child_result.stop_reason in {'backend_error', 'budget_exceeded'}:
                    failed_children += 1
                    batch_failed += 1
                    failed_labels.add(subtask_label)
                    if isinstance(max_failures, int) and failed_children > max_failures:
                        stop_processing = True
                        break
                    if not continue_on_error:
                        stop_processing = True
                        break
                else:
                    batch_completed += 1
                    completed_labels.add(subtask_label)
            batch_status = 'completed'
            if batch_failed and batch_completed:
                batch_status = 'partial'
            elif batch_failed:
                batch_status = 'failed'
            batch_summaries.append(
                {
                    'batch_index': batch_index,
                    'labels': batch_labels,
                    'completed_children': batch_completed,
                    'failed_children': batch_failed,
                    'skipped_children': batch_skipped,
                    'status': batch_status,
                }
            )
        assert child_result is not None
        completed_children = len(child_summaries) - failed_children
        resumed_children = sum(
            1 for summary in child_summaries if summary.get('resume_used')
        )
        group_status = 'completed'
        if failed_children and completed_children:
            group_status = 'partial'
        elif failed_children:
            group_status = 'failed'
        delegate_after_messages = (
            self.plugin_runtime.after_delegate_injections()
            if self.plugin_runtime is not None
            else ()
        )
        if group_id is not None and self.agent_manager is not None:
            self.agent_manager.finish_group(
                group_id,
                status=group_status,
                completed_children=completed_children,
                failed_children=failed_children,
                batch_count=len(batch_summaries),
                max_batch_size=max((len(batch['labels']) for batch in batch_summaries), default=0),
                dependency_skips=dependency_skips,
            )
        summary_lines = [
            (
                'Delegated agent completed the subtask.'
                if len(child_summaries) == 1
                else f'Delegated agent completed {len(child_summaries)} sequential subtasks.'
            ),
        ]
        if group_id is not None:
            summary_lines.append(f'group_id={group_id}')
            summary_lines.append(f'group_status={group_status}')
            summary_lines.append(f'resumed_children={resumed_children}')
            summary_lines.append(f'strategy={strategy}')
            summary_lines.append(f'batch_count={len(batch_summaries)}')
            summary_lines.append(f'dependency_skips={dependency_skips}')
            summary_lines.append('')
        if delegate_preflight_messages:
            summary_lines.append('Plugin delegate preflight:')
            summary_lines.extend(f'- {message}' for message in delegate_preflight_messages)
            summary_lines.append('')
        for batch in batch_summaries:
            summary_lines.append(
                f"[batch {batch['batch_index']}] status={batch['status']} "
                f"labels={','.join(batch['labels']) or '(none)'} "
                f"completed={batch['completed_children']} failed={batch['failed_children']} "
                f"skipped={batch['skipped_children']}"
            )
        if batch_summaries:
            summary_lines.append('')
        for summary in child_summaries:
            summary_lines.extend(
                [
                    f"[{summary['label']}]",
                    f"batch_index={summary['batch_index']}",
                    f"session_id={summary['session_id']}",
                    f"turns={summary['turns']}",
                    f"tool_calls={summary['tool_calls']}",
                    f"stop_reason={summary['stop_reason']}",
                    f"resume_used={summary['resume_used']}",
                    f"resumed_from_session_id={summary['resumed_from_session_id']}",
                    f"depends_on={','.join(summary.get('depends_on', [])) or '(none)'}",
                    f"output_preview={summary['output_preview']}",
                    '',
                ]
            )
        if delegate_after_messages:
            summary_lines.append('Plugin delegate completion:')
            summary_lines.extend(f'- {message}' for message in delegate_after_messages)
            summary_lines.append('')
        summary_lines.append('Final delegated output:')
        summary_lines.append(child_result.final_output)
        return ToolExecutionResult(
            name=tool_name,
            ok=True,
            content='\n'.join(summary_lines).strip(),
            metadata={
                'action': 'Agent',
                'subagent_type': agent_def.agent_type,
                'child_session_id': child_result.session_id,
                'child_session_ids': child_session_ids,
                'child_turns': child_result.turns,
                'child_tool_calls': child_result.tool_calls,
                'child_stop_reason': child_result.stop_reason,
                'child_results': child_summaries,
                'subtask_count': len(child_summaries),
                'group_id': group_id,
                'group_status': group_status,
                'failed_children': failed_children,
                'completed_children': completed_children,
                'resumed_children': resumed_children,
                'strategy': strategy,
                'max_failures': max_failures,
                'delegate_batches': batch_summaries,
                'dependency_skips': dependency_skips,
                'plugin_delegate_preflight_messages': list(delegate_preflight_messages),
                'plugin_delegate_after_messages': list(delegate_after_messages),
            },
        )

    def _normalize_delegate_subtasks(
        self,
        arguments: dict[str, object],
    ) -> list[dict[str, object]]:
        """标准化委派任务的 subtasks 参数格式。"""
        subtasks: list[dict[str, object]] = []
        raw_subtasks = arguments.get('subtasks')
        if isinstance(raw_subtasks, list):
            for index, item in enumerate(raw_subtasks, start=1):
                if isinstance(item, str) and item.strip():
                    subtasks.append(
                        {
                            'prompt': item.strip(),
                            'label': f'subtask_{index}',
                            '_delegate_index': index,
                        }
                    )
                    continue
                if isinstance(item, dict):
                    prompt = item.get('prompt')
                    if not isinstance(prompt, str) or not prompt.strip():
                        continue
                    label = item.get('label')
                    max_turns = item.get('max_turns')
                    task: dict[str, object] = {
                        'prompt': prompt.strip(),
                        'label': label if isinstance(label, str) and label.strip() else f'subtask_{index}',
                    }
                    resume_session_id = item.get('resume_session_id')
                    if resume_session_id is None:
                        resume_session_id = item.get('session_id')
                    if isinstance(resume_session_id, str) and resume_session_id.strip():
                        task['resume_session_id'] = resume_session_id.strip()
                    depends_on = item.get('depends_on')
                    if isinstance(depends_on, list):
                        task['depends_on'] = tuple(
                            dependency.strip()
                            for dependency in depends_on
                            if isinstance(dependency, str) and dependency.strip()
                        )
                    if isinstance(max_turns, int) and not isinstance(max_turns, bool) and max_turns > 0:
                        task['max_turns'] = max_turns
                    task['_delegate_index'] = index
                    subtasks.append(task)
        prompt = arguments.get('prompt')
        if isinstance(prompt, str) and prompt.strip():
            if not subtasks:
                task: dict[str, object] = {'prompt': prompt.strip(), 'label': 'subtask_1'}
                resume_session_id = arguments.get('resume_session_id')
                if resume_session_id is None:
                    resume_session_id = arguments.get('session_id')
                if isinstance(resume_session_id, str) and resume_session_id.strip():
                    task['resume_session_id'] = resume_session_id.strip()
                task['_delegate_index'] = 1
                subtasks.append(task)
        return [
            {
                **task,
                '_delegate_index': int(task.get('_delegate_index', index)),
            }
            for index, task in enumerate(subtasks[:8], start=1)
        ]

    def _normalize_delegate_strategy(self, strategy: object) -> str:
        """标准化委派策略参数（'sequential'/'parallel' 等）。"""
        if not isinstance(strategy, str) or not strategy.strip():
            return 'serial'
        normalized = strategy.strip().lower().replace('-', '_')
        if normalized in {'graph', 'topological', 'dependency_graph', 'parallel', 'parallel_batches'}:
            return 'topological'
        return 'serial'

    def _plan_delegate_batches(
        self,
        subtasks: list[dict[str, object]],
        strategy: str,
    ) -> list[list[dict[str, object]]]:
        """根据委派策略将子任务分成批次（顺序执行或并行执行）。"""
        if strategy != 'topological':
            return [subtasks]
        remaining = list(subtasks)
        scheduled_labels: set[str] = set()
        known_labels = {
            str(task.get('label'))
            for task in subtasks
            if isinstance(task.get('label'), str) and str(task.get('label')).strip()
        }
        batches: list[list[dict[str, object]]] = []
        while remaining:
            ready: list[dict[str, object]] = []
            blocked: list[dict[str, object]] = []
            for task in remaining:
                dependencies = tuple(
                    item
                    for item in task.get('depends_on', ())
                    if isinstance(item, str) and item
                )
                if any(dependency not in known_labels for dependency in dependencies):
                    blocked.append(task)
                    continue
                if all(dependency in scheduled_labels for dependency in dependencies):
                    ready.append(task)
                else:
                    blocked.append(task)
            if not ready:
                batches.append(blocked)
                break
            batches.append(
                sorted(
                    ready,
                    key=lambda task: int(task.get('_delegate_index', 0)),
                )
            )
            scheduled_labels.update(
                str(task.get('label'))
                for task in ready
                if isinstance(task.get('label'), str) and str(task.get('label')).strip()
            )
            remaining = blocked
        return batches

    def _delegated_task_units(
        self,
        arguments: dict[str, object],
    ) -> int:
        """计算一次委派调用消耗的任务单元数（用于预算检查）。"""
        subtasks = arguments.get('subtasks')
        if isinstance(subtasks, list):
            count = sum(
                1
                for item in subtasks
                if (
                    isinstance(item, str)
                    and item.strip()
                ) or (
                    isinstance(item, dict)
                    and isinstance(item.get('prompt'), str)
                    and item.get('prompt', '').strip()
                )
            )
            if count:
                return count
        return 1

    def _prepend_delegate_context(
        self,
        prompt: str,
        prior_results: list[dict[str, str]],
    ) -> str:
        """在子 agent 的 prompt 前注入父 agent 的上下文信息。"""
        lines = [
            '<system-reminder>',
            'Prior delegated subtask summaries:',
        ]
        for result in prior_results[-4:]:
            lines.append(f"- {result['label']}: {result['output_preview']}")
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _prepend_plugin_delegate_context(
        self,
        prompt: str,
        messages: tuple[str, ...],
    ) -> str:
        """在子 agent 的 prompt 前注入插件提供的委派上下文。"""
        if not messages:
            return prompt
        lines = [
            '<system-reminder>',
            'Plugin delegate guidance:',
        ]
        lines.extend(f'- {message}' for message in messages)
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _append_runtime_tool_followup_events(
        self,
        stream_events: list[dict[str, object]],
        *,
        tool_call: ToolCall,
        tool_result: ToolExecutionResult,
    ) -> None:
        """在工具执行后追加运行时产生的后续事件（如 CWD 变更通知）。"""
        metadata = tool_result.metadata
        if metadata.get('action') == 'plugin_virtual_tool':
            stream_events.append(
                {
                    'type': 'plugin_virtual_tool_result',
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'plugin_name': metadata.get('plugin_name'),
                    'virtual_tool': metadata.get('virtual_tool'),
                }
            )
        plugin_delegate_preflight = metadata.get('plugin_delegate_preflight_messages')
        if isinstance(plugin_delegate_preflight, list) and plugin_delegate_preflight:
            stream_events.append(
                {
                    'type': 'plugin_delegate_preflight',
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'message_count': len(plugin_delegate_preflight),
                }
            )
        plugin_delegate_after = metadata.get('plugin_delegate_after_messages')
        if isinstance(plugin_delegate_after, list) and plugin_delegate_after:
            stream_events.append(
                {
                    'type': 'plugin_delegate_after',
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'message_count': len(plugin_delegate_after),
                }
            )
        if tool_call.name not in ('Agent', 'delegate_agent'):
            return
        delegate_batches = metadata.get('delegate_batches')
        if isinstance(delegate_batches, list):
            for batch in delegate_batches:
                if not isinstance(batch, dict):
                    continue
                stream_events.append(
                    {
                        'type': 'delegate_batch_result',
                        'tool_call_id': tool_call.id,
                        'group_id': metadata.get('group_id'),
                        'batch_index': batch.get('batch_index'),
                        'status': batch.get('status'),
                        'labels': batch.get('labels'),
                        'completed_children': batch.get('completed_children'),
                        'failed_children': batch.get('failed_children'),
                        'skipped_children': batch.get('skipped_children'),
                    }
                )
        child_results = metadata.get('child_results')
        if isinstance(child_results, list):
            for child in child_results:
                if not isinstance(child, dict):
                    continue
                stream_events.append(
                    {
                        'type': 'delegate_subtask_result',
                        'tool_call_id': tool_call.id,
                        'group_id': metadata.get('group_id'),
                        'label': child.get('label'),
                        'index': child.get('index'),
                        'session_id': child.get('session_id'),
                        'stop_reason': child.get('stop_reason'),
                        'tool_calls': child.get('tool_calls'),
                        'turns': child.get('turns'),
                        'resume_used': child.get('resume_used'),
                        'resumed_from_session_id': child.get('resumed_from_session_id'),
                        'depends_on': child.get('depends_on'),
                        'batch_index': child.get('batch_index'),
                    }
                )
        if metadata.get('group_id') is not None:
            stream_events.append(
                {
                    'type': 'delegate_group_result',
                    'tool_call_id': tool_call.id,
                    'group_id': metadata.get('group_id'),
                    'group_status': metadata.get('group_status'),
                    'subtask_count': metadata.get('subtask_count'),
                    'completed_children': metadata.get('completed_children'),
                    'failed_children': metadata.get('failed_children'),
                    'resumed_children': metadata.get('resumed_children'),
                    'strategy': metadata.get('strategy'),
                    'max_failures': metadata.get('max_failures'),
                    'batch_count': len(delegate_batches) if isinstance(delegate_batches, list) else 0,
                    'dependency_skips': metadata.get('dependency_skips'),
                }
            )

    def _preview_text(self, text: str, limit: int) -> str:
        """将长文本截断为指定长度的预览，超出部分用省略号替代。"""
        normalized = ' '.join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + '...'

    def _ensure_scratchpad_directory(self, session_id: str) -> Path:
        """确保 scratchpad 目录存在，用于存放会话中的临时文件。"""
        scratchpad_directory = (self.runtime_config.scratchpad_root / session_id).resolve()
        scratchpad_directory.mkdir(parents=True, exist_ok=True)
        return scratchpad_directory

    def _append_file_history_replay_if_needed(
        self,
        session: AgentSessionState,
        file_history: tuple[dict[str, object], ...],
    ) -> None:
        """resume 时将文件操作历史回放为消息，让模型知道之前修改了哪些文件。"""
        if not file_history:
            return
        replay_count = len(file_history)
        unique_paths = sorted(
            {
                path
                for entry in file_history
                for path in (
                    entry.get('changed_paths')
                    if isinstance(entry.get('changed_paths'), list)
                    else ([entry.get('path')] if isinstance(entry.get('path'), str) else [])
                )
                if isinstance(path, str) and path
            }
        )
        snapshot_count = sum(
            1
            for entry in file_history
            for key in ('before_snapshot_id', 'after_snapshot_id')
            if isinstance(entry.get(key), str) and entry.get(key)
        )
        for message in reversed(session.messages):
            if message.metadata.get('kind') != 'file_history_replay':
                continue
            if message.metadata.get('file_history_count') == replay_count:
                return
            break
        session.append_user(
            self._render_file_history_replay(file_history),
            metadata={
                'kind': 'file_history_replay',
                'file_history_count': replay_count,
                'file_history_unique_paths': len(unique_paths),
                'file_history_snapshot_count': snapshot_count,
            },
            message_id=f'file_history_replay_{replay_count}',
        )

    def _render_file_history_replay(
        self,
        file_history: tuple[dict[str, object], ...],
    ) -> str:
        """将文件操作历史渲染为可读的消息文本。"""
        unique_paths = sorted(
            {
                path
                for entry in file_history
                for path in (
                    entry.get('changed_paths')
                    if isinstance(entry.get('changed_paths'), list)
                    else ([entry.get('path')] if isinstance(entry.get('path'), str) else [])
                )
                if isinstance(path, str) and path
            }
        )
        snapshot_count = sum(
            1
            for entry in file_history
            for key in ('before_snapshot_id', 'after_snapshot_id')
            if isinstance(entry.get(key), str) and entry.get(key)
        )
        lines = [
            '<system-reminder>',
            'Recent file history from this saved session:',
            f'- History entries: {len(file_history)}',
            f'- Unique changed paths: {len(unique_paths)}',
            f'- Snapshot ids: {snapshot_count}',
        ]
        if unique_paths:
            preview_paths = ', '.join(unique_paths[:4])
            if len(unique_paths) > 4:
                preview_paths += f', ... (+{len(unique_paths) - 4} more)'
            lines.append(f'- Changed path preview: {preview_paths}')
        for entry in file_history[-10:]:
            action = str(entry.get('action', entry.get('tool_name', 'tool')))
            turn = entry.get('turn_index')
            path = entry.get('path')
            command = entry.get('command')
            details = [f'action={action}']
            history_entry_id = entry.get('history_entry_id')
            if isinstance(history_entry_id, str) and history_entry_id:
                details.append(f'entry_id={history_entry_id}')
            if turn is not None:
                details.append(f'turn={turn}')
            if path:
                details.append(f'path={path}')
            if command:
                details.append(f'command={command}')
            child_session_ids = entry.get('child_session_ids')
            if isinstance(child_session_ids, list) and child_session_ids:
                details.append(f'child_sessions={len(child_session_ids)}')
            delegate_batch_count = entry.get('delegate_batch_count')
            if isinstance(delegate_batch_count, int) and not isinstance(delegate_batch_count, bool):
                details.append(f'batches={delegate_batch_count}')
            dependency_skips = entry.get('dependency_skips')
            if isinstance(dependency_skips, int) and not isinstance(dependency_skips, bool):
                details.append(f'dependency_skips={dependency_skips}')
            lines.append(f"- {'; '.join(details)}")
            before_snapshot_id = entry.get('before_snapshot_id')
            if isinstance(before_snapshot_id, str) and before_snapshot_id:
                lines.append(f'  before_snapshot: {before_snapshot_id}')
            after_snapshot_id = entry.get('after_snapshot_id')
            if isinstance(after_snapshot_id, str) and after_snapshot_id:
                lines.append(f'  after_snapshot: {after_snapshot_id}')
            before_preview = entry.get('before_preview')
            if isinstance(before_preview, str) and before_preview:
                lines.append(f'  before: {before_preview}')
            after_preview = entry.get('after_preview')
            if isinstance(after_preview, str) and after_preview:
                lines.append(f'  after: {after_preview}')
            result_preview = entry.get('result_preview')
            if isinstance(result_preview, str) and result_preview:
                lines.append(f'  result: {result_preview}')
        if len(file_history) > 10:
            lines.append(f'- ... plus {len(file_history) - 10} older file-history entries')
        lines.extend(
            [
                '',
                'Use this replayed history when continuing the task so you avoid repeating prior edits or commands.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _append_compaction_replay_if_needed(
        self,
        session: AgentSessionState,
    ) -> None:
        """resume 时如果历史消息曾被压缩，追加压缩边界标记。"""
        compact_messages = [
            message for message in session.messages
            if message.metadata.get('kind') == 'compact_boundary'
        ]
        snipped_messages = [
            message for message in session.messages
            if message.metadata.get('kind') == 'snipped_message'
        ]
        if not compact_messages and not snipped_messages:
            return
        for message in reversed(session.messages):
            if message.metadata.get('kind') != 'compaction_replay':
                continue
            return
        session.append_user(
            self._render_compaction_replay(compact_messages, snipped_messages),
            metadata={
                'kind': 'compaction_replay',
                'compact_boundary_count': len(compact_messages),
                'snipped_message_count': len(snipped_messages),
            },
            message_id=(
                f'compaction_replay_{len(compact_messages)}_{len(snipped_messages)}'
            ),
        )

    def _render_compaction_replay(
        self,
        compact_messages,
        snipped_messages,
    ) -> str:
        """渲染压缩回放消息的文本内容。"""
        lines = [
            '<system-reminder>',
            'This resumed session already contains compacted or snipped history.',
            f'- Compact boundaries: {len(compact_messages)}',
            f'- Snipped/tombstoned messages: {len(snipped_messages)}',
        ]
        latest_boundary = compact_messages[-1] if compact_messages else None
        if latest_boundary is not None:
            lines.append(
                f"- Latest compact boundary id: {latest_boundary.message_id or '(none)'}"
            )
            depth = latest_boundary.metadata.get('compaction_depth')
            if isinstance(depth, int) and not isinstance(depth, bool):
                lines.append(f'- Latest compaction depth: {depth}')
            compacted_lineages = latest_boundary.metadata.get('compacted_lineage_ids')
            if isinstance(compacted_lineages, list) and compacted_lineages:
                lines.append(f'- Latest compacted lineages: {len(compacted_lineages)}')
            max_source_mutation_serial = latest_boundary.metadata.get('max_source_mutation_serial')
            if (
                isinstance(max_source_mutation_serial, int)
                and not isinstance(max_source_mutation_serial, bool)
                and max_source_mutation_serial > 0
            ):
                lines.append(
                    f'- Latest source mutation serial: {max_source_mutation_serial}'
                )
            source_mutation_totals = latest_boundary.metadata.get('source_mutation_totals')
            if isinstance(source_mutation_totals, dict) and source_mutation_totals:
                rendered = ', '.join(
                    f'{name}:{count}'
                    for name, count in sorted(source_mutation_totals.items())
                    if isinstance(name, str)
                    and name
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                    and count > 0
                )
                if rendered:
                    lines.append(f'- Latest compacted mutations: {rendered}')
            preserved_tail = latest_boundary.metadata.get('preserved_tail_ids')
            if isinstance(preserved_tail, list) and preserved_tail:
                lines.append(
                    '- Latest preserved tail ids: '
                    + ', '.join(str(item) for item in preserved_tail[:4])
                )
        if snipped_messages:
            last_ids = [
                message.message_id or '(none)'
                for message in snipped_messages[-3:]
            ]
            lines.append(f"- Recent snipped ids: {', '.join(last_ids)}")
            snipped_lineages = [
                str(message.metadata.get('snipped_from_lineage_id'))
                for message in snipped_messages[-3:]
                if isinstance(message.metadata.get('snipped_from_lineage_id'), str)
            ]
            if snipped_lineages:
                lines.append(f"- Recent snipped lineages: {', '.join(snipped_lineages)}")
        lines.extend(
            [
                '',
                'Use the surviving transcript plus the compacted summaries as the authoritative context when continuing.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _apply_hook_policy_before_prompt_hooks(self, prompt: str) -> str:
        """执行 hook 策略的 before-prompt 钩子，可修改或增强用户 prompt。"""
        if self.hook_policy_runtime is None or not self.hook_policy_runtime.manifests:
            return prompt
        injections = self.hook_policy_runtime.before_prompt_messages()
        managed_settings = self.hook_policy_runtime.managed_settings()
        safe_env = self.hook_policy_runtime.safe_env()
        trusted = self.hook_policy_runtime.is_trusted()
        if not injections and not managed_settings and not safe_env and trusted:
            return prompt
        lines = ['<system-reminder>', 'Workspace hook/policy guidance:']
        lines.append(
            f'- Trust mode: {"trusted" if trusted else "untrusted"}'
        )
        if not trusted:
            lines.append(
                '- Untrusted workspaces should favor inspection-first behavior. '
                'Avoid unnecessary writes or shell actions unless the task clearly requires them.'
            )
        for entry in injections:
            lines.append(f'- Before prompt: {entry}')
        if managed_settings:
            lines.append(
                '- Managed settings: '
                + ', '.join(f'{key}={value}' for key, value in sorted(managed_settings.items()))
            )
        if safe_env:
            lines.append(
                '- Safe environment values loaded for tools: '
                + ', '.join(sorted(safe_env))
            )
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _build_plugin_tool_runtime_message(
        self,
        *,
        tool_name: str,
        preflight_messages: tuple[str, ...],
        block_message: str | None,
        plugin_messages: tuple[str, ...],
        hook_policy_preflight_messages: tuple[str, ...] = (),
        hook_policy_block_message: str | None = None,
        hook_policy_messages: tuple[str, ...] = (),
        delegate_preflight_messages: tuple[str, ...] = (),
        delegate_after_messages: tuple[str, ...] = (),
    ) -> str | None:
        """构建插件系统在工具执行前后注入的运行时消息。"""
        if (
            block_message is None
            and not plugin_messages
            and not preflight_messages
            and hook_policy_block_message is None
            and not hook_policy_preflight_messages
            and not hook_policy_messages
            and not delegate_preflight_messages
            and not delegate_after_messages
        ):
            return None
        plugin_only = (
            hook_policy_block_message is None
            and not hook_policy_preflight_messages
            and not hook_policy_messages
        )
        lines = [
            '<system-reminder>',
            (
                f'Plugin tool runtime guidance for `{tool_name}`:'
                if plugin_only
                else f'Runtime tool guidance for `{tool_name}`:'
            ),
        ]
        for message in preflight_messages:
            lines.append(f'- Before tool: {message}')
        for message in hook_policy_preflight_messages:
            lines.append(f'- Hook/policy before tool: {message}')
        for message in delegate_preflight_messages:
            lines.append(f'- Before delegate: {message}')
        if block_message is not None:
            lines.append(f'- Blocked: {block_message}')
        if hook_policy_block_message is not None:
            lines.append(f'- Hook/policy blocked: {hook_policy_block_message}')
        for message in plugin_messages:
            lines.append(f'- After result: {message}')
        for message in hook_policy_messages:
            lines.append(f'- Hook/policy after result: {message}')
        for message in delegate_after_messages:
            lines.append(f'- After delegate: {message}')
        lines.extend(
            [
                '',
                'Use this runtime guidance when deciding the next tool call or assistant response.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _plugin_tool_preflight_messages(self, tool_name: str) -> tuple[str, ...]:
        """获取插件在工具执行前要注入的预检消息。"""
        if self.plugin_runtime is None:
            return ()
        return self.plugin_runtime.tool_preflight_injections(tool_name)

    def _plugin_block_message(self, tool_name: str) -> str | None:
        """获取插件是否要阻止某工具执行的拦截消息。"""
        if self.plugin_runtime is None:
            return None
        return self.plugin_runtime.blocked_tool_message(tool_name)

    def _plugin_tool_result_messages(self, tool_name: str) -> tuple[str, ...]:
        """获取插件在工具执行后要注入的后处理消息。"""
        if self.plugin_runtime is None:
            return ()
        return self.plugin_runtime.tool_result_injections(tool_name)

    def _hook_policy_tool_preflight_messages(self, tool_name: str) -> tuple[str, ...]:
        """获取 hook 策略在工具执行前要注入的预检消息。"""
        if self.hook_policy_runtime is None:
            return ()
        return self.hook_policy_runtime.before_tool_messages(tool_name)

    def _hook_policy_block_message(self, tool_name: str) -> str | None:
        """获取 hook 策略是否要阻止某工具执行的拦截消息。"""
        if self.hook_policy_runtime is None:
            return None
        return self.hook_policy_runtime.denied_tool_message(tool_name)

    def _hook_policy_tool_result_messages(self, tool_name: str) -> tuple[str, ...]:
        """获取 hook 策略在工具执行后要注入的后处理消息。"""
        if self.hook_policy_runtime is None:
            return ()
        return self.hook_policy_runtime.after_tool_messages(tool_name)

    def _persist_session(
        self,
        session: AgentSessionState,
        result: AgentRunResult,
    ) -> AgentRunResult:
        """将会话状态（消息、用量、文件历史等）序列化到磁盘，支持跨进程 resume。"""
        if result.session_id is None:
            return result
        persist_events = list(result.events)
        if self.plugin_runtime is not None:
            persist_messages = self.plugin_runtime.before_persist_injections()
            if persist_messages:
                session.append_user(
                    self._render_plugin_persist_message(persist_messages),
                    metadata={
                        'kind': 'plugin_persist',
                        'message_count': len(persist_messages),
                    },
                    message_id=f'plugin_persist_{result.session_id}',
                )
                persist_events.append(
                    {
                        'type': 'plugin_before_persist',
                        'session_id': result.session_id,
                        'message_count': len(persist_messages),
                    }
                )
        previous_turns = 0
        previous_tool_calls = 0
        previous_budget_state: dict[str, object] = {}
        existing_path = self.runtime_config.session_directory / f'{result.session_id}.json'
        if existing_path.exists():
            try:
                previous = load_agent_session(
                    result.session_id,
                    directory=self.runtime_config.session_directory,
                )
            except OSError:
                previous = None
            if previous is not None:
                previous_turns = previous.turns
                previous_tool_calls = previous.tool_calls
                if isinstance(previous.budget_state, dict):
                    previous_budget_state = dict(previous.budget_state)
        budget_state = {
            'model_calls': int(previous_budget_state.get('model_calls', 0))
            + max(result.turns, 0),
            'session_turns': previous_turns + result.turns,
            'tool_calls': previous_tool_calls + result.tool_calls,
            'delegated_tasks': sum(
                1 for entry in result.file_history if entry.get('action') in ('delegate_agent', 'Agent')
            ),
        }
        stored = StoredAgentSession(
            session_id=result.session_id,
            model_config=serialize_model_config(self.model_config),
            runtime_config=serialize_runtime_config(self.runtime_config),
            system_prompt_parts=session.system_prompt_parts,
            user_context=dict(session.user_context),
            system_context=dict(session.system_context),
            messages=session.transcript(),
            turns=previous_turns + result.turns,
            tool_calls=previous_tool_calls + result.tool_calls,
            usage=result.usage.to_dict(),
            total_cost_usd=result.total_cost_usd,
            file_history=result.file_history,
            budget_state=budget_state,
            plugin_state=(
                self.plugin_runtime.export_session_state()
                if self.plugin_runtime is not None
                else {}
            ),
            scratchpad_directory=result.scratchpad_directory,
        )
        path = save_agent_session(
            stored,
            directory=self.runtime_config.session_directory,
        )
        self.last_session_path = str(path)
        return replace(
            result,
            session_path=self.last_session_path,
            events=tuple(persist_events),
            transcript=session.transcript(),
        )

    def _finalize_managed_agent(self, result: AgentRunResult) -> None:
        """运行结束后向 AgentManager 报告结果（用量、费用、停止原因等）。"""
        if self.managed_agent_id is None or self.agent_manager is None:
            self.resume_source_session_id = None
            return
        self.agent_manager.finish_agent(
            self.managed_agent_id,
            session_id=result.session_id,
            session_path=result.session_path,
            turns=result.turns,
            tool_calls=result.tool_calls,
            stop_reason=result.stop_reason,
        )
        self.resume_source_session_id = None

    def _accumulate_usage(self, result: AgentRunResult) -> None:
        """Add a run's usage to the cumulative session totals."""
        self.cumulative_usage = self.cumulative_usage + result.usage
        self.cumulative_cost_usd += result.total_cost_usd

    def _refresh_runtime_views_for_tool_result(
        self,
        tool_name: str,
        tool_result: ToolExecutionResult,
    ) -> None:
        """根据工具执行结果刷新各 runtime 的视图状态（如 CWD 变更后刷新工具上下文）。"""
        if not tool_result.ok:
            return
        cwd_update = tool_result.metadata.get('cwd_update')
        if isinstance(cwd_update, str) and cwd_update:
            self._apply_runtime_cwd_update(Path(cwd_update))
        refresh_tool_names = {
            'update_plan',
            'plan_clear',
            'task_create',
            'task_update',
            'task_start',
            'task_complete',
            'task_block',
            'task_cancel',
            'todo_write',
            'search_activate_provider',
            'remote_connect',
            'remote_disconnect',
            'account_login',
            'account_logout',
            'config_set',
            'ask_user_question',
            'team_create',
            'team_delete',
            'send_message',
            'workflow_run',
            'remote_trigger',
            'worktree_enter',
            'worktree_exit',
        }
        if tool_name not in refresh_tool_names:
            return
        clear_context_caches()
        additional_dirs = tuple(
            str(path) for path in self.runtime_config.additional_working_directories
        )
        if tool_name.startswith('remote_'):
            self.remote_runtime = RemoteRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name == 'remote_trigger':
            self.remote_trigger_runtime = RemoteTriggerRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('search_'):
            self.search_runtime = SearchRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('account_'):
            self.account_runtime = AccountRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name == 'ask_user_question':
            self.ask_user_runtime = AskUserRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name == 'config_set':
            self.config_runtime = ConfigRuntime.from_workspace(self.runtime_config.cwd)
        if tool_name.startswith('task_') or tool_name == 'todo_write':
            self.task_runtime = TaskRuntime.from_workspace(self.runtime_config.cwd)
        if tool_name.startswith('plan_') or tool_name == 'update_plan':
            self.plan_runtime = PlanRuntime.from_workspace(self.runtime_config.cwd)
        if tool_name.startswith('team_') or tool_name == 'send_message':
            self.team_runtime = TeamRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('workflow_'):
            self.workflow_runtime = WorkflowRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('worktree_'):
            self.worktree_runtime = WorktreeRuntime.from_workspace(self.runtime_config.cwd)
        self.tool_context = replace(
            self.tool_context,
            tool_registry=self.tool_registry,
            search_runtime=self.search_runtime,
            account_runtime=self.account_runtime,
            ask_user_runtime=self.ask_user_runtime,
            config_runtime=self.config_runtime,
            lsp_runtime=self.lsp_runtime,
            remote_runtime=self.remote_runtime,
            remote_trigger_runtime=self.remote_trigger_runtime,
            plan_runtime=self.plan_runtime,
            task_runtime=self.task_runtime,
            team_runtime=self.team_runtime,
            workflow_runtime=self.workflow_runtime,
            worktree_runtime=self.worktree_runtime,
        )

    def _apply_runtime_cwd_update(self, new_cwd: Path) -> None:
        """当工作目录发生变更时，刷新所有依赖 CWD 的 runtime 和工具上下文。"""
        resolved_cwd = new_cwd.resolve()
        if resolved_cwd == self.runtime_config.cwd.resolve():
            return
        self.runtime_config = replace(self.runtime_config, cwd=resolved_cwd)
        clear_context_caches()
        additional_dirs = tuple(
            str(path) for path in self.runtime_config.additional_working_directories
        )
        self.plugin_runtime = PluginRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.hook_policy_runtime = HookPolicyRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.mcp_runtime = MCPRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.remote_runtime = RemoteRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.remote_trigger_runtime = RemoteTriggerRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.search_runtime = SearchRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.account_runtime = AccountRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.ask_user_runtime = AskUserRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.config_runtime = ConfigRuntime.from_workspace(self.runtime_config.cwd)
        self.lsp_runtime = LSPRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.task_runtime = TaskRuntime.from_workspace(self.runtime_config.cwd)
        self.plan_runtime = PlanRuntime.from_workspace(self.runtime_config.cwd)
        self.team_runtime = TeamRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.workflow_runtime = WorkflowRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.worktree_runtime = WorktreeRuntime.from_workspace(self.runtime_config.cwd)
        self.runtime_config = self._apply_hook_policy_budget_overrides(self.runtime_config)
        registry = dict(default_tool_registry())
        if self.plugin_runtime is not None:
            alias_tools = self.plugin_runtime.register_tool_aliases(registry)
            if alias_tools:
                registry = {**registry, **alias_tools}
            virtual_tools = self.plugin_runtime.register_virtual_tools(registry)
            if virtual_tools:
                registry = {**registry, **virtual_tools}
        self.tool_registry = registry
        self.tool_context = build_tool_context(
            self.runtime_config,
            tool_registry=self.tool_registry,
            extra_env=(
                self.hook_policy_runtime.safe_env()
                if self.hook_policy_runtime is not None
                else None
            ),
            search_runtime=self.search_runtime,
            account_runtime=self.account_runtime,
            ask_user_runtime=self.ask_user_runtime,
            config_runtime=self.config_runtime,
            lsp_runtime=self.lsp_runtime,
            mcp_runtime=self.mcp_runtime,
            remote_runtime=self.remote_runtime,
            remote_trigger_runtime=self.remote_trigger_runtime,
            plan_runtime=self.plan_runtime,
            task_runtime=self.task_runtime,
            team_runtime=self.team_runtime,
            workflow_runtime=self.workflow_runtime,
            worktree_runtime=self.worktree_runtime,
        )

    def _apply_plugin_before_prompt_hooks(self, prompt: str) -> str:
        """执行插件的 before-prompt 钩子。"""
        if self.plugin_runtime is None:
            return prompt
        injections = self.plugin_runtime.before_prompt_injections()
        state_reminder = self.plugin_runtime.runtime_state_reminder()
        if not injections and not state_reminder:
            return prompt
        lines = ['<system-reminder>', 'Plugin before-prompt hooks:']
        lines.extend(f'- {entry}' for entry in injections)
        if state_reminder:
            lines.extend(['', state_reminder])
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _apply_plugin_resume_hooks(
        self,
        prompt: str,
        *,
        resumed: bool,
    ) -> str:
        """执行插件的 resume 钩子（仅在 resume 场景触发）。"""
        if not resumed or self.plugin_runtime is None:
            return prompt
        injections = self.plugin_runtime.on_resume_injections()
        if not injections:
            return prompt
        lines = ['<system-reminder>', 'Plugin resume hooks:']
        lines.extend(f'- {entry}' for entry in injections)
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _render_plugin_persist_message(
        self,
        messages: tuple[str, ...],
    ) -> str:
        """渲染插件需要持久化的会话状态消息。"""
        lines = ['<system-reminder>', 'Plugin persist hooks:']
        lines.extend(f'- {entry}' for entry in messages)
        lines.extend(
            [
                '',
                'This session state was persisted with plugin lifecycle guidance.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _append_plugin_after_turn_events(
        self,
        result: AgentRunResult,
        *,
        prompt: str,
        turn_index: int,
    ) -> AgentRunResult:
        """在每轮结束后追加插件产生的后续事件。"""
        if self.plugin_runtime is None:
            return result
        injections = self.plugin_runtime.after_turn_injections()
        if not injections:
            return result
        appended = list(result.events)
        for entry in injections:
            appended.append(
                {
                    'type': 'plugin_after_turn',
                    'turn_index': turn_index,
                    'message': entry,
                    'prompt_preview': self._preview_text(prompt, 120),
                    'stop_reason': result.stop_reason,
                }
            )
        return replace(result, events=tuple(appended))

    def _append_runtime_after_turn_events(
        self,
        result: AgentRunResult,
        *,
        prompt: str,
        turn_index: int,
    ) -> AgentRunResult:
        """在每轮结束后追加运行时产生的后续事件。"""
        updated = self._append_plugin_after_turn_events(
            result,
            prompt=prompt,
            turn_index=turn_index,
        )
        if self.hook_policy_runtime is None:
            return updated
        injections = self.hook_policy_runtime.after_turn_messages()
        if not injections:
            return updated
        appended = list(updated.events)
        for entry in injections:
            appended.append(
                {
                    'type': 'hook_policy_after_turn',
                    'turn_index': turn_index,
                    'message': entry,
                    'prompt_preview': self._preview_text(prompt, 120),
                    'stop_reason': updated.stop_reason,
                    'trusted': self.hook_policy_runtime.is_trusted(),
                }
            )
        return replace(updated, events=tuple(appended))


def _optional_policy_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_policy_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
