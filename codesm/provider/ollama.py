"""Ollama provider implementation for local LLM inference"""

from typing import AsyncIterator
import json
import os
import logging

try:
    from ollama import AsyncClient
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    AsyncClient = None

from .base import Provider, StreamChunk

logger = logging.getLogger(__name__)


class OllamaProvider(Provider):
    """Provider for local Ollama models"""
    
    def __init__(self, model: str, host: str | None = None):
        if not OLLAMA_AVAILABLE:
            raise ImportError(
                "Ollama package not installed. Install with: uv add ollama"
            )
        
        self.model = model
        self.host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.client = AsyncClient(host=self.host)
    
    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Convert internal tool format to Ollama format (OpenAI-compatible)"""
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
    
    def _convert_messages(self, system: str, messages: list[dict]) -> list[dict]:
        """Convert internal message format to Ollama format"""
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
                
                if msg.get("tool_calls"):
                    assistant_msg["tool_calls"] = msg["tool_calls"]
                
                full_messages.append(assistant_msg)
            
            elif role == "tool":
                full_messages.append({
                    "role": "tool",
                    "content": msg.get("content", ""),
                })
        
        return full_messages
    
    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a response from Ollama"""
        
        logger.info(f"Making Ollama API call with model: {self.model}")
        logger.debug(f"Messages count: {len(messages)}, Tools: {len(tools) if tools else 0}")
        
        full_messages = self._convert_messages(system, messages)
        ollama_tools = self._convert_tools(tools)
        
        kwargs = {
            "model": self.model,
            "messages": full_messages,
            "stream": True,
        }
        
        if ollama_tools:
            kwargs["tools"] = ollama_tools
        
        tool_calls_accumulator: dict[int, dict] = {}
        
        logger.info("Ollama API request sent, awaiting response...")
        
        try:
            async for chunk in await self.client.chat(**kwargs):
                message = chunk.get("message", {})
                
                if message.get("content"):
                    yield StreamChunk(type="text", content=message["content"])
                
                if message.get("tool_calls"):
                    for idx, tc in enumerate(message["tool_calls"]):
                        if idx not in tool_calls_accumulator:
                            tool_calls_accumulator[idx] = {
                                "id": f"call_{idx}",
                                "name": "",
                                "arguments": "",
                            }
                        
                        acc = tool_calls_accumulator[idx]
                        func = tc.get("function", {})
                        
                        if func.get("name"):
                            acc["name"] = func["name"]
                        if func.get("arguments"):
                            if isinstance(func["arguments"], dict):
                                acc["arguments"] = json.dumps(func["arguments"])
                            else:
                                acc["arguments"] += str(func["arguments"])
                
                if chunk.get("done"):
                    break
            
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
                
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            raise ConnectionError(
                f"Failed to connect to Ollama at {self.host}. "
                f"Make sure Ollama is running: ollama serve"
            ) from e
