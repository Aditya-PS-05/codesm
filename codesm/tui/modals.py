"""Modal dialogs for codesm TUI"""

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Static, Input, Label, Button
from textual.binding import Binding

from codesm.permission import PermissionRequest, PermissionResponse
from codesm.config.config import Config
from codesm.provider.catalog import canonical_provider, discover_models, model_catalog, provider_specs
from codesm.provider.router import ModelRouter


class ModalListItem(Static):
    """A selectable item in a modal list"""

    def __init__(self, item_id: str, label: str, hint: str = "", **kwargs):
        super().__init__(**kwargs)
        self.item_id = item_id
        self.label = label
        self.hint = hint
        self._selected = False

    def render(self) -> Text:
        text = Text("› " if self._selected else "  ", no_wrap=True, overflow="ellipsis")
        text.append(self.label, style="bold" if self._selected else "")
        if self.hint:
            text.append(f"  {self.hint}", style="dim")
        return text

    def set_selected(self, selected: bool):
        if selected == self._selected:
            return
        self._selected = selected
        self.set_class(selected, "-selected")
        self.refresh()

    def on_click(self):
        self.screen.dismiss(self.item_id)


class ModelSelectModal(ModalScreen):
    """Modal for selecting a model"""

    CSS = """
    ModelSelectModal {
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #modal-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: 24;
        max-height: 100%;
        overflow: hidden;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
    }

    #search-input:focus {
        border: none;
    }

    #model-list {
        height: 1fr;
        min-height: 1;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        padding: 0;
    }

    #model-status {
        height: 3;
        overflow-y: auto;
        margin-top: 1;
        color: $text-muted;
    }

    .group-header {
        color: $text-muted;
        text-style: bold;
        padding: 1 0 0 0;
    }

    ModalListItem {
        height: 1;
        padding: 0;
    }

    ModalListItem.-selected {
        background: $boost;
        color: $text;
    }

    ModalListItem.-selected:ansi {
        text-style: reverse;
    }

    #modal-footer {
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }

    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("enter", "select", "Select", show=False),
        Binding("ctrl+a", "connect_provider", "Connect Provider", show=False, priority=True),
        Binding("ctrl+r", "refresh_models", "Refresh models", show=False, priority=True),
    ]

    def __init__(self, current_model: str = "", config: Config | None = None, provider: str | None = None):
        super().__init__()
        self.current_model = current_model
        self.config = config
        self.provider = canonical_provider(provider) if provider else None
        self.selected_index = 0
        self.visible_items: list[ModalListItem] = []
        self.search_query = ""
        self.models: list[dict] = []
        self._items: list[tuple[dict, ModalListItem]] = []
        self._headers: list[tuple[Static, list[ModalListItem]]] = []
        self._exact_item: ModalListItem | None = None
        self._empty_message: Static | None = None
        self._refresh_worker = None

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Select model", id="modal-title")
                yield Static("esc", id="esc-hint")
            yield Input(placeholder="Search or enter provider/model", id="search-input")
            yield VerticalScroll(id="model-list")
            yield Static("", id="model-status", markup=False)
            yield Static("ctrl+a connect · ctrl+r refresh · enter select", id="modal-footer")

    async def on_mount(self):
        if self.config is None:
            agent = getattr(self.app, "agent", None)
            self.config = agent.config if agent else Config.load(directory=getattr(self.app, "directory", None))
        self.models = model_catalog(self.config)
        self._include_current_model()
        await self._build_list()
        self.query_one("#search-input", Input).focus()
        self.action_refresh_models()

    def _include_current_model(self):
        if not self.current_model:
            return
        provider, model = ModelRouter.resolve_model(self.current_model)
        model_id = f"{provider}/{model}"
        self.current_model = model_id
        if not any(entry["id"] == model_id for entry in self.models):
            spec = provider_specs(self.config).get(provider)
            self.models.insert(0, {"id": model_id, "name": model,
                                   "provider": spec.name if spec else provider, "source": "current"})

    def _exact_model_entry(self) -> dict | None:
        query = self.search_query.strip()
        if "/" not in query:
            return None
        try:
            provider, model = ModelRouter.resolve_model(query)
        except ValueError:
            return None
        spec = provider_specs(self.config).get(provider)
        if spec and (not self.provider or provider == self.provider):
            return {"id": f"{provider}/{model}", "name": f"Use {provider}/{model}", "provider": "Exact model ID"}
        return None

    def _selected_model_id(self) -> str:
        return self.visible_items[self.selected_index].item_id if self.visible_items else self.current_model

    async def _build_list(self):
        """Replace a refreshed catalog atomically; filtering reuses these rows."""
        container = self.query_one("#model-list", VerticalScroll)
        async with container.batch():
            previous_id = self._selected_model_id()
            offset = (self.visible_items[self.selected_index].virtual_region.y - container.scroll_y
                      if self.visible_items else None)
            exact_item = ModalListItem("", "", "Exact model ID")
            exact_item.display = False
            empty_message = Static("No matches. Enter provider/model to use an exact ID.", markup=False)
            empty_message.display = False
            widgets = [exact_item]
            items = []
            headers = []
            group = None
            for entry in self.models:
                if self.provider and not entry["id"].startswith(self.provider + "/"):
                    continue
                if entry["provider"] != group:
                    group = entry["provider"]
                    header = Static(group, classes="group-header", markup=False)
                    headers.append((header, []))
                    widgets.append(header)
                item = ModalListItem(entry["id"], entry["name"], entry["id"])
                items.append((entry, item))
                headers[-1][1].append(item)
                widgets.append(item)
            widgets.append(empty_message)
            await container.remove_children()
            await container.mount(*widgets)
            # Navigation/search may have continued while the widgets mounted.
            selected = self._selected_model_id()
            self._items, self._headers = items, headers
            self._exact_item, self._empty_message = exact_item, empty_message
            self._filter_list(selected, offset if selected == previous_id else None)

    def _filter_list(self, selected: str | None = None, offset: float | None = None):
        if self._exact_item is None:
            return
        query = self.search_query.strip().lower()
        exact = self._exact_model_entry()
        selected = selected or (exact["id"] if exact else self._selected_model_id())
        with self.app.batch_update():
            visible = []
            self._exact_item.display = exact is not None
            self._exact_item.set_selected(False)
            if exact:
                self._exact_item.item_id = exact["id"]
                self._exact_item.label = exact["name"]
                self._exact_item.refresh()
                visible.append(self._exact_item)
            for entry, item in self._items:
                item.set_selected(False)
                matches = (not exact or entry["id"] != exact["id"]) and any(
                    query in entry.get(key, "").lower() for key in ("id", "name", "provider"))
                item.display = matches
                if matches:
                    visible.append(item)
            for header, group_items in self._headers:
                header.display = any(item.display for item in group_items)
            self.visible_items = visible
            self.selected_index = next((i for i, item in enumerate(visible) if item.item_id == selected), 0)
            self._empty_message.display = not visible
            if visible:
                item = visible[self.selected_index]
                item.set_selected(True)
                self.call_after_refresh(self._scroll_selection, item.item_id,
                                        offset if item.item_id == selected else None)

    def _scroll_selection(self, selected_id: str | None = None, offset: float | None = None):
        if not self.is_mounted or not self.visible_items:
            return
        item = self.visible_items[self.selected_index]
        if selected_id is not None and item.item_id != selected_id:
            return
        container = self.query_one("#model-list", VerticalScroll)
        if offset is not None:
            container.scroll_to(y=item.virtual_region.y - offset, animate=False, immediate=True)
        container.scroll_to_widget(item, animate=False, immediate=True)

    def action_refresh_models(self):
        if self._refresh_worker and not self._refresh_worker.is_finished:
            return
        self._refresh_worker = self.run_worker(self._refresh_models(), group="model-discovery", exclusive=True)

    async def _refresh_models(self):
        status = self.query_one("#model-status", Static)
        status.update("Refreshing available models…")
        try:
            config = self.config.model_copy(update={"model": self.current_model}) if self.current_model else self.config
            entries, errors = await discover_models(config, self.provider)
        except Exception as exc:
            status.update(f"Refresh failed ({type(exc).__name__}); showing cached models.")
            return
        ids = {entry["id"] for entry in entries}
        entries.extend(entry for entry in self.models
                       if entry["id"].split("/", 1)[0] in errors and entry["id"] not in ids)
        previous = self.models
        self.models = entries
        self._include_current_model()
        if self.models != previous:
            await self._build_list()
        if errors:
            failed = ", ".join(f"{provider} ({error})" for provider, error in errors.items())
            status.update(f"Refresh failed: {failed}. Cached models remain available.")
        else:
            status.update("Models refreshed. Exact provider/model IDs are also accepted.")

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search-input":
            self.search_query = event.value
            self._filter_list()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "search-input":
            self.action_select()

    def _move_selection(self, delta: int):
        if not self.visible_items:
            return
        index = max(0, min(self.selected_index + delta, len(self.visible_items) - 1))
        if index == self.selected_index:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = index
        self.visible_items[index].set_selected(True)
        self._scroll_selection()
        self.call_after_refresh(self._scroll_selection, self.visible_items[index].item_id)

    def action_move_up(self):
        self._move_selection(-1)

    def action_move_down(self):
        self._move_selection(1)

    def action_select(self):
        if self.visible_items:
            selected = self.visible_items[self.selected_index]
            self.dismiss(selected.item_id)

    def action_dismiss(self):
        self.dismiss(None)

    def action_connect_provider(self):
        self.dismiss("__connect_provider__")


class ProviderConnectModal(ModalScreen):
    """Modal for connecting a provider"""

    CSS = """
    ProviderConnectModal {
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #modal-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: 24;
        max-height: 100%;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
    }

    #search-input:focus {
        border: none;
    }

    #provider-list {
        height: 1fr;
        min-height: 1;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        padding: 0;
    }

    .group-header {
        color: $text-muted;
        text-style: bold;
        padding: 1 0 0 0;
    }

    ModalListItem {
        height: 1;
        padding: 0;
    }

    ModalListItem.-selected {
        background: $boost;
        color: $text;
    }

    ModalListItem.-selected:ansi {
        text-style: reverse;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("enter", "select", "Select", show=False),
    ]

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config
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

    async def on_mount(self):
        if self.config is None:
            agent = getattr(self.app, "agent", None)
            self.config = agent.config if agent else Config.load(directory=getattr(self.app, "directory", None))
        await self._build_list()
        self.query_one("#search-input", Input).focus()

    async def _build_list(self, filter_text: str = ""):
        container = self.query_one("#provider-list", VerticalScroll)
        await container.remove_children()
        self.visible_items = []
        query = filter_text.strip().lower()
        for provider, spec in provider_specs(self.config).items():
            if query and query not in f"{provider} {spec.name}".lower():
                continue
            hint = "Local models" if provider == "ollama" else "API key"
            self.visible_items.append(ModalListItem(provider, spec.name, hint))
        self.selected_index = 0
        if self.visible_items:
            await container.mount(*self.visible_items)
            self.visible_items[0].set_selected(True)

    async def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search-input":
            self.search_query = event.value
            await self._build_list(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "search-input":
            self.action_select()

    def action_move_up(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index - 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible(animate=False, immediate=True)

    def action_move_down(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index + 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible(animate=False, immediate=True)

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
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #modal-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
    }

    #search-input:focus {
        border: none;
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
        background: $boost;
        color: $text;
    }

    ModalListItem.-selected:ansi {
        text-style: reverse;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
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
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #modal-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        overflow-y: auto;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
    }

    #code-input:focus {
        border: none;
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
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #modal-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: 24;
        max-height: 100%;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
    }

    #search-input:focus {
        border: none;
    }

    #theme-list {
        height: 1fr;
        min-height: 1;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        padding: 0;
    }

    .group-header {
        color: $text-muted;
        text-style: bold;
        padding: 1 0 0 0;
    }

    ModalListItem {
        height: 1;
        padding: 0;
    }

    ModalListItem.-selected {
        background: $boost;
        color: $text;
    }

    ModalListItem.-selected:ansi {
        text-style: reverse;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("enter", "select", "Select", show=False),
    ]

    def __init__(self, current_theme: str = "terminal"):
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
        self.original_theme = self.app.theme
        self._build_list()
        self.query_one("#search-input", Input).focus()

    def _preview_theme(self, theme_name: str):
        """Apply theme preview without saving"""
        from .themes import THEMES

        self.app.theme = THEMES[theme_name].name

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
        self.visible_items[self.selected_index].scroll_visible(animate=False, immediate=True)
        # Preview the theme
        self._preview_theme(self.visible_items[self.selected_index].item_id)

    def action_move_down(self):
        if not self.visible_items:
            return
        self.visible_items[self.selected_index].set_selected(False)
        self.selected_index = (self.selected_index + 1) % len(self.visible_items)
        self.visible_items[self.selected_index].set_selected(True)
        self.visible_items[self.selected_index].scroll_visible(animate=False, immediate=True)
        # Preview the theme
        self._preview_theme(self.visible_items[self.selected_index].item_id)

    def action_select(self):
        if self.visible_items:
            selected = self.visible_items[self.selected_index]
            self.dismiss(selected.item_id)

    def action_dismiss_modal(self):
        # Restore original theme on cancel
        self.app.theme = self.original_theme
        self.dismiss(None)


