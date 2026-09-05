"""Stable model navigation while the catalog refreshes in the background."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from textual import events
from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import Input

from codesm.auth.credentials import CredentialStore
from codesm.config.config import Config
from codesm.tui.app import CodesmApp
from codesm.tui.modals import ModelSelectModal


@pytest.fixture
def catalog(monkeypatch):
    entries = [
        {"id": f"openai/model-{i:03}", "name": f"Model {i:03}", "provider": "OpenAI"}
        for i in range(80)
    ]
    state = SimpleNamespace(entries=entries, remote=deepcopy(entries), release=asyncio.Event(), calls=[])
    monkeypatch.setattr("codesm.tui.modals.model_catalog", lambda config: deepcopy(state.entries))

    async def discover(config, provider=None):
        state.calls.append(provider)
        await state.release.wait()
        return deepcopy(state.remote), {}

    monkeypatch.setattr("codesm.tui.modals.discover_models", discover)
    return state


async def arrow_burst(app, modal, pilot, key, count):
    """Send held-key events without Pilot.press waiting for every animation."""
    action_name = "action_move_down" if key == "down" else "action_move_up"
    action = getattr(modal, action_name)
    complete = asyncio.Event()
    remaining = count

    def handle():
        nonlocal remaining
        action()
        remaining -= 1
        if not remaining:
            complete.set()

    setattr(modal, action_name, handle)
    try:
        for _ in range(count):
            app._driver.send_message(events.Key(key, None))
        await asyncio.wait_for(complete.wait(), 2)
        await pilot.pause(0)
    finally:
        setattr(modal, action_name, action)


def assert_selection_visible(modal):
    listing = modal.query_one("#model-list", VerticalScroll)
    selected = modal.visible_items[modal.selected_index]
    assert selected.region.y >= listing.content_region.y
    assert selected.region.bottom <= listing.content_region.bottom
    assert not listing.app.animator.is_being_animated(listing, "scroll_y")


@pytest.mark.asyncio
async def test_arrow_bursts_are_immediate_and_boundaries_do_not_wrap(catalog):
    app = App(ansi_color=True)
    modal = ModelSelectModal("openai/model-000", Config())
    async with app.run_test(size=(80, 40)) as pilot:
        await app.push_screen(modal)
        await pilot.pause()
        panel_region = modal.query_one("#modal-container").region
        search_region = modal.query_one(Input).region
        assert panel_region.height <= 32

        await arrow_burst(app, modal, pilot, "down", 50)
        assert modal.selected_index == 50
        assert_selection_visible(modal)
        await arrow_burst(app, modal, pilot, "up", 12)
        assert modal.selected_index == 38
        assert_selection_visible(modal)

        # Clicking the scrollable list must not hand arrows to its own scroller.
        modal.query_one("#model-list", VerticalScroll).focus()
        await pilot.press("down")
        assert modal.selected_index == 39
        assert_selection_visible(modal)
        await arrow_burst(app, modal, pilot, "down", 50)
        assert modal.selected_index == 79
        assert_selection_visible(modal)
        await arrow_burst(app, modal, pilot, "up", 90)
        assert modal.selected_index == 0
        assert_selection_visible(modal)
        assert modal.query_one("#modal-container").region == panel_region
        assert modal.query_one(Input).region == search_region


@pytest.mark.asyncio
@pytest.mark.parametrize("reorder", [False, True])
async def test_refresh_preserves_highlight_and_scrolled_viewport(catalog, reorder):
    app = App(ansi_color=True)
    modal = ModelSelectModal("openai/model-000", Config())
    async with app.run_test(size=(80, 40)) as pilot:
        await app.push_screen(modal)
        await pilot.pause()
        await arrow_burst(app, modal, pilot, "down", 60)
        await pilot.wait_for_scheduled_animations()
        listing = modal.query_one("#model-list", VerticalScroll)
        before_scroll = listing.scroll_y
        before_region = modal.query_one("#modal-container").region
        highlighted = modal.visible_items[modal.selected_index].item_id
        screen_row = modal.visible_items[modal.selected_index].region.y
        catalog.remote.append({"id": "openai/new-release", "name": "New release", "provider": "OpenAI"})
        if reorder:
            catalog.remote.reverse()
        catalog.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert modal.visible_items[modal.selected_index].item_id == highlighted
        if not reorder:
            assert listing.scroll_y == before_scroll
        assert modal.visible_items[modal.selected_index].region.y == screen_row
        assert_selection_visible(modal)
        assert modal.query_one("#modal-container").region == before_region
        assert any(item.item_id == "openai/new-release" for item in modal.visible_items)


@pytest.mark.asyncio
async def test_filtering_reuses_rows_and_keeps_latest_query_during_refresh(catalog):
    app = App(ansi_color=True)
    selected = []
    modal = ModelSelectModal("openai/model-000", Config())
    async with app.run_test(size=(80, 40)) as pilot:
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        panel_region = modal.query_one("#modal-container").region
        rows = {item.item_id: item for item in modal.visible_items}
        assert len(catalog.calls) == 1
        await pilot.press("ctrl+r", "ctrl+r", "ctrl+r")
        assert len(catalog.calls) == 1

        search = modal.query_one(Input)
        search.value = "model-07"
        await pilot.pause()
        assert len(modal.visible_items) == 10
        assert all(rows[item.item_id] is item for item in modal.visible_items)
        assert modal.query_one("#modal-container").region == panel_region

        catalog.remote.append({"id": "openai/new-release", "name": "New release", "provider": "OpenAI"})
        catalog.release.set()
        search.value = "model-079"
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert search.value == "model-079"
        assert [item.item_id for item in modal.visible_items] == ["openai/model-079"]
        assert modal.query_one("#modal-container").region == panel_region

        search.value = "no matching model"
        await pilot.pause()
        assert not modal.visible_items
        assert modal.query_one("#modal-container").region == panel_region
        search.value = "openai/private-future-model"
        await pilot.pause()
        assert modal.visible_items[modal.selected_index].item_id == "openai/private-future-model"
        assert modal.query_one("#modal-container").region == panel_region
        await pilot.press("enter")
        assert selected == ["openai/private-future-model"]


@pytest.mark.asyncio
async def test_navigation_and_query_during_catalog_mount_keep_latest_selection(catalog, monkeypatch):
    app = App(ansi_color=True)
    modal = ModelSelectModal("openai/model-000", Config())
    async with app.run_test(size=(80, 40)) as pilot:
        await app.push_screen(modal)
        await pilot.pause()
        await arrow_burst(app, modal, pilot, "down", 20)
        listing = modal.query_one("#model-list", VerticalScroll)
        mount = listing.mount
        mounted = asyncio.Event()
        finish_mount = asyncio.Event()

        async def delayed_mount(*widgets, **kwargs):
            await mount(*widgets, **kwargs)
            mounted.set()
            await finish_mount.wait()

        monkeypatch.setattr(listing, "mount", delayed_mount)
        catalog.remote.append({"id": "openai/new-release", "name": "New release", "provider": "OpenAI"})
        catalog.release.set()
        await asyncio.wait_for(mounted.wait(), 2)
        try:
            await arrow_burst(app, modal, pilot, "down", 3)
            assert modal.visible_items[modal.selected_index].item_id == "openai/model-023"
            modal.query_one(Input).value = "model-02"
            await pilot.pause()
            assert modal.visible_items[modal.selected_index].item_id == "openai/model-023"
        finally:
            finish_mount.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert modal.query_one(Input).value == "model-02"
        assert len(modal.visible_items) == 10
        assert modal.visible_items[modal.selected_index].item_id == "openai/model-023"
        assert_selection_visible(modal)


@pytest.mark.asyncio
async def test_arrow_before_filter_layout_refresh_keeps_selection_visible(catalog):
    for index, entry in enumerate(catalog.entries):
        entry["name"] = f"{'Other' if index < 20 else 'Keep'} {index}"
    app = App(ansi_color=True)
    modal = ModelSelectModal("openai/model-000", Config())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.push_screen(modal)
        await pilot.pause()
        # A filter event and an arrow can arrive in the same display frame.
        modal.search_query = "Keep"
        modal._filter_list()
        modal.action_move_down()
        await pilot.pause()
        assert modal.visible_items[modal.selected_index].item_id == "openai/model-021"
        assert_selection_visible(modal)


@pytest.mark.asyncio
async def test_escape_closes_model_picker_without_cancelling_app(catalog, monkeypatch, tmp_path):
    class MountedApp(CodesmApp):
        async def on_mount(self, event):
            event.prevent_default()
            self._update_model_display()

    monkeypatch.setattr(CredentialStore, "__init__", lambda self: None)
    app = MountedApp(tmp_path, "openai/model-000")
    cancellations = []
    monkeypatch.setattr(app, "action_cancel_chat", lambda: cancellations.append(True))
    async with app.run_test(size=(80, 40)) as pilot:
        conversation = app.screen
        await app.push_screen(ModelSelectModal(app.model, Config()))
        await pilot.pause()
        await pilot.press("escape")
        assert app.screen is conversation
        assert not cancellations
