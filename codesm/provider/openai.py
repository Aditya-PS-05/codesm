"""Native OpenAI Responses and OpenAI-compatible Chat Completions transport."""

from contextlib import aclosing
from copy import deepcopy
import json
import os

import openai

from .base import Provider, StreamChunk
from codesm.auth.credentials import CredentialStore


class OpenAIProvider(Provider):
    provider_id = "openai"
    default_api = "responses"

    def __init__(self, model: str, settings=None, *, provider_id="openai",
                 default_base_url=None, env_vars=("OPENAI_API_KEY",), default_api="responses"):
        self.model = model
        self.settings = settings
        self.provider_id = provider_id
        self.default_base_url = default_base_url
        self.env_vars = tuple(key for key in env_vars if provider_id == "openai" or key != "OPENAI_API_KEY")
        self.default_api = default_api
        self.options = dict(getattr(settings, "options", {}) or {})
        self.client = self._create_client()

    def _create_client(self) -> openai.AsyncOpenAI:
        settings = getattr(self, "settings", None)
        api_key = getattr(settings, "api_key", None)
        configured_env = getattr(settings, "api_key_env", None)
        if not api_key and configured_env:
            api_key = os.environ.get(configured_env)
            if not api_key:
                raise ValueError(f"Configured API key environment variable {configured_env} is not set")
        if not api_key:
            creds = CredentialStore().get(self.provider_id) or {}
            api_key = creds.get("api_key") or next(
                (os.environ[key] for key in self.env_vars if os.environ.get(key)), None)
        if not api_key:
            raise ValueError(f"No {self.provider_id} credentials found. Run /connect to authenticate.")
        return openai.AsyncOpenAI(api_key=api_key,
            base_url=getattr(settings, "base_url", None) or self.default_base_url, max_retries=0)

    def _convert_tools(self, tools):
        return [{"type": "function", "function": tool} for tool in tools] if tools else None

    def _extra_body(self):
        body = getattr(self, "options", {}).get("extra_body", {})
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise ValueError("extra_body must be an object")
        reserved = {"model", "models", "messages", "input", "tools", "functions", "instructions",
            "stream", "max_tokens", "max_completion_tokens", "max_output_tokens", "store", "n"}
        if overridden := reserved.intersection(body):
            raise ValueError(f"extra_body cannot override managed request fields: {', '.join(sorted(overridden))}")
        return deepcopy(body)

    async def _stream(self, system, messages, tools=None):
        api = getattr(self, "options", {}).get("api", self.default_api)
        if api not in ("responses", "chat_completions"):
            raise ValueError(f"Unsupported OpenAI API: {api}")
        method = self._stream_responses if api == "responses" else self._stream_chat
        async with aclosing(method(system, messages, tools)) as stream:
            async for chunk in stream:
                yield chunk

    def _same_response(self, message):
        # Older saved Responses turns only came from native OpenAI.
        return (message.get("response_model") == self.model
            and message.get("response_provider", "openai") == self.provider_id)

    def _response_input(self, messages):
        items = []
        for message in messages:
            role = message.get("role")
            if role == "assistant" and self._same_response(message) and message.get("response_items"):
                items.extend(message["response_items"])
            elif role in ("user", "assistant"):
                if message.get("content"):
                    content = message["content"]
                    if isinstance(content, list):
                        content = [{"type": "input_image", "image_url": c["image_url"]["url"]}
                            if c["type"] == "image_url" else {"type": "input_text", "text": c["text"]} for c in content]
                    items.append({"role": role, "content": content})
                for call in message.get("tool_calls", []):
                    items.append({"type": "function_call", "call_id": call["id"],
                        "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
            elif role == "tool":
                items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                    "output": message.get("content", "")})
        return items

    async def _stream_responses(self, system, messages, tools=None):
        options = getattr(self, "options", {})
        kwargs = {"model": self.model, "instructions": system,
            "input": self._response_input(messages), "stream": True, "store": False,
            "include": ["reasoning.encrypted_content"],
            "max_output_tokens": options.get("max_output_tokens", 8192)}
        if options.get("reasoning_effort"):
            kwargs["reasoning"] = {"effort": options["reasoning_effort"]}
        if extra_body := self._extra_body():
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = [{"type": "function", **tool, "strict": False} for tool in tools]
        completed = False
        stream = await self.client.responses.create(**kwargs)
        async with stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield StreamChunk(type="text", content=event.delta)
                elif event.type == "response.output_item.done" and event.item.type == "function_call":
                    yield StreamChunk(type="tool_call", id=event.item.call_id,
                        name=event.item.name, args=event.item.arguments)
                elif event.type == "response.completed":
                    completed = True
                    response = event.response
                    yield StreamChunk(type="response_items", metadata={
                        "response_provider": self.provider_id,
                        "response_model": self.model,
                        "response_items": [item.model_dump(exclude_none=True) for item in response.output],
                    })
                    if response.usage:
                        yield StreamChunk(type="usage", metadata={
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                        })
                elif event.type in ("error", "response.failed", "response.incomplete"):
                    response = getattr(event, "response", None)
                    detail = getattr(response, "error", None) or getattr(response, "incomplete_details", None) or event
                    message = getattr(detail, "message", None) or getattr(detail, "reason", None) or "unknown error"
                    code = getattr(detail, "code", None)
                    raise ValueError(f"OpenAI {event.type}: {code + ': ' if code else ''}{message}")
        if not completed:
            raise ValueError("OpenAI stream ended before response.completed")

    def _chat_messages(self, system, messages):
        full_messages = [{"role": "system", "content": system}]
        for message in messages:
            role = message.get("role")
            if role not in ("user", "assistant", "tool"):
                continue
            converted = {"role": role, "content": message.get("content", "")}
            if role == "tool":
                converted["tool_call_id"] = message.get("tool_call_id", "")
            elif role == "assistant":
                if message.get("tool_calls"):
                    # Provider extensions belong to the original model only.
                    converted["tool_calls"] = [{key: deepcopy(call[key])
                        for key in ("id", "type", "function") if key in call}
                        for call in message["tool_calls"]]
                if self._same_response(message):
                    replay = message.get("chat_response", {})
                    for key in ("content", "reasoning_content", "reasoning_details", "extra_content"):
                        if key in replay:
                            converted[key] = deepcopy(replay[key])
                    saved_calls = replay.get("tool_calls")
                    if saved_calls and [c["id"] for c in saved_calls] == [
                            c["id"] for c in converted.get("tool_calls", [])]:
                        converted["tool_calls"] = deepcopy(saved_calls)
            full_messages.append(converted)
        return full_messages

    async def _stream_chat(self, system, messages, tools=None):
        """Retain reasoning and opaque signatures needed for vendor tool loops."""
        options = getattr(self, "options", {})
        kwargs = {"model": self.model, "messages": self._chat_messages(system, messages), "stream": True}
        openai_tools = self._convert_tools(tools)
        if openai_tools:
            kwargs["tools"] = openai_tools
        stream_options = options.get("stream_options", {"include_usage": True})
        if stream_options is not None:
            kwargs["stream_options"] = stream_options
        modern_tokens = self.provider_id == "openai" or (
            self.provider_id == "kimi" and self.model.startswith("kimi-k3"))
        token_parameter = "max_completion_tokens" if modern_tokens else "max_tokens"
        kwargs[token_parameter] = options.get("max_output_tokens", 8192)
        if "temperature" in options:
            kwargs["temperature"] = options["temperature"]
        if getattr(self, "extra_headers", None):
            kwargs["extra_headers"] = self.extra_headers
        extra_body = self._extra_body()
        if options.get("reasoning_effort"):
            if self.provider_id == "openrouter":
                extra_body.setdefault("reasoning", {}).setdefault("effort", options["reasoning_effort"])
            else:
                kwargs["reasoning_effort"] = options["reasoning_effort"]
        if extra_body:
            kwargs["extra_body"] = extra_body
        stream = await self.client.chat.completions.create(**kwargs)
        tool_calls_accumulator: dict[int, dict] = {}
        reasoning_content = None
        reasoning_details: dict[int, dict] = {}
        extra_content = {}
        content_parts = None
        visible_text = ""
        finish = None
        async with stream:
            async for chunk in stream:
                if getattr(chunk, "error", None):
                    raise ValueError(f"{self.provider_id} stream error: {chunk.error}")
                if chunk.choices and chunk.choices[0].finish_reason:
                    finish = chunk.choices[0].finish_reason
                    if finish not in ("stop", "tool_calls", "function_call"):
                        raise ValueError(f"Provider stopped without completing: {finish}")
                if getattr(chunk, "usage", None):
                    yield StreamChunk(type="usage", metadata={"input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens})
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue
                if isinstance(delta.content, list):
                    if content_parts is None:
                        content_parts = [{"type": "text", "text": visible_text}] if visible_text else []
                    for part in delta.content:
                        part = part.model_dump(exclude_none=True) if hasattr(part, "model_dump") else part
                        content_parts.append(deepcopy(part))
                        if part.get("type") == "text" and part.get("text"):
                            visible_text += part["text"]
                            yield StreamChunk(type="text", content=part["text"])
                elif delta.content:
                    visible_text += delta.content
                    if content_parts is not None:
                        if content_parts and content_parts[-1].get("type") == "text":
                            content_parts[-1]["text"] += delta.content
                        else:
                            content_parts.append({"type": "text", "text": delta.content})
                    yield StreamChunk(type="text", content=delta.content)
                if getattr(delta, "reasoning_content", None) is not None:
                    reasoning_content = (reasoning_content or "") + delta.reasoning_content
                for position, detail in enumerate(getattr(delta, "reasoning_details", None) or []):
                    if hasattr(detail, "model_dump"):
                        detail = detail.model_dump(exclude_none=True)
                    target = reasoning_details.setdefault(detail.get("index", position), {})
                    _merge_stream_fields(target, detail, concatenate=("text", "summary", "data"))
                _merge_stream_fields(extra_content, getattr(delta, "extra_content", None) or {})
                for tc in delta.tool_calls or []:
                    acc = tool_calls_accumulator.setdefault(tc.index, {
                        "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            acc["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            acc["function"]["arguments"] += tc.function.arguments
                    if getattr(tc, "extra_content", None):
                        _merge_stream_fields(acc.setdefault("extra_content", {}), tc.extra_content)
        if finish is None:
            raise ValueError("Provider stream ended before completion")
        replay = {}
        if content_parts is not None:
            replay["content"] = content_parts
        if reasoning_content is not None:
            replay["reasoning_content"] = reasoning_content
        if reasoning_details:
            replay["reasoning_details"] = list(reasoning_details.values())
        if extra_content:
            replay["extra_content"] = extra_content
        if any("extra_content" in call for call in tool_calls_accumulator.values()):
            replay["tool_calls"] = [tool_calls_accumulator[idx] for idx in sorted(tool_calls_accumulator)]
        if replay:
            yield StreamChunk(type="response_items", metadata={"response_provider": self.provider_id,
                "response_model": self.model, "chat_response": replay})
        for idx in sorted(tool_calls_accumulator):
            tc = tool_calls_accumulator[idx]
            try:
                args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
            except json.JSONDecodeError:
                args = tc["function"]["arguments"]
            yield StreamChunk(type="tool_call", id=tc["id"], name=tc["function"]["name"], args=args)


def _merge_stream_fields(target, source, concatenate=()):
    """Merge extension fragments without interpreting opaque provider signatures."""
    for key, value in source.items():
        if value is None:
            continue
        if isinstance(value, dict):
            _merge_stream_fields(target.setdefault(key, {}), value, concatenate)
        elif key in concatenate and isinstance(value, str):
            target[key] = target.get(key, "") + value
        else:
            target[key] = deepcopy(value)
