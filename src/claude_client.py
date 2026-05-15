"""Claude Messages API client using raw urllib HTTP requests."""
from __future__ import annotations

import json
from typing import Any, Iterator
from urllib import error, request

from .agent_types import (
    AssistantTurn,
    ModelConfig,
    OutputSchemaConfig,
    StreamEvent,
    ToolCall,
    UsageStats,
)

ANTHROPIC_VERSION = '2023-06-01'


class ClaudeAPIError(RuntimeError):
    """Raised when the Claude Messages API returns an error."""


def _map_stop_reason(claude_reason: str) -> str:
    """将 Claude stop_reason 映射为 OpenAI 风格的 finish_reason。"""
    mapping = {
        'end_turn': 'stop',
        'stop_sequence': 'stop',
        'tool_use': 'tool_calls',
        'max_tokens': 'length',
    }
    return mapping.get(claude_reason, claude_reason)


def _parse_claude_usage(usage: dict[str, Any]) -> UsageStats:
    """从 Claude 响应中解析 token 用量。"""
    if not isinstance(usage, dict):
        return UsageStats()
    return UsageStats(
        input_tokens=int(usage.get('input_tokens', 0)),
        output_tokens=int(usage.get('output_tokens', 0)),
        cache_creation_input_tokens=int(usage.get('cache_creation_input_tokens', 0)),
        cache_read_input_tokens=int(usage.get('cache_read_input_tokens', 0)),
    )


