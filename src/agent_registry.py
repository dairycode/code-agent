from __future__ import annotations

import ast
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .builtin_agents import AgentDefinition, get_builtin_agents

_FRONTMATTER_RE = re.compile(r'^---\n(.*?)\n---\n?(.*)$', re.DOTALL)
_AGENTS_DIR = Path('.claude') / 'agents'
_SOURCE_ORDER = {
    'built-in': 0,
    'userSettings': 1,
    'projectSettings': 2,
}
@dataclass(frozen=True)
class AgentLoadError:
    path: str
    source: str
    error: str


@dataclass(frozen=True)
class AgentRegistrySnapshot:
    all_agents: tuple[AgentDefinition, ...]
    active_agents: tuple[AgentDefinition, ...]
    shadowed_agents: tuple[AgentDefinition, ...]
    failed_files: tuple[AgentLoadError, ...]


def load_agent_registry(cwd: Path) -> AgentRegistrySnapshot:
    """加载所有内置和自定义代理定义，返回注册表快照。"""
    builtin_agents = tuple(get_builtin_agents())
    loaded_agents: list[AgentDefinition] = list(builtin_agents)
    failed_files: list[AgentLoadError] = []
    for source, directory in iter_agent_directories(cwd):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob('*.md')):
            try:
                loaded_agents.append(load_agent_markdown(path, source=source))
            except (OSError, ValueError) as exc:
                failed_files.append(
                    AgentLoadError(
                        path=str(path),
                        source=source,
                        error=str(exc),
                    )
                )
    active_agents, shadowed_agents = resolve_active_agents(tuple(loaded_agents))
    return AgentRegistrySnapshot(
        all_agents=tuple(loaded_agents),
        active_agents=active_agents,
        shadowed_agents=shadowed_agents,
        failed_files=tuple(failed_files),
    )


def iter_agent_directories(cwd: Path) -> tuple[tuple[str, Path], ...]:
    """返回用户和项目级别的代理定义目录。"""
    return (
        ('userSettings', Path.home() / _AGENTS_DIR),
        ('projectSettings', cwd / _AGENTS_DIR),
    )


def find_agent_definition(
    cwd: Path,
    agent_type: str,
    *,
    active_only: bool = True,
) -> AgentDefinition | None:
    snapshot = load_agent_registry(cwd)
    pool = snapshot.active_agents if active_only else snapshot.all_agents
    for agent in pool:
        if agent.agent_type == agent_type:
            return agent
    return None


def resolve_active_agents(
    all_agents: tuple[AgentDefinition, ...],
) -> tuple[tuple[AgentDefinition, ...], tuple[AgentDefinition, ...]]:
    active_by_name: dict[str, AgentDefinition] = {}
    for agent in all_agents:
        current = active_by_name.get(agent.agent_type)
        if current is None or _source_rank(agent.source) >= _source_rank(current.source):
            active_by_name[agent.agent_type] = agent

    active_agents = tuple(
        sorted(
            active_by_name.values(),
            key=lambda agent: (_source_rank(agent.source), agent.agent_type.lower()),
        )
    )
    shadowed_agents = tuple(
        sorted(
            (
                agent
                for agent in all_agents
                if active_by_name.get(agent.agent_type) is not agent
            ),
            key=lambda agent: (agent.agent_type.lower(), _source_rank(agent.source)),
        )
    )
    return active_agents, shadowed_agents


def load_agent_markdown(path: Path, *, source: str) -> AgentDefinition:
    text = path.read_text(encoding='utf-8')
    metadata, body = _split_frontmatter(text)
    agent_type = str(metadata.get('name') or path.stem).strip()
    if not agent_type:
        raise ValueError(f'Agent file {path} is missing a name')
    when_to_use = str(
        metadata.get('description')
        or metadata.get('whenToUse')
        or metadata.get('when_to_use')
        or ''
    ).strip()
    if not when_to_use:
        raise ValueError(f'Agent file {path} is missing a description')
    system_prompt = body.strip()
    if not system_prompt:
        system_prompt = str(metadata.get('prompt') or '').strip()
    if not system_prompt:
        raise ValueError(f'Agent file {path} is missing a system prompt body')
    tools = _parse_tool_list(metadata.get('tools'))
    disallowed_tools = tuple(_coerce_string_list(metadata.get('disallowedTools')))
    return AgentDefinition(
        agent_type=agent_type,
        when_to_use=when_to_use,
        system_prompt=system_prompt,
        model=_coerce_optional_string(metadata.get('model')),
        tools=tools,
        disallowed_tools=disallowed_tools,
        color=_coerce_optional_string(metadata.get('color')),
        background=_coerce_bool(metadata.get('background')),
        one_shot=_coerce_bool(metadata.get('oneShot')),
        omit_claude_md=_coerce_bool(metadata.get('omitClaudeMd')),
        permission_mode=_coerce_optional_string(
            metadata.get('permissionMode') or metadata.get('permission_mode')
        ),
        max_turns=_coerce_optional_int(metadata.get('maxTurns')),
        critical_system_reminder=_coerce_optional_string(
            metadata.get('criticalSystemReminder')
            or metadata.get('criticalSystemReminder_EXPERIMENTAL')
        ),
        source=source,
        filename=path.stem,
        base_dir=str(path.parent),
        skills=tuple(_coerce_string_list(metadata.get('skills'))),
        memory=_coerce_optional_string(metadata.get('memory')),
        effort=_coerce_effort(metadata.get('effort')),
        initial_prompt=_coerce_optional_string(
            metadata.get('initialPrompt') or metadata.get('initial_prompt')
        ),
        isolation=_coerce_optional_string(metadata.get('isolation')),
        hook_names=tuple(_coerce_hook_names(metadata.get('hooks'))),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    normalized = text.replace('\r\n', '\n')
    match = _FRONTMATTER_RE.match(normalized)
    if match is None:
        return {}, normalized
    return _parse_frontmatter(match.group(1)), match.group(2)


def _parse_frontmatter(block: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' not in line:
            continue
        key, raw_value = line.split(':', 1)
        metadata[key.strip()] = _parse_frontmatter_value(raw_value.strip())
    return metadata


def _parse_frontmatter_value(value: str) -> Any:
    if not value:
        return ''
    if value.startswith(('"', "'")) and value.endswith(('"', "'")):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value[1:-1]
    lowered = value.lower()
    if lowered == 'true':
        return True
    if lowered == 'false':
        return False
    if re.fullmatch(r'-?\d+', value):
        return int(value)
    if value.startswith('[') and value.endswith(']'):
        inner = value[1:-1].strip()
        if not inner:
            return []
        reader = csv.reader([inner], skipinitialspace=True)
        return [item.strip().strip('"').strip("'") for item in next(reader)]
    return value


def _parse_tool_list(value: Any) -> tuple[str, ...] | None:
    if value is None or value == '':
        return None
    tools = tuple(_coerce_string_list(value))
    if not tools:
        return None
    if tools == ('*',):
        return None
    return tools


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith('[') and stripped.endswith(']'):
            parsed = _parse_frontmatter_value(stripped)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in stripped.split(',') if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _coerce_effort(value: Any) -> str | int | None:
    if value is None or value == '':
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return text


def _coerce_hook_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [str(item).strip() for item in value if str(item).strip()]
    return _coerce_string_list(value)


def _source_rank(source: str) -> int:
    return _SOURCE_ORDER.get(source, -1)
