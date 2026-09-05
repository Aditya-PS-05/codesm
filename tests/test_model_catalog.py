"""Model selection/discovery contracts, independent of account access."""

import json
from types import SimpleNamespace as NS

import httpx
import pytest
from typer.testing import CliRunner

from codesm.config.config import AgentConfig, Config, ProviderConfig
from codesm.provider.base import get_provider
from codesm.provider.catalog import discover_models, model_catalog, _fetch_models
from codesm.provider.router import ModelRouter


@pytest.mark.parametrize("model,expected", [
    ("gpt-6-astra", ("openai", "gpt-6-astra")),
    ("gpt-5.6-luna", ("openai", "gpt-5.6-luna")),
    ("kimi-k3", ("kimi", "kimi-k3")),
    ("moonshot/kimi-next", ("kimi", "kimi-next")),
    ("glm/glm-5.3", ("zai", "glm-5.3")),
    ("gemini-3.8-flash", ("google", "gemini-3.8-flash")),
    ("claude-fable-5-1", ("anthropic", "claude-fable-5-1")),
    ("openrouter/vendor/future-model", ("openrouter", "vendor/future-model")),
    ("company/deployment-vNext", ("company", "deployment-vNext")),
])
def test_exact_ids_and_provider_aliases(model, expected):
    assert ModelRouter.resolve_model(model) == expected


@pytest.mark.parametrize("model", ["", "openai/", "/gpt-6-astra", "openai/invalid model"])
def test_invalid_model_strings_fail_early(model):
    with pytest.raises(ValueError):
        ModelRouter.resolve_model(model)


def test_compatible_and_custom_routing_use_their_own_credentials(monkeypatch):
    created = []
    monkeypatch.setattr("codesm.provider.openai.openai.AsyncOpenAI", lambda **kw: created.append(kw) or NS())
    monkeypatch.setattr("codesm.auth.credentials.CredentialStore.get", lambda self, name: None)
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-provider")
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-credential")
    monkeypatch.setenv("COMPANY_KEY", "custom-credential")
    config = Config(providers={
        "glm": ProviderConfig(api_key="glm-credential", base_url="https://api.z.ai/api/coding/paas/v4"),
        "company": ProviderConfig(api_key_env="COMPANY_KEY", base_url="https://example.invalid/v1"),
    })
    for name, key, endpoint in [
        ("moonshot/kimi-k3", "kimi-credential", "https://api.moonshot.ai/v1"),
        ("glm/glm-5.3", "glm-credential", "https://api.z.ai/api/coding/paas/v4"),
        ("company/future", "custom-credential", "https://example.invalid/v1"),
    ]:
        instance = get_provider(name, config)
        assert created[-1]["api_key"] == key
        assert created[-1]["base_url"] == endpoint
        assert instance.default_api == "chat_completions"
        assert created[-1]["max_retries"] == 0
    monkeypatch.delenv("MOONSHOT_API_KEY")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="kimi credentials"):
        get_provider("kimi/kimi-k3", config)
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("unconfigured/future", config)


def test_offline_catalog_includes_profiles_and_future_custom_ids():
    config = Config(agents={"main": AgentConfig(model="openai/unreleased", reasoning_effort="max")},
        providers={"company": ProviderConfig(base_url="https://example.invalid/v1", models=["my-model"])})
    ids = {entry["id"] for entry in model_catalog(config)}
    assert {"openai/gpt-6-astra", "openai/gpt-5.6-luna", "anthropic/claude-fable-5-1",
            "kimi/kimi-k3", "zai/glm-5.3", "google/gemini-3.8-flash",
            "openai/unreleased", "company/my-model"} <= ids


@pytest.mark.asyncio
async def test_discovery_isolates_errors_preserves_configured_and_replaces_stale(monkeypatch):
    from codesm.provider import catalog
    config = Config(model="openai/gpt-6-astra", providers={"openai": ProviderConfig(models=["private-model"])})
    monkeypatch.setattr(catalog, "is_connected", lambda provider, config: provider in {"openai", "kimi"})

    async def fetch(provider, config):
        if provider == "kimi":
            request = httpx.Request("GET", "https://example.invalid/models?key=secret")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("secret", request=request, response=response)
        return [{"id": provider + "/next-release", "name": "Next", "provider": provider, "source": "api"}]

    monkeypatch.setattr(catalog, "_fetch_models", fetch)
    entries, errors = await discover_models(config)
    ids = {entry["id"] for entry in entries}
    assert "openai/next-release" in ids and "openai/private-model" in ids
    assert "openai/gpt-6-astra" in ids
    assert "openai/gpt-5.6-sol" not in ids
    assert "kimi/kimi-k3" in ids
    assert errors == {"kimi": "HTTP 401"}


