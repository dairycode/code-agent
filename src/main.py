"""agent-chat 链路入口：精简版 main.py，仅保留交互式 REPL 对话所需的代码。"""
from __future__ import annotations

import argparse
import os
import json
from pathlib import Path
from typing import Callable

from .agent_runtime import LocalCodingAgent
from .agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    BudgetConfig,
    ModelConfig,
    ModelPricing,
    OutputSchemaConfig,
)
from .session_store import (
    load_agent_session,
)




def _load_output_schema_config(args: argparse.Namespace) -> OutputSchemaConfig | None:
    """加载可选的结构化输出 JSON Schema，用于约束模型响应格式。"""
    schema_file = getattr(args, 'response_schema_file', None)
    if not schema_file:
        return None
    payload = json.loads(Path(schema_file).read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('response schema file must contain a top-level JSON object')
    name = getattr(args, 'response_schema_name', None) or Path(schema_file).stem
    return OutputSchemaConfig(
        name=name,
        schema=payload,
        strict=bool(getattr(args, 'response_schema_strict', False)),
    )


def _build_runtime_config(args: argparse.Namespace) -> AgentRuntimeConfig:
    """从命令行参数构建 Agent 运行时配置，包括权限、预算、压缩策略等。"""
    return AgentRuntimeConfig(
        cwd=Path(args.cwd).resolve(),
        max_turns=getattr(args, 'max_turns', 12),
        permissions=AgentPermissions(
            allow_file_write=args.allow_write,
            allow_shell_commands=args.allow_shell,
            allow_destructive_shell_commands=args.unsafe,
        ),
        stream_model_responses=bool(getattr(args, 'stream', False)),
        auto_snip_threshold_tokens=getattr(args, 'auto_snip_threshold', None),
        auto_compact_threshold_tokens=getattr(args, 'auto_compact_threshold', None),
        compact_preserve_messages=max(0, int(getattr(args, 'compact_preserve_messages', 4))),
        additional_working_directories=tuple(Path(path).resolve() for path in args.add_dir),
        disable_claude_md_discovery=args.disable_claude_md,
        budget_config=BudgetConfig(
            max_total_tokens=getattr(args, 'max_total_tokens', None),
            max_input_tokens=getattr(args, 'max_input_tokens', None),
            max_output_tokens=getattr(args, 'max_output_tokens', None),
            max_reasoning_tokens=getattr(args, 'max_reasoning_tokens', None),
            max_total_cost_usd=getattr(args, 'max_budget_usd', None),
            max_tool_calls=getattr(args, 'max_tool_calls', None),
            max_delegated_tasks=getattr(args, 'max_delegated_tasks', None),
            max_model_calls=getattr(args, 'max_model_calls', None),
            max_session_turns=getattr(args, 'max_session_turns', None),
        ),
        output_schema=_load_output_schema_config(args),
        session_directory=(Path('.port_sessions') / 'agent').resolve(),
        scratchpad_root=(
            Path(getattr(args, 'scratchpad_root')).resolve()
            if getattr(args, 'scratchpad_root', None)
            else (Path('.port_sessions') / 'scratchpad').resolve()
        ),
    )


def _build_model_config(args: argparse.Namespace) -> ModelConfig:
    """从命令行参数构建模型连接配置：模型名、API 地址、密钥、温度、超时、定价。"""
    return ModelConfig(
        model=args.model,
        base_url=getattr(args, 'base_url', os.environ.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')),
        api_key=getattr(args, 'api_key', os.environ.get('ANTHROPIC_API_KEY', '')),
        temperature=getattr(args, 'temperature', 0.0),
        timeout_seconds=getattr(args, 'timeout_seconds', 120.0),
        max_tokens=int(getattr(args, 'max_tokens', 8192) or 8192),
        pricing=ModelPricing(
            input_cost_per_million_tokens_usd=float(
                getattr(args, 'input_cost_per_million', 0.0) or 0.0
            ),
            output_cost_per_million_tokens_usd=float(
                getattr(args, 'output_cost_per_million', 0.0) or 0.0
            ),
        ),
    )


def _build_agent(args: argparse.Namespace) -> LocalCodingAgent:
    """组装并返回一个完整的 LocalCodingAgent 实例，内部会初始化所有 runtime 子系统。"""
    return LocalCodingAgent(
        model_config=_build_model_config(args),
        runtime_config=_build_runtime_config(args),
        custom_system_prompt=args.system_prompt,
        append_system_prompt=args.append_system_prompt,
        override_system_prompt=args.override_system_prompt,
    )


def _print_agent_result(result, *, show_transcript: bool) -> None:
    """打印单轮 Agent 执行结果：最终输出、token 用量、费用、会话 ID，可选打印完整 transcript。"""
    print(result.final_output)
    print('\n# Usage')
    print(f'total_tokens={result.usage.total_tokens}')
    print(f'input_tokens={result.usage.input_tokens}')
    print(f'output_tokens={result.usage.output_tokens}')
    print(f'total_cost_usd={result.total_cost_usd:.6f}')
    if result.stop_reason:
        print(f'stop_reason={result.stop_reason}')
    if result.session_id:
        print('\n# Session')
        print(f'session_id={result.session_id}')
        if result.session_path:
            print(f'session_path={result.session_path}')
    if result.scratchpad_directory:
        print(f'scratchpad_directory={result.scratchpad_directory}')
    if show_transcript:
        print('\n# Transcript')
        for message in result.transcript:
            role = message.get('role', 'unknown')
            print(f'[{role}]')
            print(message.get('content', ''))


def _run_agent_chat_loop(
    agent: LocalCodingAgent,
    *,
    initial_prompt: str | None,
    resume_session_id: str | None,
    show_transcript: bool,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    result_printer: Callable[..., None] = _print_agent_result,
) -> int:
    """
    交互式 REPL 对话循环。
    首轮使用 agent.run() 创建新会话，后续轮次自动用 agent.resume() 携带历史上下文继续。
    支持通过 resume_session_id 跨进程恢复已有会话。
    """
    active_session_id = resume_session_id
    first_prompt = initial_prompt

    output_func('# Agent Chat')
    output_func("Enter a prompt. Use '/exit' or '/quit' to stop.")
    if active_session_id:
        output_func(f'resuming_session_id={active_session_id}')

    while True:
        if first_prompt is not None:
            prompt = first_prompt
            first_prompt = None
        else:
            try:
                prompt = input_func('user> ')
            except EOFError:
                output_func('chat_ended=eof')
                return 0
            except KeyboardInterrupt:
                output_func('\nchat_ended=interrupt')
                return 130

        normalized = prompt.strip()
        if not normalized:
            continue
        if normalized in {'/exit', '/quit'}:
            output_func('chat_ended=user_exit')
            return 0

        if active_session_id:
            stored_session = load_agent_session(
                active_session_id,
                directory=agent.runtime_config.session_directory,
            )
            result = agent.resume(prompt, stored_session)
        else:
            result = agent.run(prompt)
        result_printer(result, show_transcript=show_transcript)
        active_session_id = result.session_id


def build_parser() -> argparse.ArgumentParser:
    """构建主命令解析器，直接进入交互式对话模式。"""
    parser = argparse.ArgumentParser(description='interactive REPL for the Python local-model agent')
    parser.add_argument('prompt', nargs='?')
    parser.add_argument('--resume-session-id')
    parser.add_argument('--max-turns', type=int, default=12)
    parser.add_argument('--show-transcript', action='store_true')
    parser.add_argument('--model', default=os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-20250514'))
    parser.add_argument('--base-url', default=os.environ.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com'))
    parser.add_argument('--api-key', default=os.environ.get('ANTHROPIC_API_KEY', ''))
    parser.add_argument('--temperature', type=float, default=0.0)
    parser.add_argument('--max-tokens', type=int, default=8192)
    parser.add_argument('--timeout-seconds', type=float, default=120.0)
    parser.add_argument('--input-cost-per-million', type=float, default=0.0)
    parser.add_argument('--output-cost-per-million', type=float, default=0.0)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--add-dir', action='append', default=[])
    parser.add_argument('--disable-claude-md', action='store_true')
    parser.add_argument('--allow-write', action='store_true')
    parser.add_argument('--allow-shell', action='store_true')
    parser.add_argument('--unsafe', action='store_true')
    parser.add_argument('--stream', action='store_true')
    parser.add_argument('--auto-snip-threshold', type=int)
    parser.add_argument('--auto-compact-threshold', type=int)
    parser.add_argument('--compact-preserve-messages', type=int, default=4)
    parser.add_argument('--max-total-tokens', type=int)
    parser.add_argument('--max-input-tokens', type=int)
    parser.add_argument('--max-output-tokens', type=int)
    parser.add_argument('--max-reasoning-tokens', type=int)
    parser.add_argument('--max-budget-usd', type=float)
    parser.add_argument('--max-tool-calls', type=int)
    parser.add_argument('--max-delegated-tasks', type=int)
    parser.add_argument('--max-model-calls', type=int)
    parser.add_argument('--max-session-turns', type=int)
    parser.add_argument('--response-schema-file')
    parser.add_argument('--response-schema-name')
    parser.add_argument('--response-schema-strict', action='store_true')
    parser.add_argument('--scratchpad-root')
    parser.add_argument('--system-prompt')
    parser.add_argument('--append-system-prompt')
    parser.add_argument('--override-system-prompt')

    return parser


def main(argv: list[str] | None = None) -> int:
    """主入口：直接进入交互式对话模式。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    agent = _build_agent(args)
    return _run_agent_chat_loop(
        agent,
        initial_prompt=args.prompt,
        resume_session_id=args.resume_session_id,
        show_transcript=args.show_transcript,
    )


if __name__ == '__main__':
    raise SystemExit(main())
