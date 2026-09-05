"""OpenRouter uses the common OpenAI-compatible streaming transport."""

from .openai import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    provider_id = "openrouter"
    default_api = "chat_completions"
    BASE_URL = "https://openrouter.ai/api/v1"
    extra_headers = {
        "HTTP-Referer": "https://github.com/Aditya-PS-05",
        "X-Title": "codesm",
    }

    def __init__(self, model: str, settings=None):
        super().__init__(model, settings, provider_id="openrouter",
            default_base_url=self.BASE_URL, env_vars=("OPENROUTER_API_KEY",),
            default_api="chat_completions")
