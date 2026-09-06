"""Command palette for slash commands"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static, Input
from textual.binding import Binding
from rich.text import Text


COMMANDS = [
    {"cmd": "/init", "desc": "create/update AGENTS.md"},
    {"cmd": "/new", "desc": "create a new session"},
    {"cmd": "/fork", "desc": "fork session to explore alternative"},
    {"cmd": "/branches", "desc": "list session branches"},
    {"cmd": "/models", "desc": "list models"},
    {"cmd": "/backend", "desc": "switch between codesm, Claude Code, and Codex"},
    {"cmd": "/mode", "desc": "switch mode (smart/rush)"},
    {"cmd": "/rush", "desc": "rush mode - fast & cheap"},
    {"cmd": "/smart", "desc": "smart mode - full capability"},
    {"cmd": "/dryrun", "desc": "toggle dry-run mode (preview only)"},
    {"cmd": "/audit", "desc": "show recent agent actions"},
    {"cmd": "/agents", "desc": "list agents"},
    {"cmd": "/session", "desc": "list sessions"},
    {"cmd": "/status", "desc": "show status"},
    {"cmd": "/debug", "desc": "reproduce and verify a bug"},
    {"cmd": "/cost", "desc": "show cost/usage stats"},
    {"cmd": "/theme", "desc": "toggle theme"},
    {"cmd": "/editor", "desc": "open editor"},
    {"cmd": "/connect", "desc": "connect to a provider"},
    {"cmd": "/help", "desc": "show help"},
]


class CommandItem(Static):
    """A single command item"""

    def __init__(self, cmd: str, desc: str, **kwargs):
        super().__init__(**kwargs)
        self.cmd = cmd
        self.desc = desc
        self._selected = False

    def render(self) -> Text:
        text = Text("› " if self._selected else "  ", no_wrap=True, overflow="ellipsis")
        text.append(f"{self.cmd:<12}", style="bold")
        text.append(f" {self.desc}", style="dim")
        return text

    def set_selected(self, selected: bool):
        self._selected = selected
        self.set_class(selected, "-selected")
        self.refresh()


class CommandPaletteModal(ModalScreen):
    """Command palette modal that appears when typing /"""

    CSS = """
    CommandPaletteModal {
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #palette-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        background: $surface;
        border: round $panel;
        padding: 0 1;
    }

    #palette-input {
        width: 100%;
        height: 1;
        border: none;
        background: $panel;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #palette-input:focus {
        border: none;
    }

    #commands-list {
        height: auto;
        max-height: 45vh;
        padding: 0;
        scrollbar-size: 1 1;
    }

    CommandItem {
        height: 1;
        padding: 0 1;
    }

    CommandItem.-selected {
        background: $boost;
        color: $text;
        text-style: bold;
    }

    CommandItem.-selected:ansi {
        text-style: bold reverse;
    }

    #palette-hint {
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("enter", "select", "Run", show=False, priority=True),
    ]

    def __init__(self, initial_text: str = "/", commands: list[dict] | None = None):
        super().__init__()
        self.initial_text = initial_text
        self.commands = COMMANDS if commands is None else commands
        self.selected_index = 0
        self.visible_items: list[CommandItem] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Input(value=self.initial_text, id="palette-input")
            with VerticalScroll(id="commands-list"):
                for cmd_info in self.commands:
                    yield CommandItem(cmd_info["cmd"], cmd_info["desc"])
            yield Static("↑↓ select · enter run · esc close", id="palette-hint")

    def on_mount(self):
        self._refresh_items(self.initial_text)
        input_widget = self.query_one("#palette-input", Input)
        input_widget.cursor_position = len(self.initial_text)
        input_widget.focus()

    def _refresh_items(self, filter_text: str = "/"):
        """Rank command-name matches before description matches."""
        self.visible_items = []
        filter_lower = filter_text.lower().lstrip("/")

        def match_rank(item: CommandItem):
            name = item.cmd.lower().lstrip("/")
            return (name != filter_lower, not name.startswith(filter_lower), filter_lower not in name)

        listing = self.query_one("#commands-list", VerticalScroll)
        # Reset to construction order so ties and clearing the filter stay stable.
        listing.sort_children()
        listing.sort_children(key=match_rank)

        for item in self.query(CommandItem):
            cmd_lower = item.cmd.lower().lstrip("/")
            if filter_lower in cmd_lower or filter_lower in item.desc.lower():
                item.display = True
                self.visible_items.append(item)
            else:
                item.display = False

        if self.selected_index >= len(self.visible_items):
            self.selected_index = max(0, len(self.visible_items) - 1)
        self._update_selection()

    def _update_selection(self):
        """Update visual selection"""
        for i, item in enumerate(self.visible_items):
            item.set_selected(i == self.selected_index)
        if self.visible_items:
            self.visible_items[self.selected_index].scroll_visible(animate=False, immediate=True)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "palette-input":
            text = event.value
            if not text.startswith("/"):
                self.dismiss(None)
                return
            self.selected_index = 0
            self._refresh_items(text)

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "palette-input":
            await self._select_current()

    async def _select_current(self):
        if self.visible_items:
            selected = self.visible_items[self.selected_index]
            self.dismiss(selected.cmd)
        else:
            self.dismiss(None)

    async def action_select(self):
        await self._select_current()

    def action_move_up(self):
        if self.visible_items:
            self.selected_index = (self.selected_index - 1) % len(self.visible_items)
            self._update_selection()

    def action_move_down(self):
        if self.visible_items:
            self.selected_index = (self.selected_index + 1) % len(self.visible_items)
            self._update_selection()

    def action_dismiss(self):
        self.dismiss(None)
