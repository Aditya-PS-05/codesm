"""Chat view for the TUI - OpenCode style layout"""

from textual.widgets import Static, Input, Label
from textual.containers import VerticalScroll, Vertical, Horizontal
from textual import events
from textual.reactive import reactive
from rich.markdown import Markdown, MarkdownContext
from rich.text import Text
from rich.style import Style


class ThemedMarkdown(Markdown):
    """Markdown with themed link colors"""
    
    LINK_COLOR = "#5dd9c1"
    
    def __init__(self, markup: str, **kwargs):
        super().__init__(markup, hyperlinks=True, **kwargs)
        self.style_stack = []
    
    def _set_link_style(self):
        """Override the link style in the style lookup"""
        if hasattr(self, '_style'):
            self._style = self._style.copy()
        
    def __rich_console__(self, console, options):
        from rich.console import Console
        from rich.theme import Theme
        
        themed_console = Console(
            theme=Theme({
                "markdown.link": f"bold {self.LINK_COLOR}",
                "markdown.link_url": f"dim {self.LINK_COLOR}",
            }),
            force_terminal=True,
            width=options.max_width,
        )
        
        with themed_console.capture() as capture:
            themed_console.print(Markdown(self.markup, hyperlinks=True))
        
        yield Text.from_ansi(capture.get())


def styled_markdown(content: str, link_color: str = "#5dd9c1") -> ThemedMarkdown:
    """Create Markdown with themed link styling"""
    ThemedMarkdown.LINK_COLOR = link_color
    return ThemedMarkdown(content)


from datetime import datetime

from .clipboard import SelectableMixin


class UserMessage(SelectableMixin, Static):
    """User message with left blue border - OpenCode style.
    
    Click on message, then press 'c' to copy. Or right-click for menu.
    """

    can_focus = True

    DEFAULT_CSS = """
    UserMessage {
        height: auto;
        margin: 1 0 0 0;
        padding: 0;
    }

    UserMessage:focus {
        border-left: heavy $secondary;
    }

    UserMessage > .user-content {
        padding: 1 2;
        background: $surface;
    }

    UserMessage > .user-content:hover {
        background: $panel;
    }
    """

    def __init__(self, content: str, timestamp: datetime | None = None, **kwargs):
        super().__init__(**kwargs)
        self.content = content
        self.timestamp = timestamp or datetime.now()
        self.border_title = ""
        self.styles.border = ("heavy", "left")
        self.styles.border_left = ("heavy", "$secondary")

    def compose(self):
        with Vertical(classes="user-content"):
            yield Static(self.content)
            yield Static(
                f"[dim]You · {self.timestamp.strftime('%H:%M')}[/dim]",
                classes="message-meta"
            )


