from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from .agent_plugin_cache import load_plugin_cache_summary
from .account_runtime import AccountRuntime
from .ask_user_runtime import AskUserRuntime
from .config_runtime import ConfigRuntime
from .hook_policy import HookPolicyRuntime
from .lsp_runtime import LSPRuntime
from .mcp_runtime import MCPRuntime
from .plan_runtime import PlanRuntime
from .plugin_runtime import PluginRuntime
from .remote_runtime import RemoteRuntime
from .remote_trigger_runtime import RemoteTriggerRuntime
from .search_runtime import SearchRuntime
from .task_runtime import TaskRuntime
from .team_runtime import TeamRuntime
from .workflow_runtime import WorkflowRuntime
from .worktree_runtime import WorktreeRuntime
from .agent_types import AgentRuntimeConfig

MAX_STATUS_CHARS = 2000
MAX_MEMORY_CHARACTER_COUNT = 40000
MEMORY_INSTRUCTION_PROMPT = (
    'Codebase and user instructions are shown below. Be sure to adhere to '
    'these instructions. IMPORTANT: These instructions override default '
    'behavior when they directly apply to the task.'
)

_SYSTEM_PROMPT_INJECTION: str | None = None


@dataclass(frozen=True)
class AgentContextSnapshot:
    cwd: Path
    shell: str
    platform_name: str
    os_version: str
    current_date: str
    is_git_repo: bool
    is_git_worktree: bool
    scratchpad_directory: str | None
    additional_working_directories: tuple[str, ...]
    user_context: dict[str, str]
    system_context: dict[str, str]


def clear_context_caches() -> None:
    """清除所有上下文相关的 LRU 缓存。"""
    _get_git_status_cached.cache_clear()
    _get_system_context_cached.cache_clear()
    _get_user_context_cached.cache_clear()


def get_system_prompt_injection() -> str | None:
    """返回当前的系统提示词注入内容。"""
    return _SYSTEM_PROMPT_INJECTION


def build_context_snapshot(
    runtime_config: AgentRuntimeConfig,
    *,
    scratchpad_directory: Path | None = None,
) -> AgentContextSnapshot:
    """根据运行时配置构建完整的上下文快照。"""
    cwd = runtime_config.cwd.resolve()
    additional_dirs = tuple(
        str(path.resolve()) for path in runtime_config.additional_working_directories
    )
    return AgentContextSnapshot(
        cwd=cwd,
        shell=os.environ.get('SHELL', 'unknown'),
        platform_name=platform.system().lower() or os.name,
        os_version=_get_os_version(),
        current_date=date.today().isoformat(),
        is_git_repo=_is_git_repo(cwd),
        is_git_worktree=_is_git_worktree(cwd),
        scratchpad_directory=(
            str(scratchpad_directory.resolve()) if scratchpad_directory is not None else None
        ),
        additional_working_directories=additional_dirs,
        user_context=get_user_context(
            cwd,
            additional_dirs,
            runtime_config.disable_claude_md_discovery,
            scratchpad_directory=scratchpad_directory,
        ),
        system_context=get_system_context(cwd, scratchpad_directory=scratchpad_directory),
    )


def get_system_context(
    cwd: Path,
    *,
    scratchpad_directory: Path | None = None,
) -> dict[str, str]:
    """获取系统级上下文信息（如 git 状态）。"""
    scratchpad = str(scratchpad_directory.resolve()) if scratchpad_directory is not None else ''
    return dict(_get_system_context_cached(str(cwd.resolve()), scratchpad))


def get_user_context(
    cwd: Path,
    additional_working_directories: tuple[str, ...] = (),
    disable_claude_md_discovery: bool = False,
    scratchpad_directory: Path | None = None,
) -> dict[str, str]:
    """获取用户级上下文信息（如 CLAUDE.md、插件缓存等）。"""
    normalized_dirs = tuple(
        str(Path(path).resolve()) for path in additional_working_directories
    )
    return dict(
        _get_user_context_cached(
            str(cwd.resolve()),
            normalized_dirs,
            disable_claude_md_discovery,
            str(scratchpad_directory.resolve()) if scratchpad_directory is not None else '',
        )
    )


@lru_cache(maxsize=32)
def _get_system_context_cached(cwd: str, scratchpad_directory: str) -> dict[str, str]:
    """缓存版本的系统上下文获取。"""
    context: dict[str, str] = {}
    git_status = _get_git_status_cached(cwd)
    if git_status is not None:
        context['gitStatus'] = git_status
    injection = get_system_prompt_injection()
    if injection:
        context['cacheBreaker'] = f'[CACHE_BREAKER: {injection}]'
    if scratchpad_directory:
        context['scratchpadDirectory'] = scratchpad_directory
    return context


