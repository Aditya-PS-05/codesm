"""Compact, full-width terminal conversation for codesm."""

from contextlib import aclosing

import asyncio
from pathlib import Path
import logging
import time
import uuid
from textual.app import App, ComposeResult
from textual import events
from textual.worker import Worker

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static
from textual.binding import Binding

from .themes import THEMES
from .modals import ModelSelectModal, ProviderConnectModal, APIKeyInputModal, ThemeSelectModal, PermissionModal, ModeSelectModal, DiffPreviewModal, DiffPreviewResponse
from .session_modal import SessionListModal
from .command_palette import CommandPaletteModal
from .chat import ChatMessage
from .clipboard import copy_text
from .tools import (
    TodoListWidget, StreamingTextWidget, ToolTreeWidget, ThinkingTreeWidget,
    OracleTreeWidget, SubAgentTreeWidget, ClickablePath
)
from .autocomplete import AutocompletePopup
from codesm.auth import ClaudeOAuth
from codesm.config.config import Config
from codesm.provider.catalog import canonical_provider, provider_specs
from codesm.provider.router import ModelRouter
from codesm.permission import get_permission_manager, PermissionRequest, PermissionResponse, respond_permission
from codesm.diff_preview import get_diff_preview_manager, DiffPreviewRequest, respond_diff_preview
from codesm.diff_preview import DiffPreviewResponse as DiffPreviewResponseEnum

logger = logging.getLogger(__name__)

VERSION = "0.1.0"

