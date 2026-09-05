"""Model discovery and provider connection through the terminal UI."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from textual.app import App
from textual.widgets import Input, Static

from codesm.auth.credentials import CredentialStore
from codesm.config.config import Config, ProviderConfig
from codesm.provider.catalog import model_catalog, provider_specs
from codesm.tui.app import CodesmApp
from codesm.tui.modals import APIKeyInputModal, ModelSelectModal, ProviderConnectModal


@pytest.fixture(autouse=True)
def isolated_providers(monkeypatch):
    stored = {}
    monkeypatch.setattr(CredentialStore, "__init__", lambda self: None)
    monkeypatch.setattr(CredentialStore, "_load", lambda self: deepcopy(stored))
    monkeypatch.setattr(CredentialStore, "_save", lambda self, data: stored.update(data))

    async def offline_catalog(config, provider=None):
        entries = model_catalog(config)
        return [entry for entry in entries if not provider or entry["id"].startswith(provider + "/")], {}

    monkeypatch.setattr("codesm.tui.modals.discover_models", offline_catalog)
    return stored


@pytest.mark.asyncio
async def test_model_refresh_is_nonblocking_preserves_current_and_failed_catalog(monkeypatch):
    release = asyncio.Event()
    calls = []
    fresh = {"id": "kimi/kimi-new", "name": "Newest Kimi", "provider": "Kimi", "source": "api"}

    async def discover(config, provider=None):
        calls.append((config.model, provider))
        if len(calls) == 1:
            await release.wait()
            return [fresh], {}
        return [], {"kimi": "HTTP 503"}

    monkeypatch.setattr("codesm.tui.modals.discover_models", discover)
    app = App()
    selected = []
    modal = ModelSelectModal("ollama/private-model", Config())
    async with app.run_test(size=(100, 45)) as pilot:
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        assert calls == [("ollama/private-model", None)]
        assert any(item.item_id == "ollama/private-model" for item in modal.visible_items)
        modal.query_one(Input).value = "kimi"
        await pilot.pause()
        assert modal.visible_items and not release.is_set()
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert any(item.item_id == fresh["id"] for item in modal.visible_items)
        await pilot.press("ctrl+r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(calls) == 2
        assert any(item.item_id == fresh["id"] for item in modal.visible_items)
        assert "HTTP 503" in str(modal.query_one("#model-status", Static).render())
        modal.query_one(Input).value = "moonshot/kimi-future"
        await pilot.pause()
        await pilot.press("enter")
        assert selected == ["kimi/kimi-future"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", ["custom/org/future-code-model", "custom/model"])
async def test_exact_custom_model_selection_and_whitespace_rejection(model_id):
    config = Config(providers={"custom": ProviderConfig(base_url="https://example.test/v1", models=["model", "model-snapshot"])})
    app = App()
    selected = []
    async with app.run_test(size=(100, 45)) as pilot:
        modal = ModelSelectModal("custom/model-snapshot", config=config)
        await app.push_screen(modal, selected.append)
        await app.workers.wait_for_complete()
        modal.query_one(Input).value = "custom/invalid model"
        await pilot.pause()
        assert not modal.visible_items
        modal.query_one(Input).value = model_id
        await pilot.pause()
        assert modal.visible_items[modal.selected_index].item_id == model_id
        await pilot.press("enter")
        assert selected == [model_id]


class ConnectionApp(CodesmApp):
    async def on_mount(self, event):
        event.prevent_default()
        self._update_model_display()


def connection_app(tmp_path, config):
    app = ConnectionApp(tmp_path, config.model)
    app.agent = SimpleNamespace(config=config, model=config.model, cleanup=AsyncMock())
    return app


@pytest.mark.asyncio
async def test_connect_picker_uses_registry_and_updates_base_model(tmp_path, isolated_providers):
    config = Config(providers={"custom": ProviderConfig(base_url="https://example.test/v1", models=["team-code"])})
    app = connection_app(tmp_path, config)
    app._mode = "rush"
    async with app.run_test(size=(100, 45)) as pilot:
        app.action_connect_provider()
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, ProviderConnectModal)
        assert {item.item_id for item in picker.visible_items} == set(provider_specs(config))
        picker.query_one(Input).value = "kimi"
        await pilot.pause()
        await pilot.press("enter")
        assert isinstance(app.screen, APIKeyInputModal)
        app.screen.query_one(Input).value = "test-only-key"
        await pilot.press("enter")
        assert isolated_providers["kimi"]["api_key"] == "test-only-key"
        assert not app.in_chat and app._chat_worker is None
        expected = "kimi/" + provider_specs(config)["kimi"].models[0]
        assert app.model == app._base_model == app.agent.model == expected
        assert app._mode == "smart"
        assert isolated_providers["_preferences"] == {"model": expected, "mode": "smart"}
        app._set_mode("rush")
        assert app.model == "kimi/" + provider_specs(config)["kimi"].rush_model
        app._set_mode("smart")
        assert app.model == expected


@pytest.mark.asyncio
async def test_connect_custom_and_local_models(tmp_path, isolated_providers):
    config = Config(providers={"custom": ProviderConfig(base_url="https://example.test/v1")})
    app = connection_app(tmp_path, config)
    async with app.run_test(size=(100, 45)) as pilot:
        app._on_provider_selected("custom")
        await pilot.pause()
        assert isinstance(app.screen, APIKeyInputModal)
        app.screen.query_one(Input).value = "custom-test-key"
        await pilot.press("enter")
        assert isinstance(app.screen, ModelSelectModal)
        app.screen.query_one(Input).value = "custom/new-model"
        await pilot.pause()
        await pilot.press("enter")
        assert app.model == app._base_model == "custom/new-model"
        app._set_mode("rush")
        assert app.model == "custom/new-model"
        app._on_provider_selected("ollama")
        await pilot.pause()
        assert isinstance(app.screen, ModelSelectModal)
        assert all(item.item_id.startswith("ollama/") for item in app.screen.visible_items)
        await pilot.press("enter")
        assert app.model.startswith("ollama/")
        assert "ollama" not in isolated_providers


@pytest.mark.asyncio
async def test_models_connect_shortcut_uses_real_callback(tmp_path):
    app = connection_app(tmp_path, Config())
    async with app.run_test(size=(100, 45)) as pilot:
        app._execute_command_sync("/models")
        await pilot.pause()
        assert isinstance(app.screen, ModelSelectModal)
        await pilot.press("ctrl+a")
        assert isinstance(app.screen, ProviderConnectModal)
