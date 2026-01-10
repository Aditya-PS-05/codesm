"""Modal dialogs for codesm TUI"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Static, Input, Label, Button
from textual.binding import Binding
from textual import events

from codesm.permission import PermissionRequest, PermissionResponse


MODELS_BY_PROVIDER = {
    "Recent": [
        {"id": "anthropic/claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "provider": "Anthropic"},
    ],
    "Anthropic": [
        {"id": "anthropic/claude-haiku-3", "name": "Claude Haiku 3", "provider": "Anthropic"},
        {"id": "anthropic/claude-haiku-3.5", "name": "Claude Haiku 3.5", "provider": "Anthropic"},
        {"id": "anthropic/claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "provider": "Anthropic"},
        {"id": "anthropic/claude-opus-4", "name": "Claude Opus 4", "provider": "Anthropic"},
    ],
    "OpenAI": [
        {"id": "openai/gpt-4o", "name": "GPT-4o", "provider": "OpenAI"},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "provider": "OpenAI"},
        {"id": "openai/gpt-4-turbo", "name": "GPT-4 Turbo", "provider": "OpenAI"},
        {"id": "openai/o1", "name": "O1", "provider": "OpenAI"},
        {"id": "openai/o1-mini", "name": "O1 Mini", "provider": "OpenAI"},
    ],
    "Google": [
        {"id": "google/gemini-2.0-flash", "name": "Gemini 2.0 Flash", "provider": "Google"},
        {"id": "google/gemini-2.5-pro", "name": "Gemini 2.5 Pro", "provider": "Google"},
    ],
}

PROVIDERS = {
    "Popular": [
        {"id": "anthropic", "name": "Anthropic", "hint": "Claude Max or API key"},
        {"id": "github-copilot", "name": "GitHub Copilot", "hint": ""},
        {"id": "openai", "name": "OpenAI", "hint": ""},
        {"id": "google", "name": "Google", "hint": ""},
        {"id": "openrouter", "name": "OpenRouter", "hint": ""},
    ],
    "Other": [
        {"id": "ollama", "name": "Ollama", "hint": "Local models"},
        {"id": "groq", "name": "Groq", "hint": "Fast inference"},
        {"id": "together", "name": "Together AI", "hint": ""},
        {"id": "fireworks", "name": "Fireworks AI", "hint": ""},
        {"id": "deepseek", "name": "DeepSeek", "hint": ""},
    ],
}


class ModalListItem(Static):
    """A selectable item in a modal list"""

    def __init__(self, item_id: str, label: str, hint: str = "", **kwargs):
        super().__init__(**kwargs)
        self.item_id = item_id
        self.label = label
        self.hint = hint
        self._selected = False

    def compose(self) -> ComposeResult:
        if self.hint:
            yield Static(f"  {self.label} [dim]{self.hint}[/]", classes="item-content")
        else:
            yield Static(f"  {self.label}", classes="item-content")

    def set_selected(self, selected: bool):
        self._selected = selected
        self.set_class(selected, "-selected")

    def on_click(self):
        self.screen.dismiss(self.item_id)


class ModelSelectModal(ModalScreen):
    """Modal for selecting a model"""

    CSS = """
    ModelSelectModal {
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

    #search-input {
        margin-bottom: 1;
        border: tall $secondary;
        background: $panel;
    }

    #search-input:focus {
        border: tall $secondary;
    }

    #model-list {
        height: auto;
        max-height: 20;
        padding: 0;
    }

    .group-header {
        color: $secondary;
        text-style: bold;
        padding: 1 0 0 0;
    }

    ModalListItem {
        height: 1;
        padding: 0;
    }

    ModalListItem.-selected {
        background: $secondary;
        color: $background;
    }

    ModalListItem.-selected .item-content {
        color: $background;
        text-style: bold;
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
        Binding("ctrl+a", "connect_provider", "Connect Provider", show=False),
    ]

    def __init__(self, current_model: str = ""):
        super().__init__()
        self.current_model = current_model
        self.selected_index = 0
        self.visible_items: list[ModalListItem] = []
        self.search_query = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Select model", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Input(placeholder="Search", id="search-input")
            yield VerticalScroll(id="model-list")
            with Horizontal(id="modal-footer"):
                yield Static("[bold]Connect provider[/] ctrl+a")
                yield Static("[bold]Favorite[/] ctrl+f")

    def on_mount(self):
        self._build_list()
        self.query_one("#search-input", Input).focus()

    def _build_list(self, filter_text: str = ""):
        container = self.query_one("#model-list", VerticalScroll)
        container.remove_children()
        self.visible_items = []

        filter_lower = filter_text.lower()

        for group_name, models in MODELS_BY_PROVIDER.items():
            filtered_models = [
                m for m in models
                if filter_lower in m["name"].lower() or filter_lower in m.get("provider", "").lower()
            ] if filter_text else models

            if not filtered_models:
                continue

            container.mount(Static(group_name, classes="group-header"))

            for model in filtered_models:
                hint = model.get("provider", "")
                item = ModalListItem(model["id"], model["name"], hint)
                container.mount(item)
                self.visible_items.append(item)

        if self.visible_items:
            self.selected_index = 0
            self.visible_items[0].set_selected(True)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search-input":
            self.search_query = event.value
            self._build_list(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "search-input":
            self.action_select()

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
            self.dismiss(selected.item_id)

    def action_dismiss(self):
        self.dismiss(None)

    def action_connect_provider(self):
        self.dismiss("__connect_provider__")

    def on_key(self, event: events.Key):
        if event.key == "ctrl+a":
            event.prevent_default()
            event.stop()
            self.action_connect_provider()


class ProviderConnectModal(ModalScreen):
    """Modal for connecting a provider"""

    CSS = """
    ProviderConnectModal {
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

    #search-input {
        margin-bottom: 1;
        border: tall $secondary;
        background: $panel;
    }

    #search-input:focus {
        border: tall $secondary;
    }

    #provider-list {
        height: auto;
        max-height: 20;
        padding: 0;
    }

    .group-header {
        color: $secondary;
        text-style: bold;
        padding: 1 0 0 0;
    }

    ModalListItem {
        height: 1;
        padding: 0;
    }

    ModalListItem.-selected {
        background: $secondary;
        color: $background;
    }

    ModalListItem.-selected .item-content {
        color: $background;
        text-style: bold;
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
        self.visible_items: list[ModalListItem] = []
        self.search_query = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Connect a provider", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Input(placeholder="Search", id="search-input")
            yield VerticalScroll(id="provider-list")

    def on_mount(self):
        self._build_list()
        self.query_one("#search-input", Input).focus()

    def _build_list(self, filter_text: str = ""):
        container = self.query_one("#provider-list", VerticalScroll)
        container.remove_children()
        self.visible_items = []

        filter_lower = filter_text.lower()

        for group_name, providers in PROVIDERS.items():
            filtered_providers = [
                p for p in providers
                if filter_lower in p["name"].lower() or filter_lower in p.get("hint", "").lower()
            ] if filter_text else providers

            if not filtered_providers:
                continue

            container.mount(Static(group_name, classes="group-header"))

            for provider in filtered_providers:
                hint = provider.get("hint", "")
                item = ModalListItem(provider["id"], provider["name"], hint)
                container.mount(item)
                self.visible_items.append(item)

        if self.visible_items:
            self.selected_index = 0
            self.visible_items[0].set_selected(True)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search-input":
            self.search_query = event.value
            self._build_list(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "search-input":
            self.action_select()

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
            self.dismiss(selected.item_id)

    def action_dismiss(self):
        self.dismiss(None)


class ClickableURL(Static):
    """A clickable URL that copies to clipboard when clicked"""

    DEFAULT_CSS = """
    ClickableURL {
        padding: 1;
        background: $panel;
        color: $secondary;
        margin: 1 0;
    }

    ClickableURL:hover {
        background: $primary;
    }
    """

    def __init__(self, url: str, **kwargs):
        super().__init__(url, **kwargs)
        self.url = url

    def on_click(self):
        self._copy_to_clipboard()

    def _copy_to_clipboard(self):
        try:
            import subprocess
            process = subprocess.Popen(
                ['xclip', '-selection', 'clipboard'],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            process.communicate(self.url.encode())
            self.app.notify("URL copied to clipboard!")
        except FileNotFoundError:
            try:
                import subprocess
                process = subprocess.Popen(
                    ['xsel', '--clipboard', '--input'],
                    stdin=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                process.communicate(self.url.encode())
                self.app.notify("URL copied to clipboard!")
            except FileNotFoundError:
                try:
                    import subprocess
                    process = subprocess.Popen(
                        ['pbcopy'],
                        stdin=subprocess.PIPE,
                        stderr=subprocess.DEVNULL
                    )
                    process.communicate(self.url.encode())
                    self.app.notify("URL copied to clipboard!")
                except FileNotFoundError:
                    self.app.notify("Could not copy - install xclip or xsel")


ANTHROPIC_AUTH_METHODS = [
    {"id": "manual-api-key", "name": "Enter API Key", "hint": "Recommended"},
    {"id": "create-api-key", "name": "Create API Key in Browser", "hint": "Opens console.anthropic.com"},
]


class AuthMethodModal(ModalScreen):
    """Modal for selecting authentication method"""

    CSS = """
    AuthMethodModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.5);
    }

    #modal-container {
        width: 60;
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

    #search-input {
        margin-bottom: 1;
        border: tall $secondary;
        background: $panel;
    }

    #search-input:focus {
        border: tall $secondary;
    }

    #method-list {
        height: auto;
        max-height: 10;
        padding: 0;
    }

    ModalListItem {
        height: 1;
        padding: 0;
    }

    ModalListItem.-selected {
        background: $secondary;
        color: $background;
    }

    ModalListItem.-selected .item-content {
        color: $background;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
    ]

    def __init__(self, provider: str = "anthropic"):
        super().__init__()
        self.provider = provider
        self.selected_index = 0
        self.visible_items: list[ModalListItem] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Select auth method", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Input(placeholder="Search", id="search-input")
            yield VerticalScroll(id="method-list")

    def on_mount(self):
        self._build_list()
        self.query_one("#search-input", Input).focus()

    def _build_list(self, filter_text: str = ""):
        container = self.query_one("#method-list", VerticalScroll)
        container.remove_children()
        self.visible_items = []

        filter_lower = filter_text.lower()

        methods = ANTHROPIC_AUTH_METHODS
        filtered = [
            m for m in methods
            if filter_lower in m["name"].lower()
        ] if filter_text else methods

        for method in filtered:
            item = ModalListItem(method["id"], method["name"], method.get("hint", ""))
            container.mount(item)
            self.visible_items.append(item)

        if self.visible_items:
            self.selected_index = 0
            self.visible_items[0].set_selected(True)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search-input":
            self._build_list(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "search-input":
            self.action_select()

    def action_move_up(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index - 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)

    def action_move_down(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index + 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)

    def action_select(self):
        if self.visible_items:
            selected = self.visible_items[self.selected_index]
            self.dismiss(selected.item_id)

    def action_dismiss_modal(self):
        self.dismiss(None)


class ClaudeOAuthModal(ModalScreen):
    """Modal for Claude Pro/Max OAuth authentication"""

    CSS = """
    ClaudeOAuthModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.5);
    }

    #modal-container {
        width: 70;
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
        color: $secondary;
        width: 1fr;
    }

    #esc-hint {
        width: auto;
        color: $text-muted;
    }

    #instructions {
        color: $text-muted;
    }

    #oauth-url {
        padding: 1;
        background: $panel;
        color: $secondary;
        margin: 1 0;
    }

    #code-input {
        margin: 1 0;
        border: tall $secondary;
        background: $panel;
    }

    #code-input:focus {
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

    def __init__(self):
        super().__init__()
        self._generate_oauth_url()

    def _generate_oauth_url(self):
        import secrets
        import hashlib
        import base64

        # In opencode, state = code_verifier (they use the same value)
        self.code_verifier = secrets.token_urlsafe(43)  # ~32 bytes = 43 chars in base64url
        self.state = self.code_verifier  # Use same value like opencode does

        code_challenge_bytes = hashlib.sha256(self.code_verifier.encode()).digest()
        self.code_challenge = base64.urlsafe_b64encode(code_challenge_bytes).rstrip(b'=').decode()

        self.client_id = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
        self.redirect_uri = "https://console.anthropic.com/oauth/code/callback"

        # Use console.anthropic.com to get a token that can create API keys
        # (claude.ai tokens are restricted to whitelisted apps like Claude Code)
        self.oauth_url = (
            f"https://console.anthropic.com/oauth/authorize?"
            f"code=true&client_id={self.client_id}&"
            f"response_type=code&"
            f"redirect_uri={self.redirect_uri}&"
            f"scope=org:create_api_key+user:profile+user:inference&"
            f"code_challenge={self.code_challenge}&"
            f"code_challenge_method=S256&"
            f"state={self.state}"
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Connect Anthropic", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Static("Click the URL to copy, then paste in your browser:", id="instructions")
            yield ClickableURL(self.oauth_url, id="oauth-url")
            yield Input(placeholder="Paste authorization code here", id="code-input")
            yield Static("[bold]enter[/] submit  [bold]click URL[/] to copy", id="footer-hint")

    def on_mount(self):
        self.query_one("#code-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "code-input":
            code = event.value.strip()
            if code:
                self.dismiss({
                    "code": code,
                    "state": self.state,
                    "code_verifier": self.code_verifier,
                })

    def action_dismiss_modal(self):
        self.dismiss(None)


class ThemeSelectModal(ModalScreen):
    """Modal for selecting a theme"""

    CSS = """
    ThemeSelectModal {
        align: center middle;
        background: transparent;
    }

    #modal-container {
        width: 50;
        height: auto;
        max-height: 70%;
        background: $surface;
        border: tall $secondary;
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

    #search-input {
        margin-bottom: 1;
        border: tall $secondary;
        background: $panel;
    }

    #search-input:focus {
        border: tall $secondary;
    }

    #theme-list {
        height: auto;
        max-height: 20;
        padding: 0;
    }

    .group-header {
        color: $secondary;
        text-style: bold;
        padding: 1 0 0 0;
    }

    ModalListItem {
        height: 1;
        padding: 0;
    }

    ModalListItem.-selected {
        background: $secondary;
        color: $background;
    }

    ModalListItem.-selected .item-content {
        color: $background;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
    ]

    def __init__(self, current_theme: str = "dark"):
        super().__init__()
        self.current_theme = current_theme
        self.original_theme = current_theme  # Store original to restore on cancel
        self.selected_index = 0
        self.visible_items: list[ModalListItem] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Select theme", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Input(placeholder="Search themes...", id="search-input")
            yield VerticalScroll(id="theme-list")

    def on_mount(self):
        self._build_list()
        self.query_one("#search-input", Input).focus()

    def _preview_theme(self, theme_name: str):
        """Apply theme preview without saving"""
        self.app.theme = f"codesm-{theme_name}"

    def _build_list(self, filter_text: str = ""):
        from .themes import THEME_DEFINITIONS
        
        container = self.query_one("#theme-list", VerticalScroll)
        container.remove_children()
        self.visible_items = []

        filter_lower = filter_text.lower()

        for category, themes in THEME_DEFINITIONS.items():
            filtered_themes = [
                t for t in themes
                if filter_lower in t["display"].lower() or filter_lower in t["name"].lower()
            ] if filter_text else themes

            if not filtered_themes:
                continue

            container.mount(Static(category, classes="group-header"))

            for theme in filtered_themes:
                is_current = theme["name"] == self.current_theme
                hint = "✓ current" if is_current else ""
                item = ModalListItem(theme["name"], theme["display"], hint)
                container.mount(item)
                self.visible_items.append(item)

        if self.visible_items:
            self.selected_index = 0
            self.visible_items[0].set_selected(True)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search-input":
            self._build_list(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "search-input":
            self.action_select()

    def action_move_up(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index - 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible()
        # Preview the theme
        self._preview_theme(self.visible_items[self.selected_index].item_id)

    def action_move_down(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index + 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible()
        # Preview the theme
        self._preview_theme(self.visible_items[self.selected_index].item_id)

    def action_select(self):
        if self.visible_items:
            selected = self.visible_items[self.selected_index]
            self.dismiss(selected.item_id)

    def action_dismiss_modal(self):
        # Restore original theme on cancel
        self._preview_theme(self.original_theme)
        self.dismiss(None)


class APIKeyInputModal(ModalScreen):
    """Modal for manually entering API key"""

    CSS = """
    APIKeyInputModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.5);
    }

    #modal-container {
        width: 70;
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

    #instructions {
        color: $text-muted;
    }

    #api-key-input {
        margin: 1 0;
        border: tall $secondary;
        background: $panel;
    }

    #api-key-input:focus {
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

    def __init__(self, provider: str = "anthropic"):
        super().__init__()
        self.provider = provider

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static(f"Enter {self.provider.title()} API Key", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Static("Paste your API key below:", id="instructions")
            yield Input(placeholder="sk-ant-...", id="api-key-input", password=True)
            yield Static("[bold]enter[/] submit", id="footer-hint")

    def on_mount(self):
        self.query_one("#api-key-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "api-key-input":
            api_key = event.value.strip()
            if api_key:
                self.dismiss({"api_key": api_key, "provider": self.provider})

    def action_dismiss_modal(self):
        self.dismiss(None)


class PermissionModal(ModalScreen):
    """Modal for requesting user permission before executing sensitive commands."""

    CSS = """
    PermissionModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #permission-container {
        width: 80;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: tall $warning;
        padding: 1 2;
    }

    #permission-header {
        height: 1;
        width: 100%;
        margin-bottom: 1;
    }

    #permission-title {
        text-style: bold;
        color: $warning;
        width: 1fr;
    }

    #permission-type {
        width: auto;
        color: $text-muted;
    }

    #permission-description {
        margin: 1 0;
        padding: 1;
        background: $panel;
        height: auto;
        max-height: 30;
        overflow-y: auto;
    }

    #command-display {
        margin: 1 0;
        padding: 1;
        background: $background;
        color: $text;
    }

    #button-row {
        height: 3;
        margin-top: 1;
        align: center middle;
    }

    #button-row Button {
        margin: 0 1;
        min-width: 16;
    }

    #hint-row {
        height: 1;
        margin-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("y", "allow_once", "Allow Once", show=True),
        Binding("a", "allow_always", "Allow Always", show=True),
        Binding("n", "deny", "Deny", show=True),
        Binding("escape", "deny", "Deny", show=False),
    ]

    def __init__(self, request: PermissionRequest):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        from .chat import styled_markdown

        with Vertical(id="permission-container"):
            with Horizontal(id="permission-header"):
                yield Static(f"⚠️  {self.request.title}", id="permission-title")
                yield Static(f"[{self.request.type}]", id="permission-type")

            # Render description as markdown for syntax highlighting
            yield Static(styled_markdown(self.request.description), id="permission-description")
            yield Static(f"Command: {self.request.command}", id="command-display")
            
            with Horizontal(id="button-row"):
                yield Button("Allow Once (y)", id="btn-allow-once", variant="success")
                yield Button("Allow Always (a)", id="btn-allow-always", variant="primary")
                yield Button("Deny (n)", id="btn-deny", variant="error")
            
            yield Static(
                "[y] allow once  [a] allow always  [n/esc] deny",
                id="hint-row",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-allow-once":
            self.action_allow_once()
        elif event.button.id == "btn-allow-always":
            self.action_allow_always()
        elif event.button.id == "btn-deny":
            self.action_deny()

    def action_allow_once(self):
        self.dismiss(PermissionResponse.ALLOW_ONCE)

    def action_allow_always(self):
        self.dismiss(PermissionResponse.ALLOW_ALWAYS)

    def action_deny(self):
        self.dismiss(PermissionResponse.DENY)
