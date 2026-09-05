"""Provider endpoints and an offline catalog, refreshed on demand from provider APIs.

The catalog is a convenience, never an allowlist. Exact provider/model IDs pass
through unchanged. Seed IDs verified against official docs on 2026-09-05; see
docs/providers for sources. Account access is established by the provider API.
"""

import asyncio
import os
from dataclasses import dataclass

from codesm.auth.credentials import CredentialStore


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    env_vars: tuple[str, ...]
    models: tuple[str, ...] = ()
    rush_model: str | None = None


PROVIDER_SPECS = {
    "anthropic": ProviderSpec("Anthropic", "https://api.anthropic.com", ("ANTHROPIC_API_KEY",), (
        "claude-sonnet-5", "claude-fable-5-1", "claude-opus-5", "claude-haiku-4-5-20251001",
    ), "claude-haiku-4-5-20251001"),
    "openai": ProviderSpec("OpenAI", "https://api.openai.com/v1", ("OPENAI_API_KEY",), (
        "gpt-5.6-sol", "gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6",
        "gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
        "gpt-5.3-codex", "gpt-5.2", "gpt-5.2-pro", "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano",
        "gpt-5-pro", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3", "o3-pro",
    ), "gpt-5.6-luna"),
    "kimi": ProviderSpec("Kimi (Moonshot)", "https://api.moonshot.ai/v1", ("MOONSHOT_API_KEY", "KIMI_API_KEY"), (
        "kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6",
    ), "kimi-k2.7-code-highspeed"),
    "zai": ProviderSpec("Z.ai (GLM)", "https://api.z.ai/api/paas/v4", ("ZAI_API_KEY", "GLM_API_KEY", "ZHIPU_API_KEY"), (
        "glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5.1", "glm-5", "glm-4.7",
    ), "glm-5.3-flash"),
    "google": ProviderSpec("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), (
        "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
    ), "gemini-3.5-flash-lite"),
    "deepseek": ProviderSpec("DeepSeek", "https://api.deepseek.com", ("DEEPSEEK_API_KEY",), (
        "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
    ), "deepseek-v4-flash"),
    "xai": ProviderSpec("xAI", "https://api.x.ai/v1", ("XAI_API_KEY",), ("grok-4.6", "grok-4.5", "grok-4.3", "grok-build-0.1")),
    "mistral": ProviderSpec("Mistral", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",), (
        "mistral-medium-3-5", "mistral-small-latest", "mistral-small-2603", "mistral-large-latest", "mistral-large-2512",
    ), "mistral-small-latest"),
    "openrouter": ProviderSpec("OpenRouter", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",), (
        "anthropic/claude-sonnet-5", "anthropic/claude-fable-5.1", "anthropic/claude-opus-5", "anthropic/claude-haiku-4.5",
        "openai/gpt-6-astra", "openai/gpt-5.6-sol", "openai/gpt-5.6-terra", "openai/gpt-5.6-luna",
        "moonshotai/kimi-k3", "moonshotai/kimi-k2.7-code", "z-ai/glm-5.3", "z-ai/glm-5.3-flash",
        "google/gemini-3.8-flash", "google/gemini-3.1-pro-preview", "deepseek/deepseek-v4-pro",
    ), "anthropic/claude-haiku-4.5"),
    "ollama": ProviderSpec("Ollama (Local)", "http://localhost:11434", (), (
        "qwen3:14b", "qwen3:4b", "qwen2.5-coder:7b", "llama3.3:70b",
    ), "qwen3:4b"),
}

PROVIDER_ALIASES = {
    "moonshot": "kimi", "glm": "zai", "zhipu": "zai", "z.ai": "zai",
    "gemini": "google", "x-ai": "xai",
}


def canonical_provider(provider: str) -> str:
    return PROVIDER_ALIASES.get(provider.lower(), provider.lower())


def provider_settings(config, provider: str):
    if config is None:
        return None
    if provider in config.providers:
        return config.providers[provider]
    return next((settings for name, settings in config.providers.items()
                 if canonical_provider(name) == provider), None)


def provider_specs(config=None) -> dict[str, ProviderSpec]:
    specs = dict(PROVIDER_SPECS)
    for name, settings in (config.providers.items() if config else []):
        provider = canonical_provider(name)
        if provider not in specs and settings.base_url:
            specs[provider] = ProviderSpec(name, settings.base_url,
                (settings.api_key_env or f"{provider.upper()}_API_KEY",), tuple(settings.models))
    return specs


def model_entry(provider: str, model: str, *, name: str | None = None, source="built-in", config=None) -> dict:
    spec = provider_specs(config).get(provider)
    return {"id": f"{provider}/{model}", "name": name or model,
            "provider": spec.name if spec else provider, "source": source}


