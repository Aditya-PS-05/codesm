"""Provider abstraction for LLM APIs"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal


@dataclass
class StreamChunk:
    """A chunk of streamed response from an LLM"""
    type: Literal["text", "tool_call", "tool_call_delta", "tool_result", "handoff", "thinking", "thinking_done", "subagent_start", "subagent_done", "usage", "response_items", "run_status", "subagent_progress"]
    content: str = ""
    name: str = ""
    args: dict | str = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    id: str = ""
    # For handoff events
    new_session_id: str = ""
    # For thinking events
    thinking_summary: str = ""
    # For subagent events
    subagent_type: str = ""
    subagent_id: str = ""


class Provider(ABC):
    """Base class for LLM providers"""
    
    async def stream(self, system: str, messages: list[dict], tools: list[dict] | None = None):
        from codesm.agent.execution import current_context, emit
        from codesm.agent.optimizer import get_optimizer
        from codesm.session.context import TokenEstimator
        import json
        import time
        from dataclasses import asdict
        from contextlib import aclosing

        context = current_context.get() or {}
        budget = context.get("budget") or get_optimizer()
        model = getattr(self, "model_key", f"{self.provider_id}/{self.model}")
        options = getattr(self, "options", {})
        estimator = TokenEstimator()
        input_estimate = estimator.estimate_text(system + json.dumps(messages, default=str) + json.dumps(tools or []))
        output_limit = options.get("max_output_tokens", 8192)
        if isinstance(output_limit, bool) or not isinstance(output_limit, int) or output_limit < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        reservation = budget.reserve(model, input_estimate, output_limit)
        started = time.monotonic()
        usage = None
        success = False
        try:
            async with aclosing(self._stream(system, messages, tools)) as stream:
                async for chunk in stream:
                    if chunk.type == "usage":
                        usage = chunk.metadata
                    else:
                        yield chunk
            success = True
        finally:
            # Interrupted streams may have billed output that never arrived: retain the allowance.
            reported_in = usage.get("input_tokens") if usage else None
            reported_out = usage.get("output_tokens") if usage else None
            valid_in = type(reported_in) is int and reported_in >= 0
            valid_out = type(reported_out) is int and reported_out >= 0
            estimated = not (valid_in and valid_out and success)
            tokens_in = reported_in if valid_in else input_estimate
            # Hidden reasoning can consume the allowance even when visible text is short.
            tokens_out = reported_out if valid_out else output_limit
            if not success:
                tokens_out = max(tokens_out, output_limit)
            record = budget.settle(reservation, model=model, input_tokens=tokens_in,
                output_tokens=tokens_out, latency_ms=(time.monotonic() - started) * 1000,
                task_type=context.get("subagent_type", "main"), success=success, estimated=estimated,
                run_id=context.get("run_id", ""))
            fields = asdict(record)
            fields.pop("timestamp")
            if "usage_records" in context:
                context["usage_records"].append(fields.copy())
                context.get("save_session", lambda: None)()
            emit(context, "usage", **fields)
            totals = context.get("eval_usage")
            if totals is not None:
                totals["tokens_in"] = totals.get("tokens_in", 0) + tokens_in
                totals["tokens_out"] = totals.get("tokens_out", 0) + tokens_out
            queue = context.get("event_queue")
            if queue is not None:
                queue.put_nowait(StreamChunk(type="usage", metadata=fields,
                    subagent_id=context.get("run_id", "")))

    async def close(self):
        client = getattr(self, "client", None)
        if client is not None:
            closer = getattr(client, "close", None) or getattr(client, "aclose", None)
            if closer:
                await closer()
            elif hasattr(client, "_client"):
                await client._client.aclose()

    @abstractmethod
    async def _stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a response from the model"""
        pass


def get_provider(model: str, config=None) -> Provider:
    """Get a provider instance from a model string like 'anthropic/claude-sonnet-4'
    
    Supports:
        - anthropic/model-name
        - openai/model-name
        - openrouter/provider/model-name (e.g., openrouter/anthropic/claude-sonnet-4)
    """
    from .router import ModelRouter
    from .catalog import provider_settings
    from codesm.agent.execution import current_context
    context = current_context.get() or {}
    config = config or context.get("config")
    helper_aliases = {"main", "coder", "planner", "oracle", "reviewer", "researcher", "librarian", "finder", "diagram", "topics", "handoff", "router", "search", "review"}
    if "/" not in model and config is not None and (model in config.agents or model in helper_aliases):
        profile = config.agents.get(model)
        model = context.get("model", config.model) if config.pin_model else (profile.model if profile else None) or context.get("model", config.model)
    provider_id, model_id = ModelRouter.resolve_model(model)
    provider = ModelRouter.PROVIDERS.get(provider_id)
    settings = provider_settings(config, provider_id)
    if provider is None:
        if not settings or not settings.base_url:
            raise ValueError(f"Unknown provider: {provider_id}. Add providers.{provider_id}.base_url to codesm.json for an OpenAI-compatible endpoint.")
        from .openai import OpenAIProvider
        instance = OpenAIProvider(model_id, settings=settings, provider_id=provider_id,
            default_api="chat_completions", env_vars=(settings.api_key_env or f"{provider_id.upper()}_API_KEY",))
    else:
        instance = provider(model_id, settings=settings) if settings else provider(model_id)
    instance.model_key = f"{provider_id}/{model_id}"
    instance.options = {}
    if settings:
        instance.options.update(settings.options)
    return instance


async def complete(system: str, prompt: str | list[dict], model: str | None = None) -> str:
    """One-shot helpers use the same provider, accounting and limits as the main task."""
    from codesm.agent.execution import current_context
    context = current_context.get() or {}
    provider = get_provider(model or context.get("model", "smart"))
    text = []
    try:
        async for chunk in provider.stream(system, [{"role": "user", "content": prompt}]):
            if chunk.type == "text":
                text.append(chunk.content)
        return "".join(text)
    finally:
        await provider.close()
