"""OpenRouter provider implementation - unified access to multiple models"""

from typing import AsyncIterator
import json
import os
import logging
import time
import openai

from .base import Provider, StreamChunk
from codesm.auth.credentials import CredentialStore

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Rough token estimation (~4 chars per token)"""
    return max(1, len(text) // 4)


class OpenRouterProvider(Provider):
    """Provider for OpenRouter API - access Claude, GPT, Gemini, and more with one key"""
    
    BASE_URL = "https://openrouter.ai/api/v1"
    
    def __init__(self, model: str):
        self.model = model
        self.client = self._create_client()
    
    def _create_client(self) -> openai.AsyncOpenAI:
        """Create OpenRouter client (OpenAI-compatible) with stored credentials or env var"""
        store = CredentialStore()
        creds = store.get("openrouter")
        
        if creds and creds.get("api_key"):
            return openai.AsyncOpenAI(
                api_key=creds["api_key"],
                base_url=self.BASE_URL,
            )
        
        # Fall back to environment variable
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if api_key:
            return openai.AsyncOpenAI(
                api_key=api_key,
                base_url=self.BASE_URL,
            )
        
        raise ValueError("No OpenRouter credentials found. Run /connect to authenticate.")
    
    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Convert internal tool format to OpenAI format"""
        if not tools:
            return None
        
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]
    
    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a response from OpenRouter"""

        logger.info(f"Making OpenRouter API call with model: {self.model}")
        logger.debug(f"Messages count: {len(messages)}, Tools: {len(tools) if tools else 0}")

        # Build messages with system prompt
        full_messages = [{"role": "system", "content": system}]
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "user":
                full_messages.append({
                    "role": "user",
                    "content": msg.get("content", ""),
                })
            
            elif role == "assistant":
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.get("content", ""),
                }
                
                # Add tool calls if present
                if msg.get("tool_calls"):
                    assistant_msg["tool_calls"] = msg["tool_calls"]
                
                full_messages.append(assistant_msg)
            
            elif role == "tool":
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
        
        # Build request kwargs
        kwargs = {
            "model": self.model,
            "messages": full_messages,
            "stream": True,
            "stream_options": {"include_usage": True},  # Enable proper streaming
            "extra_headers": {
                "HTTP-Referer": "https://github.com/Aditya-PS-05",
                "X-Title": "codesm",
            },
        }
        
        openai_tools = self._convert_tools(tools)
        if openai_tools:
            kwargs["tools"] = openai_tools
        
        # Track tool calls during streaming
        tool_calls_accumulator: dict[int, dict] = {}

        logger.info("OpenRouter API request sent, awaiting response...")
        start_time = time.time()
        stream = await self.client.chat.completions.create(**kwargs)
        logger.info("OpenRouter stream started, receiving chunks...")
        
        # Track output for usage recording
        output_text = ""

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            
            # Handle text content
            if delta.content:
                output_text += delta.content
                yield StreamChunk(type="text", content=delta.content)
            
            # Handle tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    
                    if idx not in tool_calls_accumulator:
                        tool_calls_accumulator[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    
                    acc = tool_calls_accumulator[idx]
                    
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            acc["name"] = tc.function.name
                        if tc.function.arguments:
                            acc["arguments"] += tc.function.arguments
        
        # Emit accumulated tool calls
        for idx in sorted(tool_calls_accumulator.keys()):
            tc = tool_calls_accumulator[idx]
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            
            yield StreamChunk(
                type="tool_call",
                id=tc["id"],
                name=tc["name"],
                args=args,
            )
        
        # Record usage metrics
        try:
            from codesm.agent.optimizer import record_usage
            
            # Estimate input tokens from messages
            input_text = system + " ".join(
                m.get("content", "") for m in full_messages if m.get("content")
            )
            input_tokens = _estimate_tokens(input_text)
            output_tokens = _estimate_tokens(output_text)
            latency_ms = (time.time() - start_time) * 1000
            
            record_usage(
                model=f"openrouter/{self.model}",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.debug(f"Failed to record usage: {e}")
