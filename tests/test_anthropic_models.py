"""Anthropic request/replay contracts; all HTTP uses an in-memory transport."""

import json
from types import SimpleNamespace as NS

import httpx
import pytest

from codesm.provider.anthropic import AnthropicProvider


def make_provider(monkeypatch, handler, model="claude-opus-5", **options):
    client = httpx.AsyncClient
    monkeypatch.setattr("codesm.provider.anthropic.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr("codesm.provider.anthropic.ClaudeOAuth",
        lambda: NS(get_credentials=lambda: None))
    return AnthropicProvider(model, settings=NS(api_key="test-key", api_key_env=None,
        base_url="https://anthropic.invalid/v1", options=options))


def sse(events):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join(f"data: {json.dumps(event)}\n\n" for event in events))


def content_events(index, block, *deltas):
    return [
        {"type": "content_block_start", "index": index, "content_block": block},
        *({"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas),
        {"type": "content_block_stop", "index": index},
    ]


def finish(reason="end_turn"):
    return [{"type": "message_delta", "delta": {"stop_reason": reason},
             "usage": {"output_tokens": 11}}, {"type": "message_stop"}]


@pytest.mark.asyncio
async def test_thinking_tool_replay_native_options_and_usage(monkeypatch):
    requests = []
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10,
            "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5, "output_tokens": 1}}},
        *content_events(0, {"type": "thinking", "thinking": "", "signature": ""},
            {"type": "signature_delta", "signature": "opaque-signature"}),
        *content_events(1, {"type": "redacted_thinking", "data": "opaque-redaction"}),
        *content_events(2, {"type": "text", "text": ""}, {"type": "text_delta", "text": "Inspecting"}),
        *content_events(3, {"type": "tool_use", "id": "read-1", "name": "read", "input": {}},
            {"type": "input_json_delta", "partial_json": '{"path":'},
            {"type": "input_json_delta", "partial_json": '"app.py"}'}),
        *finish("tool_use"),
    ]

    def handle(request):
        requests.append(json.loads(request.content))
        assert str(request.url) == "https://anthropic.invalid/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        return sse(events if len(requests) == 1 else [
            *content_events(0, {"type": "text", "text": "Done"}), *finish()])

    provider = make_provider(monkeypatch, handle, thinking={"type": "adaptive", "display": "omitted"},
        output_config={"effort": "medium"}, max_output_tokens=4096)
    messages = [{"role": "user", "content": "Inspect app.py"}]
    tools = [{"name": "read", "description": "Read a file", "parameters": {"type": "object"}}]
    chunks = [chunk async for chunk in provider._stream("rules", messages, tools)]
    metadata = next(chunk.metadata for chunk in chunks if chunk.type == "response_items")
    call = next(chunk for chunk in chunks if chunk.type == "tool_call")
    assert (call.id, call.name, call.args) == ("read-1", "read", {"path": "app.py"})
    assert chunks[-1].metadata == {"input_tokens": 35, "output_tokens": 11}
    assert metadata["response_items"][:2] == [
        {"type": "thinking", "thinking": "", "signature": "opaque-signature"},
        {"type": "redacted_thinking", "data": "opaque-redaction"},
    ]
    assert requests[0]["thinking"] == provider.options["thinking"]
    assert requests[0]["output_config"] == {"effort": "medium"}
    assert requests[0]["max_tokens"] == 4096
    messages += [
        {"role": "assistant", "content": "Inspecting", "tool_calls": [
            {"id": call.id, "function": {"name": call.name, "arguments": json.dumps(call.args)}}], **metadata},
        {"role": "tool", "tool_call_id": call.id, "content": "source"},
    ]
    _ = [chunk async for chunk in provider._stream("rules", messages, tools)]
    assert requests[1]["messages"][1]["content"] == metadata["response_items"]
    assert requests[1]["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "read-1", "content": "source"}]

    provider.model = "claude-sonnet-5"
    assert [block["type"] for block in provider._convert_messages(messages)[1]["content"]] == ["text", "tool_use"]
    provider.model = "claude-opus-5"
    messages[1]["response_provider"] = "openai"
    assert [block["type"] for block in provider._convert_messages(messages)[1]["content"]] == ["text", "tool_use"]


@pytest.mark.asyncio
async def test_default_request_does_not_force_thinking_or_report_missing_usage(monkeypatch):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return sse([*content_events(0, {"type": "text", "text": "Done"}), *finish()])

    provider = make_provider(monkeypatch, handle)
    chunks = [chunk async for chunk in provider._stream("", [{"role": "user", "content": "hi"}])]
    assert "thinking" not in requests[0] and "output_config" not in requests[0]
    assert not any(chunk.type == "usage" for chunk in chunks)
    provider.options = {"reasoning_effort": "low", "output_config": {"effort": "high"}}
    _ = [chunk async for chunk in provider._stream("", [])]
    assert requests[1]["output_config"] == {"effort": "low"}
    assert provider.options["output_config"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_fable_replays_unchanged_prefix_and_drops_invalidated_thinking(monkeypatch):
    provider = make_provider(monkeypatch, lambda request: sse([
        *content_events(0, {"type": "thinking", "thinking": "", "signature": ""},
            {"type": "thinking_delta", "thinking": "Checking"},
            {"type": "signature_delta", "signature": "bound-signature"}),
        *content_events(1, {"type": "text", "text": "Done"}), *finish(),
    ]), model="claude-fable-5-1")
    messages = [{"role": "user", "content": "hello"}]
    chunks = [chunk async for chunk in provider._stream("rules", messages)]
    metadata = next(chunk.metadata for chunk in chunks if chunk.type == "response_items")
    messages.append({"role": "assistant", "content": "Done", **metadata})
    unchanged = provider._convert_messages(messages, system="rules")
    assert unchanged[1]["content"][0] == {"type": "thinking", "thinking": "Checking", "signature": "bound-signature"}
    for system, history in (("changed rules", messages), ("rules", messages[1:])):
        converted = provider._convert_messages(history, system=system)
        assert converted[-1]["content"] == [{"type": "text", "text": "Done"}]
    assert metadata["response_items"][0]["signature"] == "bound-signature"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["max_tokens", "model_context_window_exceeded", "pause_turn", "refusal", None])
async def test_incomplete_stop_reasons_fail(monkeypatch, reason):
    provider = make_provider(monkeypatch, lambda request: sse(finish(reason)))
    with pytest.raises(ValueError, match="stopped without completing"):
        _ = [chunk async for chunk in provider._stream("", [])]


@pytest.mark.asyncio
@pytest.mark.parametrize("events, error", [
    ([{"type": "message_delta", "delta": {"stop_reason": "end_turn"}}], "before message_stop"),
    ([{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
      *finish()], "incomplete content blocks"),
    (content_events(0, {"type": "tool_use", "id": "a", "name": "read", "input": {}},
        {"type": "input_json_delta", "partial_json": '{"path":'}), "incomplete tool arguments"),
    (finish("tool_use"), "without a tool call"),
])
async def test_partial_streams_are_not_successful(monkeypatch, events, error):
    provider = make_provider(monkeypatch, lambda request: sse(events))
    with pytest.raises(ValueError, match=error):
        _ = [chunk async for chunk in provider._stream("", [])]


@pytest.mark.asyncio
async def test_malformed_stream_data_is_not_silently_dropped(monkeypatch):
    provider = make_provider(monkeypatch, lambda request:
        httpx.Response(200, content='data: {"type":\n\n'))
    with pytest.raises(ValueError, match="malformed stream data"):
        _ = [chunk async for chunk in provider._stream("", [])]


@pytest.mark.asyncio
async def test_model_discovery_paginates_with_same_auth_and_endpoint(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "test-key"
        assert request.url.path == "/v1/models"
        if len(requests) == 1:
            return httpx.Response(200, json={"data": [{"id": "claude-future", "max_input_tokens": 1000000}],
                "has_more": True, "last_id": "claude-future"})
        assert request.url.params["after_id"] == "claude-future"
        return httpx.Response(200, json={"data": [{"id": "claude-opus-5"}], "has_more": False})

    provider = make_provider(monkeypatch, handle)
    assert await provider.list_models() == [
        {"id": "claude-future", "max_input_tokens": 1000000}, {"id": "claude-opus-5"}]


@pytest.mark.asyncio
async def test_model_listing_reports_http_status(monkeypatch):
    provider = make_provider(monkeypatch, lambda request: httpx.Response(401,
        json={"error": {"message": "authentication failed"}}))
    with pytest.raises(httpx.HTTPStatusError) as error:
        await provider.list_models()
    assert error.value.response.status_code == 401


@pytest.mark.asyncio
async def test_configured_credentials_and_oauth_refresh(monkeypatch):
    provider = make_provider(monkeypatch, lambda request: httpx.Response(401))
    provider.settings.api_key = None
    provider.settings.api_key_env = "CODESM_TEST_ANTHROPIC_KEY"
    provider.oauth = NS(get_credentials=lambda: {"auth_type": "api_key", "api_key": "saved"})
    monkeypatch.setenv("CODESM_TEST_ANTHROPIC_KEY", "configured-env")
    assert (await provider._get_headers())["x-api-key"] == "configured-env"
    monkeypatch.delenv("CODESM_TEST_ANTHROPIC_KEY")
    with pytest.raises(ValueError, match="CODESM_TEST_ANTHROPIC_KEY"):
        await provider._get_headers()
    provider.settings.api_key = "explicit-key"
    assert (await provider._get_headers())["x-api-key"] == "explicit-key"

    provider.settings.api_key = None
    provider.settings.api_key_env = None
    creds = {"auth_type": "oauth", "access_token": "old", "refresh_token": "refresh"}

    async def refresh(token):
        assert token == "refresh"
        creds["access_token"] = "fresh"
        return {"success": True}

    provider.oauth = NS(get_credentials=lambda: creds, is_token_expired=lambda: True, refresh_token=refresh)
    headers = await provider._get_headers()
    assert headers["Authorization"] == "Bearer fresh"
    assert "oauth-2025-04-20" in headers["anthropic-beta"]
    assert "x-api-key" not in headers

    async def failed_refresh(token):
        return {"success": False, "error": "details that must not be displayed"}

    provider.oauth.refresh_token = failed_refresh
    with pytest.raises(PermissionError, match="Run /connect") as error:
        await provider._get_headers()
    assert "details" not in str(error.value)
