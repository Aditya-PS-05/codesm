"""Dialogs remain usable in compact terminals without changing decisions."""

import pytest
from textual.app import App
from textual.widgets import Input, Static

from codesm.permission import PermissionRequest, PermissionResponse
from codesm.tui.autocomplete import AutocompletePopup
from codesm.tui.command_palette import COMMANDS, CommandPaletteModal
from codesm.tui.modals import APIKeyInputModal, DiffPreviewModal, DiffPreviewResponse, PermissionModal, ThemeSelectModal
from codesm.tui.session_modal import SessionListItem, SessionRenameModal
from codesm.tui.themes import THEMES


def assert_visible(app, widget):
    assert app.screen.region.contains_region(widget.region)
    assert widget.region.width > 0 and widget.region.height > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 24), (40, 18)])
@pytest.mark.parametrize("modal_type", [
    CommandPaletteModal, AutocompletePopup, ThemeSelectModal, SessionRenameModal,
])
async def test_popup_preserves_composited_transcript_cells(size, modal_type, tmp_path):
    width, height = size
    transcript = [
        f"{row:02} Transcript message at left".ljust(width - 5, ".") + "RIGHT"
        for row in range(height)
    ]

    class TranscriptApp(App):
        def compose(self):
            yield Static("\n".join(transcript), markup=False)

    app = TranscriptApp()

    def rendered_lines():
        update = app.screen._compositor.render_update(
            full=True, screen_stack=app._background_screens,
        )
        return ["".join(strip.text for strip in line) for line in update.strips]

    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        before = rendered_lines()
        assert before == transcript
        args = {
            AutocompletePopup: ("/", tmp_path),
            SessionRenameModal: ("test", "Session title"),
        }.get(modal_type, ())
        modal = modal_type(*args)
        await app.push_screen(modal)
        await pilot.pause()
        panel = modal.children[0].region
        assert panel.x == 2 and panel.width == min(76, width - 4)
        assert panel.bottom == height - 4
        after = rendered_lines()
        assert len(after) == height and all(len(line) == width for line in after)
        for y, line in enumerate(after):
            for x, character in enumerate(line):
                if not panel.contains(x, y):
                    assert character == before[y][x], (x, y)
        await pilot.press("escape")
        await pilot.pause()
        assert rendered_lines() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 24), (40, 18)])
async def test_theme_picker_stays_above_prompt_while_navigating_and_filtering(size):
    app = App(ansi_color=True)
    for theme in THEMES.values():
        app.register_theme(theme)
    selected = []
    async with app.run_test(size=size) as pilot:
        modal = ThemeSelectModal()
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        panel = modal.query_one("#modal-container")
        region = panel.region
        assert region.x == 2 and region.width == min(76, size[0] - 4)
        assert region.bottom == size[1] - 4 and region.height <= 24
        assert_visible(app, panel)
        # Up wraps to the final theme, which must remain reachable in a short panel.
        await pilot.press("up")
        await pilot.pause()
        item = modal.visible_items[-1]
        assert modal.selected_index == len(modal.visible_items) - 1
        assert modal.query_one("#theme-list").content_region.contains_region(item.region)
        assert panel.region == region
        modal.query_one(Input).value = "monokai"
        await pilot.pause()
        assert panel.region == region
        await pilot.press("enter")
        assert selected == ["monokai"]


