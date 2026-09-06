"""Backend controls using the existing anchored popover styles."""

from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from codesm.agent.backends import BACKENDS, availability
from .command_palette import CommandPaletteModal
from .modals import APIKeyInputModal


class BackendSelectModal(CommandPaletteModal):
    CSS = CommandPaletteModal.CSS + "\nBackendSelectModal { align: left bottom; padding: 0; background: transparent; }"

    def __init__(self, current="native"):
        self.current = current
        choices = [{"cmd": key, "desc": name + (" · current" if key == current else "") +
                    (" · " + problem if (problem := availability(key)) else "")}
                   for key, name in BACKENDS.items()]
        super().__init__(initial_text="", commands=choices)

    def on_mount(self, event):
        event.prevent_default()
        super().on_mount()
        self.selected_index = list(BACKENDS).index(self.current)
        self._update_selection()
        self.query_one("#palette-input", Input).placeholder = "Search backends..."
        self.query_one("#palette-hint", Static).update("↑↓ select · enter switch backend · esc close")

    def on_input_changed(self, event: Input.Changed):
        event.prevent_default()
        self.selected_index = list(BACKENDS).index(self.current) if not event.value else 0
        self._refresh_items(event.value)


class BackendInputModal(APIKeyInputModal):
    CSS = APIKeyInputModal.CSS + "\nBackendInputModal { align: left bottom; padding: 0; background: transparent; }"

    def __init__(self, title, description, value="", secret=False):
        super().__init__()
        self.title_text, self.description, self.value, self.secret = title, description, value, secret

    def compose(self):
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static(self.title_text, id="modal-title", markup=False)
                yield Static("esc", id="esc-hint")
            yield Static(self.description, id="instructions", markup=False)
            yield Input(value=self.value, id="api-key-input", password=self.secret)
            yield Static("enter submit · esc cancel", id="footer-hint")

    async def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        event.prevent_default()
        if event.value.strip():
            self.dismiss(event.value.strip())