class ClaudeClient:
    """Claude Messages API client using raw urllib."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        output_schema: OutputSchemaConfig | None = None,
    ) -> AssistantTurn:
        """发送非流式 Messages API 请求并返回助手回复。"""
        system_text, claude_messages = self._convert_messages(messages)
        claude_tools = self._convert_tools(tools)
        payload = self._build_payload(
            system=system_text,
            messages=claude_messages,
            tools=claude_tools,
            stream=False,
        )
        response_data = self._request_json(payload)
        return self._parse_response(response_data)

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        output_schema: OutputSchemaConfig | None = None,
    ) -> Iterator[StreamEvent]:
        """发送流式 Messages API 请求，逐步产出 StreamEvent。"""
        system_text, claude_messages = self._convert_messages(messages)
        claude_tools = self._convert_tools(tools)
        payload = self._build_payload(
            system=system_text,
            messages=claude_messages,
            tools=claude_tools,
            stream=True,
        )
        body = json.dumps(payload).encode('utf-8')
        req = request.Request(
            self._endpoint_url(),
            data=body,
            headers=self._build_headers(),
            method='POST',
        )
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                yield StreamEvent(type='message_start')
                yield from self._process_stream(response)
        except error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise ClaudeAPIError(
                f'HTTP {exc.code} from Claude API: {detail}'
            ) from exc
        except error.URLError as exc:
            raise ClaudeAPIError(
                f'Unable to reach Claude API at {self.config.base_url}: {exc.reason}'
            ) from exc

    def _endpoint_url(self) -> str:
        base = self.config.base_url.rstrip('/')
        return f'{base}/v1/messages'

    def _build_headers(self) -> dict[str, str]:
        return {
            'x-api-key': self.config.api_key,
            'anthropic-version': ANTHROPIC_VERSION,
            'content-type': 'application/json',
        }

    def _build_payload(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream: bool,
    ) -> dict[str, Any]:
        """构建 Claude Messages API 请求体。"""
        payload: dict[str, Any] = {
            'model': self.config.model,
            'max_tokens': self.config.max_tokens,
            'messages': messages,
            'stream': stream,
        }
        if system:
            payload['system'] = system
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = {'type': 'auto'}
        if self.config.temperature > 0:
            payload['temperature'] = self.config.temperature
        return payload

    def _request_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送非流式请求并返回解析后的 JSON 响应。"""
        body = json.dumps(payload).encode('utf-8')
        req = request.Request(
            self._endpoint_url(),
            data=body,
            headers=self._build_headers(),
            method='POST',
        )
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise ClaudeAPIError(
                f'HTTP {exc.code} from Claude API: {detail}'
            ) from exc
        except error.URLError as exc:
            raise ClaudeAPIError(
                f'Unable to reach Claude API at {self.config.base_url}: {exc.reason}'
            ) from exc
        try:
            data = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError as exc:
            raise ClaudeAPIError('Claude API returned invalid JSON') from exc
        if not isinstance(data, dict):
            raise ClaudeAPIError('Claude API returned malformed JSON payload')
        if data.get('type') == 'error':
            err = data.get('error', {})
            raise ClaudeAPIError(
                f'Claude API error: {err.get("type", "unknown")}: {err.get("message", "")}'
            )
        return data

    def _convert_messages(
        self, openai_messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """将 OpenAI 格式消息转换为 Claude 格式，返回 (system, messages)。"""
        system_parts: list[str] = []
        claude_messages: list[dict[str, Any]] = []

        for msg in openai_messages:
            role = msg.get('role', '')

            if role == 'system':
                system_parts.append(msg.get('content', ''))

            elif role == 'user' and msg.get('tool_call_id'):
                block = {
                    'type': 'tool_result',
                    'tool_use_id': msg['tool_call_id'],
                    'content': msg.get('content', ''),
                }
                if claude_messages and claude_messages[-1]['role'] == 'user':
                    claude_messages[-1]['content'].append(block)
                else:
                    claude_messages.append({'role': 'user', 'content': [block]})

            elif role == 'tool':
                block = {
                    'type': 'tool_result',
                    'tool_use_id': msg.get('tool_call_id', ''),
                    'content': msg.get('content', ''),
                }
                if claude_messages and claude_messages[-1]['role'] == 'user':
                    claude_messages[-1]['content'].append(block)
                else:
                    claude_messages.append({'role': 'user', 'content': [block]})

            elif role == 'user':
                content = msg.get('content', '')
                block = {'type': 'text', 'text': content} if content else {'type': 'text', 'text': ''}
                if claude_messages and claude_messages[-1]['role'] == 'user':
                    claude_messages[-1]['content'].append(block)
                else:
                    claude_messages.append({'role': 'user', 'content': [block]})

            elif role == 'assistant':
                content_blocks: list[dict[str, Any]] = []
                text = msg.get('content', '')
                if text:
                    content_blocks.append({'type': 'text', 'text': text})
                for tc in msg.get('tool_calls', []):
                    func = tc.get('function', {})
                    arguments = func.get('arguments', '{}')
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments) if arguments.strip() else {}
                    content_blocks.append({
                        'type': 'tool_use',
                        'id': tc.get('id', ''),
                        'name': func.get('name', ''),
                        'input': arguments,
                    })
                if not content_blocks:
                    content_blocks.append({'type': 'text', 'text': ''})
                claude_messages.append({'role': 'assistant', 'content': content_blocks})

        if not claude_messages or claude_messages[0]['role'] != 'user':
            claude_messages.insert(0, {'role': 'user', 'content': [{'type': 'text', 'text': ''}]})

        return '\n\n'.join(system_parts), claude_messages

    def _convert_tools(self, openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将 OpenAI function-calling 工具定义转换为 Claude 格式。"""
        claude_tools: list[dict[str, Any]] = []
        for tool in openai_tools:
            if tool.get('type') != 'function':
                continue
            func = tool.get('function', {})
            claude_tools.append({
                'name': func.get('name', ''),
                'description': func.get('description', ''),
                'input_schema': func.get('parameters', {'type': 'object', 'properties': {}}),
            })
        return claude_tools

    def _parse_response(self, data: dict[str, Any]) -> AssistantTurn:
        """解析 Claude 非流式响应为 AssistantTurn。"""
        content_blocks = data.get('content', [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            block_type = block.get('type', '')
            if block_type == 'text':
                text_parts.append(block.get('text', ''))
            elif block_type == 'tool_use':
                tool_calls.append(ToolCall(
                    id=block.get('id', ''),
                    name=block.get('name', ''),
                    arguments=block.get('input', {}),
                ))

        stop_reason = _map_stop_reason(data.get('stop_reason', ''))
        usage = _parse_claude_usage(data.get('usage', {}))

        return AssistantTurn(
            content=''.join(text_parts),
            tool_calls=tuple(tool_calls),
            finish_reason=stop_reason,
            raw_message=data,
            usage=usage,
        )

    def _process_stream(self, response: Any) -> Iterator[StreamEvent]:
        """处理 Claude SSE 流，将事件转换为内部 StreamEvent。"""
        current_block_index = -1
        current_block_type = ''
        current_tool_id = ''
        current_tool_name = ''
        tool_use_count = 0

        for event_type, data in self._iter_sse_events(response):
            if event_type == 'message_start':
                message = data.get('message', {})
                usage_data = message.get('usage', {})
                if usage_data:
                    yield StreamEvent(
                        type='usage',
                        usage=_parse_claude_usage(usage_data),
                        raw_event=data,
                    )

            elif event_type == 'content_block_start':
                current_block_index += 1
                block = data.get('content_block', {})
                current_block_type = block.get('type', '')
                if current_block_type == 'tool_use':
                    current_tool_id = block.get('id', '')
                    current_tool_name = block.get('name', '')
                    yield StreamEvent(
                        type='tool_call_delta',
                        tool_call_index=tool_use_count,
                        tool_call_id=current_tool_id,
                        tool_name=current_tool_name,
                        arguments_delta='',
                        raw_event=data,
                    )
                    tool_use_count += 1

            elif event_type == 'content_block_delta':
                delta = data.get('delta', {})
                delta_type = delta.get('type', '')
                if delta_type == 'text_delta':
                    text = delta.get('text', '')
                    if text:
                        yield StreamEvent(
                            type='content_delta',
                            delta=text,
                            raw_event=data,
                        )
                elif delta_type == 'input_json_delta':
                    partial_json = delta.get('partial_json', '')
                    if partial_json:
                        yield StreamEvent(
                            type='tool_call_delta',
                            tool_call_index=tool_use_count - 1,
                            tool_call_id=current_tool_id,
                            tool_name=None,
                            arguments_delta=partial_json,
                            raw_event=data,
                        )

            elif event_type == 'message_delta':
                delta = data.get('delta', {})
                usage_data = data.get('usage', {})
                if usage_data:
                    yield StreamEvent(
                        type='usage',
                        usage=_parse_claude_usage(usage_data),
                        raw_event=data,
                    )
                stop_reason = delta.get('stop_reason')
                if stop_reason:
                    yield StreamEvent(
                        type='message_stop',
                        finish_reason=_map_stop_reason(stop_reason),
                        raw_event=data,
                    )

    def _iter_sse_events(self, response: Any) -> Iterator[tuple[str, dict[str, Any]]]:
        """解析 Claude SSE 流，产出 (event_type, data_dict) 对。"""
        event_type = ''
        data_lines: list[str] = []

        while True:
            line = response.readline()
            if not line:
                break
            if isinstance(line, bytes):
                text = line.decode('utf-8', errors='replace')
            else:
                text = str(line)
            stripped = text.rstrip('\n\r')

            if stripped.startswith('event:'):
                event_type = stripped[6:].strip()
            elif stripped.startswith('data:'):
                data_lines.append(stripped[5:].strip())
            elif stripped == '':
                if event_type and data_lines:
                    joined = '\n'.join(data_lines)
                    try:
                        data = json.loads(joined)
                    except json.JSONDecodeError:
                        data = {}
                    yield event_type, data
                event_type = ''
                data_lines = []
