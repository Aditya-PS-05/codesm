"""Provider and budget contracts, with no network or credentials."""

from types import SimpleNamespace as NS

import pytest

from codesm.agent.execution import current_context
from codesm.agent.optimizer import Budget, CostLatencyOptimizer
from codesm.provider.base import Provider, StreamChunk
from codesm.provider.openai import OpenAIProvider


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def __aiter__(self):
        for event in self.events:
            yield event


@pytest.mark.asyncio
async def test_responses_preserves_tool_ids_reasoning_and_actual_usage():
    provider = object.__new__(OpenAIProvider)
    provider.model = "test-model"
    provider.options = {"reasoning_effort": "low", "max_output_tokens": 64}
    item = NS(type="function_call", call_id="call-1", name="read", arguments='{"path":"a.py"}')
    reasoning = {"type": "reasoning", "id": "r-1", "summary": [], "encrypted_content": "opaque"}
    call = {"type": "function_call", "id": "f-1", "call_id": "call-1", "name": "read", "arguments": item.arguments}
    stream = Stream([
        NS(type="response.output_text.delta", delta="Inspecting"),
        NS(type="response.output_item.done", item=item),
        NS(type="response.completed", response=NS(
            output=[NS(model_dump=lambda **kw: reasoning), NS(model_dump=lambda **kw: call)],
            usage=NS(input_tokens=30, output_tokens=7))),
    ])
    request = {}

    async def create(**kwargs):
        request.update(kwargs)
        return stream

    provider.client = NS(responses=NS(create=create))
    budget = CostLatencyOptimizer()
    events = []
    token = current_context.set({"budget": budget, "eval_events": events})
    try:
        chunks = [chunk async for chunk in provider.stream("rules", [{"role": "user", "content": "inspect"}], [])]
    finally:
        current_context.reset(token)
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    assert stream.closed
    assert chunks[1].id == "call-1"
    metadata = chunks[2].metadata
    assert provider._response_input([{"role": "assistant", **metadata}, {"role": "tool", "tool_call_id": "call-1", "content": "source"}]) == [
        reasoning, call, {"type": "function_call_output", "call_id": "call-1", "output": "source"},
    ]
    assert events[-1]["input_tokens"] == 30
    assert events[-1]["output_tokens"] == 7
    assert events[-1]["estimated"] is False
    assert events[-1]["cost_known"] is False


def test_parallel_reservations_cannot_overcommit_budget():
    budget = CostLatencyOptimizer(budget=Budget(session_limit=1.0, daily_limit=10, hard_limit=True))
    budget.prices = {"openai/test": {"input": 1000, "output": 1000}}
    first = budget.reserve("openai/test", 300, 300)
    with pytest.raises(RuntimeError, match="budget"):
        budget.reserve("openai/test", 300, 300)
    budget.settle(first, model="openai/test", input_tokens=100, output_tokens=100, latency_ms=1)
    budget.reserve("openai/test", 300, 300)
    with pytest.raises(RuntimeError, match="model_prices"):
        budget.reserve("openai/unknown", 1, 1)


@pytest.mark.asyncio
async def test_failed_stream_releases_reservation_and_records_uncertain_usage():
    class Broken(Provider):
        model = "broken"
        provider_id = "openai"
        options = {"max_output_tokens": 20}

        async def _stream(self, *args):
            yield StreamChunk(type="text", content="Partial")
            raise ConnectionError("lost stream")

    budget = CostLatencyOptimizer()
    token = current_context.set({"budget": budget})
    try:
        with pytest.raises(ConnectionError):
            async for _ in Broken().stream("", []):
                pass
    finally:
        current_context.reset(token)
    assert budget._reserved_tokens == 0
    record = budget._session_usage[0]
    assert not record.success and record.estimated and record.output_tokens == 20


