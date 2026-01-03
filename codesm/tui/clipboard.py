"""Clipboard utilities - copy with 'c' key or Ctrl+Shift+C"""

import subprocess
from textual.widgets import Static
from textual.binding import Binding


def copy_to_system_clipboard(text: str) -> bool:
    """Copy text to system clipboard using available methods."""
    
    # Try wl-copy first (Wayland - most reliable on modern Linux)
    try:
        result = subprocess.run(
            ["wl-copy", "--"],
            input=text.encode('utf-8'),
            capture_output=True,
            timeout=2
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try xsel (X11)
    try:
        result = subprocess.run(
            ["xsel", "--clipboard", "--input"],
            input=text.encode('utf-8'),
            capture_output=True,
            timeout=2
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try xclip (X11) - use Popen since it doesn't exit immediately
    try:
        process = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        process.stdin.write(text.encode('utf-8'))
        process.stdin.close()
        return True
    except FileNotFoundError:
        pass

    # Try pbcopy (macOS)
    try:
        result = subprocess.run(
            ["pbcopy"],
            input=text.encode('utf-8'),
            capture_output=True,
            timeout=2
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return False


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

    def action_copy_content(self) -> None:
        """Copy message content"""
        self._do_copy()

    def _do_copy(self) -> None:
        """Perform the copy operation"""
        text = self._get_content_text()
        if not text:
            self.app.notify("Nothing to copy", severity="warning", timeout=1)
            return
        
        # Try system clipboard
        if copy_to_system_clipboard(text):
            self.app.notify("Copied!", timeout=1.5)
        else:
            # Try Textual's OSC 52 as fallback
            try:
                self.app.copy_to_clipboard(text)
                self.app.notify("Copied (OSC52)!", timeout=1.5)
            except Exception:
                self.app.notify("Copy failed - install xclip", severity="error", timeout=2)

    def _get_content_text(self) -> str:
        """Get the text content to copy"""
        if hasattr(self, 'content'):
            return str(self.content)
        elif hasattr(self, 'renderable'):
            return str(self.renderable)
        return ""


class SelectableStatic(SelectableMixin, Static):
    """A Static widget that supports copy via 'c' key"""
    pass
