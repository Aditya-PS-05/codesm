"""Anthropic provider implementation with OAuth and API key support"""

from typing import AsyncIterator
import hashlib
import json
import os
import httpx

from .base import Provider, StreamChunk
from codesm.auth import ClaudeOAuth


class AnthropicProvider(Provider):
    provider_id = "anthropic"
    """Provider for Anthropic Claude models with OAuth support"""
    
    API_URL = "https://api.anthropic.com/v1/messages"
    CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    
    def __init__(self, model: str, settings=None):
        self.settings = settings
        self.options = dict(getattr(settings, "options", {}) or {})
        if settings and settings.base_url:
            base_url = settings.base_url.rstrip("/")
            self.API_URL = base_url + ("/messages" if base_url.endswith("/v1") else "/v1/messages")
        self.model = model
        self.oauth = ClaudeOAuth()
    
    async def _get_headers(self) -> dict:
        """Get headers for API request, handling OAuth vs API key"""
        creds = self.oauth.get_credentials()
        settings = getattr(self, "settings", None)
        if settings and settings.api_key:
            creds = {"auth_type": "api_key", "api_key": settings.api_key}
        elif settings and getattr(settings, "api_key_env", None):
            api_key = os.environ.get(settings.api_key_env)
            if not api_key:
                raise ValueError(f"Environment variable {settings.api_key_env} is not set")
            creds = {"auth_type": "api_key", "api_key": api_key}
        
        if not creds:
            # No credentials - try environment variable as fallback
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                creds = {"auth_type": "api_key", "api_key": api_key}
            else:
                raise ValueError("No Anthropic credentials found. Run /connect to authenticate.")

        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        betas = getattr(self, "options", {}).get("betas", [])
        if not isinstance(betas, list) or any(not isinstance(beta, str) for beta in betas):
            raise ValueError("Anthropic betas must be a list of header names")
        
        if creds.get("auth_type") == "api_key":
            headers["x-api-key"] = creds["api_key"]
        
        elif creds.get("auth_type") == "oauth":
            # Check if token is expired and refresh if needed
            if self.oauth.is_token_expired():
                refresh_token = creds.get("refresh_token")
                if refresh_token:
                    result = await self.oauth.refresh_token(refresh_token)
                    if not result["success"]:
                        raise PermissionError("Anthropic OAuth refresh failed. Run /connect to reconnect.")
                    creds = self.oauth.get_credentials()
                else:
                    raise PermissionError("Anthropic OAuth token expired. Run /connect to reconnect.")
            
            if not creds or not creds.get("access_token"):
                raise ValueError("No Anthropic OAuth access token available")
            headers["Authorization"] = f"Bearer {creds['access_token']}"
            betas = ["oauth-2025-04-20", "claude-code-20250219",
                "interleaved-thinking-2025-05-14", "fine-grained-tool-streaming-2025-05-14", *betas]
        else:
            raise ValueError("Invalid credential type")
        if betas:
            headers["anthropic-beta"] = ",".join(dict.fromkeys(betas))
        return headers

    async def list_models(self) -> list[dict]:
        """List account-visible models, including future model IDs and capabilities."""
        headers = await self._get_headers()
        models = []
        params = {"limit": 1000}
        seen = set()
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                response = await client.get(self.API_URL.rsplit("/", 1)[0] + "/models",
                    headers=headers, params=params)
                await self._check_response(response)
                page = response.json()
                models.extend(page.get("data", []))
                if not page.get("has_more"):
                    return models
                cursor = page.get("last_id")
                if not cursor or cursor in seen:
                    raise ValueError("Anthropic model listing returned an invalid pagination cursor")
                seen.add(cursor)
                params["after_id"] = cursor

    @staticmethod
    async def _check_response(response):
        if response.status_code != 200:
            error_text = await response.aread()
            try:
                error_json = json.loads(error_text)
                error_msg = error_json.get("error", {}).get("message", str(error_json))
            except (ValueError, AttributeError):
                error_msg = error_text.decode(errors="replace")[:500]
            raise httpx.HTTPStatusError(f"API error ({response.status_code}): {error_msg}",
                request=response.request, response=response)
    
    @staticmethod
    def _prefix_hash(system, tools, messages):
        return hashlib.sha256(json.dumps([system, tools, messages],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _convert_messages(self, messages: list[dict], *, system=None, tools=None) -> list[dict]:
        """Convert internal message format to Anthropic format"""
        result = []
        pending_tool_results = []
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "user":
                # Flush any pending tool results first
                if pending_tool_results:
                    result.append({
                        "role": "user",
                        "content": pending_tool_results,
                    })
                    pending_tool_results = []
                
                content = msg.get("content", "")
                if isinstance(content, list):
                    converted = []
                    for block in content:
                        if block["type"] == "image_url":
                            url = block["image_url"]["url"]
                            if url.startswith("data:"):
                                header, data = url.split(",", 1)
                                source = {"type": "base64", "media_type": header[5:].split(";")[0], "data": data}
                            else:
                                source = {"type": "url", "url": url}
                            converted.append({"type": "image", "source": source})
                        else:
                            converted.append(block)
                    content = converted
                result.append({"role": "user", "content": content})
            
            elif role == "assistant":
                if pending_tool_results:
                    result.append({"role": "user", "content": pending_tool_results})
                    pending_tool_results = []
                if (msg.get("response_provider") == self.provider_id
                        and msg.get("response_model") == getattr(self, "model", None)
                        and msg.get("response_items")):
                    content = msg["response_items"]
                    # Fable 5.1 binds thinking to the preceding system/tools/history.
                    # After local compaction, keep text/tools and discard invalid signatures.
                    if (system is not None
                            and self.model.startswith(("claude-fable-5-1", "claude-mythos-5-1"))
                            and msg.get("response_prefix") != self._prefix_hash(system, tools, result)):
                        content = [block for block in content
                            if block.get("type") not in ("thinking", "redacted_thinking")]
                    if content:
                        result.append({"role": "assistant", "content": content})
                    continue
                content = []
                if msg.get("content"):
                    content.append({
                        "type": "text",
                        "text": msg["content"],
                    })
                
                # Add tool use blocks
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        args = tc.get("function", {}).get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        
                        content.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "input": args,
                        })
                
                if content:
                    result.append({
                        "role": "assistant",
                        "content": content,
                    })
            
            elif role == "tool":
                # Collect tool results to send as user message
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
        
        # Flush remaining tool results
        if pending_tool_results:
            result.append({
                "role": "user",
                "content": pending_tool_results,
            })
        
        return result
    
    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Convert internal tool format to Anthropic format"""
        if not tools:
            return None
        
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]
    
    async def _stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a response from Claude using raw HTTP with OAuth support"""
        
        headers = await self._get_headers()
        anthropic_tools = self._convert_tools(tools)
        anthropic_messages = self._convert_messages(messages, system=system, tools=anthropic_tools)
        
        # Build request body
        body = {
            "model": self.model,
            "max_tokens": getattr(self, "options", {}).get("max_output_tokens", 8192),
            "system": system,
            "messages": anthropic_messages,
            "stream": True,
        }
        
        options = getattr(self, "options", {})
        for key in ("thinking", "output_config"):
            if key in options:
                if not isinstance(options[key], dict):
                    raise ValueError(f"Anthropic {key} must be an object")
                body[key] = dict(options[key])
        if options.get("reasoning_effort"):
            body.setdefault("output_config", {})["effort"] = options["reasoning_effort"]
        if anthropic_tools:
            body["tools"] = anthropic_tools
        
        # Preserve the complete ordered response, including opaque thinking signatures.
        blocks = {}
        pending = set()
        tool_inputs = {}
        usage = {}
        stopped = False
        stop_reason = None
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                self.API_URL,
                headers=headers,
                json=body,
            ) as response:
                await self._check_response(response)
                
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    
                    data = line[5:].lstrip()
                    if data == "[DONE]":
                        break
                    
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ValueError("Anthropic returned malformed stream data") from exc
                    
                    event_type = event.get("type")
                    
                    if event_type == "message_start":
                        usage.update(event.get("message", {}).get("usage", {}))
                    elif event_type == "message_delta":
                        usage.update(event.get("usage", {}))
                        stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
                    elif event_type == "message_stop":
                        if pending:
                            raise ValueError("Anthropic stream ended with incomplete content blocks")
                        if stop_reason not in ("end_turn", "stop_sequence", "tool_use"):
                            raise ValueError(f"Anthropic stopped without completing: {stop_reason}")
                        if stop_reason == "tool_use" and not any(block.get("type") == "tool_use" for block in blocks.values()):
                            raise ValueError("Anthropic stopped for tool use without a tool call")
                        stopped = True
                        yield StreamChunk(type="response_items", metadata={
                            "response_provider": self.provider_id,
                            "response_model": self.model,
                            "response_prefix": self._prefix_hash(system, anthropic_tools, anthropic_messages),
                            "response_items": [blocks[i] for i in sorted(blocks)],
                        })
                        if "input_tokens" in usage and "output_tokens" in usage:
                            yield StreamChunk(type="usage", metadata={
                                "input_tokens": sum(usage.get(k, 0) or 0 for k in
                                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                                "output_tokens": usage["output_tokens"],
                            })
                        break
                    elif event_type == "content_block_start":
                        index = event["index"]
                        if index in blocks:
                            raise ValueError("Anthropic returned a duplicate content block")
                        block = dict(event["content_block"])
                        blocks[index] = block
                        pending.add(index)
                        if block.get("type") == "text" and block.get("text"):
                            yield StreamChunk(type="text", content=block["text"])
                    
                    elif event_type == "content_block_delta":
                        index = event["index"]
                        block = blocks[index]
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            block["text"] = block.get("text", "") + delta.get("text", "")
                            yield StreamChunk(type="text", content=delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            tool_inputs[index] = tool_inputs.get(index, "") + delta.get("partial_json", "")
                        elif delta.get("type") == "thinking_delta":
                            block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                        elif delta.get("type") == "signature_delta":
                            block["signature"] = block.get("signature", "") + delta.get("signature", "")
                        elif delta.get("type") == "citations_delta":
                            block.setdefault("citations", []).append(delta["citation"])
                    
                    elif event_type == "content_block_stop":
                        index = event["index"]
                        block = blocks[index]
                        pending.discard(index)
                        if tool_inputs.get(index):
                            try:
                                block["input"] = json.loads(tool_inputs[index])
                            except json.JSONDecodeError as exc:
                                raise ValueError("Anthropic returned incomplete tool arguments") from exc
                        if block.get("type") == "tool_use":
                            yield StreamChunk(
                                type="tool_call",
                                id=block.get("id", ""),
                                name=block.get("name", ""),
                                args=block.get("input", {}),
                            )
                    
                    elif event_type == "error":
                        error = event.get("error", {})
                        raise ValueError(f"Stream error: {error.get('message', str(error))}")

        if not stopped:
            raise ValueError("Anthropic stream ended before message_stop")
