import io
import json
from urllib.error import HTTPError, URLError

import pytest

from app.llm import AIConnectionError, OpenAICompatibleClient, PROVIDER_PRESETS


@pytest.mark.parametrize("provider", [p for p in PROVIDER_PRESETS if p != "custom"])
def test_every_preset_has_a_usable_request_and_json_parser(provider, monkeypatch):
    client = OpenAICompatibleClient(None, "test-key", None, provider)
    captured = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data)
        captured.append(payload)
        assert payload["model"] == client.model
        assert "temperature" not in payload
        assert timeout == 90
        if client.wire_api == "anthropic":
            assert request.get_header("X-api-key") == "test-key"
            assert request.get_header("Anthropic-version") == "2023-06-01"
            assert payload["max_tokens"] > 0
            response = {"content": [{"type": "text", "text": '{"ok":true}'}]}
        elif client.wire_api == "responses":
            assert isinstance(payload["input"], list)
            assert payload["store"] is False
            response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}]}
        else:
            assert request.get_header("Authorization") == "Bearer test-key"
            response = {"choices": [{"message": {"content": '{"ok":true}'}}]}
        return Response(json.dumps(response))

    monkeypatch.setattr("app.llm.urlopen", fake_urlopen)
    client.test()
    assert len(captured) == 1


class Response(io.BytesIO):
    def __init__(self, raw, content_type="application/json"):
        super().__init__(raw.encode())
        self.headers = {"Content-Type": content_type}


def test_official_responses_never_uses_third_party():
    client = OpenAICompatibleClient(None, "test-key", None, "openai_responses")
    assert client.endpoint == "https://api.openai.com/v1/responses"
    assert client.wire_api == "responses"


@pytest.mark.parametrize("url,protocol,expected", [
    ("https://example.test/v1/responses/", None, "responses"),
    ("https://example.test/chat/completions", "auto", "chat"),
    ("https://example.test/v1/messages", None, "anthropic"),
])
def test_full_endpoint_is_not_appended(url, protocol, expected):
    client = OpenAICompatibleClient(url, "test-key", "demo", "custom", protocol)
    assert client.endpoint == url.rstrip("/")
    assert client.wire_api == expected
    assert len(client._connection_candidates) == 1


def test_explicit_protocol_conflict_is_actionable():
    with pytest.raises(AIConnectionError, match="不一致"):
        OpenAICompatibleClient("https://example.test/responses", "test-key", "demo", "custom", "chat")


def test_bare_host_tries_v1_but_preserves_vendor_paths():
    client = OpenAICompatibleClient("https://example.test", "test-key", "demo")
    assert client.endpoint == "https://example.test/v1/chat/completions"
    vendor = OpenAICompatibleClient("https://example.test/api/v3", "test-key", "demo")
    assert all("/api/v3/" in url and "/v1/" not in url for url, _ in vendor._connection_candidates)


def test_auto_detection_keeps_working_protocol(monkeypatch):
    client = OpenAICompatibleClient("https://example.test/v1", "test-key", "demo")
    calls = []
    def fake_chat(messages, temperature=0.35):
        calls.append(client.wire_api)
        if client.wire_api == "chat":
            raise AIConnectionError("HTTP 404", status=404, category="http")
        return '{"ok":true}'
    monkeypatch.setattr(client, "_chat", fake_chat)
    client.test()
    assert calls == ["chat", "responses"]
    assert client.endpoint.endswith("/responses")


@pytest.mark.parametrize("status,hint", [(401, "密钥"), (403, "权限"), (402, "余额"), (429, "限流"), (500, "上游")])
def test_auth_quota_and_server_errors_stop_and_hide_key(monkeypatch, status, hint):
    calls = []
    def fail(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, status, "error", {}, io.BytesIO(b'{"error":{"message":"rejected test-secret-key"}}'))
    monkeypatch.setattr("app.llm.urlopen", fail)
    client = OpenAICompatibleClient("https://example.test/v1", "test-secret-key", "demo")
    with pytest.raises(AIConnectionError) as error:
        client.test()
    assert hint in str(error.value)
    assert "test-secret-key" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("data", ["<html>proxy login</html>", "[]", '{"choices":[]}', '{"choices":[{"message":{"content":null}}]}', '{"output":[]}'])
def test_malformed_or_empty_response_is_diagnostic(monkeypatch, data):
    monkeypatch.setattr("app.llm.urlopen", lambda *a, **k: Response(data))
    client = OpenAICompatibleClient("https://example.test/v1", "test-key", "demo", wire_api="chat")
    with pytest.raises(AIConnectionError):
        client.test()


@pytest.mark.parametrize("error,hint", [(URLError("DNS failed"), "网络"), (TimeoutError(), "秒"), (ConnectionResetError(), "中断")])
def test_network_errors_are_readable(monkeypatch, error, hint):
    def fail(*a, **k): raise error
    monkeypatch.setattr("app.llm.urlopen", fail)
    with pytest.raises(AIConnectionError, match=hint):
        OpenAICompatibleClient("https://example.test/v1", "test-key", "demo").test()


@pytest.mark.parametrize("wire,events", [
    ("chat", [{"choices": [{"delta": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}]),
    ("responses", [{"type": "response.output_text.delta", "delta": '{"ok":true}'}, {"type": "response.completed", "response": {"output_text": '{"ok":true}'}}]),
    ("anthropic", [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": '{"ok":true}'}}, {"type": "message_stop"}]),
])
def test_streams_for_all_three_protocols(monkeypatch, wire, events):
    raw = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    monkeypatch.setattr("app.llm.urlopen", lambda *a, **k: Response(raw, "text/event-stream"))
    client = OpenAICompatibleClient("https://example.test/v1", "test-key", "demo", wire_api=wire, stream=True)
    client.test()


def test_truncated_stream_is_not_success():
    client = OpenAICompatibleClient("https://example.test/v1", "test-key", "demo")
    with pytest.raises(AIConnectionError, match="提前结束"):
        client._stream_text('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')


def test_json_validation_distinguishes_connectivity(monkeypatch):
    client = OpenAICompatibleClient("https://example.test/v1", "test-key", "demo")
    monkeypatch.setattr(client, "_chat", lambda *a, **k: "Hello!")
    with pytest.raises(AIConnectionError, match="服务已连通"):
        client.test()


def test_thinking_does_not_corrupt_json():
    assert OpenAICompatibleClient._json('<think>{"draft": false}</think>```json\n{"ok":true}\n```') == {"ok": True}


@pytest.mark.parametrize("key", ["", "   ", "key\nvalue", "Bearer key", "中文密钥"])
def test_bad_key_is_rejected_before_network(key):
    with pytest.raises(AIConnectionError, match="API Key"):
        OpenAICompatibleClient("https://example.test/v1", key, "demo")