@lru_cache(maxsize=32)
def _get_user_context_cached(
    cwd: str,
    additional_working_directories: tuple[str, ...],
    disable_claude_md_discovery: bool,
    scratchpad_directory: str,
) -> dict[str, str]:
    """缓存版本的用户上下文获取。"""
    context: dict[str, str] = {
        'currentDate': f"Today's date is {date.today().isoformat()}.",
    }
    if scratchpad_directory:
        context['scratchpad'] = (
            'Use this session-specific scratchpad directory for temporary files instead '
            f'of /tmp when you need throwaway workspace: {scratchpad_directory}'
        )
    if disable_claude_md_discovery:
        return context

    memory_bundle = _load_memory_bundle(Path(cwd), additional_working_directories)
    if memory_bundle:
        context['claudeMd'] = memory_bundle
    plugin_cache = load_plugin_cache_summary(Path(cwd), additional_working_directories)
    if plugin_cache:
        context['pluginCache'] = plugin_cache
    plugin_runtime = PluginRuntime.from_workspace(Path(cwd), additional_working_directories)
    if plugin_runtime.manifests:
        context['pluginRuntime'] = plugin_runtime.render_summary()
    hook_policy_runtime = HookPolicyRuntime.from_workspace(Path(cwd), additional_working_directories)
    if hook_policy_runtime.manifests:
        context['hookPolicy'] = hook_policy_runtime.render_summary()
        managed_settings = hook_policy_runtime.managed_settings()
        if managed_settings:
            context['managedSettings'] = '\n'.join(
                f'{key}={value}'
                for key, value in sorted(managed_settings.items())
            )
        safe_env = hook_policy_runtime.safe_env()
        if safe_env:
            context['safeEnv'] = '\n'.join(
                f'{key}={value}'
                for key, value in sorted(safe_env.items())
            )
        context['trustMode'] = (
            'Workspace trust mode: trusted'
            if hook_policy_runtime.is_trusted()
            else 'Workspace trust mode: untrusted'
        )
    mcp_runtime = MCPRuntime.from_workspace(Path(cwd), additional_working_directories)
    if mcp_runtime.resources:
        context['mcpRuntime'] = mcp_runtime.render_summary()
    remote_runtime = RemoteRuntime.from_workspace(Path(cwd), additional_working_directories)
    if remote_runtime.has_remote_config():
        context['remoteRuntime'] = remote_runtime.render_summary()
    remote_trigger_runtime = RemoteTriggerRuntime.from_workspace(
        Path(cwd),
        additional_working_directories,
    )
    if remote_trigger_runtime.has_state():
        context['remoteTriggerRuntime'] = remote_trigger_runtime.render_summary()
    search_runtime = SearchRuntime.from_workspace(Path(cwd), additional_working_directories)
    if search_runtime.has_search_runtime():
        context['searchRuntime'] = search_runtime.render_summary()
    account_runtime = AccountRuntime.from_workspace(Path(cwd), additional_working_directories)
    if account_runtime.has_account_state():
        context['accountRuntime'] = account_runtime.render_summary()
    ask_user_runtime = AskUserRuntime.from_workspace(Path(cwd), additional_working_directories)
    if ask_user_runtime.has_state():
        context['askUserRuntime'] = ask_user_runtime.render_summary()
    config_runtime = ConfigRuntime.from_workspace(Path(cwd))
    if config_runtime.has_config():
        context['configRuntime'] = config_runtime.render_summary()
    lsp_runtime = LSPRuntime.from_workspace(Path(cwd), additional_working_directories)
    if lsp_runtime.has_lsp_support():
        context['lspRuntime'] = lsp_runtime.render_summary()
    plan_runtime = PlanRuntime.from_workspace(Path(cwd))
    if plan_runtime.steps:
        context['planRuntime'] = plan_runtime.render_summary()
    task_runtime = TaskRuntime.from_workspace(Path(cwd))
    if task_runtime.tasks:
        context['taskRuntime'] = task_runtime.render_summary()
    team_runtime = TeamRuntime.from_workspace(Path(cwd), additional_working_directories)
    if team_runtime.has_team_state():
        context['teamRuntime'] = team_runtime.render_summary()
    workflow_runtime = WorkflowRuntime.from_workspace(Path(cwd), additional_working_directories)
    if workflow_runtime.has_workflows():
        context['workflowRuntime'] = workflow_runtime.render_summary()
    worktree_runtime = WorktreeRuntime.from_workspace(Path(cwd))
    if worktree_runtime.repo_root is not None or worktree_runtime.has_state():
        context['worktreeRuntime'] = worktree_runtime.render_summary()
    return context