@pytest.mark.asyncio
async def test_command_name_matches_rank_before_descriptions_and_restore_order():
    app = App()
    selected = []
    async with app.run_test(size=(80, 24)) as pilot:
        modal = CommandPaletteModal()
        await app.push_screen(modal, selected.append)
        await pilot.press("s", "e", "s", "s")
        field = modal.query_one("#palette-input", Input)
        for query, expected in [
            ("/sess", ["/session", "/new", "/fork", "/branches"]),
            ("/SESS", ["/session", "/new", "/fork", "/branches"]),
            ("/mode", ["/mode", "/models", "/rush", "/smart", "/dryrun"]),
            ("/ion", ["/session", "/new", "/fork", "/branches", "/audit"]),
            ("/no-such-command", []),
            ("/", [entry["cmd"] for entry in COMMANDS]),
        ]:
            field.value = query
            await pilot.pause()
            assert [item.cmd for item in modal.visible_items] == expected
            rows = [item for item in modal.query_one("#commands-list").children if item.display]
            assert [item.cmd for item in rows] == expected
            assert [item.region.y for item in rows] == sorted(item.region.y for item in rows)
            if rows:
                assert rows[0]._selected

        field.value = "/sess"
        await pilot.pause()
        await pilot.press("down")
        assert modal.visible_items[modal.selected_index].cmd == "/new"
        await pilot.press("up", "enter")
        assert selected == ["/session"]


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 80])
async def test_command_picker_scrolls_selected_row_into_view(width):
    app = App()
    selected = []
    async with app.run_test(size=(width, 24)) as pilot:
        modal = CommandPaletteModal()
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        await pilot.press(*["down"] * (len(COMMANDS) - 1))
        await pilot.pause()
        item = modal.visible_items[modal.selected_index]
        assert item.cmd == "/help"
        assert modal.query_one("#commands-list").content_region.contains_region(item.region)
        for selector in ("#palette-container", "#palette-input", "#palette-hint"):
            assert_visible(app, modal.query_one(selector))
        modal.query_one("#commands-list").focus()
        await pilot.press("enter")
        assert selected == ["/help"]


@pytest.mark.asyncio
async def test_api_key_is_masked_and_submitted_in_narrow_dialog():
    app = App()
    selected = []
    async with app.run_test(size=(40, 24)) as pilot:
        modal = APIKeyInputModal("kimi", "Kimi")
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        field = modal.query_one(Input)
        field.value = "test-only-private-key"
        assert field.password
        for selector in ("#modal-container", "#api-key-input", "#footer-hint"):
            assert_visible(app, modal.query_one(selector))
        await pilot.press("enter")
        assert selected == [{"api_key": "test-only-private-key", "provider": "kimi"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["permission", "diff"])
async def test_decision_buttons_fit_narrow_terminal_and_escape_cancels(kind):
    app = App()
    selected = []
    if kind == "permission":
        request = PermissionRequest("1", "bash", "git status --short", "Run command?",
                                    "Inspect the working directory.", "test")
        modal = PermissionModal(request)
        expected = PermissionResponse.DENY
    else:
        modal = DiffPreviewModal("app[1].py", "items[old]\n", "items[new]\n" * 30)
        expected = DiffPreviewResponse.CANCEL
    async with app.run_test(size=(40, 24)) as pilot:
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        for selector in ("#button-row", "#hint-row"):
            assert_visible(app, modal.query_one(selector))
            panel = modal.children[0]
            assert panel.content_region.contains_region(modal.query_one(selector).region)
        for button in modal.query("Button"):
            assert_visible(app, button)
            assert button.region.width >= len(str(button.label))
        if kind == "diff":
            assert "items[new]" in str(modal.query_one("#diff-content", Static).render())
        await pilot.press("escape")
        assert selected == [expected]


@pytest.mark.asyncio
async def test_completion_paths_and_session_titles_render_literal_brackets(tmp_path):
    (tmp_path / "file[red].py").touch()
    app = App()
    selected = []
    async with app.run_test(size=(40, 24)) as pilot:
        modal = AutocompletePopup("@", tmp_path)
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        for selector in ("#popup-container", "#filter-input", "#completion-hint"):
            assert_visible(app, modal.query_one(selector))
        await pilot.press("enter")
        assert selected == ["file[red].py"]
    assert "session[red]" in SessionListItem("test", "session[red]").render().plain


@pytest.mark.asyncio
@pytest.mark.parametrize("original", ["terminal", "dark"])
async def test_native_terminal_theme_preview_and_cancel_restore_actual_theme(original):
    app = App()
    for theme in THEMES.values():
        app.register_theme(theme)
    app.theme = THEMES[original].name
    async with app.run_test(size=(40, 24)) as pilot:
        modal = ThemeSelectModal(original)
        await app.push_screen(modal)
        await pilot.pause()
        assert modal.visible_items[0].item_id == "terminal"
        await pilot.press("down")
        if original == "dark":
            await pilot.press("up")
            assert app.theme == "ansi-dark" and app.native_ansi_color
            assert app.current_theme.foreground == app.current_theme.background == "ansi_default"
            assert modal.visible_items[0].styles.text_style.reverse
        else:
            assert app.theme == "codesm-dark" and not app.native_ansi_color
        await pilot.press("escape")
        assert app.theme == THEMES[original].name
        assert app.native_ansi_color == (original == "terminal")
