"""Tool display widgets - Terminal-inspired style with tree connectors

Implements Amp-style hierarchical tree display with:
- Collapsible sections using └─── and ├─── connectors
- Grouped tool calls under action headers
- Minimal, compact visual hierarchy
"""

from textual.widgets import Static, Collapsible
from textual.containers import Vertical
from textual.reactive import reactive
from textual.message import Message
from rich.text import Text
import re


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
    "look_at": "✓",
    "default": "✓",
}

# Define tool categories for grouping
TOOL_CATEGORIES = {
    "search": ["grep", "glob", "codesearch", "websearch"],
    "file": ["read", "write", "edit", "multiedit", "ls", "look_at"],
    "web": ["web", "webfetch", "websearch"],
    "system": ["bash"],
    "task": ["todo"],
}

# Colors
YELLOW = "#FFFF00"  # Keywords, patterns
CYAN = "#5dd9c1"    # File paths
GREEN = "#a6da95"   # Checkmarks
DIM = "#666666"     # Tree connectors, secondary text


class ToolCallWidget(Static):
    """Widget to display a tool call - with tree-style connectors and color highlighting"""

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
        tree_position: str = "none",  # "first", "middle", "last", "none"
        **kwargs
    ):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.args = args or {}
        self.pending = pending
        self.tree_position = tree_position
        self._result_summary = ""
        self.set_class(pending, "pending")
        self.set_class(not pending, "completed")

    def render(self) -> Text:
        text = Text()
        
        # Add tree connector based on position
        # "first" and "middle" use ├──, "last" uses └──, "only" uses └──
        if self.tree_position in ("first", "middle"):
            text.append("├── ", style=DIM)
        elif self.tree_position in ("last", "only"):
            text.append("└── ", style=DIM)
        # "none" gets no connector
        
        if self.pending:
            text.append("~ ", style="dim")
            text.append_text(self._format_tool_call_styled(dim=True))
        else:
            text.append("✓ ", style=f"bold {GREEN}")
            text.append_text(self._format_tool_call_styled(dim=False))
            if self._result_summary:
                text.append(f" {self._result_summary}", style="dim")
        
        return text

    def _format_tool_call_styled(self, dim: bool = False) -> Text:
        """Format tool call with rich styling - yellow keywords, cyan paths"""
        text = Text()
        name = self.tool_name
        args = self.args
        base_style = "dim" if dim else ""
        
        if name == "read":
            path = args.get("path", args.get("file_path", ""))
            text.append("Read ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN} underline" if not dim else "dim")
        
        elif name == "write":
            path = args.get("path", args.get("file_path", ""))
            text.append("Write ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN} underline" if not dim else "dim")
        
        elif name == "edit":
            path = args.get("path", args.get("file_path", ""))
            text.append("Edit ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN} underline" if not dim else "dim")

        elif name == "multiedit":
            path = args.get("path", "")
            edits = args.get("edits", [])
            num_edits = len(edits) if isinstance(edits, list) else 0
            text.append("MultiEdit ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN} underline" if not dim else "dim")
            text.append(f" ({num_edits} edit{'s' if num_edits != 1 else ''})", style="dim")

        elif name == "bash":
            cmd = args.get("command", "")
            desc = args.get("description", "")
            if desc:
                text.append(desc, style=base_style)
            else:
                text.append("$ ", style=base_style)
                cmd_display = cmd[:50] + '...' if len(cmd) > 50 else cmd
                text.append(cmd_display, style=f"{YELLOW}" if not dim else "dim")
        
        elif name == "grep":
            pattern = args.get("pattern", "")
            path = args.get("path", "")
            text.append("Grep ", style=base_style)
            text.append(f'"{pattern}"', style=f"bold {YELLOW}" if not dim else "dim")
            if path:
                text.append(" in ", style="dim")
                text.append_text(self._format_path_link(path, dim))
        
        elif name == "glob":
            pattern = args.get("pattern", args.get("file_pattern", ""))
            path = args.get("path", "")
            text.append("Glob ", style=base_style)
            text.append(f'"{pattern}"', style=f"bold {YELLOW}" if not dim else "dim")
            if path:
                text.append(" in ", style="dim")
                text.append_text(self._format_path_link(path, dim))
        
        elif name == "web" or name == "webfetch":
            url = args.get("url", "")
            text.append("WebFetch ", style=base_style)
            text.append(url, style=f"{CYAN} underline" if not dim else "dim")
        
        elif name == "websearch":
            query = args.get("query", "")
            num_results = args.get("num_results", "")
            text.append("Web Search ", style=base_style)
            text.append(f'"{query}"', style=f"bold {YELLOW}" if not dim else "dim")
            if num_results:
                text.append(f" ({num_results} results)", style="dim")
        
        elif name == "codesearch":
            query = args.get("query", "")
            path = args.get("path", "")
            query_display = query[:40] + "..." if len(query) > 40 else query
            text.append("Code Search ", style=base_style)
            text.append(f'"{query_display}"', style=f"bold {YELLOW}" if not dim else "dim")
            if path:
                text.append(" in ", style="dim")
                text.append_text(self._format_path_link(path, dim))
        
        elif name == "todo":
            action = args.get("action", "")
            content = args.get("content", "")
            if action == "add":
                text.append("Todo + ", style=base_style)
                content_display = content[:50] + '...' if len(content) > 50 else content
                text.append(content_display, style=f"{YELLOW}" if not dim else "dim")
            elif action == "list":
                text.append("Todo listing", style=base_style)
            elif action == "start":
                text.append("Todo ▸", style=base_style)
            elif action == "done":
                text.append("Todo ✓", style=base_style)
            elif action == "cancel":
                text.append("Todo ✗", style=base_style)
            elif action == "delete":
                text.append("Todo -", style=base_style)
            elif action == "update":
                text.append("Todo ~", style=base_style)
            elif action == "clear_done":
                text.append("Todo clearing completed", style=base_style)
            else:
                text.append(f"Todo {action}", style=base_style)
        
        elif name == "ls":
            path = args.get("path", ".")
            text.append("List ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN}" if not dim else "dim")
        
        elif name == "look_at":
            path = args.get("path", "")
            objective = args.get("objective", "")
            text.append("Look At ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN} underline" if not dim else "dim")
            if objective:
                obj_display = objective[:40] + "..." if len(objective) > 40 else objective
                text.append(f' "{obj_display}"', style=f"{YELLOW}" if not dim else "dim")
        
        elif name == "batch":
            calls = args.get("tool_calls", [])
            tools = [c.get("tool", "?") for c in calls[:3]]
            suffix = f"+{len(calls)-3}" if len(calls) > 3 else ""
            text.append(f"Batch [{', '.join(tools)}{suffix}]", style=base_style)
        
        else:
            text.append(f"{name} ", style=base_style)
            text.append(self._format_args(args), style="dim")
        
        return text

    def _short_path(self, path: str) -> str:
        """Shorten path for display"""
        if not path:
            return ""
        parts = path.split("/")
        if len(parts) > 3:
            return f".../{'/'.join(parts[-2:])}"
        return path

    def _format_path_link(self, path: str, dim: bool = False) -> Text:
        """Format path as a clickable file:// hyperlink with shortened display"""
        text = Text()
        if not path:
            return text
        
        display_path = self._short_path(path)
        file_url = f"file://{path}"
        base_style = "dim" if dim else f"{CYAN}"
        
        # Use Rich's hyperlink - shows only display text, ctrl+click opens file
        text.append(display_path, style=f"{base_style} link {file_url}")
        return text

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

    def set_tree_position(self, position: str):
        """Update tree position (first, middle, last, none)"""
        self.tree_position = position
        self.refresh()