class APIKeyInputModal(ModalScreen):
    """Modal for manually entering API key"""

    CSS = """
    APIKeyInputModal {
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #modal-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        overflow-y: auto;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
    }

    #api-key-input:focus {
        border: none;
    }

    #footer-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
    ]

    def __init__(self, provider: str = "anthropic", provider_name: str | None = None):
        super().__init__()
        self.provider = provider
        self.provider_name = provider_name or provider

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static(f"Enter {self.provider_name} API Key", id="modal-title", markup=False)
                yield Static("esc", id="esc-hint")
            yield Static("Paste your API key below:", id="instructions")
            yield Input(placeholder="API key", id="api-key-input", password=True)
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
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #permission-container {
        margin: 0 2 4 2;
        width: 80;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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
        margin: 0 0 1 0;
        background: transparent;
        height: auto;
        max-height: 30vh;
        overflow-y: auto;
    }

    #command-display {
        height: auto;
        max-height: 20vh;
        overflow-y: auto;
        padding: 0 1;
        background: $panel;
        color: $text;
    }

    #button-row {
        height: 1;
        margin-top: 1;
    }

    #button-row Button {
        width: 1fr;
        min-width: 0;
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
        color: $text;
    }

    #button-row Button:focus {
        background: $boost;
        text-style: bold underline;
    }

    #button-row Button:focus:ansi {
        text-style: bold reverse;
    }

    #hint-row {
        height: auto;
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
                yield Static(self.request.title, id="permission-title", markup=False)
                yield Static(self.request.type, id="permission-type", markup=False)

            # Render description as markdown for syntax highlighting
            yield Static(styled_markdown(self.request.description), id="permission-description")
            yield Static(f"Command: {self.request.command}", id="command-display", markup=False)
            
            with Horizontal(id="button-row"):
                yield Button("y Once", id="btn-allow-once", variant="success")
                yield Button("a Always", id="btn-allow-always", variant="primary")
                yield Button("n Deny", id="btn-deny", variant="error")
            
            yield Static(
                "y allow once · a allow always · n/esc deny",
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


class ModeSelectModal(ModalScreen):
    """Modal for selecting agent mode (smart/rush)"""

    CSS = """
    ModeSelectModal {
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #modal-container {
        margin: 0 2 4 2;
        width: 76;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        overflow-y: auto;
        background: $surface;
        border: round $panel;
        padding: 0 1;
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

    #mode-list {
        height: auto;
        padding: 0;
    }

    .mode-item {
        height: auto;
        min-height: 2;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    .mode-item.-selected {
        background: $boost;
        color: $text;
    }

    .mode-item.-selected:ansi {
        text-style: reverse;
    }

    .mode-name {
        text-style: bold;
    }

    .mode-description {
        color: $text-muted;
    }

    .mode-item.-selected .mode-description {
        color: $text;
    }

    #mode-footer {
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("enter", "select", "Select", show=False),
        Binding("s", "select_smart", "Smart", show=False),
        Binding("r", "select_rush", "Rush", show=False),
    ]

    def __init__(self, current_mode: str = "smart"):
        super().__init__()
        self.current_mode = current_mode
        self.selected_index = 0 if current_mode == "smart" else 1
        self.modes = ["smart", "rush"]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            with Horizontal(id="modal-header"):
                yield Static("Select Mode", id="modal-title")
                yield Static("esc", id="esc-hint")
            
            with Vertical(id="mode-list"):
                # Smart mode
                with Vertical(id="mode-smart", classes="mode-item"):
                    yield Static("[bold]Smart[/bold]", classes="mode-name")
                    yield Static("Full capability, best for complex tasks", classes="mode-description")
                
                # Rush mode
                with Vertical(id="mode-rush", classes="mode-item"):
                    yield Static("[bold]Rush[/bold]", classes="mode-name")
                    yield Static("Faster model for simple, well-defined tasks", classes="mode-description")
            
            yield Static("s smart · r rush · enter select", id="mode-footer")

    def on_mount(self):
        self._update_selection()

    def _update_selection(self):
        smart = self.query_one("#mode-smart")
        rush = self.query_one("#mode-rush")
        
        if self.selected_index == 0:
            smart.add_class("-selected")
            rush.remove_class("-selected")
        else:
            smart.remove_class("-selected")
            rush.add_class("-selected")

    def action_move_up(self):
        self.selected_index = 0
        self._update_selection()

    def action_move_down(self):
        self.selected_index = 1
        self._update_selection()

    def action_select(self):
        self.dismiss(self.modes[self.selected_index])

    def action_select_smart(self):
        self.dismiss("smart")

    def action_select_rush(self):
        self.dismiss("rush")

    def action_dismiss_modal(self):
        self.dismiss(None)

    def on_click(self, event) -> None:
        # Check if click was on a mode item
        try:
            smart = self.query_one("#mode-smart")
            rush = self.query_one("#mode-rush")
            
            if smart in event.widget.ancestors_with_self:
                self.dismiss("smart")
            elif rush in event.widget.ancestors_with_self:
                self.dismiss("rush")
        except Exception:
            pass