def test_discovery_credentials_and_local_selection_match_transport(monkeypatch):
    from codesm.provider.catalog import is_connected
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("CUSTOM_MISSING_KEY", raising=False)
    monkeypatch.setattr("codesm.auth.credentials.CredentialStore.get", lambda self, name: {"api_key": "stored"})
    assert not is_connected("ollama", Config())
    assert is_connected("ollama", Config(model="ollama/local-coder"))
    assert is_connected("ollama", Config(providers={"ollama": ProviderConfig()}))
    assert not is_connected("kimi", Config(providers={"kimi": ProviderConfig(api_key_env="CUSTOM_MISSING_KEY")}))
    assert is_connected("kimi", Config(providers={"kimi": ProviderConfig(api_key="explicit", api_key_env="CUSTOM_MISSING_KEY")}))


@pytest.mark.asyncio
async def test_unreported_thinking_retains_output_allowance():
    from codesm.provider.base import Provider, StreamChunk
    from codesm.agent.execution import current_context
    from codesm.agent.optimizer import CostLatencyOptimizer

    class ThinkingProvider(Provider):
        provider_id = "kimi"
        model = "kimi-k3"
        options = {"max_output_tokens": 64}

        async def _stream(self, *args):
            yield StreamChunk(type="text", content="Done")
            yield StreamChunk(type="response_items", metadata={"chat_response": {"reasoning_content": "hidden " * 100}})

    budget = CostLatencyOptimizer()
    token = current_context.set({"budget": budget})
    try:
        _ = [chunk async for chunk in ThinkingProvider().stream("", [])]
    finally:
        current_context.reset(token)
    assert budget._session_usage[0].output_tokens == 64
    assert budget._session_usage[0].estimated


@pytest.mark.asyncio
async def test_sdk_discovery_filters_non_chat_models_and_closes_client(monkeypatch):
    import openai
    paths = []

    def response(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "gpt-future", "object": "model", "created": 0, "owned_by": "openai"},
            {"id": "gpt-image-2", "object": "model", "created": 0, "owned_by": "openai"},
            {"id": "text-embedding-3-small", "object": "model", "created": 0, "owned_by": "openai"},
        ]})

    client = openai.AsyncOpenAI(api_key="fixture", http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    monkeypatch.setattr("codesm.provider.openai.OpenAIProvider._create_client", lambda self: client)
    entries = await _fetch_models("openai", Config())
    assert [entry["id"] for entry in entries] == ["openai/gpt-future"]
    assert paths == ["/v1/models"]
    assert client.is_closed()


def test_models_cli_lists_without_credentials_and_reports_refresh_failure(monkeypatch, tmp_path):
    from codesm.cli import app
    from codesm.provider import catalog
    config_path = tmp_path / "codesm.json"
    config_path.write_text("{}")
    monkeypatch.setenv("CODESM_CONFIG", str(config_path))
    runner = CliRunner()
    result = runner.invoke(app, ["models", "--provider", "glm", "--json"])
    assert result.exit_code == 0
    assert "zai/glm-5.3" in {entry["id"] for entry in json.loads(result.stdout)["models"]}

    async def fail(*args):
        raise ConnectionError("private request details")

    monkeypatch.setattr(catalog, "_fetch_models", fail)
    result = runner.invoke(app, ["models", "--provider", "kimi", "--refresh", "--json"])
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["errors"] == {"kimi": "ConnectionError"}
    assert output["models"] and "private request" not in result.output


def test_compaction_removes_opaque_replay_when_removing_a_tool_call():
    from codesm.session.context import ContextManager, TokenEstimator
    messages = [{"role": "user", "content": "inspect"},
        {"role": "assistant", "tool_calls": [
            {"id": "a", "function": {"name": "read", "arguments": "{}"}},
            {"id": "b", "function": {"name": "read", "arguments": "{}"}},
        ], "response_model": "fixture", "response_provider": "google", "response_prefix": "prefix",
         "chat_response": {"reasoning_content": "thinking " * 100, "tool_calls": [{"id": "a"}, {"id": "b"}]}},
        {"role": "tool", "tool_call_id": "a", "content": "result"}]
    assert TokenEstimator().estimate_message(messages[1]) > TokenEstimator().estimate_message({"role": "assistant"})
    # a tool result whose sibling result is missing must not replay that sibling.
    _, recent = ContextManager()._select_recent_messages(messages, 25)
    assistant = next(message for message in recent if message["role"] == "assistant")
    assert "chat_response" not in assistant and "response_provider" not in assistant
    assert [call["id"] for call in assistant["tool_calls"]] == ["a"]