class ToolGroupWidget(Static):
    """Widget to display a group of tool calls with a header and tree connectors"""

    DEFAULT_CSS = """
    ToolGroupWidget {
        height: auto;
        padding: 0 2;
        margin: 1 0;
    }
    """

    def __init__(self, category: str, **kwargs):
        super().__init__(**kwargs)
        self.category = category
        self._tool_calls: list[tuple[str, dict, bool, str]] = []  # (name, args, pending, result_summary)

    def render(self) -> Text:
        text = Text()
        
        # Header with :: prefix style
        text.append(":: ", style="bold white")
        text.append(self.category.title(), style="bold white")
        text.append("\n", style="")
        
        # Tree connector line
        if self._tool_calls:
            text.append("│\n", style=DIM)
        
        # Render each tool call with tree connectors
        for i, (name, args, pending, result_summary) in enumerate(self._tool_calls):
            is_last = (i == len(self._tool_calls) - 1)
            
            # Tree connector
            if is_last:
                text.append("└── ", style=DIM)
            else:
                text.append("├── ", style=DIM)
            
            # Status icon
            if pending:
                text.append("~ ", style="dim")
            else:
                text.append("✓ ", style=f"bold {GREEN}")
            
            # Tool name and args (simplified inline rendering)
            text.append_text(self._format_inline_tool(name, args, pending))
            
            if result_summary and not pending:
                text.append(f" {result_summary}", style="dim")
            
            if not is_last:
                text.append("\n", style="")
        
        return text

    def _format_inline_tool(self, name: str, args: dict, pending: bool) -> Text:
        """Format a single tool inline for the group display"""
        text = Text()
        dim = pending
        base_style = "dim" if dim else ""
        
        if name == "grep":
            pattern = args.get("pattern", "")
            path = args.get("path", "")
            text.append("Grep ", style=base_style)
            text.append(f'"{pattern}"', style=f"bold {YELLOW}" if not dim else "dim")
            if path:
                text.append(" in ", style="dim")
                text.append_text(self._format_path_link(path, dim))
        elif name == "glob":
            pattern = args.get("pattern", args.get("file_pattern", ""))
            text.append("Glob ", style=base_style)
            text.append(f'"{pattern}"', style=f"bold {YELLOW}" if not dim else "dim")
        elif name == "read":
            path = args.get("path", args.get("file_path", ""))
            text.append("Read ", style=base_style)
            text.append_text(self._format_path_link(path, dim))
        else:
            text.append(f"{name}", style=base_style)
        
        return text

    def _short_path(self, path: str) -> str:
        """Shorten path for display"""
        if not path:
            return ""
        parts = path.split("/")
        if len(parts) > 3:
            return f".../{'/'.join(parts[-2:])}"
        return path

    def _format_path_link(self, path: str, dim: bool = False) -> Text:
        """Format path as a clickable file:// hyperlink with shortened display"""
        text = Text()
        if not path:
            return text
        
        display_path = self._short_path(path)
        file_url = f"file://{path}"
        base_style = "dim" if dim else f"{CYAN}"
        
        # Use Rich's hyperlink - shows only display text, ctrl+click opens file
        text.append(display_path, style=f"{base_style} link {file_url}")
        return text

    def add_tool(self, name: str, args: dict, pending: bool = True):
        """Add a tool call to the group"""
        self._tool_calls.append((name, args, pending, ""))
        self.refresh()

    def mark_tool_completed(self, index: int, result_summary: str = ""):
        """Mark a specific tool as completed"""
        if 0 <= index < len(self._tool_calls):
            name, args, _, _ = self._tool_calls[index]
            self._tool_calls[index] = (name, args, False, result_summary)
            self.refresh()


# =============================================================================
# Amp-style Collapsible Tree Widgets
# =============================================================================

class TreeNode(Static):
    """A single node in the tree hierarchy with connector styling.
    
    Renders as:
        ├── ✓ Node content
    or  └── ✓ Node content (if last)
    """
    
    DEFAULT_CSS = """
    TreeNode {
        height: auto;
        padding: 0 0 0 2;
        margin: 0;
    }
    """
    
    def __init__(
        self,
        content: str | Text,
        *,
        icon: str = "✓",
        icon_style: str = "",
        is_last: bool = False,
        indent_level: int = 0,
        pending: bool = False,
        **kwargs
    ):
        super().__init__(**kwargs)
        self._content = content
        self._icon = icon
        self._icon_style = icon_style or (f"bold {GREEN}" if icon == "✓" else "dim")
        self._is_last = is_last
        self._indent_level = indent_level
        self._pending = pending
    
    def render(self) -> Text:
        text = Text()
        
        # Add indentation for nested levels
        for _ in range(self._indent_level):
            text.append("    ", style=DIM)
        
        # Tree connector
        connector = "└── " if self._is_last else "├── "
        text.append(connector, style=DIM)
        
        # Status icon
        if self._pending:
            text.append("~ ", style="dim")
        else:
            text.append(f"{self._icon} ", style=self._icon_style)
        
        # Content (can be Text or str)
        if isinstance(self._content, Text):
            text.append_text(self._content)
        else:
            text.append(self._content)
        
        return text
    
    def mark_complete(self, icon: str = "✓", icon_style: str = ""):
        """Mark this node as complete."""
        self._pending = False
        self._icon = icon
        self._icon_style = icon_style or f"bold {GREEN}"
        self.refresh()
    
    def set_last(self, is_last: bool):
        """Update whether this is the last node."""
        self._is_last = is_last
        self.refresh()


