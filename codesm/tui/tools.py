"""Tool display widgets - OpenCode inspired"""

from textual.widgets import Static
from textual.containers import Vertical
from rich.text import Text


TOOL_ICONS = {
    "read": "→",
    "write": "←",
    "edit": "←",
    "bash": "#",
    "grep": "✓",
    "glob": "✓",
    "web": "%",
    "websearch": "◈",
    "default": "⚙",
}

TOOL_COLORS = {
    "read": "#5dd9c1",
    "write": "#f5a97f",
    "edit": "#f5a97f",
    "bash": "#cad3f5",
    "grep": "#c6a0f6",
    "glob": "#c6a0f6",
    "web": "#8aadf4",
    "websearch": "#8aadf4",
    "default": "#939ab7",
}


class ToolCallWidget(Static):
    """Widget to display a tool call - OpenCode style"""

    DEFAULT_CSS = """
    ToolCallWidget {
        height: auto;
        padding: 0 2;
        margin: 0;
    }
    
    ToolCallWidget.pending {
        color: $text-muted;
    }
    
    ToolCallWidget.completed {
        color: $text;
    }
    """

    def __init__(
        self,
        tool_name: str,
        args: dict | None = None,
        pending: bool = True,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.args = args or {}
        self.pending = pending
        self.set_class(pending, "pending")
        self.set_class(not pending, "completed")

    def render(self) -> Text:
        icon = TOOL_ICONS.get(self.tool_name, TOOL_ICONS["default"])
        color = TOOL_COLORS.get(self.tool_name, TOOL_COLORS["default"])
        
        text = Text()
        
        if self.pending:
            text.append("~ ", style="dim")
        else:
            text.append(f"{icon} ", style=f"bold {color}")
        
        text.append(self._format_tool_call(), style="dim" if self.pending else "")
        
        return text

    def _format_tool_call(self) -> str:
        """Format tool call for display"""
        name = self.tool_name
        args = self.args
        
        if name == "read":
            path = args.get("path", args.get("file_path", ""))
            return f"Read {self._short_path(path)}"
        
        elif name == "write":
            path = args.get("path", args.get("file_path", ""))
            return f"Write {self._short_path(path)}"
        
        elif name == "edit":
            path = args.get("path", args.get("file_path", ""))
            return f"Edit {self._short_path(path)}"
        
        elif name == "bash":
            cmd = args.get("command", "")
            desc = args.get("description", "")
            if desc:
                return f"{desc}"
            return f"$ {cmd[:50]}{'...' if len(cmd) > 50 else ''}"
        
        elif name == "grep":
            pattern = args.get("pattern", "")
            path = args.get("path", "")
            result = f'Grep "{pattern}"'
            if path:
                result += f" in {self._short_path(path)}"
            return result
        
        elif name == "glob":
            pattern = args.get("pattern", args.get("file_pattern", ""))
            path = args.get("path", "")
            result = f'Glob "{pattern}"'
            if path:
                result += f" in {self._short_path(path)}"
            return result
        
        elif name == "web":
            url = args.get("url", "")
            return f"Fetch {url[:50]}{'...' if len(url) > 50 else ''}"
        
        elif name == "websearch":
            query = args.get("query", "")
            return f'Web Search "{query}"'
        
        else:
            return f"{name} {self._format_args(args)}"

    def _short_path(self, path: str) -> str:
        """Shorten path for display"""
        if not path:
            return ""
        parts = path.split("/")
        if len(parts) > 3:
            return f".../{'/'.join(parts[-2:])}"
        return path

    def _format_args(self, args: dict) -> str:
        """Format args for display"""
        if not args:
            return ""
        parts = []
        for k, v in list(args.items())[:2]:
            if isinstance(v, str) and len(v) > 20:
                v = v[:20] + "..."
            parts.append(f"{k}={v}")
        return " ".join(parts)

    def mark_completed(self, metadata: dict | None = None):
        """Mark tool as completed with optional metadata"""
        self.pending = False
        self.set_class(False, "pending")
        self.set_class(True, "completed")
        if metadata:
            self.args.update(metadata)
        self.refresh()


class ToolResultWidget(Static):
    """Widget to display tool result - compact preview"""

    DEFAULT_CSS = """
    ToolResultWidget {
        height: auto;
        padding: 0 2 0 4;
        margin: 0 0 1 0;
        color: $text-muted;
    }
    """

    def __init__(self, result: str, max_lines: int = 3, **kwargs):
        super().__init__(**kwargs)
        self.result = result
        self.max_lines = max_lines

    def render(self) -> Text:
        text = Text()
        
        lines = self.result.strip().split("\n")
        preview_lines = lines[:self.max_lines]
        
        for i, line in enumerate(preview_lines):
            if len(line) > 80:
                line = line[:80] + "..."
            if i > 0:
                text.append("\n")
            text.append(f"  {line}", style="dim")
        
        if len(lines) > self.max_lines:
            text.append(f"\n  ... ({len(lines) - self.max_lines} more lines)", style="dim italic")
        
        return text


class ThinkingWidget(Static):
    """Widget to show thinking/processing state"""

    DEFAULT_CSS = """
    ThinkingWidget {
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def __init__(self, message: str = "Thinking", **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self._frame = 0
        self._spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def render(self) -> Text:
        spinner = self._spinner[self._frame % len(self._spinner)]
        return Text(f"{spinner} {self.message} ▸", style="dim")

    def next_frame(self):
        """Advance spinner animation"""
        self._frame += 1
        self.refresh()