def test_anthropic_tool_results_precede_next_assistant():
    from codesm.provider.anthropic import AnthropicProvider
    provider = object.__new__(AnthropicProvider)
    messages = provider._convert_messages([
        {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "source"},
        {"role": "assistant", "content": "Done"},
    ])
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_auto_routing_model_and_explicit_profile_precedence(monkeypatch, tmp_path):
    from codesm.agent.subagent import SubAgent, get_subagent_config
    from codesm.config.config import Config, AgentConfig
    from codesm.tool.registry import ToolRegistry
    selected = []
    monkeypatch.setattr("codesm.agent.subagent.get_provider", lambda model: selected.append(model) or NS())
    config = Config(agents={"coder": AgentConfig(model="openai/pinned")})
    args = (get_subagent_config("coder"), tmp_path, "openai/parent", ToolRegistry())
    SubAgent(*args, parent_context={"config": config}, model_override="openai/routed")
    assert selected[-1] == "openai/pinned"
    config.agents.clear()
    SubAgent(*args, parent_context={"config": config}, model_override="openai/routed")
    assert selected[-1] == "openai/routed"
    config.pin_model = True
    SubAgent(*args, parent_context={"config": config}, model_override="openai/routed")
    assert selected[-1] == "openai/parent"

@pytest.mark.asyncio
async def test_chat_stream_interleaved_calls_and_truncation():
    provider = object.__new__(OpenAIProvider)
    provider.model = "fixture"
    provider.options = {"api": "chat_completions"}
    def event(calls=None, finish=None, usage=None):
        return NS(choices=[NS(delta=NS(content=None, tool_calls=calls), finish_reason=finish)] if not usage else [], usage=usage)
    def call(index, id=None, name=None, args=None):
        return NS(index=index, id=id, function=NS(name=name, arguments=args))
    stream = Stream([
        event([call(0,"a","read",'{"path":'), call(1,"b","read",'{"path":')]),
        event([call(1,args='"b"}'),call(0,args='"a"}')]),
        event(finish="tool_calls"), event(usage=NS(prompt_tokens=10,completion_tokens=5)),
    ])
    async def create(**kwargs):
        return stream
    provider.client = NS(chat=NS(completions=NS(create=create)))
    chunks = [c async for c in provider._stream("", [])]
    assert [(c.id,c.args) for c in chunks if c.type=="tool_call"] == [("a",{"path":"a"}),("b",{"path":"b"})]
    assert stream.closed
    stream = Stream([event(finish="length")])
    with pytest.raises(ValueError, match="completing"):
        async for _ in provider._stream("", []):
            pass
    assert stream.closed


def test_compaction_drops_raw_response_items_when_filtering_calls():
    from codesm.session.context import ContextManager
    context = ContextManager()
    messages = [
        {"role":"assistant", "tool_calls":[{"id": x} for x in ("a","b")],
         "response_model":"fixture", "response_items":[{"type":"function_call","call_id":"a"},{"type":"function_call","call_id":"b"}]},
        {"role":"tool","tool_call_id":"a","content":"old "*100},
        {"role":"tool","tool_call_id":"b","content":"kept"},
    ]
    _, recent = context._select_recent_messages(messages, 25)
    assert recent[0]["tool_calls"] == [{"id":"b"}]
    assert "response_items" not in recent[0]

@pytest.mark.asyncio
async def test_explicit_provider_config_and_request_retry_accounting(monkeypatch):
    from codesm.config.config import Config, ProviderConfig
    from codesm.provider.base import get_provider
    created = {}
    class Client:
        def __init__(self, **kwargs):
            created.update(kwargs)
        async def close(self):
            pass
    monkeypatch.setattr("codesm.provider.openai.openai.AsyncOpenAI", Client)
    provider = get_provider("openai/fixture", Config(providers={"openai": ProviderConfig(
        api_key="test-credential",base_url="https://fixture.invalid/v1",options={"api":"chat_completions"})}))
    assert created["api_key"] == "test-credential" and created["base_url"] == "https://fixture.invalid/v1"
    assert created["max_retries"] == 0
    assert provider.options["api"] == "chat_completions"
    await provider.close()


def test_named_model_aliases_are_not_replaced_by_config_default(monkeypatch):
    from codesm.provider.base import get_provider
    from codesm.provider.router import ModelRouter
    from codesm.config import Config
    monkeypatch.setattr(ModelRouter, "PROVIDERS", {name: lambda model: NS(model=model) for name in ModelRouter.PROVIDERS})
    for alias in ("smart", "rush", "claude", "gpt-4o", "local"):
        assert get_provider(alias, Config()).model_key == "/".join(ModelRouter.resolve_model(alias))
    assert get_provider("finder", Config(model="openai/fixture")).model_key == "openai/fixture"
