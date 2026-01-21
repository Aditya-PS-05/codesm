"""Session modal for codesm TUI"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Static, Input
from textual.binding import Binding


class SessionListItem(Static):
    """A selectable session item in the session list"""

    can_focus = True

    def __init__(self, session_id: str, title: str, updated_at: str = "", topics: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.session_id = session_id
        self.title = title
        self.updated_at = updated_at
        self.topics = topics
        self._selected = False
        self._pending_delete = False

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
        
        # Format topic badge
        topic_badge = ""
        if self.topics and self.topics.get("primary"):
            primary = self.topics["primary"]
            topic_badge = f" [cyan]#{primary}[/]"
        
        if self._pending_delete:
            return f"  [bold white]{self.title}[/]\n  [white]Press ctrl+d again to confirm delete[/]"
        return f"  [bold]{self.title}[/]{topic_badge}\n  [dim]{date_str}[/]"

    def set_selected(self, selected: bool):
        self._selected = selected
        self.set_class(selected, "-selected")

    def set_pending_delete(self, pending: bool):
        self._pending_delete = pending
        self.set_class(pending, "-pending-delete")
        self.refresh()

    def on_click(self):
        """Handle click on session item"""
        self.screen.select_item(self)


class SessionRenameModal(ModalScreen):
    """Modal for renaming a session"""

    CSS = """
    SessionRenameModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.5);
    }

    #modal-container {
        width: 60;
        height: auto;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }

    #modal-header {
        height: 1;
        width: 100%;
        margin-bottom: 1;
    }

    #modal-title {
        text-style: bold;
        width: 1fr;
    }

    #esc-hint {
        width: auto;
        color: $text-muted;
    }

    #rename-input {
        margin: 1 0;
        border: tall $secondary;
        background: $panel;
    }

    #rename-input:focus {
        border: tall $secondary;
    }

    #footer-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
    ]

    def __init__(self, session_id: str, current_title: str):
        super().__init__()
        self.session_id = session_id
        self.current_title = current_title

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Rename Session", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Input(value=self.current_title, id="rename-input")
            yield Static("[bold]enter[/] save  [bold]esc[/] cancel", id="footer-hint")

    def on_mount(self):
        input_widget = self.query_one("#rename-input", Input)
        input_widget.focus()
        # Select all text
        input_widget.cursor_position = len(self.current_title)

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "rename-input":
            new_title = event.value.strip()
            if new_title:
                self.dismiss({"action": "rename", "session_id": self.session_id, "new_title": new_title})

    def action_dismiss_modal(self):
        self.dismiss(None)


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
        height: 1;
        width: 100%;
        margin-bottom: 1;
    }

    #modal-title {
        text-style: bold;
        width: 1fr;
    }

    #esc-hint {
        width: auto;
        color: $text-muted;
    }

    #session-list {
        height: auto;
        max-height: 20;
        padding: 0;
        scrollbar-gutter: stable;
    }

    #session-list:focus {
        border: none;
    }

    SessionListItem {
        height: 2;
        padding: 0;
    }

    SessionListItem.-selected {
        background: $secondary;
        color: $background;
    }

    SessionListItem.-pending-delete {
        background: $error;
        color: #ffffff;
    }

    SessionListItem.-pending-delete Static {
        color: #ffffff;
    }

    SessionListItem.-selected .session-title {
        color: $background;
        text-style: bold;
    }

    SessionListItem.-selected .session-date {
        color: $background;
    }

    #modal-footer {
        height: 1;
        margin-top: 1;
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
        Binding("ctrl+d", "delete_session", "Delete", show=False),
        Binding("ctrl+r", "rename_session", "Rename", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.selected_index = 0
        self.visible_items: list[SessionListItem] = []
        self.sessions: list[dict] = []
        self._pending_delete_id: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Select session", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield VerticalScroll(id="session-list")
            with Horizontal(id="modal-footer"):
                yield Static("[bold]enter[/] load  [bold]ctrl+r[/] rename  [bold]ctrl+d[/] delete")

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
            container.mount(Static("[dim]No previous sessions[/dim]", classes="group-header"))
            return
        
        for session in self.sessions:
            item = SessionListItem(
                session["id"],
                session.get("title", "Untitled Session"),
                session.get("updated_at", ""),
                session.get("topics"),
            )
            container.mount(item)
            self.visible_items.append(item)
        
        if self.visible_items:
            self.selected_index = 0
            self.visible_items[0].set_selected(True)

    def _clear_pending_delete(self):
        """Clear any pending delete state"""
        if self._pending_delete_id:
            for item in self.visible_items:
                if item.session_id == self._pending_delete_id:
                    item.set_pending_delete(False)
            self._pending_delete_id = None

    def action_move_up(self):
        if not self.visible_items:
            return
        self._clear_pending_delete()
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index - 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible()

    def action_move_down(self):
        if not self.visible_items:
            return
        self._clear_pending_delete()
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index + 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible()

    def action_select(self):
        if self.visible_items:
            self._clear_pending_delete()
            selected = self.visible_items[self.selected_index]
            self.dismiss(selected.session_id)

    def action_delete_session(self):
        """Delete selected session with two-step confirmation"""
        if not self.visible_items:
            return
        
        selected = self.visible_items[self.selected_index]
        
        if self._pending_delete_id == selected.session_id:
            # Second press - confirm delete
            from codesm.session.session import Session
            if Session.delete_by_id(selected.session_id):
                self._pending_delete_id = None
                # Rebuild the list
                self._build_list()
                # Adjust selection if needed
                if self.selected_index >= len(self.visible_items):
                    self.selected_index = max(0, len(self.visible_items) - 1)
                if self.visible_items:
                    self.visible_items[self.selected_index].set_selected(True)
        else:
            # First press - show confirmation
            self._clear_pending_delete()
            self._pending_delete_id = selected.session_id
            selected.set_pending_delete(True)

    def action_rename_session(self):
        """Open rename dialog for selected session"""
        if not self.visible_items:
            return
        
        self._clear_pending_delete()
        selected = self.visible_items[self.selected_index]
        
        # Push rename modal
        self.app.push_screen(
            SessionRenameModal(selected.session_id, selected.title),
            self._on_rename_result
        )

    def _on_rename_result(self, result: dict | None):
        """Handle rename modal result"""
        if result and result.get("action") == "rename":
            from codesm.session.session import Session
            session = Session.load(result["session_id"])
            if session:
                session.set_title(result["new_title"])
                # Rebuild the list to show updated title
                self._build_list()
                # Re-select the renamed item
                for i, item in enumerate(self.visible_items):
                    if item.session_id == result["session_id"]:
                        self.visible_items[self.selected_index].set_selected(False)
                        self.selected_index = i
                        item.set_selected(True)
                        break

    def action_dismiss(self):
        self.dismiss(None)

    def select_item(self, item: SessionListItem):
        """Select a specific item (called from click handler)"""
        if item not in self.visible_items:
            return
        
        self._clear_pending_delete()
        
        # Deselect current
        if self.visible_items and self.selected_index < len(self.visible_items):
            self.visible_items[self.selected_index].set_selected(False)
        
        # Select new
        self.selected_index = self.visible_items.index(item)
        item.set_selected(True)
        
        # Load the session on click
        self.dismiss(item.session_id)

    def on_key(self, event):
        """Handle key events to prevent VerticalScroll from capturing them"""
        if event.key in ("up", "down", "enter"):
            event.stop()
            if event.key == "up":
                self.action_move_up()
            elif event.key == "down":
                self.action_move_down()
            elif event.key == "enter":
                self.action_select()