@lru_cache(maxsize=32)
def _get_git_status_cached(cwd: str) -> str | None:
    """缓存版本的 git 状态信息获取。"""
    root = Path(cwd)
    if not _is_git_repo(root):
        return None

    branch = _run_command(['git', 'branch', '--show-current'], root)
    main_branch = _detect_default_branch(root)
    status = _run_command(['git', '--no-optional-locks', 'status', '--short'], root) or ''
    log = _run_command(['git', '--no-optional-locks', 'log', '--oneline', '-n', '5'], root) or '(none)'
    user_name = _run_command(['git', 'config', 'user.name'], root)

    if len(status) > MAX_STATUS_CHARS:
        status = (
            status[:MAX_STATUS_CHARS]
            + '\n... (truncated because it exceeds 2k characters. Use bash for full git status.)'
        )

    parts = [
        'This is the git status at the start of the conversation. It is a snapshot and does not update automatically during the run.',
        f'Current branch: {branch or "(unknown)"}',
        f'Main branch: {main_branch or "(unknown)"}',
    ]
    if user_name:
        parts.append(f'Git user: {user_name}')
    parts.extend(
        [
            f'Status:\n{status or "(clean)"}',
            f'Recent commits:\n{log}',
        ]
    )
    return '\n\n'.join(parts)


def _load_memory_bundle(cwd: Path, additional_working_directories: tuple[str, ...]) -> str | None:
    """加载并合并所有发现的 CLAUDE.md 记忆文件内容。"""
    discovered: list[Path] = []
    seen: set[Path] = set()

    for candidate in _discover_global_memory_files():
        _remember_path(candidate, discovered, seen)

    for directory in _walk_upwards(cwd):
        for candidate in _discover_memory_files_for_directory(directory):
            _remember_path(candidate, discovered, seen)

    for raw_path in additional_working_directories:
        for candidate in _discover_memory_files_for_directory(Path(raw_path)):
            _remember_path(candidate, discovered, seen)

    if not discovered:
        return None

    blocks = [MEMORY_INSTRUCTION_PROMPT]
    for path in discovered:
        try:
            content = path.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            continue
        if not content:
            continue
        if len(content) > MAX_MEMORY_CHARACTER_COUNT:
            content = (
                content[:MAX_MEMORY_CHARACTER_COUNT]
                + '\n... (truncated because it exceeds the memory size limit)'
            )
        blocks.append(f'## {path}\n{content}')
    if len(blocks) == 1:
        return None
    return '\n\n'.join(blocks)


def _discover_global_memory_files() -> list[Path]:
    """发现全局 CLAUDE.md 记忆文件（~/.claude/CLAUDE.md）。"""
    home_memory = Path.home() / '.claude' / 'CLAUDE.md'
    return [home_memory] if home_memory.is_file() else []


def _discover_memory_files_for_directory(directory: Path) -> list[Path]:
    """发现指定目录下的所有记忆文件和规则文件。"""
    files: list[Path] = []
    for candidate in (
        directory / 'CLAUDE.md',
        directory / '.claude' / 'CLAUDE.md',
        directory / 'CLAUDE.local.md',
    ):
        if candidate.is_file():
            files.append(candidate.resolve())

    rules_dir = directory / '.claude' / 'rules'
    if rules_dir.is_dir():
        files.extend(
            path.resolve()
            for path in sorted(rules_dir.glob('*.md'))
            if path.is_file()
        )
    return files


def _walk_upwards(cwd: Path) -> list[Path]:
    """从根目录到当前目录逐层返回所有祖先路径。"""
    parents = list(cwd.resolve().parents)
    parents.reverse()
    return [*parents, cwd.resolve()]


def _remember_path(path: Path, discovered: list[Path], seen: set[Path]) -> None:
    """去重后将路径添加到已发现列表中。"""
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    discovered.append(resolved)


def _detect_default_branch(cwd: Path) -> str | None:
    """检测 git 仓库的默认分支名称（main 或 master）。"""
    origin_head = _run_command(
        ['git', 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD'],
        cwd,
    )
    if origin_head and '/' in origin_head:
        return origin_head.split('/', 1)[1]

    for candidate in ('main', 'master'):
        try:
            completed = subprocess.run(
                ['git', 'show-ref', '--verify', f'refs/heads/{candidate}'],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except OSError:
            return None
        if completed.returncode == 0:
            return candidate
    return None


@lru_cache(maxsize=32)
def _is_git_repo(cwd: Path) -> bool:
    """判断指定目录是否为 git 仓库。"""
    if (cwd / '.git').exists():
        return True
    try:
        completed = subprocess.run(
            ['git', 'rev-parse', '--is-inside-work-tree'],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == 'true'


def _is_git_worktree(cwd: Path) -> bool:
    """判断指定目录是否为 git worktree。"""
    if not _is_git_repo(cwd):
        return False
    git_dir = _run_command(['git', 'rev-parse', '--git-dir'], cwd)
    git_common_dir = _run_command(['git', 'rev-parse', '--git-common-dir'], cwd)
    return bool(git_dir and git_common_dir and git_dir != git_common_dir)


def _run_command(command: list[str], cwd: Path) -> str | None:
    """执行子进程命令并返回 stdout 输出，失败时返回 None。"""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip()
    return output or None


def _get_os_version() -> str:
    """获取当前操作系统版本信息。"""
    system = platform.system()
    release = platform.release()
    if system and release:
        return f'{system} {release}'
    return platform.platform()
