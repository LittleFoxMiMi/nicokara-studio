from __future__ import annotations

"""Protocol adapter adapted from light_novel_reader/app/ai/client.py.

The business layer only sends structured messages; this module owns provider
specific URLs, headers, payloads, and response extraction.
"""

import ast
import json
from typing import Any

import httpx

OPENAI_CHAT = "openai_chat"
OPENAI_RESPONSES = "openai_responses"
ANTHROPIC_MESSAGES = "anthropic_messages"


class AIClient:
    def __init__(self, profile: dict[str, Any], api_key: str, proxy_url: str | None = None) -> None:
        self.provider = profile.get("api_format", OPENAI_CHAT)
        self.base_url = str(profile.get("base_url") or "").rstrip("/")
        self.api_key = api_key
        self.model = str(profile.get("model") or "")
        self.temperature = profile.get("temperature")
        self.max_tokens = profile.get("max_tokens")
        self.timeout_seconds = float(profile.get("timeout_seconds") or 45)
        effort = profile.get("thinking_effort")
        self.thinking_effort = str(effort or ("low" if profile.get("thinking_enabled") else "off"))
        self.custom_payload = profile.get("custom_payload") or {}
        self.proxy_url = proxy_url

    def complete(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = self._build_payload(system_prompt, user_prompt)
        payload["stream"] = True
        print("[Nicokara AI request]", self._request_url(), json.dumps(self._debug_payload(payload), ensure_ascii=False), flush=True)
        chunks: list[str] = []
        # ``read`` is an idle timeout. Reasoning-enabled Responses streams can
        # legitimately run longer than the configured value while still active.
        with httpx.Client(
            timeout=httpx.Timeout(connect=min(30.0, self.timeout_seconds), read=self.timeout_seconds, write=min(120.0, self.timeout_seconds), pool=min(30.0, self.timeout_seconds)),
            headers=self._request_headers(),
            proxy=self.proxy_url,
        ) as client:
            with client.stream("POST", self._request_url(), json=payload) as response:
                response.raise_for_status()
                raw_lines: list[str] = []
                stream_status: str | None = None
                for line in response.iter_lines():
                    raw_line = line if isinstance(line, str) else line.decode("utf-8", errors="replace")
                    status = self._extract_stream_status(raw_line)
                    if status:
                        stream_status = status
                    chunk = self._extract_stream_chunk(raw_line)
                    if chunk:
                        chunks.append(chunk)
                    elif raw_line.strip() and not raw_line.startswith(":") and not raw_line.startswith("event:") and raw_line.strip() != "[DONE]":
                        raw_lines.append(raw_line.strip())
                if not chunks and raw_lines:
                    raw_body = "\n".join(raw_lines)
                    try:
                        chunks.append(self._extract_response_content(self._parse_json(raw_body)))
                    except json.JSONDecodeError:
                        raise ValueError("AI 返回内容不是有效 JSON")
                if stream_status and stream_status != "completed":
                    raise ValueError(f"AI 响应未完成: {stream_status}")
        content = "".join(chunks)
        if not content:
            raise ValueError("AI 流式响应没有文本内容")
        print("[Nicokara AI response text]", content, flush=True)
        return self._parse_json(content)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        """Parse JSON and repair only unambiguously missing closing delimiters."""
        normalized = content.strip().removeprefix("```json").removesuffix("```").strip()
        try:
            value = json.loads(normalized)
        except json.JSONDecodeError as original:
            repaired_chars: list[str] = []
            stack: list[str] = []
            in_string = False
            escaped = False
            pairs = {"{": "}", "[": "]"}
            closing = {"}", "]"}
            for char in normalized:
                if in_string:
                    repaired_chars.append(char)
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                    repaired_chars.append(char)
                elif char in pairs:
                    stack.append(pairs[char])
                    repaired_chars.append(char)
                elif char in closing:
                    if char not in stack:
                        raise original
                    while stack and stack[-1] != char:
                        repaired_chars.append(stack.pop())
                    if not stack:
                        raise original
                    stack.pop()
                    repaired_chars.append(char)
                else:
                    repaired_chars.append(char)
            if in_string or not stack:
                if in_string:
                    raise original
            try:
                value = json.loads("".join(repaired_chars) + "".join(reversed(stack)))
            except json.JSONDecodeError:
                raise original
        if not isinstance(value, dict):
            raise ValueError("AI 返回内容必须是 JSON 对象")
        return value

    def _build_payload(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        if self.provider == ANTHROPIC_MESSAGES:
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": self.max_tokens or 4096,
                "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            }
            if self.temperature is not None:
                payload["temperature"] = self.temperature
            if self.thinking_effort != "off":
                payload["thinking"] = {"type": "adaptive"}
                payload["output_config"] = {"effort": {"minimal": "low", "xhigh": "max"}.get(self.thinking_effort, self.thinking_effort)}
        elif self.provider == OPENAI_RESPONSES:
            payload = {"model": self.model, "input": [{"role": "user", "content": user_prompt}], "instructions": system_prompt}
            if self.temperature is not None:
                payload["temperature"] = self.temperature
            if self.max_tokens is not None:
                payload["max_output_tokens"] = self.max_tokens
            if self.thinking_effort != "off":
                payload["reasoning"] = {"effort": self.thinking_effort}
        else:
            payload = {"model": self.model, "messages": messages}
            if self.temperature is not None:
                payload["temperature"] = self.temperature
            if self.max_tokens is not None:
                payload["max_tokens"] = self.max_tokens
            # Chat Completions models use different, non-standard thinking fields.
            # Configure those explicitly through custom_payload.
        self._merge_custom_payload(payload)
        return payload

    def _debug_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: ("[REDACTED]" if any(token in str(key).lower() for token in ("api_key", "authorization", "x-api-key", "token")) else self._debug_payload(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [self._debug_payload(item) for item in value]
        return value

    def _extract_stream_chunk(self, line: str) -> str:
        if not line:
            return ""
        if line.startswith(":") or line.startswith("event:"):
            return ""
        if line.startswith("data:"):
            line = line.removeprefix("data:").strip()
        else:
            line = line.strip()
        if not line or line == "[DONE]" or not line.startswith("{"):
            return ""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return ""
        if self.provider == ANTHROPIC_MESSAGES:
            if data.get("type") != "content_block_delta":
                return ""
            delta = data.get("delta") or {}
            return delta.get("text", "") if delta.get("type") == "text_delta" else ""
        if self.provider == OPENAI_RESPONSES:
            if data.get("type") != "response.output_text.delta":
                return ""
            delta = data.get("delta")
            return delta if isinstance(delta, str) else ""
        choices = data.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        return ""

    def _extract_stream_status(self, line: str) -> str | None:
        if line.startswith("data:"):
            line = line.removeprefix("data:").strip()
        else:
            line = line.strip()
        if not line.startswith("{"):
            return None
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if self.provider == OPENAI_RESPONSES and data.get("type") == "response.completed":
            return str((data.get("response") or {}).get("status") or "") or None
        if self.provider == OPENAI_CHAT:
            choices = data.get("choices") or []
            reason = choices[0].get("finish_reason") if choices else None
            return "incomplete" if reason == "length" else ("completed" if reason else None)
        if self.provider == ANTHROPIC_MESSAGES and data.get("type") == "message_stop":
            return "incomplete" if data.get("stop_reason") == "max_tokens" else "completed"
        return None

    def _merge_custom_payload(self, payload: dict[str, Any]) -> None:
        custom = self.custom_payload
        if isinstance(custom, str):
            normalized = custom.replace("“", '"').replace("”", '"')
            try:
                custom = json.loads(normalized)
            except json.JSONDecodeError:
                custom = ast.literal_eval(normalized)
        if not isinstance(custom, dict):
            raise ValueError("Custom payload must be a JSON object")
        payload.update(custom)

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.provider == ANTHROPIC_MESSAGES:
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_url(self) -> str:
        endpoint = {ANTHROPIC_MESSAGES: "messages", OPENAI_RESPONSES: "responses"}.get(self.provider, "chat/completions")
        if self.base_url.endswith(f"/{endpoint}"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/{endpoint}"
        return f"{self.base_url}/v1/{endpoint}"

    def _extract_response_content(self, data: dict[str, Any]) -> str:
        if self.provider == ANTHROPIC_MESSAGES:
            text = "".join(block.get("text", "") for block in data.get("content") or [] if block.get("type") == "text")
            if not text.strip():
                raise ValueError("Anthropic response missing text content")
            return text
        if self.provider == OPENAI_RESPONSES:
            output_text = data.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                return output_text
            text = "".join(content.get("text", "") for output in data.get("output") or [] for content in output.get("content") or [] if content.get("type") == "output_text")
            if not text.strip():
                raise ValueError("OpenAI Responses response missing output text")
            return text
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenAI response missing message content")
        return content
