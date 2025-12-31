"""Command palette for slash commands"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical
from textual.widgets import Static, Input
from textual.binding import Binding


COMMANDS = [
    {"cmd": "/init", "desc": "create/update AGENTS.md"},
    {"cmd": "/new", "desc": "create a new session"},
    {"cmd": "/models", "desc": "list models"},
    {"cmd": "/agents", "desc": "list agents"},
    {"cmd": "/session", "desc": "list sessions"},
    {"cmd": "/status", "desc": "show status"},
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

    def render(self) -> str:
        return f"[bold]{self.cmd:<12}[/] [dim]{self.desc}[/]"

    def set_selected(self, selected: bool):
        self._selected = selected
        self.set_class(selected, "-selected")


class CommandPaletteModal(ModalScreen):
    """Command palette modal that appears when typing /"""

    CSS = """
    CommandPaletteModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.5);
    }

    #palette-container {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: tall $primary;
        padding: 0 1;
    }

    #palette-input {
        width: 100%;
        border: none;
        background: $surface;
        margin: 1 0;
    }

    #palette-input:focus {
        border: none;
    }

    #commands-list {
        height: auto;
        max-height: 15;
        padding: 0;
    }

    CommandItem {
        height: 1;
        padding: 0 1;
    }

    CommandItem.-selected {
        background: $secondary;
        color: $background;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
    ]

    def __init__(self, initial_text: str = "/"):
        super().__init__()
        self.initial_text = initial_text
        self.selected_index = 0
        self.visible_items: list[CommandItem] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Input(value=self.initial_text, id="palette-input")
            with Vertical(id="commands-list"):
                for cmd_info in COMMANDS:
                    yield CommandItem(cmd_info["cmd"], cmd_info["desc"])

    def on_mount(self):
        self._refresh_items()
        input_widget = self.query_one("#palette-input", Input)
        input_widget.cursor_position = len(self.initial_text)
        input_widget.focus()

    def _refresh_items(self, filter_text: str = "/"):
        """Refresh the visible items based on filter"""
        self.visible_items = []
        filter_lower = filter_text.lower().lstrip("/")

        for item in self.query(CommandItem):
            cmd_lower = item.cmd.lower().lstrip("/")
            if not filter_text or filter_text == "/" or filter_lower in cmd_lower or filter_lower in item.desc.lower():
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
