"""Transcript content must stay visible as streams and tool output grow."""

import pytest
from textual.app import App
from textual.containers import VerticalScroll
from textual.geometry import Region
from textual.widgets import Static

from codesm.tui.chat import ChatMessage, styled_markdown
from codesm.tui.tools import StreamingTextWidget, SubAgentTreeWidget, ToolTreeWidget


class TranscriptApp(App):
    def __init__(self, *widgets):
        super().__init__()
        self.widgets = widgets

    def compose(self):
        with VerticalScroll():
            yield from self.widgets


def rendered_text(widget):
    region = Region(0, 0, widget.size.width, widget.size.height)
    return "\n".join(strip.text for strip in widget.render_lines(region))


@pytest.mark.asyncio
async def test_stream_grows_completes_and_reflows_after_resize():
    widget = StreamingTextWidget()
    app = TranscriptApp(widget)
    async with app.run_test(size=(60, 40)) as pilot:
        widget.set_content("Hello")
        await pilot.pause()
        initial_height = widget.size.height
        widget.append_text(" wrapped words" * 45 + " LASTWORD")
        await pilot.pause()
        assert widget.size.height > initial_height
        assert "LASTWORD" in rendered_text(widget)

        widget.mark_complete()
        await pilot.pause()
        assert "LASTWORD" in rendered_text(widget)
        narrow_height = widget.size.height
        await pilot.resize_terminal(100, 40)
        await pilot.pause()
        assert widget.size.height < narrow_height
        assert "LASTWORD" in rendered_text(widget)
        assert widget._get_content_text().endswith("LASTWORD")

        widget.set_content("**A heading**\n\nOne paragraph.\n\nFINAL_PARAGRAPH")
        await pilot.pause()
        assert "FINAL_PARAGRAPH" in rendered_text(widget)
        assert "LASTWORD" not in rendered_text(widget)


@pytest.mark.asyncio
async def test_messages_keep_literal_user_text_and_full_assistant_content():
    user = ChatMessage("user", "Keep [bold]tags[/bold] and a <file> literally.")
    answer = ChatMessage("assistant", "A reply " * 40 + "FINAL_ANSWER")
    app = TranscriptApp(user, answer)
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        assert "› Keep [bold]tags[/bold]" in rendered_text(user.query_one(Static))
        assert "FINAL_ANSWER" in rendered_text(answer.query_one(Static))
        assert user._get_content_text() == user.content
        assert not list(app.query(".message-meta"))
        assert user.size.height < answer.size.height


@pytest.mark.asyncio
async def test_tool_details_expand_without_truncating_output_or_failure():
    widget = ToolTreeWidget("system")
    app = TranscriptApp(widget)
    output = "\n".join(f"result {index}" for index in range(8)) + "\nLAST_RESULT"
    async with app.run_test(size=(100, 40)) as pilot:
        index = widget.add_tool("bash", {"command": "pytest -q"})
        await pilot.pause()
        assert "Running pytest -q" in rendered_text(widget)
        widget.mark_tool_complete(index, "exit 1", output, is_error=True)
        await pilot.pause()
        collapsed_height = widget.size.height
        assert "Ran pytest -q" in rendered_text(widget)
        assert "failed" in rendered_text(widget)
        assert "LAST_RESULT" not in rendered_text(widget)
        assert "LAST_RESULT" in widget._get_content_text()

        widget.toggle_collapse()
        await pilot.pause()
        assert widget.size.height > collapsed_height
        assert "LAST_RESULT" in rendered_text(widget)
        widget.update_streaming_text(index, output + "\nADDED_RESULT")
        await pilot.pause()
        assert "ADDED_RESULT" in rendered_text(widget)
        widget.toggle_collapse()
        await pilot.pause()
        assert widget.size.height == collapsed_height


def test_specialist_failure_and_full_evidence_are_available_when_collapsed():
    widget = SubAgentTreeWidget("Investigate", subagent_type="oracle", model="provider/model")
    evidence = "Connection lost\n" + "Evidence " * 30 + "LAST_EVIDENCE"
    widget.complete(evidence, status="failed")
    assert "failed" in widget.render().plain
    assert "Connection lost" in widget.render().plain
    assert "✓" not in widget.render().plain
    assert "LAST_EVIDENCE" in widget._get_content_text()
    widget.toggle_collapse()
    assert "LAST_EVIDENCE" in widget.render().plain


def test_tool_group_shows_one_disclosure_for_substantial_output():
    widget = ToolTreeWidget("Explored")
    first = widget.add_tool("read", {"path": "first.py"})
    widget.mark_tool_complete(first, "1 line", "FIRST_CONTENT")
    assert "ctrl+t" not in widget.render().plain

    second = widget.add_tool("read", {"path": "second.py"})
    widget.mark_tool_complete(second, "1 line", "SECOND_CONTENT")
    collapsed = widget.render().plain
    assert collapsed.startswith("• Explored\n  Read first.py")
    assert collapsed.count("•") == 1
    assert "ctrl+t" not in collapsed
    assert "FIRST_CONTENT" not in collapsed

    widget.update_streaming_text(second, "one\ntwo\nthree\nLAST_CONTENT")
    assert widget.render().plain.count("ctrl+t") == 1
    assert "FIRST_CONTENT" in widget._get_content_text()
    assert "LAST_CONTENT" in widget._get_content_text()
    widget.toggle_collapse()
    assert "FIRST_CONTENT" in widget.render().plain
    assert "LAST_CONTENT" in widget.render().plain


def test_collapsed_tool_failure_keeps_reason_and_copy_keeps_all_evidence():
    widget = ToolTreeWidget("Edited")
    index = widget.add_tool("edit", {"path": "file.py"})
    widget.mark_tool_complete(index, "edit rejected", "Error: permission denied",
                              "-old\n+new", is_error=True)
    assert "failed" in widget.render().plain
    assert "Error: permission denied" in widget.render().plain
    assert widget.render().plain.count("ctrl+t") == 1
    assert "Error: permission denied" in widget._get_content_text()
    assert "-old\n+new" in widget._get_content_text()


def test_markdown_emphasis_inherits_the_terminal_foreground():
    from rich.console import Console

    console = Console(width=80, height=25)
    content = "Normal **strong** and *emphasis*."
    rendered = styled_markdown(content)._render_markdown(content, console.options)
    strong = rendered.get_style_at_offset(console, rendered.plain.index("strong"))
    emphasis = rendered.get_style_at_offset(console, rendered.plain.index("emphasis"))
    assert strong.bold and strong.color is None
    assert emphasis.italic and emphasis.color is None
