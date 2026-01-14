"""Terminal UI for codesm - OpenCode style layout"""

from pathlib import Path
import logging
import traceback
from textual.app import App, ComposeResult

from textual.containers import Container, Horizontal, Vertical, Center, VerticalScroll
from textual.widgets import Footer, Input, Static, RichLog, Markdown
from textual.binding import Binding

from .themes import THEMES, get_next_theme
from .modals import ModelSelectModal, ProviderConnectModal, AuthMethodModal, APIKeyInputModal, ThemeSelectModal, PermissionModal
from .session_modal import SessionListModal
from .command_palette import CommandPaletteModal
from .chat import ChatMessage, ContextSidebar, PromptInput
from .tools import ToolCallWidget, ToolResultWidget, ThinkingWidget, TodoListWidget
from codesm.auth import ClaudeOAuth
from codesm.permission import get_permission_manager, PermissionRequest, PermissionResponse, respond_permission

logger = logging.getLogger(__name__)

LOGO = """
 ██████╗ ██████╗ ██████╗ ███████╗███████╗███╗   ███╗
██╔════╝██╔═══██╗██╔══██╗██╔════╝██╔════╝████╗ ████║
██║     ██║   ██║██║  ██║█████╗  ███████╗██╔████╔██║
██║     ██║   ██║██║  ██║██╔══╝  ╚════██║██║╚██╔╝██║
╚██████╗╚██████╔╝██████╔╝███████╗███████║██║ ╚═╝ ██║
 ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚══════╝╚═╝     ╚═╝
"""

VERSION = "0.1.0"


