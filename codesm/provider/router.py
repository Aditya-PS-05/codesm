"""Model routing and provider selection"""

from functools import partial
from .base import Provider
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider
from .ollama import OllamaProvider
from .catalog import PROVIDER_SPECS, canonical_provider


class ModelRouter:
    """Routes model requests to appropriate providers"""

    # Map of provider names to provider classes
    PROVIDERS = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "openrouter": OpenRouterProvider,
        "ollama": OllamaProvider,
        **{name: partial(OpenAIProvider, provider_id=name, default_base_url=spec.base_url,
                         env_vars=spec.env_vars, default_api="chat_completions")
           for name, spec in PROVIDER_SPECS.items()
           if name not in {"anthropic", "openai", "openrouter", "ollama"}},
    }

    # Common model aliases - can use OpenRouter for multi-model access
    MODEL_ALIASES = {
        # Direct provider access
        "claude": "anthropic/claude-sonnet-5",
        "claude-sonnet": "anthropic/claude-sonnet-5",
        "claude-opus": "anthropic/claude-opus-5",
        "claude-fable": "anthropic/claude-fable-5-1",
        "claude-haiku": "anthropic/claude-haiku-4-5-20251001",
        "gpt": "openai/gpt-5.6-sol",
        "gpt-fast": "openai/gpt-5.6-luna",
        "kimi": "kimi/kimi-k3",
        "glm": "zai/glm-5.3",
        "gemini": "google/gemini-3.8-flash",
        "deepseek": "deepseek/deepseek-v4-pro",
        "grok": "xai/grok-4.6",
        "mistral": "mistral/mistral-medium-3-5",
        "gpt-4": "openai/gpt-4-turbo",
        "gpt-4-turbo": "openai/gpt-4-turbo",
        "gpt-4o": "openai/gpt-4o",
        
        # OpenRouter aliases for multi-model orchestration
        "or-claude-sonnet": "openrouter/anthropic/claude-sonnet-5",
        "or-claude-opus": "openrouter/anthropic/claude-opus-5",
        "or-claude-haiku": "openrouter/anthropic/claude-haiku-4.5",
        "or-gpt-4o": "openrouter/openai/gpt-4o",
        "or-gpt-4o-mini": "openrouter/openai/gpt-4o-mini",
        "or-o1": "openrouter/openai/o1",
        "or-o1-mini": "openrouter/openai/o1-mini",
        "or-gemini-flash": "openrouter/google/gemini-3.8-flash",
        "or-gemini-pro": "openrouter/google/gemini-3.1-pro-preview",
        "or-deepseek": "openrouter/deepseek/deepseek-v4-pro",
        "or-llama": "openrouter/meta-llama/llama-3.1-70b-instruct",
        
        # Task-specific aliases (for subagent routing)
        "smart": "openrouter/anthropic/claude-sonnet-5",
        "rush": "openrouter/anthropic/claude-haiku-4.5",
        "oracle": "openrouter/openai/gpt-5.6-sol",
        "search": "openrouter/google/gemini-3.8-flash",
        "review": "openrouter/google/gemini-3.1-pro-preview",
        
        # Helper aliases remain overridable by per-agent configuration.
        "finder": "openrouter/google/gemini-3.8-flash",
        
        # Handoff system - context analysis and task continuation
        "handoff": "openrouter/google/gemini-3.8-flash",
        
        # Topics/Indexing - thread categorization (Flash-Lite for speed/cost)
        "topics": "openrouter/google/gemini-3.5-flash-lite",
        
        # Task Router - fast complexity classification
        "router": "openrouter/google/gemini-3.5-flash-lite",
        
        # Diagram generation
        "diagram": "openrouter/google/gemini-3.8-flash",
        
        # Local/Ollama aliases - for offline/private use
        "local": "ollama/qwen3:14b",
        "local-fast": "ollama/qwen3:4b",
        "local-code": "ollama/deepseek-coder-v2:16b",
        "local-large": "ollama/llama3.3:70b",
    }

    @classmethod
    def resolve_model(cls, model_string: str) -> tuple[str, str]:
        """Resolve model string to (provider, model_id)"""
        model_string = model_string.strip()
        if not model_string or any(c.isspace() for c in model_string):
            raise ValueError("Use a model alias or provider/model ID without whitespace")
        # Check if it's an alias
        if model_string in cls.MODEL_ALIASES:
            model_string = cls.MODEL_ALIASES[model_string]

        # Parse provider/model format
        if "/" in model_string:
            provider, model_id = model_string.split("/", 1)
            if not provider or not model_id:
                raise ValueError("Both provider and model ID are required")
            return canonical_provider(provider), model_id

        for prefixes, provider in (
            (("gpt-", "chat-", "o1", "o3", "o4"), "openai"),
            (("kimi-", "moonshot-"), "kimi"), (("glm-",), "zai"),
            (("gemini-",), "google"), (("deepseek-",), "deepseek"),
            (("grok-",), "xai"), (("mistral-", "ministral-", "codestral-", "devstral-", "magistral-"), "mistral"),
        ):
            if model_string.startswith(prefixes):
                return provider, model_string

        # Default to anthropic if no provider specified
        return "anthropic", model_string

    @classmethod
    def get_provider(cls, model_string: str, config=None) -> Provider:
        """Get appropriate provider instance for a model"""
        from .base import get_provider
        return get_provider(model_string, config)
