import sqlite3

import httpx

from app.core.database import Database
from app.services.ai_client import AIClient


def profile(provider: str, base_url: str = "https://example.com") -> dict:
    return {"api_format": provider, "base_url": base_url, "model": "test-model", "temperature": 0.3, "max_tokens": 1024, "timeout_seconds": 7, "thinking_enabled": True, "custom_payload": {}}


def test_anthropic_payload_headers_url_and_response() -> None:
    client = AIClient(profile("anthropic_messages", "https://api.anthropic.com"), "secret")
    payload = client._build_payload("stable", "changing")
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"] == [{"role": "user", "content": "changing"}]
    assert payload["thinking"] == {"type": "adaptive"}
    assert client._request_url() == "https://api.anthropic.com/v1/messages"
    assert client._request_headers()["x-api-key"] == "secret"
    assert client._extract_response_content({"content": [{"type": "thinking"}, {"type": "text", "text": "ok"}]}) == "ok"


def test_openai_responses_payload_url_and_response() -> None:
    client = AIClient(profile("openai_responses", "https://api.openai.com/v1"), "secret")
    payload = client._build_payload("stable", "changing")
    assert payload["instructions"] == "stable"
    assert payload["input"] == [{"role": "user", "content": "changing"}]
    assert payload["max_output_tokens"] == 1024
    assert payload["reasoning"] == {"effort": "low"}
    assert client._request_url() == "https://api.openai.com/v1/responses"
    assert client._extract_response_content({"output": [{"content": [{"type": "output_text", "text": "done"}]}]}) == "done"


def test_openai_chat_payload_and_custom_fields() -> None:
    client = AIClient({**profile("openai_chat"), "thinking_effort": "high", "custom_payload": {"response_format": {"type": "json_object"}, "reasoning_effort": "high"}}, "secret")
    payload = client._build_payload("stable", "changing")
    assert payload["messages"][0] == {"role": "system", "content": "stable"}
    assert payload["reasoning_effort"] == "high"
    assert payload["response_format"] == {"type": "json_object"}
    assert client._request_url() == "https://example.com/v1/chat/completions"


def test_openai_chat_does_not_inject_thinking_effort() -> None:
    payload = AIClient({**profile("openai_chat"), "thinking_effort": "high"}, "secret")._build_payload("stable", "changing")
    assert "reasoning_effort" not in payload


def test_thinking_effort_off_omits_provider_fields() -> None:
    for provider in ("openai_responses", "anthropic_messages"):
        payload = AIClient({**profile(provider), "thinking_enabled": False, "thinking_effort": "off"}, "secret")._build_payload("stable", "changing")
        assert "reasoning" not in payload
        assert "thinking" not in payload


def test_database_migrates_legacy_thinking_flag(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE ai_profiles (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, api_format TEXT NOT NULL,
          base_url TEXT NOT NULL, model TEXT NOT NULL, temperature REAL NOT NULL,
          max_tokens INTEGER NOT NULL, timeout_seconds REAL NOT NULL,
          thinking_enabled INTEGER NOT NULL DEFAULT 0, custom_payload TEXT NOT NULL,
          encrypted_api_key TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO ai_profiles VALUES ('legacy', 'Legacy', 'openai_responses', 'https://example.com', 'model', 0.2, 1000, 45, 1, '{}', NULL, 'now', 'now');
    """)
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    migrated = database.get_ai_profile("legacy")
    assert migrated and migrated["thinking_effort"] == "low"
    assert migrated["max_chars_per_request"] == 1200
    assert migrated["retry_count"] == 2


def test_stream_chunk_parsing_supports_all_protocols() -> None:
    assert AIClient(profile("openai_chat"), "secret")._extract_stream_chunk('data: {"choices":[{"delta":{"content":"a"}}]}') == "a"
    assert AIClient(profile("openai_responses"), "secret")._extract_stream_chunk('data: {"type":"response.output_text.delta","delta":"b"}') == "b"
    assert AIClient(profile("anthropic_messages"), "secret")._extract_stream_chunk('data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"c"}}') == "c"
    assert AIClient(profile("openai_responses"), "secret")._extract_stream_status('data: {"type":"response.completed","response":{"status":"incomplete"}}') == "incomplete"


def test_complete_allows_active_reasoning_stream_beyond_read_timeout(monkeypatch, capsys) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'event: response.output_item.added'
            yield 'data: {"type":"response.output_item.added","item":{"type":"reasoning"}}'
            yield 'event: response.output_text.delta'
            yield 'data: {"type":"response.output_text.delta","delta":"{\\"result\\":[]}"}'
            yield 'event: response.completed'
            yield 'data: {"type":"response.completed","response":{"status":"completed"}}'

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, json):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = AIClient({**profile("openai_responses"), "timeout_seconds": 1}, "secret").complete("system", "lyrics")
    assert result == {"result": []}
    output = capsys.readouterr().out
    assert "[Nicokara AI request]" in output
    assert "[Nicokara AI response text]" in output
    assert "[Nicokara AI response raw]" not in output


def test_debug_payload_redacts_secrets() -> None:
    client = AIClient({**profile("openai_chat"), "custom_payload": {"api_key": "secret", "nested": {"authorization": "Bearer secret"}}}, "secret")
    debug = client._debug_payload(client._build_payload("system", "user"))
    assert debug["api_key"] == "[REDACTED]"
    assert debug["nested"]["authorization"] == "[REDACTED]"


def test_parse_json_repairs_only_missing_closing_suffix() -> None:
    value = AIClient._parse_json('{"result":[{"line_index":0,"raw":["雨"],"pronunciation":[]}')
    assert value == {"result": [{"line_index": 0, "raw": ["雨"], "pronunciation": []}]}


def test_parse_json_repairs_missing_inner_pronunciation_bracket() -> None:
    value = AIClient._parse_json('{"result":[{"pronunciation":[[0,1,"霞","かす"]}]}')
    assert value == {"result": [{"pronunciation": [[0, 1, "霞", "かす"]]}]}