class CodesmApp(App):
    """A conversation, a prompt, and the details when you need them."""

    CSS = """
    Screen {
        background: $background;
        color: $foreground;
        layout: vertical;
    }
    .hidden { display: none; }
    #chat-container {
        width: 100%;
        height: 1fr;
        background: $background;
        border: none;
        scrollbar-size: 1 1;
        scrollbar-gutter: auto;
    }
    #welcome-view {
        width: 100%;
        height: auto;
        padding: 1 2;
    }
    #logo { height: 1; text-style: bold; }
    #model-indicator, #working-directory, #hints {
        height: auto;
        color: $text-muted;
    }
    #hints { margin-top: 1; }
    #messages { width: 100%; height: auto; padding: 0; }
    #messages.active { min-height: 100%; }
    #chat-status-text {
        height: 1;
        width: 100%;
        margin: 1 0;
        padding: 0 1;
        color: $text-muted;
    }
    #chat-input-section {
        width: 100%;
        height: 3;
        padding: 1 1;
        background: $surface;
    }
    #prompt-marker { width: 2; height: 1; color: $text-muted; }
    #chat-message-input {
        width: 1fr;
        height: 1;
        min-width: 1;
        border: none;
        padding: 0;
        background: transparent;
        color: $foreground;
    }
    #chat-message-input:focus { border: none; background: transparent; }
    #chat-message-input > .input--placeholder { color: $text-muted; }
    #custom-footer {
        width: 100%;
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #footer-hints { width: 1fr; height: 1; text-overflow: ellipsis; }
    #footer-context {
        width: auto;
        max-width: 55%;
        height: 1;
        text-align: right;
        text-overflow: ellipsis;
    }
    .run-outcome { height: auto; padding: 0 1; margin-top: 1; color: $warning; }
    .run-outcome.verified { color: $success; }
    Screen:ansi #chat-input-section {
        background: ansi_default;
        border-top: solid ansi_bright_black;
        padding: 0 1;
    }
    Screen:ansi #prompt-marker { text-style: bold; }
    """

    BINDINGS = [
        Binding("ctrl+c", "copy_or_cancel", "Copy/Interrupt/Quit", show=False, priority=True),
        Binding("ctrl+shift+c", "copy_selection", "Copy selection", show=False, priority=True),
        Binding("ctrl+z", "cancel_or_quit", "Interrupt/Quit", show=False, priority=True),
        Binding("escape", "cancel_chat", "Interrupt", show=False, priority=True),
        Binding("ctrl+n", "new_session", "New session", show=False, priority=True),
        Binding("ctrl+l", "clear", "Clear", show=False),
        Binding("ctrl+a", "connect_provider", "Connect", show=False),
        Binding("ctrl+t", "toggle_transcript", "Transcript", show=False, priority=True),
        Binding("ctrl+p", "show_command_palette", "Commands", show=False),
        Binding("tab", "toggle_mode", "Queue/Mode", show=False, priority=True),
    ]

    def __init__(self, directory: Path, model: str, session_id: str | None = None):
        super().__init__(ansi_color=True)
        self.directory = directory
        self.model = model
        self._base_model = model  # Store the original model for mode switching
        self.agent = None
        self.session_id = session_id
        self.in_chat = False
        self._theme_name = "terminal"
        self._showing_palette = False
        self._mouse_pointer = ""
        self._claude_oauth = ClaudeOAuth()
        self._total_tokens = 0
        self._total_cost = 0.0
        self._pending_permission_requests: dict[str, PermissionRequest] = {}
        self._chat_worker: Worker | None = None
        self._cancel_requested = False
        self._queued_messages: list[str] = []
        self._transcript_expanded = False
        self._processing = False
        self._spinner_timer = None
        self._context_left = 100
        self._mode = "smart"  # Agent mode: "smart" or "rush"
        self._showing_autocomplete = False
        self._autocomplete_trigger_pos = 0
        self._file_mentions: list[str] = []  # Track mentioned files for context
        self._file_watcher = None  # File watcher instance
        self._pending_diff_previews: dict[str, DiffPreviewRequest] = {}
        
        # Set up permission callback
        permission_manager = get_permission_manager()
        permission_manager.set_request_callback(self._on_permission_request)
        
        # Set up diff preview callback
        diff_preview_manager = get_diff_preview_manager()
        diff_preview_manager.set_request_callback(self._on_diff_preview_request)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-container"):
            with Vertical(id="welcome-view"):
                yield Static(f"codesm · {VERSION}", id="logo", markup=False)
                yield Static("", id="model-indicator", markup=False)
                yield Static(str(self.directory), id="working-directory", markup=False)
                yield Static("Describe a task to get started. / for commands · @ for files", id="hints")
            yield Vertical(id="messages")
        yield Static("", id="chat-status-text", classes="hidden", markup=False)
        with Horizontal(id="chat-input-section"):
            yield Static("›", id="prompt-marker")
            yield Input(placeholder="Ask codesm to do anything", id="chat-message-input")
        with Horizontal(id="custom-footer"):
            yield Static("", id="footer-hints", markup=False)
            yield Static("", id="footer-context", markup=False)

    def _short_model_name(self) -> str:
        """Get short display name for current model"""
        if "/" in self.model:
            _, model_id = self.model.split("/", 1)
            return model_id
        return self.model

    def _get_mode_display(self) -> str:
        return self._mode.title()

    def _get_effective_model(self) -> str:
        """Get the effective model based on current mode"""
        if self._mode == "rush":
            provider, _ = ModelRouter.resolve_model(self._base_model)
            spec = provider_specs(self._get_model_config()).get(provider)
            if spec and spec.rush_model:
                return f"{provider}/{spec.rush_model}"
        return self._base_model

    def _get_model_config(self) -> Config:
        return self.agent.config if self.agent else Config.load(directory=self.directory)

    def _get_active_input(self) -> Input:
        return self.query_one("#chat-message-input", Input)

    async def on_unmount(self):
        """Cleanup when app unmounts"""
        self._set_mouse_pointer("")
        # Stop file watcher
        if self._file_watcher:
            await self._file_watcher.stop()
        if self.agent:
            await self.agent.cleanup()
        get_permission_manager().set_request_callback(None)
        get_diff_preview_manager().set_request_callback(None)

    async def on_mount(self):
        """Initialize when app mounts"""
        for theme in THEMES.values():
            self.register_theme(theme)
        
        # Load saved preferences
        from codesm.auth.credentials import CredentialStore
        store = CredentialStore()
        
        # Load theme preference
        saved_theme = store.get_preferred_theme()
        if saved_theme in THEMES:
            self._theme_name = saved_theme
            self.theme = THEMES[saved_theme].name
        else:
            self.theme = THEMES["terminal"].name
        
        # Load mode preference
        saved_mode = store.get_preferred_mode()
        if saved_mode in ("smart", "rush"):
            self._mode = saved_mode
            self.model = self._get_effective_model()
            if self.agent:
                self.agent.model = self.model

        from codesm.agent.agent import Agent
        from codesm.session.session import Session
        

        # Load previous session if session_id was provided, otherwise create new
        if self.session_id:
            session = Session.load(self.session_id)
            if session:
                logger.info(f"Loaded previous session: {self.session_id}")
                self.directory = session.directory
                self.query_one("#working-directory", Static).update(str(self.directory))
                self.agent = Agent(directory=self.directory, model=self.model, session=session)
                # Switch to chat and display previous messages
                self._switch_to_chat()
                await self._display_session_messages(session.get_messages_for_display())
            else:
                logger.warning(f"Could not load session: {self.session_id}, creating new one")
                self.agent = Agent(directory=self.directory, model=self.model)
        else:
            self.agent = Agent(directory=self.directory, model=self.model)

        # Update model display to show provider prefix
        self._update_model_display()

        # Initialize LSP servers
        self._init_lsp()
        
        # Start file watcher
        self._init_file_watcher()

        self._get_active_input().focus()

    def _init_lsp(self):
        """Initialize LSP servers in background"""
        import asyncio
        
        async def start_lsp():
            try:
                from codesm import lsp
                results = await lsp.init(str(self.directory))
                
                logger.info(f"LSP initialized: {results}")

            except Exception as e:
                logger.error(f"Failed to initialize LSP: {e}")
        
        asyncio.create_task(start_lsp())

    def _init_file_watcher(self):
        """Initialize file watcher in background"""
        import asyncio
        
        async def start_watcher():
            try:
                from codesm.file_watcher import FileWatcher
                
                def on_file_change(change):
                    """Handle file change events"""
                    logger.debug(f"File change: {change}")

                self._file_watcher = FileWatcher(
                    directory=self.directory,
                    on_change=on_file_change,
                    poll_interval=1.5,  # Check every 1.5 seconds
                )
                await self._file_watcher.start()
                
                logger.info(f"File watcher started: {self._file_watcher.watched_file_count} files")
            except Exception as e:
                logger.error(f"Failed to initialize file watcher: {e}")
        
        asyncio.create_task(start_watcher())

    def on_input_changed(self, event: Input.Changed):
        """Handle input text changes - show command palette or autocomplete"""
        if event.input.id != "chat-message-input":
            return
        if self._showing_palette or self._showing_autocomplete:
            return

        text = event.value
        if text == "/":
            self._showing_palette = True
            event.input.value = ""
            self.push_screen(CommandPaletteModal("/"), self._on_palette_dismiss)
        elif text.endswith("@") and (len(text) == 1 or text[-2] == " "):
            # Trigger @ autocomplete for files/agents
            self._trigger_autocomplete(event.input)

    def _on_palette_dismiss(self, result: str | None):
        """Handle command palette dismiss"""
        self._showing_palette = False
        if result:
            self.call_later(self._execute_command_sync, result)
        self._get_active_input().focus()

    def _trigger_autocomplete(self, input_widget: Input):
        """Show autocomplete popup for @ mentions."""
        self._showing_autocomplete = True
        self._autocomplete_trigger_pos = len(input_widget.value)
        
        self.push_screen(
            AutocompletePopup(
                mode="@",
                workspace=Path(self.directory),
                initial_filter="",
                agents=["general"],  # Available subagents
            ),
            self._on_autocomplete_dismiss,
        )

    def _on_autocomplete_dismiss(self, result: str | None):
        """Handle autocomplete selection."""
        self._showing_autocomplete = False
        input_widget = self._get_active_input()
        
        if result:
            # Replace the @ with the selected value
            current = input_widget.value
            before_at = current[:self._autocomplete_trigger_pos - 1]  # Before '@'
            after_cursor = current[self._autocomplete_trigger_pos:]  # After trigger
            
            # Build new value with completion
            suffix = " " if not after_cursor.startswith(" ") else ""
            input_widget.value = before_at + result + suffix + after_cursor
            input_widget.cursor_position = len(before_at + result + suffix)
            
            # Track file mentions for context
            if not result.startswith("@"):
                self._file_mentions.append(result)
                logger.info(f"Added file mention: {result}")
        
        input_widget.focus()

    def _execute_command_sync(self, cmd: str):
        """Execute command synchronously, scheduling async operations"""
        if cmd == "/models":
            self.push_screen(ModelSelectModal(self.model, self._get_model_config()), self._on_model_selected)
        elif cmd == "/theme":
            self.push_screen(ThemeSelectModal(self._theme_name), self._on_theme_selected)
        elif cmd == "/new":
            self.action_new_session()
        elif cmd == "/connect":
            self.action_connect_provider()
        elif cmd == "/session":
            self.push_screen(SessionListModal(), self._on_session_selected)
        elif cmd == "/mode":
            self.push_screen(ModeSelectModal(self._mode), self._on_mode_selected)
        elif cmd == "/rush":
            self._set_mode("rush")
        elif cmd == "/smart":
            self._set_mode("smart")
        elif cmd == "/fork":
            self._fork_session()
        elif cmd == "/branches":
            self._show_branches()
        elif cmd == "/dryrun":
            self._toggle_dry_run()
        elif cmd == "/audit":
            self._show_audit_log()
        elif cmd == "/debug":
            self.notify("Use /debug <bug description> to start; /debug off to finish.")
        elif cmd == "/help":
            self.notify("Commands: /init, /new, /fork, /branches, /dryrun, /audit, /models, /mode, /session, /status, /theme, /connect, /help")
        elif cmd == "/status":
            mode_str = "Rush" if self._mode == "rush" else "Smart"
            self.notify(f"Mode: {mode_str} | Model: {self.model} | Dir: {self.directory}")
        elif cmd == "/cost":
            self._show_cost_stats()
        elif cmd == "/init":
            self._run_init_command()
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
            self.theme = THEMES[result].name
            
            # Save theme preference
            from codesm.auth.credentials import CredentialStore
            store = CredentialStore()
            store.set_preferred_theme(result)
            
            from .themes import get_theme_display_name
            self.notify(f"Theme: {get_theme_display_name(result)}")
        self._get_active_input().focus()

    def _on_mode_selected(self, result: str | None):
        """Handle mode selection from modal"""
        if result:
            self._set_mode(result)
        self._get_active_input().focus()

    def _set_mode(self, mode: str):
        """Set the agent mode (smart or rush)"""
        if mode not in ("smart", "rush"):
            return
        
        self._mode = mode
        
        # Update the effective model based on mode
        self.model = self._get_effective_model()
        if self.agent:
            self.agent.model = self.model
        
        self._update_model_display()
        
        # Save mode preference
        from codesm.auth.credentials import CredentialStore
        store = CredentialStore()
        store.set_preferred_mode(mode)
        
        mode_name = "Rush" if mode == "rush" else "Smart"
        model_short = self.model.split("/")[-1] if "/" in self.model else self.model
        logger.info(f"Mode switched to {mode_name}, using model: {self.model}")
        self.notify(f"{mode_name}: {model_short}")

    def _show_cost_stats(self):
        stats = self.agent.budget.get_session_stats()
        cost = "unknown (configure model_prices)" if stats.unpriced_requests else f"~${stats.total_cost:.4f}"
        tokens = stats.total_input_tokens + stats.total_output_tokens
        self.notify(f"Usage: {tokens:,} tokens · {stats.total_requests} requests · {cost} · "
                    f"{stats.estimated_requests} estimated requests")
    
    def _run_init_command(self):
        """Initialize AGENTS.md for the current project"""
        from codesm.rules import init_agents_md, save_agents_md
        from pathlib import Path
        
        workspace = Path(self.directory)
        agents_path = workspace / "AGENTS.md"
        
        if agents_path.exists():
            self.notify(f"AGENTS.md already exists at {agents_path}")
            return
        
        content, already_exists = init_agents_md(workspace)
        
        if already_exists:
            self.notify("AGENTS.md already exists")
            return
        
        saved_path = save_agents_md(workspace, content)
        self.notify(f"Created {saved_path.name} - review and customize it")
        
        if self.agent:
            self.agent.rules.refresh()

    def _on_session_selected(self, result: str | None):
        if result:
            self.call_later(self._switch_to_session, result)
        else:
            self._get_active_input().focus()

    async def _switch_to_session(self, session_id: str):
        from codesm.session.session import Session
        if self.agent and self.agent._chat_active:
            self.notify("Finish or cancel the active task before switching sessions")
            return
        session = Session.load(session_id)
        if not session:
            self.notify(f"Failed to load session: {session_id}")
            return
        if session.directory != self.directory:
            from codesm.agent.agent import Agent
            replacement = Agent(directory=session.directory, model=self.model, session=session)
            await self.agent.cleanup()
            self.agent = replacement
            self.directory = session.directory
            self.query_one("#working-directory", Static).update(str(self.directory))
            if self._file_watcher:
                await self._file_watcher.stop()
                self._file_watcher = None
            self._init_file_watcher()
            self._init_lsp()
        else:
            self.agent.session = session
        self._queued_messages.clear()
        self._switch_to_chat(show_session_start=False)
        await self._display_session_messages(session.get_messages_for_display())
        self._get_active_input().focus()

    def _on_model_selected(self, result: str | None):
        """Handle model selection"""
        if result and self._chat_worker and self._chat_worker.is_running:
            self.notify("Finish or interrupt the active task before switching models; its history is saved.")
            return
        if result:
            if result == "__connect_provider__":
                self.action_connect_provider()
                return
            
            # When user selects a model, store it as base and reset to smart mode
            self._base_model = result
            self._mode = "smart"
            self.model = result
            self._update_model_display()
            if self.agent:
                self.agent.model = result
                self.notify("Model changed. Saved conversation and task context retained.")

            # Save the model preference
            from codesm.auth.credentials import CredentialStore
            store = CredentialStore()
            store.set_preferred_model(result)
            store.set_preferred_mode("smart")
            logger.info(f"Saved preferred model: {result}")

        self._get_active_input().focus()

    def _on_provider_selected(self, result: str | None):
        """Handle provider selection"""
        if not result:
            self._get_active_input().focus()
            return
        provider = canonical_provider(result)
        config = self._get_model_config()
        spec = provider_specs(config).get(provider)
        if not spec:
            self.notify(f"Configure a base_url for provider {provider} first.")
            self._get_active_input().focus()
        elif provider == "ollama":
            self.push_screen(ModelSelectModal(self.model, config, provider=provider), self._on_model_selected)
        else:
            self._pending_provider = provider
            self.push_screen(APIKeyInputModal(provider, spec.name), self._on_api_key_entered)

    def _on_api_key_entered(self, result: dict | None):
        """Handle API key entry for any provider"""
        if result:
            from codesm.auth.credentials import CredentialStore
            provider = canonical_provider(result.get("provider") or self._pending_provider)
            config = self._get_model_config()
            spec = provider_specs(config).get(provider)
            if not spec:
                self.notify(f"Configure a base_url for provider {provider} first.")
                self._get_active_input().focus()
                return
            store = CredentialStore()
            store.set(provider, {"auth_type": "api_key", "api_key": result["api_key"]})
            self.notify(f"{spec.name} API key saved!")
            if spec.models:
                self._on_model_selected(f"{provider}/{spec.models[0]}")
            else:
                self.push_screen(ModelSelectModal(self.model, config, provider=provider), self._on_model_selected)
        else:
            self._get_active_input().focus()

    def on_clickable_path_path_copied(self, event: ClickablePath.PathCopied):
        """Handle path copied from ClickablePath widget."""
        self.notify(f"Copied: {event.path}")
    
    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "chat-message-input":
            return
        message = event.value.strip()
        if not message:
            return
        event.input.value = ""
        if message.startswith("/") and not message.startswith("/debug "):
            self._execute_command_sync(message)
            return
        if self._chat_worker:
            self._queue_message(message)
            return
        self._cancel_requested = False
        self._chat_worker = self.run_worker(self._run_chat(message), name="chat_worker")

    def _queue_message(self, message: str):
        self._queued_messages.append(message)
        self._animate_spinner()
        self._update_footer()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "copy_selection":
            return bool(self._selected_text())
        if action in {"toggle_mode", "toggle_transcript", "cancel_chat", "new_session"}:
            return len(self.screen_stack) == 1
        return True

    async def _run_chat(self, message: str):
        """Keep follow-ups in the same worker so agent runs never overlap."""
        try:
            first_message = True
            while message:
                self._switch_to_chat()
                await self.query_one("#messages", Vertical).mount(ChatMessage("user", message))
                self._set_processing_state(True)
                if first_message:
                    self.query_one("#chat-container", VerticalScroll).anchor()
                    first_message = False
                completed = await self._process_chat(message)
                if completed is False or self._cancel_requested or not self._queued_messages:
                    break
                message = self._queued_messages.pop(0)
        except asyncio.CancelledError:
            if self.query("#messages"):
                await self.query_one("#messages", Vertical).mount(
                    Static("• Interrupted · send a message to continue", classes="run-outcome", markup=False)
                )
            raise
        finally:
            self._chat_worker = None
            self._set_processing_state(False)
            if self.query("#chat-message-input"):
                self._get_active_input().focus()

    async def _process_chat(self, message: str):
        """Process chat in background worker"""
        
        messages_container = self.query_one("#messages", Vertical)
        chat_input = self.query_one("#chat-message-input", Input)
        
        try:
            logger.info(f"Processing message: {message[:50]}")
            logger.info(f"Using model: {self.model}")

            response_text = ""
            rendered_text = False
            
            # Track current action group for Amp-style tree display
            current_action: str | None = None
            current_tree_widget: ToolTreeWidget | None = None
            tool_index_map: dict[str, tuple[ToolTreeWidget, int]] = {}  # tool_id -> (tree_widget, index)
            
            # Track thinking and subagent widgets
            thinking_tree_widget: ThinkingTreeWidget | None = None
            thinking_timer = None
            agent_timers = []
            subagent_widgets: dict[str, SubAgentTreeWidget] = {}  # subagent_id -> widget
            
            # Streaming text widget for live response display
            streaming_widget: StreamingTextWidget | None = None

            chunk_count = 0
            last_chunk_time = time.time()
            async with aclosing(self.agent.chat(message)) as stream:
                async for chunk in stream:
                    chunk_count += 1
                    now = time.time()
                    delta = now - last_chunk_time
                    last_chunk_time = now
                
                    # Check if cancel was requested
                    if self._cancel_requested:
                        logger.info("Chat cancelled by user")
                        break
                    
                    if hasattr(chunk, 'type'):
                        if chunk.type == "text":
                            response_text += chunk.content
                            logger.info(f"[STREAM] chunk #{chunk_count} after {delta:.3f}s: {chunk.content[:30] if chunk.content else 'empty'}...")
                        
                            # Create new streaming widget or append to existing one
                            if streaming_widget is None and chunk.content:
                                # Create new streaming widget (first text or after tool call)
                                current_action = current_tree_widget = None
                                rendered_text = True
                                streaming_widget = StreamingTextWidget()
                                streaming_widget.id = f"streaming-response-{uuid.uuid4().hex[:8]}"
                                streaming_widget.set_content(chunk.content)
                                await messages_container.mount(streaming_widget)
                            elif streaming_widget and chunk.content:
                                # Append text to existing streaming widget
                                streaming_widget.append_text(chunk.content)

                        elif chunk.type == "tool_call":
                            # Finalize current streaming widget so tool call appears after it
                            if streaming_widget is not None and streaming_widget.parent is not None:
                                streaming_widget.mark_complete()
                                streaming_widget = None  # Create new one for text after tool call

                            logger.debug(f"Tool call: {chunk.name} with args {chunk.args}")

                            # Determine action category for grouping
                            action_category = self._get_tool_category(chunk.name)

                            # Create new ToolTreeWidget if category changed
                            if action_category != current_action:
                                current_action = action_category

                                # Create Amp-style grouped tree widget
                                current_tree_widget = ToolTreeWidget(action_category, collapsed=not self._transcript_expanded)
                                await messages_container.mount(current_tree_widget)

                            # Add tool to current tree group
                            if current_tree_widget is not None:
                                tool_index = current_tree_widget.add_tool(
                                    chunk.name,
                                    chunk.args if isinstance(chunk.args, dict) else {},
                                    pending=True
                                )
                                tool_index_map[chunk.id] = (current_tree_widget, tool_index)

                        elif chunk.type == "tool_result":
                            logger.debug(f"Tool result for {chunk.name}: {chunk.content[:50] if chunk.content else 'empty'}...")

                            # Generate result summary for inline display
                            result_summary = self._get_result_summary(chunk.name, chunk.content)

                            diff_preview = chunk.content if chunk.name in {"edit", "write", "multiedit"} else ""

                            # Update tree widget if using grouped display
                            if chunk.id in tool_index_map:
                                tree_widget, tool_idx = tool_index_map[chunk.id]
                                tree_widget.mark_tool_complete(
                                    tool_idx,
                                    result_summary,
                                    streaming_text=chunk.content or "",
                                    diff_preview=diff_preview,
                                    is_error=chunk.content.startswith("Error:")
                                )
                            if chunk.name == "todo":
                                # Parse todo list from result and display nicely
                                todos = self._parse_todo_result(chunk.content)
                                if todos:
                                    todo_widget = TodoListWidget(todos)
                                    await messages_container.mount(todo_widget)
                        elif chunk.type == "handoff":
                            # Handle handoff to new session
                            logger.info(f"Handoff triggered to session: {chunk.new_session_id}")
                            self._pending_handoff_session = chunk.new_session_id

                        elif chunk.type == "thinking":
                            # Start or update thinking display
                            if thinking_tree_widget is None or thinking_tree_widget._complete:
                                thinking_tree_widget = ThinkingTreeWidget(chunk.content or "Thinking")
                                thinking_tree_widget._collapsed = not self._transcript_expanded
                                await messages_container.mount(thinking_tree_widget)
                                # Start spinner animation
                                thinking_timer = self.set_interval(0.1, lambda: thinking_tree_widget.next_frame() if thinking_tree_widget else None)
                            else:
                                thinking_tree_widget.set_message(chunk.content or "Thinking")
                        
                        elif chunk.type == "thinking_done":
                            # Complete thinking with summary
                            if thinking_tree_widget:
                                thinking_tree_widget.complete(chunk.thinking_summary)
                                if thinking_timer:
                                    thinking_timer.stop()
                                    thinking_timer = None
                        
                        elif chunk.type == "subagent_start":
                            current_action = current_tree_widget = None
                            widget = SubAgentTreeWidget(chunk.content,
                                subagent_type=chunk.subagent_type,
                                model=chunk.metadata.get("model", ""))
                            widget._collapsed = not self._transcript_expanded
                            subagent_widgets[chunk.subagent_id] = widget
                            await messages_container.mount(widget)
                            agent_timers.append(self.set_interval(0.1, widget.next_frame))
                        elif chunk.type == "subagent_progress":
                            widget = subagent_widgets.get(chunk.subagent_id)
                            if widget:
                                widget.update_status(chunk.metadata.get("status", "running"))
                                if chunk.metadata.get("tool"):
                                    widget.add_action(chunk.metadata["tool"], chunk.content, complete=True)
                        elif chunk.type == "subagent_done":
                            widget = subagent_widgets.get(chunk.subagent_id)
                            if widget:
                                widget.complete(chunk.content, status=chunk.metadata.get("status", "completed"),
                                                cost=chunk.metadata.get("cost"))
                        elif chunk.type == "usage":
                            self._update_context_info("", "")
                            widget = subagent_widgets.get(chunk.subagent_id)
                            if widget and chunk.metadata.get("cost_known"):
                                widget.update_status(widget.status, (widget.cost or 0) + chunk.metadata["cost"])
                        elif chunk.type == "run_status":
                            if chunk.content != "completed":
                                classes = "run-outcome verified" if chunk.content == "verified" else "run-outcome"
                                await messages_container.mount(Static(f"• {chunk.content.capitalize()}", classes=classes, markup=False))
                        
                    else:
                        response_text += str(chunk)

            
            # Stop thinking timer if still running
            if thinking_timer:
                thinking_timer.stop()

            
            # Finalize streaming widget
            if streaming_widget and streaming_widget.parent is not None:
                streaming_widget.mark_complete()
            
            if response_text:
                if not rendered_text:
                    await messages_container.mount(ChatMessage("assistant", response_text))
                self._update_context_info(message, response_text)
            elif not self._cancel_requested and not tool_index_map and not subagent_widgets:
                await messages_container.mount(ChatMessage("assistant", "No response received"))

            logger.info(f"Messages container now has {len(messages_container.children)} children")
            
            # Handle pending handoff - switch to new session
            if hasattr(self, "_pending_handoff_session") and self._pending_handoff_session:
                new_session_id = self._pending_handoff_session
                self._pending_handoff_session = None
                await self._switch_to_session(new_session_id)

        except Exception as e:
            logger.error(f"Error in chat: {e}", exc_info=True)

            # Add error message
            error_msg = str(e)
            error_widget = ChatMessage("assistant", f"Error: {error_msg}")
            await messages_container.mount(error_widget)

            if "credentials" in error_msg.lower() or "api" in error_msg.lower():
                hint = ChatMessage("assistant", "Try running /connect to set up your API key")
                await messages_container.mount(hint)

            return False  # Pause queued work after a failed request.

        finally:
            for timer in agent_timers + [thinking_timer]:
                if timer:
                    timer.stop()
            for widget in subagent_widgets.values():
                if not widget._complete:
                    widget.complete("Execution interrupted", status="cancelled" if self._cancel_requested else "failed")
            if streaming_widget:
                streaming_widget.mark_complete()
            if thinking_tree_widget and not thinking_tree_widget._complete:
                thinking_tree_widget.complete()
            self._update_context_info("", "")
            # Re-enable input and reset status
            self._set_processing_state(False)
            chat_input.disabled = False
            if chat_input.is_mounted:
                chat_input.focus()

    def _set_processing_state(self, processing: bool, message: str = ""):
        self._processing = processing
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if processing:
            self._work_started = time.monotonic()
            self._spinner_frame = 0
            self._spinner_timer = self.set_interval(0.1, self._animate_spinner)
        self._animate_spinner()
        self._update_footer()

    def _animate_spinner(self):
        if not self.query("#chat-status-text"):
            return
        status = self.query_one("#chat-status-text", Static)
        queued = f" · {len(self._queued_messages)} queued" if self._queued_messages else ""
        status.set_class(not self._processing and not queued, "hidden")
        if self._processing:
            seconds = int(time.monotonic() - self._work_started)
            elapsed = f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"
            frame = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[self._spinner_frame % 10]
            self._spinner_frame += 1
            status.update(f"{frame} Working ({elapsed} · esc to interrupt){queued}")
        else:
            status.update(f"{len(self._queued_messages)} queued · send a message to continue · esc to discard" if queued else "")

    def _update_context_info(self, user_msg: str = "", assistant_msg: str = ""):
        """Keep usage in /cost and a transcript occupancy estimate in the footer."""
        from codesm.session.context import TokenEstimator
        stats = self.agent.budget.get_session_stats()
        self._total_tokens = stats.total_input_tokens + stats.total_output_tokens
        self._total_cost = stats.total_cost
        window = self.agent.profile.context_tokens if self.agent.profile else 128000
        tokens = TokenEstimator().estimate_messages(self.agent.session.get_messages())
        self._context_left = max(0, 100 - int(tokens / window * 100))
        self._update_footer()

    def _update_footer(self):
        if not self.query("#footer-hints"):
            return
        hints = "tab to queue message" if self._processing else "ctrl+p commands · ctrl+t transcript"
        if self.size.width < 65 and not self._processing:
            hints = "ctrl+p commands"
        if self.screen.get_selected_text():
            hints = "ctrl+c copy · esc clear selection"
        elif self._mouse_pointer == "text":
            hints = "drag to select · ctrl+c copy"
        model = self._short_model_name()
        context = f"~{self._context_left}% context left"
        model_budget = max(0, self.size.width // 2 - len(context) - 3)
        if model_budget >= 8:
            if len(model) > model_budget:
                model = model[:model_budget - 1] + "…"
            context = f"{model} · {context}"
        self.query_one("#footer-hints", Static).update(hints)
        self.query_one("#footer-context", Static).update(context)

    def on_resize(self):
        if self.is_mounted:
            self._update_footer()

    async def _display_session_messages(self, messages: list[dict]):
        messages_container = self.query_one("#messages", Vertical)
        await messages_container.remove_children()
        for msg in messages:
            role, content = msg.get("role"), msg.get("content", "")
            if not role or not content:
                continue
            if role == "tool_display":
                name = msg.get("tool_name", "tool")
                widget = ToolTreeWidget(self._get_tool_category(name), collapsed=not self._transcript_expanded)
                index = widget.add_tool(name, {}, pending=False)
                widget.mark_tool_complete(index, self._get_result_summary(name, content),
                                          streaming_text=content, is_error=content.startswith("Error:"))
            else:
                widget = ChatMessage(role, content)
            await messages_container.mount(widget)
        for record in self.agent.session.agent_runs.values():
            widget = SubAgentTreeWidget(record.get("task", "Task"),
                subagent_type=record.get("role", "coder"), model=record.get("model", ""))
            widget.complete(record.get("result", ""), status=record.get("status", "interrupted"), cost=record.get("cost"))
            widget._collapsed = not self._transcript_expanded
            await messages_container.mount(widget)
        self.agent._restore_budget()
        self._update_context_info()
        self.query_one("#chat-container", VerticalScroll).anchor()

    def _switch_to_chat(self, show_session_start: bool = True):
        self.in_chat = True
        self.query_one("#welcome-view").add_class("hidden")
        self.query_one("#messages").add_class("active")
        self._get_active_input().focus()

    def _switch_to_welcome(self):
        self.in_chat = False
        self.query_one("#welcome-view").remove_class("hidden")
        self.query_one("#messages").remove_class("active")
        self._get_active_input().focus()

    async def _show_model_selector(self):
        """Show model selection modal"""
        self.push_screen(ModelSelectModal(self.model, self._get_model_config()), self._on_model_selected)

    def _update_model_display(self):
        self.query_one("#model-indicator", Static).update(f"{self._get_mode_display()} · {self.model}")
        self._update_footer()

    def _get_tool_category(self, tool_name: str) -> str:
        if tool_name in {"read", "ls", "grep", "glob", "codesearch", "look_at"}:
            return "Explored"
        if tool_name in {"write", "edit", "multiedit"}:
            return "Edited"
        if tool_name == "bash":
            return "Ran"
        if tool_name in {"web", "webfetch", "websearch"}:
            return "Searched the web"
        if tool_name == "todo":
            return "Updated tasks"
        return "Used tools"

    def _get_result_summary(self, tool_name: str, content: str) -> str:
        """Generate a compact summary for tool results."""
        if not content:
            return ""
        
        lines = content.strip().split("\n")
        
        if tool_name == "glob":
            # Count files found
            if content == "No files found":
                return "no files"
            count = len([l for l in lines if l.strip()])
            return f"{count} file" + ("s" if count != 1 else "")
        
        elif tool_name == "grep":
            # Count matches
            if "No matches" in content or not lines:
                return "no matches"
            count = len([l for l in lines if l.strip() and not l.startswith("Results")])
            return f"{count} match" + ("es" if count != 1 else "")
        
        elif tool_name == "read":
            # Show line count
            count = len(lines)
            return f"{count} line" + ("s" if count != 1 else "")
        
        elif tool_name in ["edit", "write"]:
            return ""  # Full result shown separately
        
        elif tool_name == "bash":
            # Show exit code if available, or line count
            if "exit code" in content.lower():
                return ""
            return f"{len(lines)} line" + ("s" if len(lines) != 1 else "")
        
        elif tool_name == "codesearch":
            return ""  # Complex results
        
        elif tool_name == "todo":
            # Extract todo name from result like "Started: todo_xxx - Task name"
            if " - " in content:
                name = content.split(" - ", 1)[1].strip()
                if len(name) > 50:
                    name = name[:47] + "..."
                return name
            return ""
        
        return ""

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

    def action_cancel_chat(self):
        """Cancel the current chat processing"""
        if self.screen.get_selected_text():
            self.screen.clear_selection()
            self._update_footer()
            return
        if self._chat_worker and self._chat_worker.is_running:
            self._cancel_requested = True
            self._chat_worker.cancel()
            logger.info("Cancel requested by user")
        elif self._queued_messages:
            self._queued_messages.clear()
            self._animate_spinner()
            self._update_footer()

    def _selected_text(self) -> str | None:
        if isinstance(self.focused, Input) and self.focused.selected_text:
            return self.focused.selected_text
        return self.screen.get_selected_text()

    def action_copy_selection(self):
        if text := self._selected_text():
            copy_text(self, text)

    def action_copy_or_cancel(self):
        if self._selected_text():
            self.action_copy_selection()
        else:
            self.action_cancel_or_quit()

    def on_text_selected(self):
        self._update_footer()

    def _set_mouse_pointer(self, shape: str):
        if shape == self._mouse_pointer:
            return
        self._mouse_pointer = shape
        self._update_footer()
        driver = self._driver
        if driver and not driver.is_headless and not driver.is_web:
            # OSC 22: supported terminals show an I-beam over selectable text.
            # https://sw.kovidgoyal.net/kitty/pointer-shapes/
            driver.write(f"\x1b]22;{shape}\x1b\\")

    def on_mouse_move(self, event: events.MouseMove):
        widget = self.mouse_over
        shape = ""
        if event.style.link:
            shape = "pointer"
        elif widget and (widget.allow_select or isinstance(widget, Input)):
            shape = "text"
        self._set_mouse_pointer(shape)

    def action_cancel_or_quit(self):
        """Cancel chat if running, otherwise quit"""
        if self._chat_worker and self._chat_worker.is_running:
            self.screen.clear_selection()
            self.action_cancel_chat()
        else:
            self.exit()

    def action_toggle_transcript(self):
        self._transcript_expanded = not self._transcript_expanded
        for widget_type in (ToolTreeWidget, ThinkingTreeWidget, OracleTreeWidget, SubAgentTreeWidget):
            for widget in self.query(widget_type):
                widget._collapsed = not self._transcript_expanded
                widget.refresh(layout=True)

    def action_toggle_mode(self):
        if self._chat_worker:
            input_widget = self._get_active_input()
            message = input_widget.value.strip()
            if message:
                input_widget.value = ""
                self._queue_message(message)
        else:
            self._set_mode("rush" if self._mode == "smart" else "smart")

    def action_connect_provider(self):
        """Show connect provider modal"""
        self.push_screen(ProviderConnectModal(self._get_model_config()), self._on_provider_selected)

    def action_new_session(self):
        """Create a new session"""
        if self._chat_worker:
            self.notify("Finish or cancel the active task before switching sessions")
            return
        logger.info("action_new_session called - clearing messages and starting new session")
        from codesm.session.session import Session
        if self.agent:
            self.agent.new_session()
        
        self._total_tokens = 0
        self._total_cost = 0.0
        self._context_left = 100
        self._queued_messages.clear()
        self.query_one("#messages", Vertical).remove_children()
        self._get_active_input().value = ""
        self._set_processing_state(False)
        self._switch_to_welcome()

    def _fork_session(self):
        if not self.agent or not self.agent.session:
            self.notify("No active session to fork")
            return
        if self._chat_worker or self.agent._chat_active:
            self.notify("Finish or cancel the active task before switching sessions")
            return
        forked = self.agent.session.fork()
        self.agent.session = forked
        self.notify(f"Forked session: {forked.branch_name}")

    def _show_branches(self):
        """Show branches of current session"""
        if not self.agent or not self.agent.session:
            self.notify("No active session")
            return
        
        session = self.agent.session
        branches = session.list_branches()
        
        if not branches:
            # Check if this is a branch itself
            if session.is_branch():
                parent = session.get_parent()
                parent_title = parent.title if parent else "Unknown"
                self.notify(f"This is a branch of: {parent_title}")
            else:
                self.notify("No branches for this session")
            return
        
        branch_list = ", ".join(b.get("branch_name", b["id"][:8]) for b in branches[:5])
        if len(branches) > 5:
            branch_list += f" (+{len(branches) - 5} more)"
        self.notify(f"Branches: {branch_list}")
    
    def _toggle_dry_run(self):
        """Toggle dry-run mode (preview changes without applying)"""
        if not hasattr(self, '_dry_run_mode'):
            self._dry_run_mode = False
        
        self._dry_run_mode = not self._dry_run_mode
        
        if self._dry_run_mode:
            self.notify("Dry run mode ENABLED. Changes will be previewed only.")
        else:
            self.notify("Dry run mode DISABLED. Changes will be applied.")
    
    def _show_audit_log(self):
        """Show recent agent actions from audit log"""
        try:
            from codesm.audit import get_audit_log
            
            audit = get_audit_log()
            session_id = self.agent.session.id if self.agent and self.agent.session else None
            
            # Get recent entries
            entries = audit.get_recent(count=20, session_id=session_id)
            
            if not entries:
                self.notify("No audit entries yet")
                return
            
            # Format for display
            display = audit.format_for_display(entries, verbose=False)
            
            # Show in a notification (limited)
            lines = display.split("\n")
            if len(lines) > 5:
                preview = "\n".join(lines[:5]) + f"\n... (+{len(lines) - 5} more)"
            else:
                preview = display
            
            self.notify(f"Recent actions:\n{preview}")
            
        except ImportError:
            self.notify("Audit logging not available")
        except Exception as e:
            self.notify(f"Error loading audit log: {e}")

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

    def _on_diff_preview_request(self, request: DiffPreviewRequest):
        """Called when a tool requests diff preview - shows the modal."""
        self._pending_diff_previews[request.id] = request
        self.call_from_thread(self._show_diff_preview_modal, request)

    def _show_diff_preview_modal(self, request: DiffPreviewRequest):
        """Show the diff preview modal and handle the response."""
        async def handle_preview():
            modal = DiffPreviewModal(
                file_path=request.file_path,
                old_content=request.old_content,
                new_content=request.new_content,
                tool_name=request.tool_name,
            )
            result = await self.push_screen_wait(modal)
            
            if result is not None:
                # Map modal response to enum
                if result == DiffPreviewResponse.APPLY:
                    respond_diff_preview(request.session_id, request.id, DiffPreviewResponseEnum.APPLY)
                elif result == DiffPreviewResponse.SKIP:
                    respond_diff_preview(request.session_id, request.id, DiffPreviewResponseEnum.SKIP)
                else:
                    respond_diff_preview(request.session_id, request.id, DiffPreviewResponseEnum.CANCEL)
            else:
                respond_diff_preview(request.session_id, request.id, DiffPreviewResponseEnum.CANCEL)
            
            self._pending_diff_previews.pop(request.id, None)
        
        self.run_worker(handle_preview())
