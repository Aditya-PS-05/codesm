"""Mouse selections copy the displayed transcript without triggering its links or tools."""

import pytest
from rich.cells import cell_len
from textual import events
from textual.app import App
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from codesm.tui.chat import ChatMessage
from codesm.tui.clipboard import TranscriptScreen
from codesm.tui.tools import StreamingTextWidget, ToolTreeWidget


class SelectionApp(App):
    def get_default_screen(self):
        return TranscriptScreen()

    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self):
        yield self.message


def text_span(widget, text):
    inset = widget.content_region.offset - widget.region.offset
    for y in range(widget.content_size.height):
        line = widget.render_line(y).text
        if text in line:
            x = inset.x + cell_len(line[:line.index(text)])
            return (x, inset.y + y), (x + cell_len(text) - 1, inset.y + y)
    raise AssertionError(f"{text!r} is not visible")


async def drag(pilot, widget, start, end):
    await pilot.mouse_down(widget, offset=start)
    await pilot.hover(widget, offset=end)
    await pilot.mouse_up(widget, offset=end)
    # Pilot bypasses App.on_event, which emits a release click in a real terminal.
    await pilot._post_mouse_events([events.Click], widget, offset=end, button=1)


async def drag_to(pilot, widget, offset):
    # Pilot.hover supplies delta_y=0, which deliberately does not start scrolling.
    point = widget.region.offset + offset
    delta = point - pilot.app.mouse_position
    await pilot.app.on_event(events.MouseMove(
        pilot.app.screen, point.x, point.y, delta.x, delta.y,
        button=1, shift=False, meta=False, ctrl=False,
        screen_x=point.x, screen_y=point.y,
    ))


def highlighted_text(widget):
    background = widget.screen.get_component_rich_style("screen--selection").bgcolor
    return "".join(
        segment.text
        for y in range(widget.content_size.height)
        for segment in widget.render_line(y)
        if segment.style and segment.style.bgcolor == background
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["streaming", "complete", "saved"])
async def test_drag_selects_and_copies_only_displayed_markdown(monkeypatch, phase):
    content = "Before **selected words** after."
    message = ChatMessage("assistant", content) if phase == "saved" else StreamingTextWidget()
    app = SelectionApp(message)
    copied = []
    monkeypatch.setattr("codesm.tui.clipboard.copy_to_system_clipboard", lambda text: copied.append(text) or True)

    async with app.run_test(size=(90, 20)) as pilot:
        if phase != "saved":
            message.set_content("Before **selected")
            message.append_text(" words** after.")
            if phase == "complete":
                message.mark_complete()
        await pilot.pause()
        body = message.children[0] if phase == "saved" else message
        await drag(pilot, body, *text_span(body, "lected wo"))
        assert app.screen.get_selected_text() == "lected wo"
        assert highlighted_text(body) == "lected wo"

        if phase == "streaming":
            message.append_text(" More text.")
            content += " More text."
            assert app.screen.get_selected_text() == "lected wo"

        await pilot.press("c", "ctrl+shift+c")
        assert copied == ["lected wo", "lected wo"]
        await pilot.press("escape", "c")
        assert copied[-1] == content


@pytest.mark.asyncio
async def test_unicode_code_selection_tracks_reflow_after_resize(monkeypatch):
    code = 'print("界🙂 cafe\u0301")'
    message = ChatMessage("assistant", "Paragraph words " * 12 + f"\n\n```python\n{code}\n```")
    app = SelectionApp(message)
    copied = []
    monkeypatch.setattr("codesm.tui.clipboard.copy_to_system_clipboard", lambda text: copied.append(text) or True)

    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        body = message.children[0]
        wide_height = body.size.height
        await drag(pilot, body, *text_span(body, code))
        assert app.screen.get_selected_text() == code
        await pilot.press("c", "escape")

        await pilot.resize_terminal(36, 30)
        await pilot.pause()
        assert body.size.height > wide_height
        await drag(pilot, body, *text_span(body, code))
        assert app.screen.get_selected_text() == code
        assert highlighted_text(body) == code
        await pilot.press("ctrl+shift+c")
        assert copied == [code, code]


@pytest.mark.asyncio
async def test_dragging_a_link_selects_without_opening_it(monkeypatch):
    url = "https://example.com/README.md"
    message = ChatMessage("assistant", f"Source: [README.md]({url}).")
    app = SelectionApp(message)
    opened = []
    monkeypatch.setattr(app, "open_url", lambda target, **kwargs: opened.append(target))

    async with app.run_test(size=(90, 20)) as pilot:
        await pilot.pause()
        body = message.children[0]
        start, end = text_span(body, "README")
        await drag(pilot, body, start, end)
        assert app.screen.get_selected_text() == "README"
        assert not opened

        await pilot.click(body, offset=start)
        assert opened == [url]
        assert not app.screen.get_selected_text()


