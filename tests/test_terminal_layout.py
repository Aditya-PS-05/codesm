"""Terminal layout and session transitions without provider or credential access."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input
from textual.widgets._input import Selection as InputSelection

from codesm.agent.optimizer import UsageStats
from codesm.auth.credentials import CredentialStore
from codesm.config.config import Config
from codesm.provider.base import StreamChunk
from codesm.provider.catalog import model_catalog
from codesm.session.session import Session
from codesm.tui.app import CodesmApp
from codesm.tui.chat import ChatMessage
from codesm.tui.modals import ModelSelectModal
from codesm.tui.tools import StreamingTextWidget


REPLY = (
    "Codesm can inspect your project, explain the relevant files, and help you "
    "make and verify a change. The terminal should keep every part of this "
    "response readable when a paragraph wraps across several lines. "
) * 4


class LayoutAgent:
    def __init__(self, directory, model, session=None):
        self.directory = directory
        self.model = model
        self.config = Config(model=model)
        self.profile = None
        self.session = session or Session.create(directory)
        self.stats = UsageStats()
        self.budget = SimpleNamespace(get_session_stats=lambda: self.stats)
        self._chat_active = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []
        self.error = False

    async def chat(self, prompt):
        self.calls.append(prompt)
        self._chat_active = True
        self.session.messages.append({"role": "user", "content": prompt})
        try:
            yield StreamChunk(type="text", content=REPLY)
            self.entered.set()
            await self.release.wait()
            if self.error:
                raise RuntimeError("Fixture connection interrupted")
            yield StreamChunk(type="text", content="\n\nThe final line is visible.")
            self.session.messages.append({"role": "assistant", "content": REPLY})
            self.session.save()
            yield StreamChunk(type="run_status", content="completed")
        finally:
            self._chat_active = False

    def _restore_budget(self):
        pass

    def new_session(self):
        self.session = Session.create(self.directory)
        self.stats = UsageStats()

    async def cleanup(self):
        pass


@pytest.fixture
def offline_terminal(monkeypatch):
    stored = {}
    monkeypatch.setattr(CredentialStore, "__init__", lambda self: None)
    monkeypatch.setattr(CredentialStore, "_load", lambda self: deepcopy(stored))
    monkeypatch.setattr(CredentialStore, "_save", lambda self, data: stored.update(data))
    monkeypatch.setattr("codesm.agent.agent.Agent", LayoutAgent)
    monkeypatch.setattr(CodesmApp, "_init_lsp", lambda self: None)
    monkeypatch.setattr(CodesmApp, "_init_file_watcher", lambda self: None)

    async def discover(config, provider=None):
        models = model_catalog(config)
        return [m for m in models if not provider or m["id"].startswith(provider + "/")], {}

    monkeypatch.setattr("codesm.tui.modals.discover_models", discover)
    return stored


def rendered_lines(widget):
    return "\n".join(widget.render_line(y).text for y in range(widget.content_size.height))


@pytest.mark.asyncio
@pytest.mark.parametrize("no_color", [False, True])
async def test_native_theme_preserves_terminal_foreground_and_background(tmp_path, offline_terminal, monkeypatch, no_color):
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    app = CodesmApp(tmp_path, "openai/fixture")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        # Inspect the final rendered styles: converting both ANSI defaults to
        # black RGB makes the terminal blank even though its text still exists.
        update = app.screen._compositor.render_update(full=True)
        title = next(segment for segment in app.console.render(update) if "codesm ·" in segment.text)
        assert title.style.color.is_default
        assert title.style.bgcolor.is_default


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [80, 132])
async def test_full_width_layout_wraps_response_and_keeps_composer_fixed(tmp_path, offline_terminal, width):
    app = CodesmApp(tmp_path, "openai/fixture")
    async with app.run_test(size=(width, 32)) as pilot:
        transcript = app.query_one("#chat-container", VerticalScroll)
        composer = app.query_one("#chat-input-section")
        footer = app.query_one("#custom-footer")
        initial_composer = composer.region
        assert not app.query("#context-sidebar")
        assert len(app.query(Input)) == 1
        assert transcript.region.width == composer.region.width == footer.region.width == width
        assert composer.region.bottom == footer.region.y
        assert footer.region.bottom == 32
        assert app.focused is app._get_active_input()

        app._get_active_input().value = "What is codesm?"
        await pilot.press("enter")
        await asyncio.wait_for(app.agent.entered.wait(), 2)
        await pilot.pause()
        response = app.query_one(StreamingTextWidget)
        assert response.content_size.height > 3
        assert "paragraph" in rendered_lines(response)
        assert composer.region == initial_composer
        assert not app._get_active_input().disabled

        app.agent.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "The final line is visible." in rendered_lines(response)
        assert composer.region == initial_composer
        assert not app.query("#context-sidebar")
        assert "Run: completed" not in app.export_screenshot()
        assert app.query_one(ChatMessage).region.y <= transcript.region.y + 1

        # Growing the scrollback must never push the composer below the viewport.
        await app.query_one("#messages", Vertical).mount(
            *(ChatMessage("assistant", f"Earlier result {i}: " + REPLY) for i in range(12))
        )
        await pilot.pause()
        assert transcript.max_scroll_y > 0
        transcript.scroll_home(animate=False)
        await pilot.pause()
        assert transcript.scroll_y == 0
        assert composer.region == initial_composer
        transcript.scroll_end(animate=False)
        await pilot.pause()
        assert transcript.scroll_y > 0
        assert composer.region == initial_composer

        await pilot.resize_terminal(80, 24)
        assert composer.region.width == 80
        assert composer.region.bottom == footer.region.y
        assert footer.region.bottom == 24
        assert app._get_active_input().region.bottom <= footer.region.y


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_interrupt_and_error_restore_usable_composer(tmp_path, offline_terminal, fail):
    app = CodesmApp(tmp_path, "openai/fixture")
    async with app.run_test(size=(80, 24)) as pilot:
        app.agent.error = fail
        app._get_active_input().value = "Inspect the failure"
        await pilot.press("enter")
        await asyncio.wait_for(app.agent.entered.wait(), 2)
        if fail:
            app.agent.release.set()
            await app.workers.wait_for_complete()
        else:
            await pilot.press("escape")
        await pilot.pause()
        assert app._chat_worker is None
        assert not app.agent._chat_active
        assert not app._get_active_input().disabled
        assert app.focused is app._get_active_input()
        assert not app.query("#context-sidebar")
        if fail:
            assert any("Fixture connection interrupted" in widget.content for widget in app.query(ChatMessage))
        await pilot.press("n", "e", "x", "t")
        assert app._get_active_input().value == "next"


@pytest.mark.asyncio
async def test_copy_selection_preserves_running_task_and_unselected_ctrl_c_interrupts(
    tmp_path, offline_terminal, monkeypatch
):
    app = CodesmApp(tmp_path, "openai/fixture")
    copied = []
    monkeypatch.setattr("codesm.tui.clipboard.copy_to_system_clipboard", lambda text: copied.append(text) or True)
    async with app.run_test(size=(80, 24)) as pilot:
        app._get_active_input().value = "Inspect the code"
        await pilot.press("enter")
        await asyncio.wait_for(app.agent.entered.wait(), 2)
        await pilot.pause()
        response = app.query_one(StreamingTextWidget)
        inset = response.content_region.offset - response.region.offset
        await pilot.hover(response, offset=(inset.x, inset.y))
        assert "drag to select" in rendered_lines(app.query_one("#footer-hints"))
        await pilot.mouse_down(response, offset=(inset.x, inset.y))
        await pilot.hover(response, offset=(inset.x + 6, inset.y))
        await pilot.mouse_up(response, offset=(inset.x + 6, inset.y))
        assert app.screen.get_selected_text() == "Codesm"
        assert app._mouse_pointer == "text"
        await pilot.press("ctrl+c", "ctrl+shift+c")
        assert copied == ["Codesm", "Codesm"]
        assert app._chat_worker.is_running
        assert "ctrl+c copy" in rendered_lines(app.query_one("#footer-hints"))
        await pilot.press("escape")
        assert not app.screen.get_selected_text()
        assert app._chat_worker.is_running
        await pilot.press("ctrl+c")
        await app.workers.wait_for_complete()
        assert not app.agent._chat_active

        field = app._get_active_input()
        field.focus()
        field.value = "copy this draft"
        field.selection = InputSelection(5, 9)
        await pilot.press("ctrl+c")
        assert copied[-1] == "this"
        assert app.is_running


@pytest.mark.asyncio
async def test_running_task_allows_scrollback_and_queues_followup(tmp_path, offline_terminal):
    app = CodesmApp(tmp_path, "openai/fixture")
    async with app.run_test(size=(80, 24)) as pilot:
        app._switch_to_chat()
        await app.query_one("#messages", Vertical).mount(
            *(ChatMessage("assistant", f"Earlier result {i}") for i in range(30))
        )
        app._get_active_input().value = "First task"
        await pilot.press("enter")
        await asyncio.wait_for(app.agent.entered.wait(), 2)
        await pilot.pause()
        transcript = app.query_one("#chat-container", VerticalScroll)
        transcript.scroll_home(animate=False)
        await pilot.pause(0.3)
        assert transcript.scroll_y == 0

        app._get_active_input().value = "Follow up after the first task"
        await pilot.press("tab")
        assert app._get_active_input().value == ""
        assert app.agent.calls == ["First task"]
        assert transcript.scroll_y == 0
        app.agent.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.agent.calls == ["First task", "Follow up after the first task"]
        assert not app._get_active_input().disabled
        assert app.focused is app._get_active_input()


@pytest.mark.asyncio
async def test_resume_fork_and_new_session_keep_one_transcript_and_focus(tmp_path, offline_terminal):
    saved = Session.create(tmp_path)
    saved.messages = [
        {"role": "user", "content": "A saved question"},
        {"role": "assistant", "content": "A saved answer"},
    ]
    saved.save()
    app = CodesmApp(tmp_path, "openai/fixture", session_id=saved.id)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert [widget.content for widget in app.query(ChatMessage)] == ["A saved question", "A saved answer"]
        assert app.focused is app._get_active_input()
        assert not app.query("#context-sidebar")

        app._on_session_selected(saved.id)
        await pilot.pause()
        assert len(app.query(ChatMessage)) == 2
        app._fork_session()
        await pilot.pause()
        assert app.agent.session.parent_id == saved.id
        assert len(app.query(ChatMessage)) == 2

        await pilot.press("ctrl+n")
        assert not app.in_chat
        assert not app.query(ChatMessage)
        assert len(app.query(Input)) == 1
        assert app.focused is app._get_active_input()
        assert not app.query("#context-sidebar")


@pytest.mark.asyncio
async def test_model_picker_fits_small_terminal_and_returns_focus(tmp_path, offline_terminal):
    app = CodesmApp(tmp_path, "openai/fixture")
    async with app.run_test(size=(80, 24)) as pilot:
        app._execute_command_sync("/models")
        await app.workers.wait_for_complete()
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ModelSelectModal)
        panel = modal.query_one("#modal-container")
        assert panel.region.x >= 0 and panel.region.right <= 80
        assert panel.region.y >= 0 and panel.region.bottom <= 24
        assert panel.region.x == 2 and panel.region.bottom == 20
        assert modal.query_one("#modal-footer").region.bottom <= panel.region.bottom
        rendered = modal._compositor.render_update(full=True, screen_stack=app._background_screens)
        screen_text = "".join(segment.text for segment in app.console.render(rendered))
        assert "Ask codesm to do anything" in screen_text
        assert "ctrl+p commands" in screen_text
        modal.query_one(Input).value = "openai/layout-check"
        await pilot.pause()
        await pilot.press("enter")
        assert not isinstance(app.screen, ModelSelectModal)
        assert app.agent.model == app.model == "openai/layout-check"
        assert app.focused is app._get_active_input()
        assert "layout-check" in rendered_lines(app.query_one("#footer-context"))


@pytest.mark.asyncio
async def test_loading_another_projects_session_uses_its_workspace(tmp_path, offline_terminal):
    project = tmp_path / 'original'
    other = tmp_path / 'saved-project'
    project.mkdir()
    other.mkdir()
    saved = Session.create(other)
    saved.add_message('user', 'Continue work in the saved workspace')
    app = CodesmApp(project, 'openai/fixture')
    async with app.run_test(size=(80, 24)) as pilot:
        await app._switch_to_session(saved.id)
        await pilot.pause()
        assert app.directory == app.agent.directory == other
        assert app.agent.session.id == saved.id
        assert str(other) in str(app.query_one('#working-directory').render())
        assert app.focused is app._get_active_input()
