"""Mouse selections copy the displayed transcript without triggering its links or tools."""

import pytest
from rich.cells import cell_len
from textual import events
from textual.app import App

from codesm.tui.chat import ChatMessage
from codesm.tui.tools import StreamingTextWidget, ToolTreeWidget


class SelectionApp(App):
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
            return (x, inset.y + y), (x + cell_len(text), inset.y + y)
    raise AssertionError(f"{text!r} is not visible")


async def drag(pilot, widget, start, end):
    await pilot.mouse_down(widget, offset=start)
    await pilot.hover(widget, offset=end)
    await pilot.mouse_up(widget, offset=end)
    # Pilot bypasses App.on_event, which emits a release click in a real terminal.
    await pilot._post_mouse_events([events.Click], widget, offset=end, button=1)


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