def model_catalog(config=None) -> list[dict]:
    entries = {}
    for provider, spec in provider_specs(config).items():
        for model in spec.models:
            entry = model_entry(provider, model, config=config)
            entries[entry["id"]] = entry
        settings = provider_settings(config, provider)
        for model in settings.models if settings else []:
            entry = model_entry(provider, model, source="configured", config=config)
            entries[entry["id"]] = entry
    if config:
        from .router import ModelRouter
        configured_main = [config.model] if "model" in config.model_fields_set else []
        for model in [*configured_main, *config.routing_models.values(), *(a.model for a in config.agents.values())]:
            if model:
                provider, model_id = ModelRouter.resolve_model(model)
                entry = model_entry(provider, model_id, source="configured", config=config)
                entries[entry["id"]] = entry
    return list(entries.values())


def is_connected(provider: str, config=None) -> bool:
    spec = provider_specs(config).get(provider)
    if not spec:
        return False
    settings = provider_settings(config, provider)
    if provider == "ollama":
        from .router import ModelRouter
        models = [config.model, *(a.model for a in config.agents.values())] if config else []
        return bool(settings or os.environ.get("OLLAMA_HOST") or any(
            model and ModelRouter.resolve_model(model)[0] == "ollama" for model in models))
    if settings and settings.api_key:
        return True
    if settings and settings.api_key_env:
        return bool(os.environ.get(settings.api_key_env))
    creds = CredentialStore().get(provider) or {}
    return bool(any(os.environ.get(key) for key in spec.env_vars) or creds.get("api_key") or creds.get("access_token"))


def _is_chat_model(provider: str, item: dict) -> bool:
    """Hide known non-chat APIs; explicit model IDs never go through this filter."""
    model = item["id"].lower()
    if provider == "openrouter":
        return "tools" in item.get("supported_parameters", []) and not model.endswith(":batch")
    if provider == "openai":
        return model.startswith(("gpt-", "chat-", "o1", "o3", "o4", "ft:")) and not any(
            kind in model for kind in ("audio", "realtime", "transcribe", "tts", "image", "search-preview", "deep-research"))
    return not any(kind in model for kind in ("embedding", "embed-", "moderation", "whisper", "tts", "imagen", "veo-"))


async def _fetch_models(provider: str, config) -> list[dict]:
    from .base import get_provider
    import httpx

    spec = provider_specs(config)[provider]
    # OpenRouter's public catalog also exposes tool support, unlike generic /models.
    settings = provider_settings(config, provider)
    if provider == "openrouter":
        base_url = (settings.base_url if settings else None) or spec.base_url
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(base_url.rstrip("/") + "/models")
            response.raise_for_status()
            raw = response.json()["data"]
    else:
        instance = get_provider(f"{provider}/{spec.models[0] if spec.models else 'catalog'}", config)
        try:
            if provider == "anthropic":
                raw = await instance.list_models()
            elif provider == "ollama":
                response = await instance.client.list()
                raw = [{"id": model.model, "name": model.model} for model in response.models]
            else:
                page = await instance.client.models.list()
                raw = [item.model_dump(exclude_none=True) async for item in page]
        finally:
            await instance.close()
    return [model_entry(provider, item["id"], name=item.get("display_name") or item.get("name"), source="api", config=config)
            for item in raw if isinstance(item, dict) and isinstance(item.get("id"), str) and _is_chat_model(provider, item)]


async def discover_models(config, provider: str | None = None) -> tuple[list[dict], dict[str, str]]:
    """Refresh connected providers, retaining offline entries on per-provider failure."""
    import httpx

    specs = provider_specs(config)
    provider = canonical_provider(provider) if provider else None
    if provider and provider not in specs:
        raise ValueError(f"Unknown provider: {provider}. Configure its base_url first.")
    selected = [provider] if provider else [p for p in specs if p == "openrouter" or is_connected(p, config)]
    entries = model_catalog(config)
    errors = {}
    results = await asyncio.gather(*(asyncio.wait_for(_fetch_models(p, config), timeout=20) for p in selected), return_exceptions=True)
    for provider_id, result in zip(selected, results):
        if isinstance(result, BaseException):
            # SDK exception bodies/URLs can include credentials; display only status/type.
            status = getattr(result, "status_code", None)
            if isinstance(result, httpx.HTTPStatusError):
                status = result.response.status_code
            errors[provider_id] = ("authentication required; reconnect with /connect" if isinstance(result, PermissionError)
                else f"HTTP {status}" if status else type(result).__name__)
            continue
        entries = [entry for entry in entries if entry["id"].split("/", 1)[0] != provider_id or entry["source"] == "configured"]
        by_id = {entry["id"]: entry for entry in entries}
        by_id.update((entry["id"], entry) for entry in result)
        entries = list(by_id.values())
    if provider:
        entries = [entry for entry in entries if entry["id"].startswith(provider + "/")]
    return entries, errors
