"""Rendered transcript links open through Textual without launching a test browser."""

import pytest
from textual.app import App

from codesm.tui.chat import ChatMessage, ThemedMarkdown, styled_markdown
from codesm.tui.tools import StreamingTextWidget


class LinkApp(App):
    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self):
        yield self.message


def link_cells(widget):
    """Read the actual rendered cells, including their target and click position."""
    inset = widget.content_region.offset - widget.region.offset
    for y in range(widget.content_size.height):
        x = 0
        for segment in widget.render_line(y):
            if segment.style and segment.style.link:
                yield segment, (inset.x + x, inset.y + y)
            x += segment.cell_length


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["streaming", "complete", "saved"])
@pytest.mark.parametrize("url", [
    "https://example.com/docs?q=readme&mode=full",
    "file:///tmp/a%20project/README.md",
])
async def test_transcript_links_are_colored_clickable_and_copyable(monkeypatch, phase, url):
    monkeypatch.delenv("NO_COLOR", raising=False)
    content = f"Source: [README.md]({url})."
    message = ChatMessage("assistant", content) if phase == "saved" else StreamingTextWidget()
    app = LinkApp(message)
    opened, copied = [], []
    monkeypatch.setattr(app, "open_url", lambda target, **kwargs: opened.append(target))
    monkeypatch.setattr("codesm.tui.clipboard.copy_to_system_clipboard", lambda text: copied.append(text) or True)

    async with app.run_test(size=(90, 15)) as pilot:
        if phase != "saved":
            message.set_content("Source: [README.md](")
            await pilot.pause()
            assert not list(link_cells(message))
            message.append_text(f"{url}).")
            if phase == "complete":
                message.mark_complete()
        await pilot.pause()

        body = message.children[0] if phase == "saved" else message
        visible = "".join(body.render_line(y).text for y in range(body.content_size.height))
        assert "Source: README.md." in visible
        assert "[README.md](" not in visible
        segment, offset = next(link_cells(body))
        assert segment.style.link == url
        assert segment.style.underline
        assert segment.style.color.get_truecolor() == (108, 113, 196)
        await pilot.click(body, offset=offset)
        assert opened == [url]

        message.focus()
        await pilot.press("c")
        assert copied == [content]


@pytest.mark.asyncio
async def test_user_markup_stays_literal_and_unsafe_links_stay_inert(monkeypatch):
    content = "[README.md](file:///tmp/README.md) [bad](javascript:alert(1))"
    user = ChatMessage("user", content)
    app = LinkApp(user)
    opened = []
    monkeypatch.setattr(app, "open_url", lambda target, **kwargs: opened.append(target))
    async with app.run_test(size=(90, 15)) as pilot:
        await pilot.pause()
        body = user.children[0]
        assert not list(link_cells(body))
        await pilot.click(body)
        assert not opened
        assert user._get_content_text() == content

    from rich.console import Console
    console = Console(width=90, height=15)
    rendered = styled_markdown(content)._render_markdown(content, console.options)
    targets = {span.style.link for span in rendered.spans if getattr(span.style, "link", None)}
    assert targets == {"file:///tmp/README.md"}


def test_link_color_is_per_render_and_respects_no_color(monkeypatch):
    from rich.console import Console

    monkeypatch.setenv("NO_COLOR", "1")
    content = "[README.md](file:///tmp/README.md)"
    console = Console(width=80, height=15)
    rendered = styled_markdown(content)._render_markdown(content, console.options)
    style = rendered.get_style_at_offset(console, 0)
    assert style.link == "file:///tmp/README.md"
    assert style.underline and style.color.is_default
    styled_markdown(content, link_color="#0000ff")
    assert styled_markdown(content).link_color == ThemedMarkdown.LINK_COLOR
