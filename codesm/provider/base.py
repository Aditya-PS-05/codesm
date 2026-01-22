"""Provider abstraction for LLM APIs"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal


@dataclass
class StreamChunk:
    """A chunk of streamed response from an LLM"""
    type: Literal["text", "tool_call", "tool_call_delta", "tool_result", "handoff"]
    content: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)
    id: str = ""
    # For handoff events
    new_session_id: str = ""


class Provider(ABC):
    """Base class for LLM providers"""
    
    @abstractmethod
    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a response from the model"""
        pass


def get_provider(model: str) -> Provider:
    """Get a provider instance from a model string like 'anthropic/claude-sonnet-4'
    
    Supports:
        - anthropic/model-name
        - openai/model-name
        - openrouter/provider/model-name (e.g., openrouter/anthropic/claude-sonnet-4)
    """
    if "/" not in model:
        # Default to anthropic
        provider_id = "anthropic"
        model_id = model
    else:
        provider_id, model_id = model.split("/", 1)
    
    if provider_id == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(model_id)
    elif provider_id == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider(model_id)
    elif provider_id == "openrouter":
        from .openrouter import OpenRouterProvider
        # model_id here is "anthropic/claude-sonnet-4" or similar
        return OpenRouterProvider(model_id)
    elif provider_id == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider(model_id)
    else:
        raise ValueError(f"Unknown provider: {provider_id}. Supported: anthropic, openai, openrouter, ollama")
