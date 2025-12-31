"""Terminal UI for codesm"""

from pathlib import Path
import logging
import traceback
from textual.app import App, ComposeResult

from textual.containers import Container, Horizontal, Vertical, Center, VerticalScroll
from textual.widgets import Footer, Input, Static, RichLog, Markdown
from textual.binding import Binding

from .themes import THEMES, get_next_theme
from .modals import ModelSelectModal, ProviderConnectModal, AuthMethodModal, APIKeyInputModal
from .session_modal import SessionListModal
from .command_palette import CommandPaletteModal
from .chat import ChatMessage
from codesm.auth import ClaudeOAuth

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
    """Main TUI application"""

    CSS = """
    Screen {
        background: $background;
    }

    #main-container {
        width: 100%;
        height: 100%;
    }

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
        border-left: tall $primary;
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

    .hint-key {
        text-style: bold;
        color: $foreground;
    }

    #chat-view {
        width: 100%;
        height: 100%;
        layout: vertical;
    }

    .hidden {
        display: none;
    }

    #chat-container {
        width: 100%;
        height: 1fr;
        background: $background;
        border: none;
    }

    #messages {
        width: 100%;
        height: auto;
        padding: 1 2;
        background: $background;
    }

    .message-user {
        color: $secondary;
        text-style: bold;
        margin: 1 0 0 0;
        width: 100%;
    }

    .message-assistant {
        color: $foreground;
        margin: 1 0 0 0;
        width: 100%;
    }

    .message-separator {
        color: $text-muted;
        margin: 1 0;
    }

    #chat-input-section {
        height: auto;
        width: 100%;
        background: $surface;
        border-top: tall $primary;
        padding: 1;
    }

    #chat-input-container {
        width: 100%;
        height: auto;
        layout: horizontal;
        border-left: tall $primary;
        background: $surface;
        padding: 0 1;
    }

    #chat-message-input {
        width: 1fr;
        height: auto;
        border: none;
        background: $surface;
        color: $foreground;
    }

    #chat-model-indicator {
        width: auto;
        height: auto;
        color: $secondary;
        padding: 0 1;
        text-align: right;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }

    .user-message {
        color: $secondary;
        text-style: bold;
        margin: 1 0;
    }

    .assistant-message {
        color: $success;
        margin: 1 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+n", "new_session", "New Session", show=True),
        Binding("ctrl+l", "clear", "Clear", show=True),
        Binding("ctrl+a", "connect_provider", "Connect Provider", show=True),
        Binding("ctrl+t", "toggle_theme", "Theme", show=True),
        Binding("ctrl+p", "show_command_palette", "Commands", show=True),
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

    def compose(self) -> ComposeResult:
        with Container(id="main-container"):
            with Vertical(id="welcome-view"):
                yield Center(Static(LOGO, id="logo"))
                yield Center(Static(VERSION, id="version"))
                with Center():
                    with Vertical(id="input-container"):
                        yield Input(
                            placeholder="Build anything...",
                            id="message-input"
                        )
                        yield Static(f"Build  {self._short_model_name()}", id="model-indicator")
                yield Center(
                    Static(
                        "[bold]tab[/] switch agent  [bold]ctrl+p[/] commands",
                        id="hints"
                    )
                )

            with Vertical(id="chat-view", classes="hidden"):
                with VerticalScroll(id="chat-container"):
                    yield Vertical(id="messages")
                with Vertical(id="chat-input-section"):
                    with Horizontal(id="chat-input-container"):
                        yield Input(
                            placeholder="Continue the conversation...",
                            id="chat-message-input"
                        )
                        yield Static(f"{self._short_model_name()}", id="chat-model-indicator")

            yield Static(f"~/{self.directory}", id="status-bar")
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
        self.theme = "codesm-dark"

        from codesm.agent.agent import Agent
        from codesm.session.session import Session
        
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

        input_widget = self.query_one("#message-input", Input)
        input_widget.focus()

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
            self.action_toggle_theme()
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

        # Add user message
        user_msg = ChatMessage("user", message)
        messages_container.mount(user_msg)

        # Add thinking indicator
        thinking = Static("[dim]Thinking...[/dim]", id="thinking-indicator")
        messages_container.mount(thinking)

        # Scroll to bottom to show latest messages
        chat_container = self.query_one("#chat-container", VerticalScroll)
        # Force a refresh to ensure scroll position is updated
        self.call_later(lambda: chat_container.scroll_end(animate=False))

        logger.info("Mounted user message and thinking indicator")

        # Process chat
        try:
            logger.info(f"Processing message: {message[:50]}")
            logger.info(f"Using model: {self.model}")

            response_text = ""

            async for chunk in self.agent.chat(message):
                # chunk is a StreamChunk object, extract the content
                if hasattr(chunk, 'content'):
                    response_text += chunk.content
                    logger.debug(f"Received chunk of type {chunk.type}: {chunk.content[:50] if chunk.content else 'empty'}...")
                else:
                    # Fallback in case it's a string (shouldn't happen)
                    response_text += str(chunk)

            # Remove thinking indicator
            thinking.remove()

            # Add assistant response
            if response_text:
                logger.info(f"Got response, length: {len(response_text)}")
                assistant_msg = ChatMessage("assistant", response_text)
                messages_container.mount(assistant_msg)
            else:
                logger.warning("No response received")
                error_msg = ChatMessage("assistant", "No response received")
                messages_container.mount(error_msg)

            # Add separator
            separator = Static("[dim]" + "─" * 40 + "[/dim]", classes="message-separator")
            messages_container.mount(separator)

            # Scroll to bottom to show latest response
            # Force a refresh to ensure scroll position is updated
            self.call_later(lambda: chat_container.scroll_end(animate=False))

            logger.info(f"Messages container now has {len(messages_container.children)} children")

        except Exception as e:
            logger.error(f"Error in chat: {e}", exc_info=True)

            # Remove thinking indicator if it exists
            try:
                thinking.remove()
            except:
                pass

            # Add error message
            error_msg = str(e)
            error_widget = ChatMessage("assistant", f"Error: {error_msg}")
            messages_container.mount(error_widget)

            if "credentials" in error_msg.lower() or "api" in error_msg.lower():
                hint = ChatMessage("assistant", "Try running /connect to set up your API key")
                messages_container.mount(hint)

            self.notify(f"Error: {error_msg[:80]}")

        finally:
            # Re-enable input
            chat_input.disabled = False
            chat_input.focus()

    def _display_session_messages(self, messages: list[dict]):
        """Display loaded session messages in the chat area"""
        messages_container = self.query_one("#messages", Vertical)
        chat_container = self.query_one("#chat-container", VerticalScroll)
        
        # Add previous messages
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            
            if role and content:
                msg_widget = ChatMessage(role, content)
                messages_container.mount(msg_widget)
        
        # Add separator after previous messages if any exist
        if messages:
            separator = Static("[dim]" + "─" * 40 + "[/dim]", classes="message-separator")
            messages_container.mount(separator)
            # Scroll to bottom
            chat_container.scroll_end(animate=False)
        
        logger.info(f"Displayed {len(messages)} previous messages")

    def _switch_to_chat(self, show_session_start: bool = True):
        """Switch from welcome view to chat view"""
        self.in_chat = True
        self.query_one("#welcome-view").add_class("hidden")
        self.query_one("#chat-view").remove_class("hidden")

        logger.info(f"Switched to chat view, messages container has {len(self.query_one('#messages', Vertical).children)} children")

        self.query_one("#chat-message-input", Input).focus()

    def _switch_to_welcome(self):
        """Switch from chat view to welcome view"""
        self.in_chat = False
        self.query_one("#welcome-view").remove_class("hidden")
        self.query_one("#chat-view").add_class("hidden")
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

        # Add provider prefix for clarity
        if "/" in self.model:
            provider, _ = self.model.split("/", 1)
            display_name = f"[{provider.upper()}] {short_name}"
        else:
            display_name = short_name

        self.query_one("#model-indicator", Static).update(f"Build  {display_name}")
        self.query_one("#chat-model-indicator", Static).update(display_name)

    def action_toggle_theme(self):
        """Toggle between themes"""
        self._theme_name = get_next_theme(self._theme_name)
        self.theme = f"codesm-{self._theme_name}"
        self.notify(f"Theme: {self._theme_name}")

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
        if self.in_chat:
            messages = self.query_one("#messages", Vertical)
            messages.remove_children()
            new_session_msg = Static("[bold green]New session started[/]", classes="message-separator")
            messages.mount(new_session_msg)
        self._switch_to_welcome()
        self.notify("New session started")

    def action_clear(self):
        """Clear the message history display"""
        logger.info("action_clear called - clearing all messages")
        if self.in_chat:
            messages = self.query_one("#messages", Vertical)
            messages.remove_children()
            cleared_msg = Static("[bold yellow]Display cleared[/]", classes="message-separator")
            messages.mount(cleared_msg)

    def action_show_command_palette(self):
        """Show command palette via Ctrl+P"""
        self._showing_palette = True
        self.push_screen(CommandPaletteModal("/"), self._on_palette_dismiss)
