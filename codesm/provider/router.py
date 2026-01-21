"""Model routing and provider selection"""

from typing import Dict, Type
from .base import Provider
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider


class ModelRouter:
    """Routes model requests to appropriate providers"""

    # Map of provider names to provider classes
    PROVIDERS: Dict[str, Type[Provider]] = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "openrouter": OpenRouterProvider,
    }

    # Common model aliases - can use OpenRouter for multi-model access
    MODEL_ALIASES = {
        # Direct provider access
        "claude": "anthropic/claude-sonnet-4-5-20250929",
        "claude-sonnet": "anthropic/claude-sonnet-4-5-20250929",
        "claude-opus": "anthropic/claude-opus-4-5-20251101",
        "gpt-4": "openai/gpt-4-turbo",
        "gpt-4-turbo": "openai/gpt-4-turbo",
        "gpt-4o": "openai/gpt-4o",
        
        # OpenRouter aliases for multi-model orchestration
        "or-claude-sonnet": "openrouter/anthropic/claude-sonnet-4",
        "or-claude-opus": "openrouter/anthropic/claude-opus-4",
        "or-claude-haiku": "openrouter/anthropic/claude-3.5-haiku",
        "or-gpt-4o": "openrouter/openai/gpt-4o",
        "or-gpt-4o-mini": "openrouter/openai/gpt-4o-mini",
        "or-o1": "openrouter/openai/o1",
        "or-o1-mini": "openrouter/openai/o1-mini",
        "or-gemini-flash": "openrouter/google/gemini-flash-1.5",
        "or-gemini-pro": "openrouter/google/gemini-pro-1.5",
        "or-deepseek": "openrouter/deepseek/deepseek-chat",
        "or-llama": "openrouter/meta-llama/llama-3.1-70b-instruct",
        
        # Task-specific aliases (for subagent routing)
        "smart": "openrouter/anthropic/claude-sonnet-4",
        "rush": "openrouter/anthropic/claude-3.5-haiku",
        "oracle": "openrouter/openai/o1-mini",
        "search": "openrouter/google/gemini-flash-1.5",
        "review": "openrouter/google/gemini-pro-1.5",
        
        # Gemini 2.5 Flash for high-speed codebase retrieval
        "gemini-3-flash": "openrouter/google/gemini-2.5-flash-preview",
        "finder": "openrouter/google/gemini-2.5-flash-preview",
        
        # Handoff system - context analysis and task continuation
        "handoff": "openrouter/google/gemini-2.5-flash-preview",
        
        # Topics/Indexing - thread categorization (Flash-Lite for speed/cost)
        "topics": "openrouter/google/gemini-2.0-flash-lite-001",
        
        # Task Router - fast complexity classification
        "router": "openrouter/google/gemini-2.0-flash-lite-001",
    }

    @classmethod
    def resolve_model(cls, model_string: str) -> tuple[str, str]:
        """Resolve model string to (provider, model_id)"""
        # Check if it's an alias
        if model_string in cls.MODEL_ALIASES:
            model_string = cls.MODEL_ALIASES[model_string]

        # Parse provider/model format
        if "/" in model_string:
            provider, model_id = model_string.split("/", 1)
            return provider, model_id

        # Default to anthropic if no provider specified
        return "anthropic", model_string

    @classmethod
    def get_provider(cls, model_string: str) -> Provider:
        """Get appropriate provider instance for a model"""
        provider_name, model_id = cls.resolve_model(model_string)

        if provider_name not in cls.PROVIDERS:
            raise ValueError(
                f"Unknown provider: {provider_name}. "
                f"Available: {', '.join(cls.PROVIDERS.keys())}"
            )

        provider_class = cls.PROVIDERS[provider_name]
        return provider_class(model_id)
