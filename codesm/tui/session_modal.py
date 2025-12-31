"""Session modal for codesm TUI"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Static
from textual.binding import Binding


class SessionListItem(Static):
    """A selectable session item in the session list"""

    def __init__(self, session_id: str, title: str, updated_at: str = "", **kwargs):
        super().__init__(**kwargs)
        self.session_id = session_id
        self.title = title
        self.updated_at = updated_at
        self._selected = False

    def render(self) -> str:
        from datetime import datetime
        
        # Format the date nicely
        try:
            if self.updated_at:
                dt = datetime.fromisoformat(self.updated_at)
                date_str = dt.strftime("%b %d, %I:%M %p")
            else:
                date_str = "Unknown"
        except:
            date_str = "Unknown"
        
        return f"  [bold]{self.title}[/]\n  [dim]{date_str}[/]"

    def set_selected(self, selected: bool):
        self._selected = selected
        self.set_class(selected, "-selected")


class SessionListModal(ModalScreen):
    """Modal for selecting a previous session"""

    CSS = """
    SessionListModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.5);
    }

    #modal-container {
        width: 70;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }

    #modal-header {
        height: 3;
        width: 100%;
    }

    #modal-title {
        text-style: bold;
    }

    #esc-hint {
        dock: right;
        color: $text-muted;
    }

    #session-list {
        height: auto;
        max-height: 20;
        padding: 0;
    }

    SessionListItem {
        height: 2;
        padding: 0;
    }

    SessionListItem.-selected {
        background: $secondary;
        color: $background;
    }

    SessionListItem.-selected .session-title {
        color: $background;
        text-style: bold;
    }

    SessionListItem.-selected .session-date {
        color: $background;
    }

    #modal-footer {
        height: 2;
        padding: 1 0 0 0;
        color: $text-muted;
    }

    #modal-footer Static {
        margin-right: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.selected_index = 0
        self.visible_items: list[SessionListItem] = []
        self.sessions: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Select session", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield VerticalScroll(id="session-list")
            with Horizontal(id="modal-footer"):
                yield Static("[bold]Press Enter[/] to load session")

    def on_mount(self):
        self._build_list()

    def _build_list(self):
        """Build the list of available sessions"""
        from codesm.session.session import Session
        
        container = self.query_one("#session-list", VerticalScroll)
        container.remove_children()
        self.visible_items = []
        
        # Get all sessions
        self.sessions = Session.list_sessions()
        
        if not self.sessions:
            container.mount(Static("No previous sessions", classes="group-header"))
            return
        
        for session in self.sessions:
            item = SessionListItem(
                session["id"],
                session.get("title", "Untitled Session"),
                session.get("updated_at", "")
            )
            container.mount(item)
            self.visible_items.append(item)
        
        if self.visible_items:
            self.selected_index = 0
            self.visible_items[0].set_selected(True)

    def action_move_up(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index - 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible()

    def action_move_down(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index + 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible()

    def action_select(self):
        if self.visible_items:
            selected = self.visible_items[self.selected_index]
            self.dismiss(selected.session_id)

    def action_dismiss(self):
        self.dismiss(None)
