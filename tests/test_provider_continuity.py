"""Saved work remains portable; opaque model state stays with its origin."""

from copy import deepcopy
import json

import pytest

from codesm.provider.anthropic import AnthropicProvider
from codesm.provider.openai import OpenAIProvider
from codesm.provider.openrouter import OpenRouterProvider
from codesm.session.session import Session


def provider(name):
    cls = {"openai": OpenAIProvider, "anthropic": AnthropicProvider,
        "openrouter": OpenRouterProvider}[name]
    result = object.__new__(cls)
    # Identical IDs also catch accidental replay based on model name alone.
    result.model = "fixture-model"
    return result


def converted(target, messages):
    if target.provider_id == "anthropic":
        return target._convert_messages(messages)
    if target.provider_id == "openrouter":
        return target._chat_messages("rules", messages)
    return target._response_input(messages)


def assistant_turn(origin):
    calls = [{"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}} for call_id, name, args in (
            ("call_read", "read", {"path": "app.py"}),
            ("call_edit", "edit", {"path": "app.py", "old": "bug", "new": "fix"}))]
    turn = {"role": "assistant", "content": "I found the bug and will fix app.py.",
        "tool_calls": calls, "response_provider": origin, "response_model": "fixture-model"}
    if origin == "openai":
        turn["response_items"] = [
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-openai"},
            {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": turn["content"], "annotations": []}]},
            *({"type": "function_call", "id": f"fc_{call['id']}", "call_id": call["id"],
                "name": call["function"]["name"], "arguments": call["function"]["arguments"]}
                for call in calls),
        ]
    elif origin == "anthropic":
        turn["response_items"] = [
            {"type": "thinking", "thinking": "", "signature": "opaque-anthropic"},
            {"type": "redacted_thinking", "data": "opaque-redaction"},
            {"type": "text", "text": turn["content"]},
            *({"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                "input": json.loads(call["function"]["arguments"])} for call in calls),
        ]
    else:
        turn["chat_response"] = {"reasoning_details": [
            {"type": "reasoning.encrypted", "data": "opaque-router"}],
            "tool_calls": [{**call, "extra_content": {"signature": "opaque-router"}} for call in calls]}
    return turn


@pytest.mark.parametrize("origin,destination", [
    ("openai", "anthropic"), ("anthropic", "openai"),
    ("openrouter", "anthropic"), ("anthropic", "openrouter"),
    ("openai", "openrouter"), ("openrouter", "openai"),
])
@pytest.mark.parametrize("interrupted", [False, True])
def test_saved_tool_work_survives_provider_switch_and_switch_back(tmp_path, origin, destination, interrupted):
    session = Session.create(tmp_path)
    session.add_message(role="user", content="Fix the bug in app.py and run the tests.")
    turn = assistant_turn(origin)
    session.add_message(**turn)
    # Results may complete out of order, and a crash can leave one outcome unknown.
    if not interrupted:
        session.add_message(role="tool", tool_call_id="call_edit", content="Updated app.py.")
    session.add_message(role="tool", tool_call_id="call_read", content="app.py contains bug")
    session.add_message(role="user", content="Continue with the new provider.")
    saved = deepcopy(session.messages)
    resumed = Session.load(session.id)
    history = resumed.get_messages()

    assert resumed.messages == saved
    assert [message["tool_call_id"] for message in history if message["role"] == "tool"] == [
        "call_read", "call_edit"]
    if interrupted:
        assert "outcome of this call is unknown" in history[3]["content"]
        assert "Inspect current state before retrying" in history[3]["content"]
    else:
        assert history[3]["content"] == "Updated app.py."

    canonical = deepcopy(history)
    for message in canonical:
        for key in ("response_provider", "response_model", "response_items", "chat_response"):
            message.pop(key, None)
    target = provider(destination)
    wire = converted(target, history)
    assert wire == converted(target, canonical)
    assert "opaque-" not in json.dumps(wire)
    assert "app.py contains bug" in json.dumps(wire)
    assert "Continue with the new provider." in json.dumps(wire)

    # Switching back can still replay the original opaque state unchanged.
    original = provider(origin)
    replay = converted(original, history)
    assert "opaque-" in json.dumps(replay)
    original.model = "another-model"
    assert converted(original, history) == converted(original, canonical)
    assert resumed.messages == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type,details,expected", [
    ("error", {"code": "rate_limit_exceeded", "message": "Fixture quota exhausted"}, "Fixture quota exhausted"),
    ("response.failed", {"error": {"code": "rate_limit_exceeded", "message": "Fixture quota exhausted"}},
        "rate_limit_exceeded: Fixture quota exhausted"),
    ("response.incomplete", {"incomplete_details": {"reason": "max_output_tokens"}}, "max_output_tokens"),
    ("response.failed", {}, "unknown error"),
])
async def test_response_errors_cannot_leak_opaque_output_into_continuation(tmp_path, event_type, details, expected):
    from codesm.memory.continuation import continuation_context
    from codesm.memory.history import HistoryStore
    from tests.test_model_transport import wire_provider

    output = [{"type": "reasoning", "id": "rs_secret", "summary": [],
        "encrypted_content": "OpaqueReplayMustStayPrivate"}]
    event = {"type": event_type}
    if event_type == "error":
        event.update(details, output=output)
    else:
        event["response"] = {"id": "response_fixture", "object": "response", "output": output, **details}
    target, _ = wire_provider([event])
    try:
        with pytest.raises(ValueError, match=expected) as error:
            _ = [chunk async for chunk in target._stream("rules", [])]
    finally:
        await target.close()
    session = Session.create(tmp_path)
    session.run_state = {"status": "failed", "error": str(error.value)}
    session.save()
    assert "OpaqueReplayMustStayPrivate" not in str(error.value)
    assert "OpaqueReplayMustStayPrivate" not in continuation_context(session, "continue")
    assert HistoryStore().search(tmp_path, "OpaqueReplayMustStayPrivate") == []