class CollapsibleTreeGroup(Static, can_focus=True):
    """A collapsible group of tree nodes - Amp style.
    
    Renders as:
        ✓ Group Title
        │
        ├── ✓ Item 1
        ├── ✓ Item 2
        └── ✓ Item 3
    
    When collapsed:
        ▸ Group Title (3 items)
    
    Click or press Enter to toggle expand/collapse.
    """
    
    DEFAULT_CSS = """
    CollapsibleTreeGroup {
        height: auto;
        padding: 0 2;
        margin: 0 0 1 0;
    }
    
    CollapsibleTreeGroup:hover {
        background: $surface;
    }
    
    CollapsibleTreeGroup:focus {
        background: $surface;
    }
    
    CollapsibleTreeGroup.collapsed .tree-children {
        display: none;
    }
    """
    
    BINDINGS = [
        ("enter", "toggle", "Toggle"),
        ("space", "toggle", "Toggle"),
    ]
    
    class Toggled(Message):
        """Posted when the group is toggled."""
        def __init__(self, group: "CollapsibleTreeGroup", expanded: bool) -> None:
            super().__init__()
            self.group = group
            self.expanded = expanded
    
    expanded = reactive(True)
    
    def __init__(
        self,
        title: str,
        *,
        icon: str = "✓",
        icon_style: str = "",
        show_count: bool = True,
        **kwargs
    ):
        super().__init__(**kwargs)
        self._title = title
        self._icon = icon
        self._icon_style = icon_style or f"bold {GREEN}"
        self._show_count = show_count
        self._items: list[tuple[str | Text, str, str, bool]] = []  # (content, icon, icon_style, pending)
        self._pending = True
    
    def render(self) -> Text:
        text = Text()
        
        # Header line with expand/collapse indicator
        if self.expanded:
            indicator = "▾ " if self._items else ""
        else:
            indicator = "▸ "
        
        if self._pending:
            text.append("~ ", style="dim")
        else:
            text.append(f"{self._icon} ", style=self._icon_style)
        
        text.append(f"{indicator}", style="dim")
        text.append(self._title, style="bold white")
        
        # Show item count when collapsed or always if configured
        if not self.expanded and self._items:
            text.append(f" ({len(self._items)} items)", style="dim")
        
        if self.expanded and self._items:
            # Vertical connector line
            text.append("\n│", style=DIM)
            
            # Render each item
            for i, (content, icon, style, pending) in enumerate(self._items):
                is_last = (i == len(self._items) - 1)
                connector = "\n└── " if is_last else "\n├── "
                text.append(connector, style=DIM)
                
                if pending:
                    text.append("~ ", style="dim")
                else:
                    text.append(f"{icon} ", style=style or f"bold {GREEN}")
                
                if isinstance(content, Text):
                    text.append_text(content)
                else:
                    text.append(content)
        
        return text
    
    def add_item(
        self,
        content: str | Text,
        *,
        icon: str = "✓",
        icon_style: str = "",
        pending: bool = False
    ):
        """Add an item to the group."""
        self._items.append((content, icon, icon_style or f"bold {GREEN}", pending))
        self.refresh()
        return len(self._items) - 1
    
    def mark_item_complete(self, index: int, icon: str = "✓", icon_style: str = ""):
        """Mark a specific item as complete."""
        if 0 <= index < len(self._items):
            content, _, _, _ = self._items[index]
            self._items[index] = (content, icon, icon_style or f"bold {GREEN}", False)
            self.refresh()
    
    def mark_group_complete(self, icon: str = "✓", icon_style: str = ""):
        """Mark the entire group as complete."""
        self._pending = False
        self._icon = icon
        self._icon_style = icon_style or f"bold {GREEN}"
        self.refresh()
    
    def toggle(self):
        """Toggle expanded/collapsed state."""
        self.expanded = not self.expanded
        self.post_message(self.Toggled(self, self.expanded))
        self.refresh()
    
    def action_toggle(self):
        """Action handler for key bindings."""
        self.toggle()
    
    def on_click(self, event):
        """Handle click to toggle."""
        event.stop()
        self.toggle()