@pytest.mark.asyncio
async def test_dragging_expanded_tool_output_keeps_it_open(monkeypatch):
    message = ToolTreeWidget("system", collapsed=False)
    index = message.add_tool("bash", {"command": "pytest -q"})
    message.mark_tool_complete(index, "done", "prefix alpha beta suffix")
    app = SelectionApp(message)
    copied = []
    monkeypatch.setattr("codesm.tui.clipboard.copy_to_system_clipboard", lambda text: copied.append(text) or True)

    async with app.run_test(size=(90, 20)) as pilot:
        await pilot.pause()
        start, end = text_span(message, "alpha beta")
        await drag(pilot, message, start, end)
        assert app.screen.get_selected_text() == "alpha beta"
        assert highlighted_text(message) == "alpha beta"
        assert not message._collapsed
        await pilot.press("c")
        assert copied == ["alpha beta"]

        await pilot.click(message, offset=start)
        assert message._collapsed


@pytest.mark.asyncio
async def test_selection_across_tool_and_blank_space_has_valid_text_offsets():
    tool = ToolTreeWidget("system", collapsed=False)
    index = tool.add_tool("bash", {"command": "printf evidence"})
    tool.mark_tool_complete(index, "done", "tool evidence")
    after = ChatMessage("assistant", "Later answer to select.")

    class BlankSpaceApp(App):
        CSS = "#padding { height: 8; } #gap { height: 3; }"

        def compose(self):
            yield Static("", id="padding")
            yield tool
            yield Static("", id="gap")
            yield after

    app = BlankSpaceApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        body = after.children[0]
        await pilot.mouse_down(body, offset=text_span(body, "Later answer to select.")[1])
        await pilot.hover(tool, offset=text_span(tool, "tool evidence")[0])
        await pilot.hover("#gap", offset=(2, 1))
        await pilot.mouse_up("#gap", offset=(2, 1))
        # Leaving the tool must not turn its screen y-coordinate into a text line.
        assert "Later answer" in app.screen.get_selected_text()


@pytest.mark.asyncio
async def test_drag_autoscroll_keeps_offscreen_text_and_stops_on_release():
    messages = [ChatMessage("assistant", f"Evidence line {index:03d}.") for index in range(60)]

    class ScrollSelectionApp(App):
        SELECT_AUTO_SCROLL_SPEED = 80

        def compose(self):
            with VerticalScroll(id="transcript"):
                yield from messages

    app = ScrollSelectionApp()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript", VerticalScroll)
        first = messages[0].children[0]
        await pilot.mouse_down(first, offset=text_span(first, "Evidence")[0])
        await drag_to(pilot, transcript, (12, 19))
        await pilot.pause(0.5)
        assert transcript.scroll_y > 5
        selected = app.screen.get_selected_text()
        assert "Evidence line 000." in selected
        assert "Evidence line 006." in selected
        # A stationary pointer must keep scrolling and extending the selection.
        previous = transcript.scroll_y
        previous_text = selected
        await pilot.pause(0.2)
        assert transcript.scroll_y > previous
        assert len(app.screen.get_selected_text()) > len(previous_text)
        await pilot.mouse_up(transcript, offset=(12, 19))
        await pilot.pause()
        stopped = transcript.scroll_y
        selected = app.screen.get_selected_text()
        await pilot.pause(0.2)
        assert transcript.scroll_y == stopped
        assert app.screen.get_selected_text() == selected

        # Dragging back up selects earlier messages, and leaving the edge stops it.
        last = next(message.children[0] for message in reversed(messages)
                    if 2 < message.children[0].region.y < 18)
        await pilot.mouse_down(last, offset=text_span(last, "Evidence")[1])
        await drag_to(pilot, transcript, (12, 0))
        await pilot.pause(0.2)
        assert transcript.scroll_y < stopped
        assert app.screen.get_selected_text()
        await drag_to(pilot, transcript, (12, 10))
        await pilot.pause()
        stopped = transcript.scroll_y
        await pilot.pause(0.2)
        assert transcript.scroll_y == stopped
        await pilot.mouse_up(transcript, offset=(12, 10))


@pytest.mark.asyncio
async def test_selection_survives_tool_output_collapsing():
    tool = ToolTreeWidget("system", collapsed=False)
    index = tool.add_tool("bash", {"command": "printf evidence"})
    tool.mark_tool_complete(index, "done", "tool evidence")
    after = ChatMessage("assistant", "Later evidence remains selectable.")
    app = SelectionApp(Vertical(tool, after))

    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        body = after.children[0]
        end = text_span(body, "Later evidence remains selectable.")[1]
        await pilot.mouse_down(tool, offset=text_span(tool, "tool evidence")[0])
        await pilot.hover(body, offset=end)
        await pilot.mouse_up(body, offset=end)
        tool.toggle_collapse()
        await pilot.pause()
        # The old selection is now beyond the end of the one-line tool summary.
        selected = app.screen.get_selected_text()
        assert "Later evidence remains" in selected
        assert "tool evidence" not in selected
