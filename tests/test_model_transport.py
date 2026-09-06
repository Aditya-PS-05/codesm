"""Wire contracts for native and compatible models, with no paid API calls."""

import json
from types import SimpleNamespace as NS

import httpx2 as httpx
import openai
import pytest

from codesm.provider.openai import OpenAIProvider
from codesm.provider.openrouter import OpenRouterProvider


TOOLS = [{"name": "read", "description": "Read a file", "parameters": {"type": "object", "properties": {}}}]


def chat_event(delta=None, finish=None, usage=None):
    return {"id": "completion", "object": "chat.completion.chunk", "created": 1, "model": "fixture",
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if usage is None else [],
        "usage": usage}


def tool_delta(index, call_id=None, name=None, args=None, extra_content=None):
    result = {"index": index, "id": call_id, "function": {"name": name, "arguments": args}}
    if extra_content:
        result["extra_content"] = extra_content
    return result


def wire_provider(events, *, provider_id="openai", model="fixture", options=None, provider_class=OpenAIProvider):
    requests = []

    def handler(request):
        if provider_class is OpenRouterProvider:
            assert request.headers["X-Title"] == "codesm"
            assert request.headers["HTTP-Referer"] == "https://github.com/Aditya-PS-05"
        requests.append(json.loads(request.content))
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body + "data: [DONE]\n\n")

    provider = object.__new__(provider_class)
    provider.provider_id = provider_id
    provider.model = model
    provider.options = options or {}
    provider.default_api = "responses" if provider_id == "openai" else "chat_completions"
    provider.client = openai.AsyncOpenAI(api_key="fake-test-key", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return provider, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "future-model"])
async def test_native_responses_accepts_model_ids_and_effort_without_sampling(model):
    events = [{"type": "response.completed", "response": {"id": "response-1", "object": "response",
        "status": "completed", "output": [], "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}}]
    provider, requests = wire_provider(events, model=model, options={"reasoning_effort": "max",
        "max_output_tokens": 100, "temperature": 0.7, "top_p": 0.9, "logprobs": True})
    try:
        chunks = [c async for c in provider._stream("rules", [{"role": "user", "content": "task"}], TOOLS)]
    finally:
        await provider.close()
    assert requests[0]["model"] == model
    assert requests[0]["reasoning"] == {"effort": "max"}
    assert requests[0]["max_output_tokens"] == 100
    assert not {"temperature", "top_p", "logprobs", "max_tokens"} & requests[0].keys()
    assert requests[0]["tools"][0]["name"] == "read"
    assert chunks[0].metadata["response_provider"] == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id, model, token_field", [
    ("kimi", "kimi-k3", "max_completion_tokens"),
    ("kimi", "kimi-k2.7-code", "max_tokens"),
    ("zai", "glm-5.3", "max_tokens"),
    ("deepseek", "deepseek-v4-pro", "max_tokens"),
    ("google", "gemini-3.8-flash", "max_tokens"),
])
async def test_compatible_reasoning_parallel_tools_and_signatures_roundtrip(provider_id, model, token_field):
    signature = {"google": {"thought_signature": "opaque-signature+/="}}
    events = [
        chat_event({"reasoning_content": "Consider ", "reasoning_details": [
            {"type": "reasoning.encrypted", "index": 0, "id": "r1", "format": "google-gemini-v1", "data": "opaque-"}],
            "tool_calls": [tool_delta(0, "call-a", "read", '{"path":', signature),
                tool_delta(1, "call-b", "read", '{"path":')]}),
        chat_event({"reasoning_content": "the code.", "reasoning_details": [{"index": 0, "data": "data"}],
            "tool_calls": [tool_delta(1, args='"b.py"}'), tool_delta(0, args='"a.py"}')]}),
        chat_event(finish="tool_calls"),
        chat_event(usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}),
    ]
    provider, requests = wire_provider(events, provider_id=provider_id, model=model,
        options={"max_output_tokens": 64, "extra_body": {"thinking": {"type": "enabled"}}})
    try:
        chunks = [c async for c in provider._stream("rules", [], TOOLS)]
        metadata = next(c.metadata for c in chunks if c.type == "response_items")
        calls = [c for c in chunks if c.type == "tool_call"]
        assistant = {"role": "assistant", "content": "", **metadata, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in calls]}
        history = [assistant] + [{"role": "tool", "tool_call_id": c.id, "content": "file content"} for c in calls]
        _ = [c async for c in provider._stream("rules", history, TOOLS)]
        assert [(c.id, c.args) for c in calls] == [("call-a", {"path": "a.py"}), ("call-b", {"path": "b.py"})]
        assert requests[0][token_field] == 64
        assert requests[0]["thinking"] == {"type": "enabled"}
        replayed = requests[1]["messages"][1]
        assert replayed["reasoning_content"] == "Consider the code."
        assert replayed["reasoning_details"] == [{"type": "reasoning.encrypted", "index": 0,
            "id": "r1", "format": "google-gemini-v1", "data": "opaque-data"}]
        assert replayed["tool_calls"][0]["extra_content"] == signature
        assert replayed["tool_calls"][0]["function"]["arguments"] == '{"path":"a.py"}'
        assert "extra_content" not in replayed["tool_calls"][1]
        assert next(c.metadata for c in chunks if c.type == "usage") == {"input_tokens": 20, "output_tokens": 10}
        provider.model = "other-model"
        assert "reasoning_content" not in provider._chat_messages("rules", history)[1]
        assert "extra_content" not in provider._chat_messages("rules", history)[1]["tool_calls"][0]
        provider.model = model
        provider.provider_id = "other-provider"
        assert "reasoning_details" not in provider._chat_messages("rules", history)[1]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openrouter_headers_reasoning_options_and_final_signature():
    provider, requests = wire_provider([
        chat_event({"content": "Done", "extra_content": {"google": {"thought_signature": "final-signature"}}}),
        chat_event(finish="stop"),
    ], provider_id="openrouter", provider_class=OpenRouterProvider, options={"reasoning_effort": "high",
        "temperature": 0.6, "stream_options": None, "extra_body": {"reasoning": {"exclude": False}}})
    try:
        chunks = [c async for c in provider._stream("", [])]
        metadata = next(c.metadata for c in chunks if c.type == "response_items")
        assert provider._chat_messages("", [{"role": "assistant", "content": "Done", **metadata}])[1]["extra_content"] == {
            "google": {"thought_signature": "final-signature"}}
    finally:
        await provider.close()
    assert requests[0]["reasoning"] == {"exclude": False, "effort": "high"}
    assert requests[0]["temperature"] == 0.6
    assert "stream_options" not in requests[0]
    assert provider.extra_headers["X-Title"] == "codesm"
    assert provider.options["extra_body"] == {"reasoning": {"exclude": False}}


