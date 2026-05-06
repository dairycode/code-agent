"""Built-in agent type definitions.

Mirrors the npm ``src/tools/AgentTool/built-in/`` directory.  Each agent
type defines its model, tool restrictions, system prompt, and behavioral
flags.  The runtime uses these definitions to configure child agents when
the ``Agent`` tool is invoked with a ``subagent_type`` parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Agent definition dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentDefinition:
    """A single agent type definition (built-in, user, or plugin)."""

    agent_type: str
    when_to_use: str
    system_prompt: str = ''
    model: str | None = None  # 'sonnet', 'opus', 'haiku', 'inherit', or None (default)
    tools: tuple[str, ...] | None = None  # Allow-list; None means all
    disallowed_tools: tuple[str, ...] = ()  # Deny-list
    color: str | None = None
    background: bool = False
    one_shot: bool = False
    omit_claude_md: bool = False
    permission_mode: str | None = None  # 'dontAsk', 'plan', etc.
    max_turns: int | None = None
    critical_system_reminder: str | None = None
    source: str = 'built-in'
    filename: str | None = None
    base_dir: str | None = None
    skills: tuple[str, ...] = ()
    memory: str | None = None
    effort: str | int | None = None
    initial_prompt: str | None = None
    isolation: str | None = None
    hook_names: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Disallowed tool sets (mirrors npm constants/tools.ts)
# ---------------------------------------------------------------------------

ALL_AGENT_DISALLOWED_TOOLS = frozenset({
    'task_output', 'plan_get', 'update_plan', 'plan_clear',
    'ask_user_question', 'task_stop',
})
"""Tools disallowed for all child agents by default."""

"""Tools disallowed for read-only agents (Explore, Plan, verification)."""


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_GENERAL_PURPOSE_SYSTEM_PROMPT = """\
You are an agent for Code Agent, a Python reimplementation of a \
Claude Code-style coding agent. Given the user's message, you should use \
the tools available to complete the task. Complete the task fully — don't \
gold-plate, but don't leave it half-done.

## Strengths

- Searching code, configs, and patterns across large codebases
- Analyzing multiple files to understand architecture
- Investigating complex questions that need multi-file context
- Multi-step research and implementation tasks

## Guidelines

- Search broadly first when the location of relevant code is unknown
- Use read_file for specific known paths; use glob_search and grep_search \
for discovery
- Start broad, then narrow down to specifics
- Be thorough — check multiple locations and naming conventions
- NEVER create files unless it is absolutely necessary for achieving your goal
- NEVER proactively create documentation files (*.md) or README files \
unless explicitly requested

Your response should be a concise report covering what was done and key \
findings."""

# ---------------------------------------------------------------------------
# Built-in agent instances
# ---------------------------------------------------------------------------

GENERAL_PURPOSE_AGENT = AgentDefinition(
    agent_type='general-purpose',
    when_to_use=(
        'General-purpose agent for researching complex questions, searching '
        'for code, and executing multi-step tasks. When you are searching '
        'for a keyword or file and are not confident that you will find the '
        'right match in the first few tries use this agent to perform the '
        'search for you.'
    ),
    system_prompt=_GENERAL_PURPOSE_SYSTEM_PROMPT,
    tools=None,  # all tools
)

# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

"""Agent types that run once and return a report (no agentId / SendMessage)."""

_BUILTIN_AGENTS: tuple[AgentDefinition, ...] = (
    GENERAL_PURPOSE_AGENT,
)


def get_builtin_agents() -> tuple[AgentDefinition, ...]:
    """Return all built-in agent definitions."""
    return _BUILTIN_AGENTS


def format_agent_listing(agents: tuple[AgentDefinition, ...] | None = None) -> str:
    """Format agent types for inclusion in the Agent tool prompt.

    Mirrors the npm ``formatAgentLine`` helper.
    """
    if agents is None:
        agents = _BUILTIN_AGENTS
    lines: list[str] = []
    for agent in agents:
        tools_desc = describe_agent_tools(agent)
        lines.append(f'- {agent.agent_type}: {agent.when_to_use} (Tools: {tools_desc})')
    return '\n'.join(lines)


def describe_agent_tools(agent: AgentDefinition) -> str:
    """Describe the tool access for an agent definition."""
    if agent.tools is not None:
        return ', '.join(agent.tools) if agent.tools else 'none'
    if agent.disallowed_tools:
        denied = ', '.join(sorted(agent.disallowed_tools))
        return f'All tools except {denied}'
    return '*'
