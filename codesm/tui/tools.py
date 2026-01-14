"""Tool display widgets - OpenCode inspired"""

from textual.widgets import Static
from textual.containers import Vertical
from rich.text import Text


TOOL_ICONS = {
    "read": "✓",
    "write": "✓",
    "edit": "✓",
    "multiedit": "✓",
    "bash": "✓",
    "grep": "✓",
    "glob": "✓",
    "web": "✓",
    "webfetch": "✓",
    "websearch": "✓",
    "codesearch": "✓",
    "todo": "✓",
    "default": "✓",
}

TOOL_COLORS = {
    "read": "#5dd9c1",
    "write": "#f5a97f",
    "edit": "#f5a97f",
    "multiedit": "#eed49f",
    "bash": "#cad3f5",
    "grep": "#c6a0f6",
    "glob": "#c6a0f6",
    "web": "#8aadf4",
    "webfetch": "#8aadf4",
    "websearch": "#8aadf4",
    "codesearch": "#ee99a0",
    "todo": "#eed49f",
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
        self._result_summary = ""
        self.set_class(pending, "pending")
        self.set_class(not pending, "completed")

    def render(self) -> Text:
        icon = TOOL_ICONS.get(self.tool_name, TOOL_ICONS["default"])
        color = TOOL_COLORS.get(self.tool_name, TOOL_COLORS["default"])
        
        text = Text()
        
        if self.pending:
            text.append("~ ", style="dim")
            text.append(self._format_tool_call(), style="dim")
        else:
            text.append(f"{icon} ", style=f"bold green")
            text.append(self._format_tool_call(), style="")
            # Show result summary if available
            if self._result_summary:
                text.append(f" {self._result_summary}", style="dim")
        
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

        elif name == "multiedit":
            path = args.get("path", "")
            edits = args.get("edits", [])
            num_edits = len(edits) if isinstance(edits, list) else 0
            return f"MultiEdit {self._short_path(path)} ({num_edits} edit{'s' if num_edits != 1 else ''})"

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
        
        elif name == "web" or name == "webfetch":
            url = args.get("url", "")
            return f"WebFetch {url}"
        
        elif name == "websearch":
            query = args.get("query", "")
            num_results = args.get("num_results", "")
            result = f'Web Search "{query}"'
            if num_results:
                result += f" ({num_results} results)"
            return result
        
        elif name == "codesearch":
            query = args.get("query", "")
            path = args.get("path", "")
            result = f'Code Search "{query[:40]}{"..." if len(query) > 40 else ""}"'
            if path:
                result += f" in {self._short_path(path)}"
            return result
        
        elif name == "todo":
            action = args.get("action", "")
            content = args.get("content", "")
            if action == "add":
                return f"Todo + {content[:50]}{'...' if len(content) > 50 else ''}"
            elif action == "list":
                return "Todo listing"
            elif action == "start":
                return "Todo ▸"  # Name will be added from result
            elif action == "done":
                return "Todo ✓"  # Name will be added from result
            elif action == "cancel":
                return "Todo ✗"
            elif action == "delete":
                return "Todo -"
            elif action == "update":
                return "Todo ~"
            elif action == "clear_done":
                return "Todo clearing completed"
            return f"Todo {action}"
        
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

    def mark_completed(self, metadata: dict | None = None, result_summary: str = ""):
        """Mark tool as completed with optional metadata and result summary"""
        self.pending = False
        self._result_summary = result_summary
        self.set_class(False, "pending")
        self.set_class(True, "completed")
        if metadata:
            self.args.update(metadata)
        self.refresh()


class TodoItemWidget(Static):
    """Widget to display a single todo item - OpenCode style"""

    DEFAULT_CSS = """
    TodoItemWidget {
        height: auto;
        padding: 0 2 0 4;
        margin: 0;
    }
    """

    def __init__(self, todo_id: str, content: str, status: str = "pending", **kwargs):
        super().__init__(**kwargs)
        self.todo_id = todo_id
        self.content = content
        self.status = status

    def render(self) -> Text:
        text = Text()
        
        # Status indicator like opencode: [✓] completed, [•] in_progress, [ ] pending
        if self.status == "done":
            text.append("[✓] ", style="bold green")
        elif self.status == "in_progress":
            text.append("[•] ", style="bold yellow")
        elif self.status == "cancelled":
            text.append("[-] ", style="dim")
        else:  # pending
            text.append("[ ] ", style="dim")
        
        # Content with appropriate style
        content_style = "dim strike" if self.status in ("done", "cancelled") else ""
        if self.status == "in_progress":
            content_style = "yellow"
        text.append(self.content, style=content_style)
        
        return text


class TodoListWidget(Static):
    """Widget to display a list of todos - OpenCode/Amp style"""

    DEFAULT_CSS = """
    TodoListWidget {
        height: auto;
        padding: 0 2;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, todos: list[dict], **kwargs):
        super().__init__(**kwargs)
        self.todos = todos

    def render(self) -> Text:
        text = Text()
        
        if not self.todos:
            text.append("  No todos.", style="dim italic")
            return text
        
        for i, todo in enumerate(self.todos):
            if i > 0:
                text.append("\n")
            
            status = todo.get("status", "pending")
            content = todo.get("content", "")
            
            # Status indicator
            if status == "done":
                text.append("  [✓] ", style="bold green")
            elif status == "in_progress":
                text.append("  [•] ", style="bold yellow")
            elif status == "cancelled":
                text.append("  [-] ", style="dim")
            else:
                text.append("  [ ] ", style="dim")
            
            # Content
            content_style = "dim strike" if status in ("done", "cancelled") else ""
            if status == "in_progress":
                content_style = "yellow"
            text.append(content, style=content_style)
        
        return text


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
    """Widget to show thinking/processing state with animated spinner"""

    DEFAULT_CSS = """
    ThinkingWidget {
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }
    """

    # Interesting facts/tips to show while thinking
    THINKING_MESSAGES = [
        "Analyzing your request",
        "Reading the codebase",
        "Thinking deeply",
        "Crafting a response",
        "Processing",
        "Searching for patterns",
        "Connecting the dots",
        "Building context",
        "Almost there",
        "Working on it",
    ]

    def __init__(self, message: str = "Thinking", **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self._frame = 0
        self._message_index = 0
        # Braille spinner frames
        self._spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        # Knight rider style dots
        self._dots = ["⬥···", "·⬥··", "··⬥·", "···⬥", "··⬥·", "·⬥··"]

    def render(self) -> Text:
        spinner = self._spinner[self._frame % len(self._spinner)]
        dots = self._dots[self._frame % len(self._dots)]
        
        text = Text()
        text.append(f"[{dots}] ", style="bold cyan")
        text.append(f"{spinner} ", style="dim")
        text.append(self.message, style="dim")
        text.append(" ▸", style="dim")
        
        return text

    def next_frame(self):
        """Advance spinner animation"""
        self._frame += 1
        self.refresh()

    def set_message(self, message: str):
        """Update the thinking message"""
        self.message = message
        self.refresh()

    def cycle_message(self):
        """Cycle to next interesting message"""
        self._message_index = (self._message_index + 1) % len(self.THINKING_MESSAGES)
        self.message = self.THINKING_MESSAGES[self._message_index]
        self.refresh()
