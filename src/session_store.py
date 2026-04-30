from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent_types import (
    AgentRuntimeConfig,
    ModelConfig,
    ModelPricing,
    OutputSchemaConfig,
    UsageStats,
)


DEFAULT_SESSION_DIR = Path('.port_sessions')
DEFAULT_AGENT_SESSION_DIR = DEFAULT_SESSION_DIR / 'agent'


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class StoredAgentSession:
    """存储 Agent 会话的不可变数据结构。"""
    session_id: str
    model_config: JSONDict
    runtime_config: JSONDict
    system_prompt_parts: tuple[str, ...]
    user_context: dict[str, str]
    system_context: dict[str, str]
    messages: tuple[JSONDict, ...]
    turns: int
    tool_calls: int
    usage: JSONDict
    total_cost_usd: float
    file_history: tuple[JSONDict, ...]
    budget_state: JSONDict
    plugin_state: JSONDict
    scratchpad_directory: str | None = None


def save_agent_session(session: StoredAgentSession, directory: Path | None = None) -> Path:
    """将 Agent 会话序列化为 JSON 并保存到指定目录。"""
    target_dir = directory or DEFAULT_AGENT_SESSION_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f'{session.session_id}.json'
    path.write_text(json.dumps(asdict(session), indent=2), encoding='utf-8')
    return path


def load_agent_session(session_id: str, directory: Path | None = None) -> StoredAgentSession:
    """从 JSON 文件加载并还原指定的 Agent 会话。"""
    target_dir = directory or DEFAULT_AGENT_SESSION_DIR
    data = json.loads((target_dir / f'{session_id}.json').read_text(encoding='utf-8'))
    return StoredAgentSession(
        session_id=data['session_id'],
        model_config=dict(data['model_config']),
        runtime_config=dict(data['runtime_config']),
        system_prompt_parts=tuple(data['system_prompt_parts']),
        user_context=dict(data['user_context']),
        system_context=dict(data['system_context']),
        messages=tuple(
            message for message in data['messages'] if isinstance(message, dict)
        ),
        turns=int(data['turns']),
        tool_calls=int(data['tool_calls']),
        usage=dict(data.get('usage', {})),
        total_cost_usd=float(data.get('total_cost_usd', 0.0)),
        file_history=tuple(
            entry for entry in data.get('file_history', []) if isinstance(entry, dict)
        ),
        budget_state=(
            dict(data.get('budget_state', {}))
            if isinstance(data.get('budget_state'), dict)
            else {}
        ),
        plugin_state=(
            dict(data.get('plugin_state', {}))
            if isinstance(data.get('plugin_state'), dict)
            else {}
        ),
        scratchpad_directory=(
            str(data['scratchpad_directory'])
            if isinstance(data.get('scratchpad_directory'), str)
            else None
        ),
    )


def serialize_model_config(model_config: ModelConfig) -> JSONDict:
    """将 ModelConfig 对象序列化为 JSON 兼容的字典。"""
    return {
        'model': model_config.model,
        'base_url': model_config.base_url,
        'api_key': model_config.api_key,
        'temperature': model_config.temperature,
        'timeout_seconds': model_config.timeout_seconds,
        'pricing': {
            'input_cost_per_million_tokens_usd': model_config.pricing.input_cost_per_million_tokens_usd,
            'output_cost_per_million_tokens_usd': model_config.pricing.output_cost_per_million_tokens_usd,
            'cache_creation_input_cost_per_million_tokens_usd': model_config.pricing.cache_creation_input_cost_per_million_tokens_usd,
            'cache_read_input_cost_per_million_tokens_usd': model_config.pricing.cache_read_input_cost_per_million_tokens_usd,
        },
    }


def serialize_runtime_config(runtime_config: AgentRuntimeConfig) -> JSONDict:
    """将 AgentRuntimeConfig 对象序列化为 JSON 兼容的字典。"""
    return {
        'cwd': str(runtime_config.cwd),
        'max_turns': runtime_config.max_turns,
        'command_timeout_seconds': runtime_config.command_timeout_seconds,
        'max_output_chars': runtime_config.max_output_chars,
        'stream_model_responses': runtime_config.stream_model_responses,
        'auto_snip_threshold_tokens': runtime_config.auto_snip_threshold_tokens,
        'auto_compact_threshold_tokens': runtime_config.auto_compact_threshold_tokens,
        'compact_preserve_messages': runtime_config.compact_preserve_messages,
        'permissions': {
            'allow_file_write': runtime_config.permissions.allow_file_write,
            'allow_shell_commands': runtime_config.permissions.allow_shell_commands,
            'allow_destructive_shell_commands': runtime_config.permissions.allow_destructive_shell_commands,
        },
        'additional_working_directories': [str(path) for path in runtime_config.additional_working_directories],
        'disable_claude_md_discovery': runtime_config.disable_claude_md_discovery,
        'budget_config': {
            'max_total_tokens': runtime_config.budget_config.max_total_tokens,
            'max_input_tokens': runtime_config.budget_config.max_input_tokens,
            'max_output_tokens': runtime_config.budget_config.max_output_tokens,
            'max_reasoning_tokens': runtime_config.budget_config.max_reasoning_tokens,
            'max_total_cost_usd': runtime_config.budget_config.max_total_cost_usd,
            'max_tool_calls': runtime_config.budget_config.max_tool_calls,
            'max_delegated_tasks': runtime_config.budget_config.max_delegated_tasks,
            'max_model_calls': runtime_config.budget_config.max_model_calls,
            'max_session_turns': runtime_config.budget_config.max_session_turns,
        },
        'output_schema': (
            {
                'name': runtime_config.output_schema.name,
                'schema': runtime_config.output_schema.schema,
                'strict': runtime_config.output_schema.strict,
            }
            if runtime_config.output_schema is not None
            else None
        ),
        'session_directory': str(runtime_config.session_directory),
        'scratchpad_root': str(runtime_config.scratchpad_root),
    }


def usage_from_payload(payload: JSONDict | None) -> UsageStats:
    """从字典载荷解析并构造 UsageStats 对象。"""
    if not isinstance(payload, dict):
        return UsageStats()
    return UsageStats(
        input_tokens=_optional_int(payload.get('input_tokens')) or 0,
        output_tokens=_optional_int(payload.get('output_tokens')) or 0,
        cache_creation_input_tokens=_optional_int(payload.get('cache_creation_input_tokens')) or 0,
        cache_read_input_tokens=_optional_int(payload.get('cache_read_input_tokens')) or 0,
        reasoning_tokens=_optional_int(payload.get('reasoning_tokens')) or 0,
    )


def _optional_int(value: Any) -> int | None:
    """将任意值安全转换为 int，无法转换时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