class ToolTreeWidget(Static, can_focus=True):
    """Amp-style tool call display with tree hierarchy.
    
    Groups related tool calls and shows them in a collapsible tree:
    
        ✓ Search
        │
        ├── ✓ Grep "pattern" in src/
        ├── ✓ Glob "*.py"
        └── ✓ Read file.py
    """
    
    DEFAULT_CSS = """
    ToolTreeWidget {
        height: auto;
        padding: 0 2;
        margin: 0;
    }
    
    ToolTreeWidget:hover {
        background: $surface;
    }
    
    ToolTreeWidget:focus {
        background: $surface;
    }
    """
    
    BINDINGS = [
        ("enter", "toggle_collapse", "Toggle"),
        ("space", "toggle_collapse", "Toggle"),
    ]
    
    def __init__(
        self,
        category: str,
        *,
        collapsed: bool = False,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.category = category
        self._collapsed = collapsed
        # (name, args, pending, summary, streaming_text, diff_preview)
        self._tools: list[tuple[str, dict, bool, str, str, str]] = []
        self._pending = True
    
    def render(self) -> Text:
        text = Text()
        
        # Header with status
        if self._collapsed:
            text.append("▸ ", style="dim")
        elif self._tools:
            text.append("▾ ", style="dim")
        
        if self._pending and any(t[2] for t in self._tools):
            text.append("~ ", style="dim")
        else:
            text.append("✓ ", style=f"bold {GREEN}")
        
        text.append(self.category.title(), style="bold white")
        
        if self._collapsed:
            completed = sum(1 for t in self._tools if not t[2])
            text.append(f" ({completed}/{len(self._tools)})", style="dim")
            return text
        
        if not self._tools:
            return text
        
        # Vertical connector
        text.append("\n│", style=DIM)
        
        # Render each tool
        for i, (name, args, pending, summary, streaming_text, diff_preview) in enumerate(self._tools):
            is_last = (i == len(self._tools) - 1)
            has_sublines = bool(streaming_text or diff_preview)
            connector = "\n└── " if is_last and not has_sublines else "\n├── "
            text.append(connector, style=DIM)
            
            if pending:
                text.append("~ ", style="dim")
            else:
                text.append("✓ ", style=f"bold {GREEN}")
            
            text.append_text(self._format_tool(name, args, pending))
            
            if summary and not pending:
                text.append(f" {summary}", style="dim")
            
            # Show streaming text inline (for bash output, etc.)
            if streaming_text and not self._collapsed:
                lines = streaming_text.strip().split("\n")[:3]  # Max 3 lines preview
                for j, line in enumerate(lines):
                    is_last_line = (j == len(lines) - 1) and is_last and not diff_preview
                    line_connector = "\n│   └── " if is_last_line else "\n│   ├── "
                    text.append(line_connector, style=DIM)
                    # Truncate long lines
                    display_line = line[:60] + "..." if len(line) > 60 else line
                    text.append(display_line, style="dim")
                if len(streaming_text.strip().split("\n")) > 3:
                    text.append("\n│   └── ", style=DIM)
                    text.append(f"... ({len(streaming_text.strip().split(chr(10))) - 3} more lines)", style="dim italic")
            
            # Show diff preview for edit/write operations
            if diff_preview and not self._collapsed:
                text.append_text(self._render_diff_preview(diff_preview, is_last))
        
        return text
    
    def _render_diff_preview(self, diff: str, is_last: bool) -> Text:
        """Render a compact diff preview with colors."""
        text = Text()
        lines = diff.strip().split("\n")
        
        # Count additions/deletions
        additions = sum(1 for l in lines if l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in lines if l.startswith('-') and not l.startswith('---'))
        
        # Show summary line
        prefix = "\n│   └── " if is_last else "\n│   ├── "
        text.append(prefix, style=DIM)
        text.append(f"+{additions}", style=f"bold {GREEN}")
        text.append(" / ", style="dim")
        text.append(f"-{deletions}", style="bold red")
        
        # Show first few diff lines (max 4)
        shown_lines = 0
        for line in lines:
            if shown_lines >= 4:
                break
            if line.startswith('@@'):
                continue
            if line.startswith('+++') or line.startswith('---'):
                continue
            
            line_prefix = "\n│       "
            text.append(line_prefix, style=DIM)
            
            display_line = line[:50] + "..." if len(line) > 50 else line
            if line.startswith('+'):
                text.append(display_line, style=f"{GREEN}")
            elif line.startswith('-'):
                text.append(display_line, style="red")
            else:
                text.append(display_line, style="dim")
            shown_lines += 1
        
        return text
    
    def _format_tool(self, name: str, args: dict, pending: bool) -> Text:
        """Format a tool call with styled output."""
        text = Text()
        dim = pending
        base_style = "dim" if dim else ""
        
        if name == "read":
            path = args.get("path", args.get("file_path", ""))
            text.append("Read ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN}" if not dim else "dim")
        elif name == "write":
            path = args.get("path", "")
            text.append("Write ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN}" if not dim else "dim")
        elif name == "edit":
            path = args.get("path", "")
            text.append("Edit ", style=base_style)
            text.append(self._short_path(path), style=f"{CYAN}" if not dim else "dim")
        elif name == "grep":
            pattern = args.get("pattern", "")
            path = args.get("path", "")
            text.append("Grep ", style=base_style)
            text.append(f'"{pattern}"', style=f"bold {YELLOW}" if not dim else "dim")
            if path:
                text.append(" in ", style="dim")
                text.append(self._short_path(path), style=f"{CYAN}" if not dim else "dim")
        elif name == "glob":
            pattern = args.get("pattern", args.get("file_pattern", ""))
            text.append("Glob ", style=base_style)
            text.append(f'"{pattern}"', style=f"bold {YELLOW}" if not dim else "dim")
        elif name == "bash":
            cmd = args.get("command", "")[:50]
            desc = args.get("description", "")
            if desc:
                text.append(desc, style=base_style)
            else:
                text.append("$ ", style=base_style)
                text.append(cmd, style=f"{YELLOW}" if not dim else "dim")
        elif name == "codesearch":
            query = args.get("query", "")[:40]
            text.append("Search ", style=base_style)
            text.append(f'"{query}"', style=f"bold {YELLOW}" if not dim else "dim")
        elif name == "websearch":
            query = args.get("query", "")[:40]
            text.append("Web ", style=base_style)
            text.append(f'"{query}"', style=f"bold {YELLOW}" if not dim else "dim")
        else:
            text.append(f"{name}", style=base_style)
        
        return text
    
    def _short_path(self, path: str) -> str:
        """Shorten path for display."""
        if not path:
            return ""
        parts = path.split("/")
        if len(parts) > 3:
            return f".../{'/'.join(parts[-2:])}"
        return path
    
    def add_tool(self, name: str, args: dict, pending: bool = True) -> int:
        """Add a tool to the group. Returns the index."""
        self._tools.append((name, args, pending, "", "", ""))
        self.refresh()
        return len(self._tools) - 1
    
    def mark_tool_complete(self, index: int, summary: str = "", streaming_text: str = "", diff_preview: str = ""):
        """Mark a tool as complete with optional inline content."""
        if 0 <= index < len(self._tools):
            name, args, _, _, _, _ = self._tools[index]
            self._tools[index] = (name, args, False, summary, streaming_text, diff_preview)
            # Check if all tools are complete
            if not any(t[2] for t in self._tools):
                self._pending = False
            self.refresh()
    
    def update_streaming_text(self, index: int, text: str):
        """Update streaming text for a tool (for live output)."""
        if 0 <= index < len(self._tools):
            name, args, pending, summary, _, diff = self._tools[index]
            self._tools[index] = (name, args, pending, summary, text, diff)
            self.refresh()
    
    def set_diff_preview(self, index: int, diff: str):
        """Set diff preview for an edit/write tool."""
        if 0 <= index < len(self._tools):
            name, args, pending, summary, streaming, _ = self._tools[index]
            self._tools[index] = (name, args, pending, summary, streaming, diff)
            self.refresh()
    
    def toggle_collapse(self):
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self.refresh()
    
    def action_toggle_collapse(self):
        """Action handler for key bindings."""
        self.toggle_collapse()
    
    def on_click(self, event):
        """Handle click to toggle collapse."""
        event.stop()
        self.toggle_collapse()


class ThinkingTreeWidget(Static, can_focus=True):
    """Amp-style thinking indicator with collapsible content.
    
    Renders as:
        ▾ Thinking...
        │
        └── TL;DR: Summary of what was analyzed
    
    Or when collapsed:
        ▸ Thinking (collapsed)
    """
    
    DEFAULT_CSS = """
    ThinkingTreeWidget {
        height: auto;
        padding: 0 2;
        margin: 0 0 1 0;
    }
    
    ThinkingTreeWidget:hover {
        background: $surface;
    }
    
    ThinkingTreeWidget:focus {
        background: $surface;
    }
    """
    
    BINDINGS = [
        ("enter", "toggle", "Toggle"),
        ("space", "toggle", "Toggle"),
    ]
    
    SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    def __init__(self, message: str = "Thinking", **kwargs):
        super().__init__(**kwargs)
        self._message = message
        self._frame = 0
        self._complete = False
        self._collapsed = False
        self._summary: str | None = None
        self._sub_items: list[str] = []
    
    def render(self) -> Text:
        text = Text()
        
        if self._collapsed and self._complete:
            text.append("▸ ", style="dim")
            text.append("✓ ", style=f"bold {GREEN}")
            text.append(self._message, style="bold white")
            if self._summary:
                text.append(f": {self._summary[:50]}...", style="dim")
            return text
        
        # Expand/collapse indicator
        if self._sub_items or self._summary:
            text.append("▾ ", style="dim")
        
        # Status icon
        if self._complete:
            text.append("✓ ", style=f"bold {GREEN}")
        else:
            spinner = self.SPINNER[self._frame % len(self.SPINNER)]
            text.append(f"{spinner} ", style=f"bold {CYAN}")
        
        text.append(self._message, style="bold white" if self._complete else "")
        
        # Show summary/sub-items if expanded
        if (self._summary or self._sub_items) and not self._collapsed:
            text.append("\n│", style=DIM)
            
            if self._summary:
                text.append("\n└── ", style=DIM)
                text.append("TL;DR: ", style=f"bold {LIGHT_BLUE}")
                text.append(self._summary, style="")
            
            for i, item in enumerate(self._sub_items):
                is_last = (i == len(self._sub_items) - 1) and not self._summary
                connector = "\n└── " if is_last else "\n├── "
                text.append(connector, style=DIM)
                text.append(item, style="dim")
        
        return text
    
    def next_frame(self):
        """Advance spinner animation."""
        if not self._complete:
            self._frame += 1
            self.refresh()
    
    def set_message(self, message: str):
        """Update the thinking message."""
        self._message = message
        self.refresh()
    
    def add_sub_item(self, item: str):
        """Add a sub-item to show progress."""
        self._sub_items.append(item)
        self.refresh()
    
    def complete(self, summary: str | None = None):
        """Mark thinking as complete with optional summary."""
        self._complete = True
        self._summary = summary
        self.refresh()
    
    def toggle_collapse(self):
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self.refresh()
    
    def action_toggle(self):
        """Action handler for key bindings."""
        if self._complete:
            self.toggle_collapse()
    
    def on_click(self, event):
        """Handle click to toggle collapse."""
        if self._complete:
            event.stop()
            self.toggle_collapse()


# Additional color constant for headers
LIGHT_BLUE = "#8aadf4"
PURPLE = "#c6a0f6"


class OracleTreeWidget(Static, can_focus=True):
    """Amp-style Oracle/subagent display with collapsible results.
    
    Renders as:
        ▾ ✓ Oracle
        │
        ├── Thinking...
        │
        └── TL;DR: Summary of analysis
            │
            ├── 1. First recommendation
            ├── 2. Second recommendation
            └── 3. Third recommendation
    
    When collapsed:
        ▸ ✓ Oracle: Summary preview...
    """
    
    DEFAULT_CSS = """
    OracleTreeWidget {
        height: auto;
        padding: 0 2;
        margin: 0 0 1 0;
    }
    
    OracleTreeWidget:hover {
        background: $surface;
    }
    
    OracleTreeWidget:focus {
        background: $surface;
    }
    """
    
    BINDINGS = [
        ("enter", "toggle", "Toggle"),
        ("space", "toggle", "Toggle"),
    ]
    
    SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    def __init__(
        self,
        title: str = "Oracle",
        *,
        subagent_type: str = "oracle",
        **kwargs
    ):
        super().__init__(**kwargs)
        self._title = title
        self._subagent_type = subagent_type
        self._frame = 0
        self._complete = False
        self._collapsed = False
        self._thinking_message = "Analyzing..."
        self._summary: str | None = None
        self._recommendations: list[str] = []
        self._files_read: list[str] = []
        self._result_content: str = ""
    
    def render(self) -> Text:
        text = Text()
        
        # Collapsed state
        if self._collapsed and self._complete:
            text.append("▸ ", style="dim")
            text.append("✓ ", style=f"bold {GREEN}")
            text.append(self._title, style=f"bold {PURPLE}")
            if self._summary:
                summary_preview = self._summary[:60] + "..." if len(self._summary) > 60 else self._summary
                text.append(f": {summary_preview}", style="dim")
            return text
        
        # Expanded state - header
        if self._complete or self._summary or self._recommendations:
            text.append("▾ ", style="dim")
        
        if self._complete:
            text.append("✓ ", style=f"bold {GREEN}")
        else:
            spinner = self.SPINNER[self._frame % len(self.SPINNER)]
            text.append(f"{spinner} ", style=f"bold {PURPLE}")
        
        text.append(self._title, style=f"bold {PURPLE}")
        
        # Show subagent type if not oracle
        if self._subagent_type and self._subagent_type != "oracle":
            text.append(f" ({self._subagent_type})", style="dim")
        
        # Thinking state
        if not self._complete:
            text.append("\n│", style=DIM)
            text.append("\n└── ", style=DIM)
            spinner = self.SPINNER[self._frame % len(self.SPINNER)]
            text.append(f"{spinner} ", style=f"bold {CYAN}")
            text.append(self._thinking_message, style="dim")
            return text
        
        # Completed state with content
        if self._summary or self._recommendations or self._files_read:
            text.append("\n│", style=DIM)
        
        # Show files read
        if self._files_read:
            for i, path in enumerate(self._files_read):
                is_last = (i == len(self._files_read) - 1) and not self._summary and not self._recommendations
                connector = "\n└── " if is_last else "\n├── "
                text.append(connector, style=DIM)
                text.append("Read ", style="dim")
                # Shorten path
                parts = path.split("/")
                short_path = f".../{'/'.join(parts[-2:])}" if len(parts) > 3 else path
                text.append(short_path, style=f"{CYAN}")
        
        # Show TL;DR summary
        if self._summary:
            has_more = bool(self._recommendations)
            connector = "\n├── " if has_more else "\n└── "
            text.append(connector, style=DIM)
            text.append("TL;DR: ", style=f"bold {LIGHT_BLUE}")
            text.append(self._summary, style="")
        
        # Show recommendations
        for i, rec in enumerate(self._recommendations):
            is_last = (i == len(self._recommendations) - 1)
            connector = "\n└── " if is_last else "\n├── "
            text.append(connector, style=DIM)
            text.append(f"{i + 1}) ", style=f"bold {YELLOW}")
            # Truncate long recommendations
            rec_display = rec[:80] + "..." if len(rec) > 80 else rec
            text.append(rec_display, style="")
        
        return text
    
    def next_frame(self):
        """Advance spinner animation."""
        if not self._complete:
            self._frame += 1
            self.refresh()
    
    def set_thinking(self, message: str):
        """Update the thinking message."""
        self._thinking_message = message
        self.refresh()
    
    def add_file_read(self, path: str):
        """Add a file that was read during analysis."""
        if path not in self._files_read:
            self._files_read.append(path)
            self.refresh()
    
    def complete(
        self,
        summary: str | None = None,
        recommendations: list[str] | None = None,
        content: str = ""
    ):
        """Mark oracle as complete with results."""
        self._complete = True
        self._summary = summary
        self._recommendations = recommendations or []
        self._result_content = content
        self.refresh()
    
    def parse_and_complete(self, content: str):
        """Parse oracle response content and extract summary/recommendations."""
        self._result_content = content
        self._complete = True
        
        # Try to extract TL;DR summary
        lines = content.strip().split("\n")
        summary_lines = []
        recommendations = []
        in_summary = False
        
        for line in lines:
            line_lower = line.lower().strip()
            
            # Detect TL;DR or summary section
            if "tl;dr" in line_lower or "summary" in line_lower:
                in_summary = True
                continue
            
            # Detect numbered recommendations
            if line.strip() and line.strip()[0].isdigit() and ("." in line[:4] or ")" in line[:4]):
                # Extract the recommendation text
                rec_text = line.strip()
                for sep in [".", ")", ":"]:
                    if sep in rec_text[:4]:
                        rec_text = rec_text.split(sep, 1)[1].strip()
                        break
                if rec_text:
                    recommendations.append(rec_text)
                in_summary = False
                continue
            
            if in_summary and line.strip():
                summary_lines.append(line.strip())
        
        if summary_lines:
            self._summary = " ".join(summary_lines[:2])  # First 2 lines as summary
        elif lines:
            # Use first non-empty line as summary
            for line in lines:
                if line.strip():
                    self._summary = line.strip()[:100]
                    break
        
        self._recommendations = recommendations[:5]  # Max 5 recommendations
        self.refresh()
    
    def toggle_collapse(self):
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self.refresh()
    
    def action_toggle(self):
        """Action handler for key bindings."""
        if self._complete:
            self.toggle_collapse()
    
    def on_click(self, event):
        """Handle click to toggle collapse."""
        if self._complete:
            event.stop()
            self.toggle_collapse()


class SubAgentTreeWidget(Static, can_focus=True):
    """Amp-style subagent task display with collapsible results.
    
    Renders as:
        ▾ ✓ Task: Implement authentication
        │
        ├── Using coder subagent
        ├── ✓ Read auth.py
        ├── ✓ Edit auth.py
        └── Result: Added JWT validation
    """
    
    DEFAULT_CSS = """
    SubAgentTreeWidget {
        height: auto;
        padding: 0 2;
        margin: 0 0 1 0;
    }
    
    SubAgentTreeWidget:hover {
        background: $surface;
    }
    
    SubAgentTreeWidget:focus {
        background: $surface;
    }
    """
    
    BINDINGS = [
        ("enter", "toggle", "Toggle"),
        ("space", "toggle", "Toggle"),
    ]
    
    SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    def __init__(
        self,
        description: str,
        *,
        subagent_type: str = "coder",
        **kwargs
    ):
        super().__init__(**kwargs)
        self._description = description
        self._subagent_type = subagent_type
        self._frame = 0
        self._complete = False
        self._collapsed = False
        self._actions: list[tuple[str, str, bool]] = []  # (action, detail, complete)
        self._result_summary: str = ""
    
    def render(self) -> Text:
        text = Text()
        
        # Collapsed state
        if self._collapsed and self._complete:
            text.append("▸ ", style="dim")
            text.append("✓ ", style=f"bold {GREEN}")
            text.append("Task: ", style="dim")
            desc_preview = self._description[:50] + "..." if len(self._description) > 50 else self._description
            text.append(desc_preview, style="bold white")
            if self._result_summary:
                text.append(f": {self._result_summary[:30]}...", style="dim")
            return text
        
        # Expanded header
        if self._complete or self._actions:
            text.append("▾ ", style="dim")
        
        if self._complete:
            text.append("✓ ", style=f"bold {GREEN}")
        else:
            spinner = self.SPINNER[self._frame % len(self.SPINNER)]
            text.append(f"{spinner} ", style=f"bold {CYAN}")
        
        text.append("Task: ", style="dim")
        desc_display = self._description[:60] + "..." if len(self._description) > 60 else self._description
        text.append(desc_display, style="bold white")
        
        # Show content
        if self._actions or self._subagent_type:
            text.append("\n│", style=DIM)
        
        # Subagent type
        text.append("\n├── ", style=DIM)
        text.append(f"Using {self._subagent_type} subagent", style="dim")
        
        # Actions
        for i, (action, detail, complete) in enumerate(self._actions):
            is_last = (i == len(self._actions) - 1) and not self._result_summary
            connector = "\n└── " if is_last else "\n├── "
            text.append(connector, style=DIM)
            
            if complete:
                text.append("✓ ", style=f"bold {GREEN}")
            else:
                text.append("~ ", style="dim")
            
            text.append(action, style="")
            if detail:
                text.append(f" {detail}", style=f"{CYAN}")
        
        # Result summary
        if self._result_summary and self._complete:
            text.append("\n└── ", style=DIM)
            text.append("Result: ", style=f"bold {LIGHT_BLUE}")
            text.append(self._result_summary, style="")
        
        return text
    
    def next_frame(self):
        """Advance spinner animation."""
        if not self._complete:
            self._frame += 1
            self.refresh()
    
    def add_action(self, action: str, detail: str = "", complete: bool = False):
        """Add an action the subagent performed."""
        self._actions.append((action, detail, complete))
        self.refresh()
    
    def mark_action_complete(self, index: int):
        """Mark a specific action as complete."""
        if 0 <= index < len(self._actions):
            action, detail, _ = self._actions[index]
            self._actions[index] = (action, detail, True)
            self.refresh()
    
    def complete(self, result_summary: str = ""):
        """Mark the subagent task as complete."""
        self._complete = True
        self._result_summary = result_summary
        # Mark all actions complete
        self._actions = [(a, d, True) for a, d, _ in self._actions]
        self.refresh()
    
    def toggle_collapse(self):
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self.refresh()
    
    def action_toggle(self):
        """Action handler for key bindings."""
        if self._complete:
            self.toggle_collapse()
    
    def on_click(self, event):
        """Handle click to toggle collapse."""
        if self._complete:
            event.stop()
            self.toggle_collapse()


class ContextBreadcrumbs(Static):
    """Shows which files are currently in context - Amp style.
    
    Renders as a horizontal bar at the top:
        Context: auth.py, utils.py, config.json (+3 more)
    
    Clickable to copy file paths.
    """
    
    DEFAULT_CSS = """
    ContextBreadcrumbs {
        height: 1;
        width: 100%;
        padding: 0 2;
        background: $surface;
        border-bottom: solid $primary;
    }
    
    ContextBreadcrumbs:hover {
        background: $panel;
    }
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._files: list[str] = []
        self._max_display = 5
    
    def render(self) -> Text:
        text = Text()
        
        if not self._files:
            text.append("Context: ", style="dim")
            text.append("No files", style="dim italic")
            return text
        
        text.append("Context: ", style="dim")
        
        display_files = self._files[:self._max_display]
        for i, path in enumerate(display_files):
            if i > 0:
                text.append(", ", style="dim")
            
            # Show just filename
            filename = path.split("/")[-1]
            # Make it a clickable link
            file_url = f"file://{path}"
            text.append(filename, style=f"{CYAN} link {file_url}")
        
        # Show overflow count
        if len(self._files) > self._max_display:
            remaining = len(self._files) - self._max_display
            text.append(f" (+{remaining} more)", style="dim")
        
        return text
    
    def add_file(self, path: str):
        """Add a file to context (avoids duplicates)."""
        if path not in self._files:
            self._files.append(path)
            self.refresh()
    
    def remove_file(self, path: str):
        """Remove a file from context."""
        if path in self._files:
            self._files.remove(path)
            self.refresh()
    
    def set_files(self, files: list[str]):
        """Set the full file list."""
        self._files = list(files)
        self.refresh()
    
    def clear(self):
        """Clear all files."""
        self._files.clear()
        self.refresh()
    
    def get_files(self) -> list[str]:
        """Get current file list."""
        return list(self._files)


class ClickablePath(Static, can_focus=True):
    """A clickable file path that copies to clipboard on click.
    
    Renders as a styled path that highlights on hover.
    """
    
    DEFAULT_CSS = """
    ClickablePath {
        height: auto;
        width: auto;
    }
    
    ClickablePath:hover {
        background: $surface;
        text-style: underline;
    }
    
    ClickablePath:focus {
        background: $surface;
    }
    """
    
    class PathCopied(Message):
        """Posted when path is copied to clipboard."""
        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path
    
    def __init__(self, path: str, **kwargs):
        super().__init__(**kwargs)
        self._path = path
    
    def render(self) -> Text:
        text = Text()
        # Shorten path for display
        parts = self._path.split("/")
        if len(parts) > 3:
            display = f".../{'/'.join(parts[-2:])}"
        else:
            display = self._path
        
        file_url = f"file://{self._path}"
        text.append(display, style=f"{CYAN} link {file_url}")
        return text
    
    def on_click(self, event):
        """Copy path to clipboard on click."""
        event.stop()
        self.post_message(self.PathCopied(self._path))
        # Try to copy to clipboard
        try:
            import pyperclip
            pyperclip.copy(self._path)
        except ImportError:
            pass  # pyperclip not available


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
            text.append("[✓] ", style=f"bold {GREEN}")
        elif self.status == "in_progress":
            text.append("[•] ", style=f"bold {YELLOW}")
        elif self.status == "cancelled":
            text.append("[-] ", style="dim")
        else:  # pending
            text.append("[ ] ", style="dim")
        
        # Content with appropriate style
        content_style = "dim strike" if self.status in ("done", "cancelled") else ""
        if self.status == "in_progress":
            content_style = YELLOW
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
                text.append("  [✓] ", style=f"bold {GREEN}")
            elif status == "in_progress":
                text.append("  [•] ", style=f"bold {YELLOW}")
            elif status == "cancelled":
                text.append("  [-] ", style="dim")
            else:
                text.append("  [ ] ", style="dim")
            
            # Content
            content_style = "dim strike" if status in ("done", "cancelled") else ""
            if status == "in_progress":
                content_style = YELLOW
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
        text.append(f"[{dots}] ", style=f"bold {CYAN}")
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


class ActionHeaderWidget(Static):
    """Widget to display an action header like ':: Search'"""

    DEFAULT_CSS = """
    ActionHeaderWidget {
        height: auto;
        padding: 0 2;
        margin: 1 0 0 0;
    }
    """

    def __init__(self, title: str, **kwargs):
        super().__init__(**kwargs)
        self.title = title

    def render(self) -> Text:
        text = Text()
        text.append(":: ", style="bold white")
        text.append(self.title, style="bold white")
        return text


class TreeConnectorWidget(Static):
    """Widget to display a vertical tree connector line"""

    DEFAULT_CSS = """
    TreeConnectorWidget {
        height: auto;
        padding: 0 2;
        margin: 0;
    }
    """

    def render(self) -> Text:
        text = Text()
        text.append("│", style=DIM)
        return text


class CodeReviewWidget(Static):
    """Widget to display code review results"""

    DEFAULT_CSS = """
    CodeReviewWidget {
        height: auto;
        padding: 1 2;
        margin: 1 0;
        border-left: heavy $warning;
    }
    
    CodeReviewWidget.passed {
        border-left: heavy $success;
    }
    
    CodeReviewWidget.critical {
        border-left: heavy $error;
    }
    """

    def __init__(self, review_result, **kwargs):
        super().__init__(**kwargs)
        self.review_result = review_result
        if review_result.has_critical:
            self.add_class("critical")
        elif not review_result.issues:
            self.add_class("passed")

    def render(self) -> Text:
        text = Text()
        result = self.review_result
        
        if not result.issues:
            text.append("✓ ", style=f"bold {GREEN}")
            text.append("Code review passed", style="bold")
            text.append(f" - no issues found in {len(result.files_reviewed)} file(s)", style="dim")
            return text
        
        # Header
        text.append(":: ", style="bold white")
        text.append("Code Review", style="bold white")
        text.append(f" ({len(result.issues)} issue(s))\n", style="dim")
        
        # Group issues by severity
        critical = [i for i in result.issues if i.severity == "critical"]
        warnings = [i for i in result.issues if i.severity == "warning"]
        suggestions = [i for i in result.issues if i.severity == "suggestion"]
        
        if critical:
            text.append("\n")
            text.append("Critical Issues:\n", style="bold red")
            for issue in critical:
                loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
                text.append("  ✗ ", style="bold red")
                text.append(f"[{loc}] ", style=CYAN)
                text.append(f"{issue.description}\n", style="")
                if issue.fix:
                    text.append(f"    → {issue.fix}\n", style="dim")
        
        if warnings:
            text.append("\n")
            text.append("Warnings:\n", style=f"bold {YELLOW}")
            for issue in warnings:
                loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
                text.append("  [!] ", style=f"bold {YELLOW}")
                text.append(f"[{loc}] ", style=CYAN)
                text.append(f"{issue.description}\n", style="")
                if issue.fix:
                    text.append(f"    → {issue.fix}\n", style="dim")
        
        if suggestions:
            text.append("\n")
            text.append("Suggestions:\n", style="bold #8aadf4")
            for issue in suggestions:
                loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
                text.append("  • ", style="#8aadf4")
                text.append(f"[{loc}] ", style=CYAN)
                text.append(f"{issue.description}\n", style="dim")
        
        if result.summary:
            text.append("\n")
            text.append(result.summary, style="dim italic")
        
        return text


class StreamingTextWidget(Static):
    """Widget to display streaming text response with live updates.
    
    Shows text as it streams in, with a blinking cursor at the end.
    Uses plain text during streaming for smooth incremental updates,
    then renders full markdown on completion.
    """

    DEFAULT_CSS = """
    StreamingTextWidget {
        height: auto;
        padding: 1 2;
        margin: 1 0 0 0;
    }
    
    StreamingTextWidget.streaming {
        /* Active streaming state */
    }
    
    StreamingTextWidget.complete {
        /* Completed state */
    }
    """
    
    # Use reactive to auto-trigger updates
    _content: reactive[str] = reactive("", repaint=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._streaming = True
        self._cursor_visible = True
        self._rendered_cache: Text | None = None
        self._last_rendered_len = 0
        self.set_class(True, "streaming")

    def render(self) -> Text:
        from rich.markdown import Markdown
        from rich.console import Console
        from rich.theme import Theme
        
        if self._streaming:
            # During streaming: use plain text for smooth appending (no flicker)
            text = Text(self._content, style="white")
            
            # Add blinking cursor
            cursor = "▊" if self._cursor_visible else " "
            text.append(cursor, style=f"bold {CYAN}")
            return text
        
        # After completion: render full markdown (cached)
        if self._rendered_cache is not None and self._last_rendered_len == len(self._content):
            return self._rendered_cache
        
        text = Text()
        
        if self._content:
            themed_console = Console(
                theme=Theme({
                    "markdown.link": f"bold {CYAN}",
                    "markdown.link_url": f"dim {CYAN}",
                    "markdown.h1": "bold white",
                    "markdown.h2": f"bold #8aadf4",
                    "markdown.h3": f"bold #8aadf4",
                    "markdown.code": f"{YELLOW}",
                }),
                force_terminal=True,
                width=120,
            )
            
            with themed_console.capture() as capture:
                themed_console.print(Markdown(self._content, hyperlinks=True, code_theme="monokai"))
            
            text = Text.from_ansi(capture.get())
        
        # Cache the rendered result
        self._rendered_cache = text
        self._last_rendered_len = len(self._content)
        
        return text

    def append_text(self, content: str):
        """Append new text content (streaming) - triggers reactive repaint."""
        # Use assignment to trigger reactive update (not +=)
        self._content = self._content + content
        # Invalidate cache since content changed
        self._rendered_cache = None

    def set_content(self, content: str):
        """Set the full content."""
        self._content = content
        self._rendered_cache = None

    def get_content(self) -> str:
        """Get the current content."""
        return self._content

    def mark_complete(self):
        """Mark streaming as complete - now render full markdown."""
        self._streaming = False
        self._rendered_cache = None  # Force re-render with markdown
        self.set_class(False, "streaming")
        self.set_class(True, "complete")
        self.refresh()

    def toggle_cursor(self):
        """Toggle cursor visibility for blinking effect."""
        if self._streaming:
            self._cursor_visible = not self._cursor_visible
            self.refresh()