class DiffPreviewResponse(str):
    """Response from diff preview modal"""
    APPLY = "apply"
    SKIP = "skip"
    CANCEL = "cancel"


class DiffPreviewModal(ModalScreen):
    """Modal for previewing file diffs before applying edits."""

    CSS = """
    DiffPreviewModal {
        align: left bottom;
        padding: 0;
        background: transparent;
    }

    #diff-container {
        margin: 0 2 4 2;
        width: 100;
        max-width: 100%;
        height: 24;
        max-height: 100%;
        background: $surface;
        border: round $panel;
        padding: 0 1;
    }

    #diff-header {
        height: 1;
        width: 100%;
        margin-bottom: 1;
    }

    #diff-title {
        text-style: bold;
        color: $text;
        width: 1fr;
    }

    #diff-file-info {
        width: auto;
        max-width: 60%;
        color: $text-muted;
    }

    #diff-content {
        padding: 0 1;
        background: $panel;
        height: 1fr;
        min-height: 1;
        overflow-y: auto;
    }

    #diff-stats {
        height: 1;
        margin: 1 0;
        color: $text-muted;
    }

    .diff-added {
        color: $success;
    }

    .diff-removed {
        color: $error;
    }

    .diff-context {
        color: $text-muted;
    }

    #button-row {
        height: 1;
        margin-top: 1;
    }

    #button-row Button {
        width: 1fr;
        min-width: 0;
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
        color: $text;
    }

    #button-row Button:focus {
        background: $boost;
        text-style: bold underline;
    }

    #button-row Button:focus:ansi {
        text-style: bold reverse;
    }

    #hint-row {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("y", "apply", "Apply", show=True),
        Binding("enter", "apply", "Apply", show=False),
        Binding("s", "skip", "Skip", show=True),
        Binding("n", "cancel", "Cancel", show=True),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        file_path: str,
        old_content: str,
        new_content: str,
        tool_name: str = "edit",
    ):
        super().__init__()
        self.file_path = file_path
        self.old_content = old_content
        self.new_content = new_content
        self.tool_name = tool_name

    def compose(self) -> ComposeResult:
        from pathlib import Path
        import difflib

        path = Path(self.file_path)
        
        # Generate diff
        old_lines = self.old_content.splitlines(keepends=True)
        new_lines = self.new_content.splitlines(keepends=True)
        
        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
            lineterm="",
        ))
        
        # Calculate stats
        added = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
        removed = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
        
        # Format diff with colors
        diff_text = self._format_diff(diff)
        
        with Vertical(id="diff-container"):
            with Horizontal(id="diff-header"):
                yield Static(f"Preview: {self.tool_name.title()}", id="diff-title")
                yield Static(path.name, id="diff-file-info", markup=False)

            yield Static(diff_text, id="diff-content")
            yield Static(f"[green]+{added}[/] [red]-{removed}[/] lines", id="diff-stats")
            
            with Horizontal(id="button-row"):
                yield Button("y Apply", id="btn-apply", variant="success")
                yield Button("s Skip", id="btn-skip", variant="warning")
                yield Button("n Cancel", id="btn-cancel", variant="error")
            
            yield Static(
                "y/enter apply · s skip · n/esc cancel all",
                id="hint-row",
            )

    def _format_diff(self, diff_lines: list[str]) -> str:
        """Format diff lines with Rich markup for colors"""
        result = []
        for line in diff_lines[:100]:  # Limit to 100 lines
            line = escape(line.rstrip('\n'))
            if line.startswith('+++') or line.startswith('---'):
                result.append(f"[bold]{line}[/]")
            elif line.startswith('@@'):
                result.append(f"[cyan]{line}[/]")
            elif line.startswith('+'):
                result.append(f"[green]{line}[/]")
            elif line.startswith('-'):
                result.append(f"[red]{line}[/]")
            else:
                result.append(f"[dim]{line}[/]")
        
        if len(diff_lines) > 100:
            result.append(f"\n[dim]... ({len(diff_lines) - 100} more lines)[/]")
        
        return '\n'.join(result) if result else "[dim]No changes[/]"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            self.action_apply()
        elif event.button.id == "btn-skip":
            self.action_skip()
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def action_apply(self):
        self.dismiss(DiffPreviewResponse.APPLY)

    def action_skip(self):
        self.dismiss(DiffPreviewResponse.SKIP)

    def action_cancel(self):
        self.dismiss(DiffPreviewResponse.CANCEL)
