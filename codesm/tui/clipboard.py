"""Clipboard utilities - copy with 'c' key or Ctrl+Shift+C"""

import subprocess
from urllib.parse import urlsplit
from textual import events
from textual.screen import Screen
from textual.widgets import Static
from textual.binding import Binding


def copy_to_system_clipboard(text: str) -> bool:
    """Copy text to system clipboard using available methods."""

    commands = (
        ["wl-copy", "--type", "text/plain;charset=utf-8", "--"],
        ["xsel", "--clipboard", "--input"],
        ["xclip", "-selection", "clipboard"],
        ["pbcopy"],
    )
    for command in commands:
        try:
            # Clipboard owners fork. Captured output pipes can stay open in the
            # child, making a successful copy look like a timeout.
            result = subprocess.run(
                command,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


def copy_text(app, text: str) -> None:
    """Use the desktop clipboard, falling back to the terminal clipboard."""
    if not copy_to_system_clipboard(text):
        try:
            app.copy_to_clipboard(text)
        except Exception:
            app.notify("Could not copy to clipboard", severity="error", timeout=2)
            return
        # OSC 52 has no success acknowledgement from the terminal.
        app.notify("Sent copy request to terminal", timeout=2)
        return
    app.notify("Copied to clipboard", timeout=1.5)


class TranscriptScreen(Screen):
    """Keep copying valid text when a selected tool or message changes height."""

    def _start_auto_scroll(self, widget, direction, speed=1.0):
        # User selection must take precedence over following the live response.
        widget.release_anchor()
        super()._start_auto_scroll(widget, direction, speed)

    def get_selected_text(self) -> str | None:
        if not self.selections:
            return None
        parts: list[str] = []
        for widget, selection in self.selections.items():
            if not widget.is_attached:
                continue
            try:
                selected = widget.get_selection(selection)
            except IndexError:
                # Textual 8.2 can still index past a collapsed/reflowed widget.
                # Discard its stale range while preserving the other messages.
                continue
            if selected is not None:
                parts.extend(selected)
        return "".join(parts).rstrip("\n")


class SelectableMixin:
    """Mixin that adds copy support to Static widgets.
    
    - Click on message to focus it
    - Press 'c' or 'y' to copy the message content  
    - Ctrl+Shift+C to copy
    """

    BINDINGS = [
        Binding("c", "copy_content", "Copy", show=False),
        Binding("y", "copy_content", "Copy", show=False),
        Binding("ctrl+shift+c", "copy_content", "Copy", show=False),
    ]

    can_focus = True

    def on_click(self, event: events.Click) -> None:
        """Open rendered links through Textual's default-browser driver."""
        if self.screen.get_selected_text():
            event.stop()
            event.prevent_default()
            return
        url = event.style.link
        if event.button != 1 or not url:
            return
        try:
            scheme = urlsplit(url).scheme.lower()
        except ValueError:
            return
        if scheme in {"http", "https", "file", "mailto", "ftp"}:
            event.stop()
            event.prevent_default()
            self.app.open_url(url)

    def action_copy_content(self) -> None:
        """Copy message content"""
        self._do_copy()

    def _do_copy(self) -> None:
        """Perform the copy operation"""
        text = self.screen.get_selected_text() or self._get_content_text()
        if not text:
            self.app.notify("Nothing to copy", severity="warning", timeout=1)
            return
        
        copy_text(self.app, text)

    def _get_content_text(self) -> str:
        """Get the text content to copy"""
        if hasattr(self, 'content'):
            return str(self.content)
        elif hasattr(self, 'renderable'):
            return str(self.renderable)
        return ""


class SelectableStatic(SelectableMixin, Static):
    """A Static widget that supports copy via 'c' key"""

    BINDINGS = SelectableMixin.BINDINGS