@pytest.mark.asyncio
async def test_mistral_thinking_content_lists_are_hidden_and_replayed():
    thinking = {"type": "thinking", "thinking": [{"type": "text", "text": "Private intermediate text"}]}
    closing = {"type": "thinking", "thinking": [{"type": "text", "text": "."}]}
    provider, requests = wire_provider([
        chat_event({"content": [thinking]}),
        chat_event({"content": [closing, {"type": "text", "text": "The answer"}]}),
        chat_event({"content": " is 42."}), chat_event(finish="stop"),
    ], provider_id="mistral", model="mistral-medium-3-5")
    try:
        chunks = [c async for c in provider._stream("", [])]
        answer = "".join(c.content for c in chunks if c.type == "text")
        assert answer == "The answer is 42."
        metadata = next(c.metadata for c in chunks if c.type == "response_items")
        replayed = provider._chat_messages("", [{"role": "assistant", "content": answer, **metadata}])[1]
        assert replayed["content"] == [thinking, closing, {"type": "text", "text": answer}]
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [
    chat_event(finish="length"), chat_event(finish="content_filter"),
    {"error": {"message": "upstream unavailable", "code": 503}, "choices": []}, None,
])
async def test_failed_chat_stream_never_emits_executable_tools(ending):
    events = [chat_event({"tool_calls": [tool_delta(0, "call-a", "read", "{}")]})]
    if ending is not None:
        events.append(ending)
    provider, _ = wire_provider(events, provider_id="zai")
    emitted = []
    try:
        with pytest.raises((ValueError, openai.APIError)):
            async for chunk in provider._stream("", []):
                emitted.append(chunk)
        assert not any(chunk.type in ("tool_call", "response_items") for chunk in emitted)
    finally:
        await provider.close()


def test_response_replay_is_scoped_to_original_provider_and_model():
    provider = object.__new__(OpenAIProvider)
    provider.model = "same-name"
    message = {"role": "assistant", "content": "Visible", "response_provider": "anthropic",
        "response_model": "same-name", "response_items": [{"type": "thinking", "signature": "secret"}]}
    assert provider._response_input([message]) == [{"role": "assistant", "content": "Visible"}]
    message.update(response_provider="openai", response_model="another-model")
    assert provider._response_input([message]) == [{"role": "assistant", "content": "Visible"}]


def test_provider_credentials_never_fall_back_to_openai(monkeypatch):
    created = []
    stores = {"openai": {"api_key": "openai-stored"}, "kimi": {"api_key": "kimi-stored"}}
    monkeypatch.setattr("codesm.provider.openai.CredentialStore", lambda: NS(get=lambda name: stores.get(name)))
    monkeypatch.setattr("codesm.provider.openai.openai.AsyncOpenAI", lambda **kw: created.append(kw) or NS())
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("CUSTOM_VENDOR_KEY", raising=False)
    with pytest.raises(ValueError, match="No zai credentials"):
        OpenAIProvider("glm", provider_id="zai", env_vars=("ZAI_API_KEY",))
    with pytest.raises(ValueError, match="No custom credentials"):
        OpenAIProvider("custom-model", provider_id="custom")
    with pytest.raises(ValueError, match="CUSTOM_VENDOR_KEY"):
        OpenAIProvider("kimi-k3", NS(api_key_env="CUSTOM_VENDOR_KEY"), provider_id="kimi", env_vars=("MOONSHOT_API_KEY",))
    monkeypatch.setenv("CUSTOM_VENDOR_KEY", "custom-env")
    OpenAIProvider("kimi-k3", NS(api_key_env="CUSTOM_VENDOR_KEY"), provider_id="kimi",
        default_base_url="https://vendor.invalid/v1", env_vars=("MOONSHOT_API_KEY",), default_api="chat_completions")
    assert created[-1]["api_key"] == "custom-env"
    assert created[-1]["base_url"] == "https://vendor.invalid/v1"
    assert created[-1]["max_retries"] == 0
    OpenAIProvider("kimi-k3", NS(api_key="explicit", api_key_env="CUSTOM_VENDOR_KEY"), provider_id="kimi")
    assert created[-1]["api_key"] == "explicit"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["openai", "openrouter", "kimi"])
async def test_extra_body_cannot_override_budget_or_model(provider_id):
    provider, requests = wire_provider([], provider_id=provider_id,
        options={"max_output_tokens": 64, "extra_body": {"max_tokens": 1000000, "model": "other"}})
    try:
        with pytest.raises(ValueError, match="managed request fields"):
            _ = [c async for c in provider._stream("", [])]
        assert requests == []
    finally:
        await provider.close()