class AssistantMessage(SelectableMixin, Static):
    """Assistant message - OpenCode style.
    
    Click on message, then press 'c' to copy. Or right-click for menu.
    """

    can_focus = True

    DEFAULT_CSS = """
    AssistantMessage {
        height: auto;
        margin: 1 0 0 0;
        padding: 1 2;
    }

    AssistantMessage:focus {
        background: $surface;
    }

    AssistantMessage .message-meta {
        margin-top: 1;
    }
    """

    def __init__(
        self,
        content: str,
        model: str = "",
        duration: float | None = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.content = content
        self.model = model
        self.duration = duration

    def compose(self):
        yield Static(styled_markdown(self.content))
        meta_parts = ["[#5dd9c1]▣[/]", "[dim]Assistant[/dim]"]
        if self.model:
            meta_parts.append(f"[dim]· {self.model}[/dim]")
        if self.duration:
            meta_parts.append(f"[dim]· {self.duration:.1f}s[/dim]")
        yield Static(" ".join(meta_parts), classes="message-meta")


class ChatMessage(SelectableMixin, Static):
    """Legacy chat message for compatibility.
    
    Click on message, then press 'c' to copy. Or right-click for menu.
    """

    can_focus = True

    DEFAULT_CSS = """
    ChatMessage {
        height: auto;
        margin: 1 0 0 0;
        padding: 1 2;
    }

    ChatMessage:focus {
        background: $surface;
    }

    ChatMessage.user {
        border-left: heavy #5dd9c1;
        background: $surface;
    }

    ChatMessage.user:hover {
        background: $panel;
    }

    ChatMessage.assistant {
        background: transparent;
    }
    """

    def __init__(self, role: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self.role = role
        self.content = content
        self.set_class(True, role)

    def compose(self):
        if self.role == "user":
            yield Static(self.content)
            yield Static("[dim]You[/dim]", classes="message-meta")
        else:
            yield Static(styled_markdown(self.content))
            yield Static("[#5dd9c1]▣[/] [dim]Assistant[/dim]", classes="message-meta")


class ContextSidebar(Static):
    """Right sidebar showing context, tokens, cost - OpenCode style"""

    DEFAULT_CSS = """
    ContextSidebar {
        width: 42;
        height: 100%;
        background: $surface;
        padding: 1 2;
        border-left: solid $primary;
    }

    ContextSidebar .sidebar-section {
        margin-bottom: 1;
    }

    ContextSidebar .sidebar-title {
        text-style: bold;
        margin-bottom: 1;
    }

    ContextSidebar .sidebar-value {
        color: $text-muted;
    }

    ContextSidebar .sidebar-footer {
        dock: bottom;
        height: auto;
        border-top: solid $primary;
        padding-top: 1;
    }
    """

    tokens = reactive(0)
    context_pct = reactive(0)
    cost = reactive(0.0)
    session_title = reactive("New Session")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.working_dir = "~/"
        self.version = "codesm 0.1.0"

    def compose(self):
        with Vertical():
            yield Static(f"[bold]{self.session_title}[/]", id="sidebar-title")
            yield Static("")
            
            with Vertical(classes="sidebar-section"):
                yield Static("[bold]Context[/]", classes="sidebar-title")
                yield Static(f"{self.tokens:,} tokens", id="token-count", classes="sidebar-value")
                yield Static(f"{self.context_pct}% used", id="context-pct", classes="sidebar-value")
                yield Static(f"${self.cost:.2f} spent", id="cost-display", classes="sidebar-value")
            
            yield Static("")
            
            with Vertical(classes="sidebar-section"):
                yield Static("[bold]LSP[/]", classes="sidebar-title")
                yield Static("[dim]No active servers[/dim]", id="lsp-status", classes="sidebar-value")
            
            yield Static("")
            
            with Vertical(classes="sidebar-footer"):
                yield Static(f"[dim]{self.working_dir}[/dim]", id="working-dir")
                yield Static(f"[dim]{self.version}[/dim]", id="version-info")

    def update_context(self, tokens: int, context_pct: int, cost: float):
        """Update context information"""
        self.tokens = tokens
        self.context_pct = context_pct
        self.cost = cost
        try:
            self.query_one("#token-count", Static).update(f"{tokens:,} tokens")
            self.query_one("#context-pct", Static).update(f"{context_pct}% used")
            self.query_one("#cost-display", Static).update(f"${cost:.2f} spent")
        except Exception:
            pass

    def update_title(self, title: str):
        """Update session title"""
        self.session_title = title
        try:
            self.query_one("#sidebar-title", Static).update(f"[bold]{title}[/]")
        except Exception:
            pass

    def update_working_dir(self, path: str):
        """Update working directory display"""
        self.working_dir = path
        try:
            self.query_one("#working-dir", Static).update(f"[dim]{path}[/dim]")
        except Exception:
            pass


class PromptInput(Vertical):
    """Input component with status bar - OpenCode style"""

    DEFAULT_CSS = """
    PromptInput {
        height: auto;
        background: $surface;
        padding: 1;
    }

    PromptInput #input-wrapper {
        border: heavy left $secondary;
        padding: 0 1;
        background: $surface;
    }

    PromptInput #prompt-input {
        border: none;
        background: transparent;
        width: 1fr;
    }

    PromptInput #prompt-input:focus {
        border: none;
    }

    PromptInput #input-status-bar {
        height: 1;
        padding: 0 1;
    }

    PromptInput #agent-indicator {
        width: auto;
    }

    PromptInput #model-indicator {
        width: auto;
        text-align: right;
    }

    PromptInput #hint-bar {
        height: 1;
        margin-top: 1;
    }

    PromptInput .hint-left {
        width: 1fr;
    }

    PromptInput .hint-right {
        width: auto;
        text-align: right;
    }
    """

    def __init__(
        self,
        placeholder: str = "Ask anything...",
        model: str = "",
        agent: str = "Build",
        **kwargs
    ):
        super().__init__(**kwargs)
        self._placeholder = placeholder
        self._model = model
        self._agent = agent
        self._is_processing = False

    def compose(self):
        with Vertical(id="input-wrapper"):
            yield Input(placeholder=self._placeholder, id="prompt-input")
            with Horizontal(id="input-status-bar"):
                yield Static(
                    f"[bold #5dd9c1]{self._agent}[/]",
                    id="agent-indicator"
                )
                yield Static("", classes="spacer")
                yield Static(
                    f"[dim]{self._model}[/dim]",
                    id="model-indicator"
                )
        
        with Horizontal(id="hint-bar"):
            yield Static("", id="status-text", classes="hint-left")
            yield Static(
                "[dim]tab[/dim] switch agent  [dim]ctrl+p[/dim] commands",
                classes="hint-right"
            )

    def get_input(self) -> Input:
        """Get the input widget"""
        return self.query_one("#prompt-input", Input)

    def set_model(self, model: str):
        """Update model display"""
        self._model = model
        try:
            self.query_one("#model-indicator", Static).update(f"[dim]{model}[/dim]")
        except Exception:
            pass

    def set_agent(self, agent: str):
        """Update agent display"""
        self._agent = agent
        try:
            self.query_one("#agent-indicator", Static).update(
                f"[bold #5dd9c1]{agent}[/]"
            )
        except Exception:
            pass

    def set_processing(self, processing: bool, message: str = ""):
        """Set processing state with optional message"""
        self._is_processing = processing
        try:
            status = self.query_one("#status-text", Static)
            hint = self.query_one(".hint-right", Static)
            if processing:
                status.update(f"[dim]⠋ {message}[/dim]" if message else "[dim]⠋ Thinking...[/dim]")
                hint.update("[dim]esc[/dim] to interrupt")
            else:
                status.update("")
                hint.update("[dim]tab[/dim] switch agent  [dim]ctrl+p[/dim] commands")
        except Exception:
            pass


class ChatView(VerticalScroll):
    """Chat conversation view - legacy compatibility"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.messages = []

    def add_message(self, role: str, content: str):
        """Add a message to the chat"""
        message = ChatMessage(role, content)
        self.mount(message)
        self.messages.append({"role": role, "content": content})
        self.scroll_end(animate=False)

    def clear_messages(self):
        """Clear all messages"""
        self.query(ChatMessage).remove()
        self.messages.clear()


class ChatInput(Input):
    """Input field for chat messages - legacy compatibility"""

    def __init__(self, **kwargs):
        super().__init__(placeholder="Type your message...", **kwargs)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle message submission"""
        if event.value.strip():
            self.post_message(self.MessageSubmitted(event.value))
            self.value = ""

    class MessageSubmitted(events.Event):
        """Event when a message is submitted"""

        def __init__(self, message: str):
            super().__init__()
            self.message = message