class CodesmApp(App):
    """Main TUI application - OpenCode style"""

    CSS = """
    Screen {
        background: $background;
    }

    /* Footer styling - use theme colors */
    Footer {
        background: $surface;
        color: $text-muted;
    }

    Footer > .footer--highlight {
        background: transparent;
        color: $secondary;
        text-style: bold;
    }

    Footer > .footer--highlight-key {
        background: $secondary;
        color: $background;
        text-style: bold;
    }

    Footer > .footer--key {
        background: $primary;
        color: $foreground;
        text-style: bold;
    }

    Footer > .footer--description {
        color: $text-muted;
    }

    #main-container {
        width: 100%;
        height: 100%;
        layout: horizontal;
    }

    #content-area {
        width: 1fr;
        height: 100%;
        layout: vertical;
    }

    /* Welcome view styles */
    #welcome-view {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #logo {
        text-align: center;
        color: $foreground;
        width: auto;
        text-style: bold;
    }

    #version {
        text-align: center;
        color: $text-muted;
        padding-bottom: 2;
    }

    #input-container {
        width: 80;
        height: auto;
        align: center middle;
        border-left: heavy $secondary;
        background: $surface;
        padding: 0 1;
    }

    #message-input {
        width: 100%;
        border: none;
        background: $surface;
        color: $foreground;
        padding: 0 1;
    }

    #message-input:focus {
        border: none;
    }

    #model-indicator {
        color: $secondary;
        text-style: bold;
        padding: 0 1;
    }

    #hints {
        text-align: center;
        color: $text-muted;
        padding-top: 2;
    }

    /* Chat view styles */
    #chat-view {
        width: 100%;
        height: 100%;
        layout: grid;
        grid-size: 1 2;
        grid-rows: 1fr auto;
    }

    .hidden {
        display: none;
    }

    #chat-container {
        width: 100%;
        height: 100%;
        background: $background;
        border: none;
        row-span: 1;
    }

    #messages {
        width: 100%;
        height: auto;
        padding: 1 2;
        background: $background;
    }

    /* Message styles - OpenCode inspired */
    ChatMessage.user {
        border-left: heavy $secondary;
        padding: 1 2;
        margin: 1 0 0 0;
        background: $surface;
    }

    ChatMessage.user:hover {
        background: $panel;
    }

    ChatMessage.assistant {
        padding: 1 2;
        margin: 1 0 0 0;
    }

    .message-separator {
        color: $text-muted;
        margin: 1 0;
        height: 0;
    }

    /* Chat input section - OpenCode style */
    #chat-input-section {
        height: auto;
        width: 100%;
        background: $surface;
        padding: 1;
        row-span: 1;
    }

    #chat-input-wrapper {
        border-left: heavy $secondary;
        padding: 0 1;
        background: $surface;
    }

    #chat-message-input {
        width: 1fr;
        border: none;
        background: transparent;
        color: $foreground;
    }

    #chat-message-input:focus {
        border: none;
    }

    #chat-status-bar {
        height: 1;
        padding: 0 1;
    }

    #chat-agent-indicator {
        width: auto;
        color: $secondary;
        text-style: bold;
    }

    #chat-model-indicator {
        width: auto;
        color: $text-muted;
    }

    #chat-hint-bar {
        height: 1;
        margin-top: 1;
    }

    #chat-status-text {
        width: 1fr;
    }

    #chat-hints {
        width: auto;
        text-align: right;
        color: $text-muted;
    }

    /* Right sidebar */
    #context-sidebar {
        width: 42;
        height: 100%;
        background: $surface;
        padding: 1 2;
        border-left: solid $primary;
    }

    #context-sidebar .sidebar-section {
        margin-bottom: 1;
    }

    #context-sidebar .sidebar-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #context-sidebar .sidebar-value {
        color: $text-muted;
    }

    #context-sidebar .sidebar-footer {
        dock: bottom;
        height: auto;
        border-top: solid $primary;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+n", "new_session", "New Session", show=True),
        Binding("ctrl+l", "clear", "Clear", show=True),
        Binding("ctrl+a", "connect_provider", "Connect Provider", show=True),
        Binding("ctrl+t", "toggle_theme", "Theme", show=True),
        Binding("ctrl+p", "show_command_palette", "Commands", show=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=False),
    ]

    def __init__(self, directory: Path, model: str, session_id: str | None = None):
        super().__init__()
        self.directory = directory
        self.model = model
        self.agent = None
        self.session_id = session_id
        self.in_chat = False
        self._theme_name = "dark"
        self._showing_palette = False
        self._claude_oauth = ClaudeOAuth()
        self._sidebar_visible = True
        self._total_tokens = 0
        self._total_cost = 0.0
        self._pending_permission_requests: dict[str, PermissionRequest] = {}
        
        # Set up permission callback
        permission_manager = get_permission_manager()
        permission_manager.set_request_callback(self._on_permission_request)

    def compose(self) -> ComposeResult:
        with Container(id="main-container"):
            with Vertical(id="content-area"):
                with Vertical(id="welcome-view"):
                    yield Center(Static(LOGO, id="logo"))
                    yield Center(Static(VERSION, id="version"))
                    with Center():
                        with Vertical(id="input-container"):
                            yield Input(
                                placeholder="Build anything...",
                                id="message-input"
                            )
                            yield Static(f"[bold #5dd9c1]Build[/]  {self._short_model_name()}", id="model-indicator")
                    yield Center(
                        Static(
                            "[dim]tab[/dim] switch agent  [dim]ctrl+p[/dim] commands",
                            id="hints"
                        )
                    )

                with Container(id="chat-view", classes="hidden"):
                    with VerticalScroll(id="chat-container"):
                        yield Vertical(id="messages")
                    
                    with Vertical(id="chat-input-section"):
                        with Vertical(id="chat-input-wrapper"):
                            yield Input(
                                placeholder="Ask anything...",
                                id="chat-message-input"
                            )
                            with Horizontal(id="chat-status-bar"):
                                yield Static("[bold #5dd9c1]Build[/]", id="chat-agent-indicator")
                                yield Static("", id="chat-spacer")
                                yield Static(f"[dim]{self._short_model_name()}[/dim]", id="chat-model-indicator")
                        
                        with Horizontal(id="chat-hint-bar"):
                            yield Static("", id="chat-status-text")
                            yield Static("[dim]tab[/dim] switch agent  [dim]ctrl+p[/dim] commands", id="chat-hints")

            yield ContextSidebar(id="context-sidebar", classes="hidden")

        yield Footer()

    def _short_model_name(self) -> str:
        """Get short display name for current model"""
        if "/" in self.model:
            _, model_id = self.model.split("/", 1)
            return model_id
        return self.model

    def _get_active_input(self) -> Input:
        """Get the currently active input"""
        if self.in_chat:
            return self.query_one("#chat-message-input", Input)
        return self.query_one("#message-input", Input)

    async def on_mount(self):
        """Initialize when app mounts"""
        for theme in THEMES.values():
            self.register_theme(theme)
        
        # Load saved theme preference
        from codesm.auth.credentials import CredentialStore
        store = CredentialStore()
        saved_theme = store.get_preferred_theme()
        if saved_theme and saved_theme in [t.name.replace("codesm-", "") for t in THEMES.values()]:
            self._theme_name = saved_theme
            self.theme = f"codesm-{saved_theme}"
        else:
            self.theme = "codesm-dark"

        from codesm.agent.agent import Agent
        from codesm.session.session import Session
        
        # Update sidebar with working directory
        try:
            sidebar = self.query_one("#context-sidebar", ContextSidebar)
            sidebar.update_working_dir(f"~/{self.directory}")
        except Exception:
            pass
        
        # Load previous session if session_id was provided, otherwise create new
        if self.session_id:
            session = Session.load(self.session_id)
            if session:
                logger.info(f"Loaded previous session: {self.session_id}")
                self.agent = Agent(directory=self.directory, model=self.model, session=session)
                # Switch to chat and display previous messages
                self._switch_to_chat()
                self._display_session_messages(session.get_messages_for_display())
            else:
                logger.warning(f"Could not load session: {self.session_id}, creating new one")
                self.agent = Agent(directory=self.directory, model=self.model)
        else:
            self.agent = Agent(directory=self.directory, model=self.model)

        # Update model display to show provider prefix
        self._update_model_display()

        # Initialize LSP servers
        self._init_lsp()

        input_widget = self.query_one("#message-input", Input)
        input_widget.focus()

    def _init_lsp(self):
        """Initialize LSP servers in background"""
        import asyncio
        
        async def start_lsp():
            try:
                from codesm import lsp
                results = await lsp.init(str(self.directory))
                
                # Update sidebar with LSP status
                servers = []
                status = lsp.status()
                for key, info in status.items():
                    servers.append({
                        "name": info["name"],
                        "status": "connected" if info["running"] else "error",
                    })
                
                if servers:
                    try:
                        sidebar = self.query_one("#context-sidebar", ContextSidebar)
                        sidebar.update_lsp_status(servers)
                    except Exception:
                        pass
                    
                    self.notify(f"LSP: {len(servers)} server(s) connected")
                    logger.info(f"LSP initialized: {results}")
            except Exception as e:
                logger.error(f"Failed to initialize LSP: {e}")
        
        asyncio.create_task(start_lsp())

    def on_input_changed(self, event: Input.Changed):
        """Handle input text changes - show command palette when / is typed"""
        if self._showing_palette:
            return

        text = event.value
        if text == "/":
            self._showing_palette = True
            event.input.value = ""
            self.push_screen(CommandPaletteModal("/"), self._on_palette_dismiss)

    def _on_palette_dismiss(self, result: str | None):
        """Handle command palette dismiss"""
        self._showing_palette = False
        if result:
            self.call_later(self._execute_command_sync, result)
        self._get_active_input().focus()

    def _execute_command_sync(self, cmd: str):
        """Execute command synchronously, scheduling async operations"""
        if cmd == "/models":
            self.push_screen(ModelSelectModal(self.model), self._on_model_selected)
        elif cmd == "/theme":
            self.push_screen(ThemeSelectModal(self._theme_name), self._on_theme_selected)
        elif cmd == "/new":
            self.action_new_session()
        elif cmd == "/connect":
            self.push_screen(ProviderConnectModal(), self._on_provider_selected)
        elif cmd == "/session":
            self.push_screen(SessionListModal(), self._on_session_selected)
        elif cmd == "/help":
            self.notify("Commands: /init, /new, /models, /agents, /session, /status, /theme, /editor, /connect, /help")
        elif cmd == "/status":
            self.notify(f"Model: {self.model} | Dir: {self.directory}")
        elif cmd == "/init":
            self.notify("AGENTS.md initialization (coming soon)")
        elif cmd == "/agents":
            self.notify("Agent list (coming soon)")
        elif cmd == "/editor":
            self.notify("Editor (coming soon)")
        else:
            self.notify(f"Unknown command: {cmd}")

    def _on_theme_selected(self, result: str | None):
        """Handle theme selection from modal"""
        if result:
            self._theme_name = result
            self.theme = f"codesm-{result}"
            
            # Save theme preference
            from codesm.auth.credentials import CredentialStore
            store = CredentialStore()
            store.set_preferred_theme(result)
            
            from .themes import get_theme_display_name
            self.notify(f"Theme: {get_theme_display_name(result)}")
        self._get_active_input().focus()

    def _on_session_selected(self, result: str | None):
        """Handle session selection"""
        if result:
            from codesm.session.session import Session
            session = Session.load(result)
            if session:
                # Update agent with loaded session
                if self.agent:
                    self.agent.session = session
                
                # Switch to chat view and display messages
                self._switch_to_chat(show_session_start=False)
                messages = session.get_messages_for_display()
                self._display_session_messages(messages)
                
                self.notify(f"Loaded session: {session.title}")
            else:
                self.notify("Failed to load session")
        
        self._get_active_input().focus()

    def _on_model_selected(self, result: str | None):
        """Handle model selection"""
        if result:
            if result == "__connect_provider__":
                self.push_screen(ProviderConnectModal(), self._on_provider_selected)
                return
            self.model = result
            self._update_model_display()
            if self.agent:
                self.agent.model = result

            # Save the model preference
            from codesm.auth.credentials import CredentialStore
            store = CredentialStore()
            store.set_preferred_model(result)
            logger.info(f"Saved preferred model: {result}")

        self._get_active_input().focus()

    def _on_provider_selected(self, result: str | None):
        """Handle provider selection"""
        if result:
            if result == "anthropic":
                self.push_screen(AuthMethodModal("anthropic"), self._on_auth_method_selected)
            elif result == "openai":
                self._pending_provider = "openai"
                self.push_screen(APIKeyInputModal("openai"), self._on_api_key_entered)
            elif result == "openrouter":
                self._pending_provider = "openrouter"
                self.push_screen(APIKeyInputModal("openrouter"), self._on_api_key_entered)
            else:
                self.notify(f"Selected provider: {result} (coming soon)")
                self._get_active_input().focus()
        else:
            self._get_active_input().focus()

    def _on_auth_method_selected(self, result: str | None):
        """Handle auth method selection for Anthropic"""
        if result:
            if result == "manual-api-key":
                self._pending_provider = "anthropic"
                self.push_screen(APIKeyInputModal("anthropic"), self._on_api_key_entered)
            elif result == "create-api-key":
                import webbrowser
                webbrowser.open("https://console.anthropic.com/settings/keys")
                self.notify("Opening Anthropic console - copy your API key and use /connect again")
                self._get_active_input().focus()
        else:
            self._get_active_input().focus()

    def _on_api_key_entered(self, result: dict | None):
        """Handle API key entry for any provider"""
        if result:
            from codesm.auth.credentials import CredentialStore
            provider = getattr(self, "_pending_provider", "anthropic")
            store = CredentialStore()
            store.set(provider, {"auth_type": "api_key", "api_key": result["api_key"]})
            provider_name = provider.capitalize()

            self.notify(f"{provider_name} API key saved!")

            # Automatically switch to a model from this provider
            default_models = {
                "openai": "openai/gpt-4o",
                "anthropic": "anthropic/claude-sonnet-4-20250514",
                "google": "google/gemini-2.0-flash",
                "openrouter": "openrouter/anthropic/claude-sonnet-4",
            }

            if provider in default_models:
                new_model = default_models[provider]
                self.model = new_model
                self._update_model_display()
                if self.agent:
                    self.agent.model = new_model
                store.set_preferred_model(new_model)
                logger.info(f"Auto-switched to {new_model}")
                self.notify(f"Switched to {new_model}")

            self._get_active_input().focus()
        else:
            self._get_active_input().focus()

    async def on_input_submitted(self, event: Input.Submitted):
        """Handle message submission"""
        message = event.value.strip()
        if not message:
            return

        if message.startswith("/"):
            event.input.value = ""
            self._execute_command_sync(message)
            return

        # Clear input immediately
        event.input.value = ""

        # Switch to chat view if needed (with session start indicator for new chats)
        if not self.in_chat:
            self._switch_to_chat(show_session_start=True)

        # Get widgets
        chat_input = self.query_one("#chat-message-input", Input)
        chat_input.value = ""
        chat_input.disabled = True

        messages_container = self.query_one("#messages", Vertical)
        logger.info(f"Messages container children count: {len(messages_container.children)}")

        # Add user message first
        user_msg = ChatMessage("user", message)
        messages_container.mount(user_msg)

        # Update status to show processing (after user message so it appears below)
        self._set_processing_state(True, "Thinking...")

        # Scroll to bottom to show latest messages
        chat_container = self.query_one("#chat-container", VerticalScroll)
        self.call_later(lambda: chat_container.scroll_end(animate=False))

        logger.info("Mounted user message")

        # Process chat
        import time
        start_time = time.time()
        
        try:
            logger.info(f"Processing message: {message[:50]}")
            logger.info(f"Using model: {self.model}")

            response_text = ""
            tool_widgets: dict[str, ToolCallWidget] = {}
            tools_used: list[str] = []

            async for chunk in self.agent.chat(message):
                if hasattr(chunk, 'type'):
                    if chunk.type == "text":
                        response_text += chunk.content
                        logger.debug(f"Received text chunk: {chunk.content[:50] if chunk.content else 'empty'}...")
                    elif chunk.type == "tool_call":
                        logger.debug(f"Tool call: {chunk.name} with args {chunk.args}")
                        
                        # Update thinking message based on tool
                        tool_messages = {
                            "read": "Reading files",
                            "write": "Writing code",
                            "edit": "Editing code",
                            "bash": "Running command",
                            "grep": "Searching codebase",
                            "glob": "Finding files",
                            "webfetch": "Fetching from web",
                            "websearch": "Searching the web",
                            "codesearch": "Semantic code search",
                        }
                        if hasattr(self, '_thinking_widget') and self._thinking_widget:
                            msg = tool_messages.get(chunk.name, f"Using {chunk.name}")
                            self._thinking_widget.set_message(msg)
                        
                        tool_widget = ToolCallWidget(
                            tool_name=chunk.name,
                            args=chunk.args if isinstance(chunk.args, dict) else {},
                            pending=True,
                        )
                        tool_widgets[chunk.id] = tool_widget
                        if chunk.name not in tools_used:
                            tools_used.append(chunk.name)
                        messages_container.mount(tool_widget)
                        self.call_later(lambda: chat_container.scroll_end(animate=False))
                    elif chunk.type == "tool_result":
                        logger.debug(f"Tool result for {chunk.name}: {chunk.content[:50] if chunk.content else 'empty'}...")
                        if chunk.id in tool_widgets:
                            tool_widgets[chunk.id].mark_completed()

                            # Display tool result for certain tools (edit, write, bash)
                            if chunk.name in ["edit", "write", "bash", "grep", "glob"]:
                                # Show the full result as a message (not just preview)
                                from .chat import styled_markdown, AssistantMessage
                                result_msg = Static(styled_markdown(chunk.content))
                                result_msg.styles.padding = (0, 2, 1, 4)
                                result_msg.styles.margin = (0, 0, 1, 0)
                                messages_container.mount(result_msg)
                                self.call_later(lambda: chat_container.scroll_end(animate=False))
                            elif chunk.name == "todo":
                                # Parse todo list from result and display nicely
                                todos = self._parse_todo_result(chunk.content)
                                if todos:
                                    todo_widget = TodoListWidget(todos)
                                    messages_container.mount(todo_widget)
                                    self.call_later(lambda: chat_container.scroll_end(animate=False))
                else:
                    response_text += str(chunk)

            duration = time.time() - start_time

            # Add assistant response
            if response_text:
                logger.info(f"Got response, length: {len(response_text)}")
                assistant_msg = ChatMessage("assistant", response_text, tools_used=tools_used)
                messages_container.mount(assistant_msg)
                
                # Update sidebar with token/cost estimates and session title
                self._update_context_info(message, response_text)
                self._update_sidebar_title()
            else:
                logger.warning("No response received")
                error_msg = ChatMessage("assistant", "No response received")
                messages_container.mount(error_msg)

            # Scroll to bottom
            self.call_later(lambda: chat_container.scroll_end(animate=False))

            logger.info(f"Messages container now has {len(messages_container.children)} children")

        except Exception as e:
            logger.error(f"Error in chat: {e}", exc_info=True)

            # Add error message
            error_msg = str(e)
            error_widget = ChatMessage("assistant", f"Error: {error_msg}")
            messages_container.mount(error_widget)

            if "credentials" in error_msg.lower() or "api" in error_msg.lower():
                hint = ChatMessage("assistant", "Try running /connect to set up your API key")
                messages_container.mount(hint)

            self.notify(f"Error: {error_msg[:80]}")

        finally:
            # Re-enable input and reset status
            self._set_processing_state(False)
            chat_input.disabled = False
            chat_input.focus()

    def _set_processing_state(self, processing: bool, message: str = ""):
        """Update UI to show processing state with animated thinking indicator"""
        try:
            status_text = self.query_one("#chat-status-text", Static)
            hints = self.query_one("#chat-hints", Static)
            
            if processing:
                # Start animated thinking indicator
                self._thinking_widget = ThinkingWidget(message or "Thinking")
                self._thinking_widget.id = "thinking-indicator"
                
                # Mount thinking widget in messages area
                messages_container = self.query_one("#messages", Vertical)
                messages_container.mount(self._thinking_widget)
                
                # Start spinner animation timer
                self._spinner_timer = self.set_interval(0.08, self._animate_spinner)
                # Start message cycling timer (every 3 seconds)
                self._message_timer = self.set_interval(3.0, self._cycle_thinking_message)
                
                hints.update("[dim]esc[/dim] to interrupt")
                status_text.update("")
            else:
                # Stop timers and remove thinking widget
                if hasattr(self, '_spinner_timer') and self._spinner_timer:
                    self._spinner_timer.stop()
                    self._spinner_timer = None
                if hasattr(self, '_message_timer') and self._message_timer:
                    self._message_timer.stop()
                    self._message_timer = None
                
                # Remove thinking widget if exists
                try:
                    thinking = self.query_one("#thinking-indicator")
                    thinking.remove()
                except Exception:
                    pass
                
                status_text.update("")
                hints.update("[dim]tab[/dim] switch agent  [dim]ctrl+p[/dim] commands")
        except Exception:
            pass

    def _animate_spinner(self):
        """Animate the thinking spinner"""
        try:
            if hasattr(self, '_thinking_widget') and self._thinking_widget:
                self._thinking_widget.next_frame()
                # Scroll to keep thinking widget visible
                chat_container = self.query_one("#chat-container", VerticalScroll)
                chat_container.scroll_end(animate=False)
        except Exception:
            pass

    def _cycle_thinking_message(self):
        """Cycle through interesting thinking messages"""
        try:
            if hasattr(self, '_thinking_widget') and self._thinking_widget:
                self._thinking_widget.cycle_message()
        except Exception:
            pass

    def _update_sidebar_title(self):
        """Update sidebar with current session title"""
        try:
            if self.agent and self.agent.session:
                sidebar = self.query_one("#context-sidebar", ContextSidebar)
                sidebar.update_title(self.agent.session.title)
        except Exception:
            pass

    def _update_context_info(self, user_msg: str, assistant_msg: str):
        """Update sidebar with token and cost estimates"""
        # Rough token estimation (4 chars per token)
        user_tokens = len(user_msg) // 4
        assistant_tokens = len(assistant_msg) // 4
        self._total_tokens += user_tokens + assistant_tokens
        
        # Rough cost estimation
        input_cost = user_tokens * 0.000003  # $3 per 1M tokens
        output_cost = assistant_tokens * 0.000015  # $15 per 1M tokens
        self._total_cost += input_cost + output_cost
        
        # Context percentage (assuming 128k context window)
        context_pct = min(int((self._total_tokens / 128000) * 100), 100)
        
        try:
            sidebar = self.query_one("#context-sidebar", ContextSidebar)
            sidebar.update_context(self._total_tokens, context_pct, self._total_cost)
        except Exception:
            pass

    def _display_session_messages(self, messages: list[dict]):
        """Display loaded session messages in the chat area"""
        from .chat import styled_markdown
        
        messages_container = self.query_one("#messages", Vertical)
        chat_container = self.query_one("#chat-container", VerticalScroll)
        
        # Add previous messages
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            
            if not role or not content:
                continue
            
            if role == "tool_display":
                # Render tool results with styled markdown (for diffs, etc.)
                result_widget = Static(styled_markdown(content))
                result_widget.styles.padding = (0, 2, 1, 4)
                result_widget.styles.margin = (0, 0, 1, 0)
                messages_container.mount(result_widget)
            else:
                msg_widget = ChatMessage(role, content)
                messages_container.mount(msg_widget)
        
        if messages:
            chat_container.scroll_end(animate=False)
        
        logger.info(f"Displayed {len(messages)} previous messages")

    def _switch_to_chat(self, show_session_start: bool = True):
        """Switch from welcome view to chat view"""
        self.in_chat = True
        self.query_one("#welcome-view").add_class("hidden")
        self.query_one("#chat-view").remove_class("hidden")
        
        # Show sidebar in chat view
        try:
            sidebar = self.query_one("#context-sidebar", ContextSidebar)
            sidebar.remove_class("hidden")
            self._sidebar_visible = True
        except Exception:
            pass

        logger.info(f"Switched to chat view, messages container has {len(self.query_one('#messages', Vertical).children)} children")

        self.query_one("#chat-message-input", Input).focus()

    def _switch_to_welcome(self):
        """Switch from chat view to welcome view"""
        self.in_chat = False
        self.query_one("#welcome-view").remove_class("hidden")
        self.query_one("#chat-view").add_class("hidden")
        
        # Hide sidebar in welcome view
        try:
            sidebar = self.query_one("#context-sidebar", ContextSidebar)
            sidebar.add_class("hidden")
            self._sidebar_visible = False
        except Exception:
            pass
        
        self.query_one("#message-input", Input).focus()

    async def _show_model_selector(self):
        """Show model selection modal"""
        result = await self.push_screen_wait(ModelSelectModal(self.model))
        if result:
            self.model = result
            self._update_model_display()
            if self.agent:
                self.agent.model = result

    def _update_model_display(self):
        """Update model display in UI"""
        short_name = self._short_model_name()

        # Update welcome screen
        self.query_one("#model-indicator", Static).update(f"[bold #5dd9c1]Build[/]  {short_name}")
        
        # Update chat screen
        try:
            self.query_one("#chat-model-indicator", Static).update(f"[dim]{short_name}[/dim]")
        except Exception:
            pass

    def _parse_todo_result(self, content: str) -> list[dict]:
        """Parse todo tool result into a list of todo items for display.
        
        Only shows the full list on explicit 'list' action to avoid repetition.
        """
        if not content or not self.agent or not self.agent.session:
            return []
        
        # Only show full list on explicit list action (contains summary line)
        if "Pending:" in content and "In Progress:" in content:
            from codesm.session.todo import TodoList
            todo_list = TodoList(self.agent.session.id)
            return [
                {"content": t.content, "status": t.status}
                for t in todo_list.list()
            ]
        
        return []

    def action_toggle_theme(self):
        """Toggle between themes"""
        self._theme_name = get_next_theme(self._theme_name)
        self.theme = f"codesm-{self._theme_name}"
        
        # Save theme preference
        from codesm.auth.credentials import CredentialStore
        store = CredentialStore()
        store.set_preferred_theme(self._theme_name)
        
        self.notify(f"Theme: {self._theme_name}")

    def action_toggle_sidebar(self):
        """Toggle sidebar visibility"""
        try:
            sidebar = self.query_one("#context-sidebar", ContextSidebar)
            self._sidebar_visible = not self._sidebar_visible
            if self._sidebar_visible:
                sidebar.remove_class("hidden")
            else:
                sidebar.add_class("hidden")
        except Exception:
            pass

    async def action_connect_provider(self):
        """Show connect provider modal"""
        result = await self.push_screen_wait(ProviderConnectModal())
        if result:
            self.notify(f"Selected provider: {result}")

    def action_new_session(self):
        """Create a new session"""
        logger.info("action_new_session called - clearing messages and starting new session")
        from codesm.session.session import Session
        if self.agent:
            self.agent.session = Session.create(self.directory)
        
        # Reset token/cost tracking
        self._total_tokens = 0
        self._total_cost = 0.0
        try:
            sidebar = self.query_one("#context-sidebar", ContextSidebar)
            sidebar.update_context(0, 0, 0.0)
            sidebar.update_title("New Session")
        except Exception:
            pass
        
        if self.in_chat:
            messages = self.query_one("#messages", Vertical)
            messages.remove_children()
        self._switch_to_welcome()
        self.notify("New session started")

    def action_clear(self):
        """Clear the message history display"""
        logger.info("action_clear called - clearing all messages")
        if self.in_chat:
            messages = self.query_one("#messages", Vertical)
            messages.remove_children()

    def action_show_command_palette(self):
        """Show command palette via Ctrl+P"""
        self._showing_palette = True
        self.push_screen(CommandPaletteModal("/"), self._on_palette_dismiss)

    def _on_permission_request(self, request: PermissionRequest):
        """Called when a tool requests permission - shows the modal."""
        self._pending_permission_requests[request.id] = request
        self.call_from_thread(self._show_permission_modal, request)

    def _show_permission_modal(self, request: PermissionRequest):
        """Show the permission modal and handle the response."""
        async def handle_permission():
            result = await self.push_screen_wait(PermissionModal(request))
            if result is not None:
                respond_permission(request.session_id, request.id, result)
            else:
                respond_permission(request.session_id, request.id, PermissionResponse.DENY)
            self._pending_permission_requests.pop(request.id, None)
        
        self.run_worker(handle_permission())
