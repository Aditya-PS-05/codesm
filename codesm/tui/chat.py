"""Chat view for the TUI - Terminal-inspired style with rich formatting"""

import re
from textual.widgets import Static, Input, Label
from textual.containers import VerticalScroll, Vertical, Horizontal
from textual import events
from textual.reactive import reactive
from rich.markdown import Markdown, MarkdownContext, Heading
from rich.text import Text
from rich.style import Style
from rich.console import Group
from rich.panel import Panel

# Color constants matching Amp/OpenCode reference design  
YELLOW = "#eed49f"  # Keywords, patterns, inline code (softer gold)
CYAN = "#8bd5ca"    # File paths, links (teal)
GREEN = "#a6da95"   # Success indicators
LIGHT_BLUE = "#8aadf4"  # Headers (periwinkle)
WHITE = "#cad3f5"   # Primary text
STRONG = "#f5f5f5"  # Bold/strong text
DIM = "#6e738d"     # Secondary/muted text
SURFACE = "#24273a" # Background accent


class LeftAlignedHeading(Heading):
    """Heading that renders left-aligned instead of centered."""
    
    def __rich_console__(self, console, options):
        text = self.text
        text.justify = "left"
        yield Text()  # blank line before
        yield text


class LeftAlignedMarkdown(Markdown):
    """Markdown renderer with left-aligned headers (like Amp/OpenCode)."""
    
    elements = {
        **Markdown.elements,
        "heading_open": LeftAlignedHeading,
    }


def render_diff_block(diff_content: str) -> Text:
    """Render a diff block with red/green background colors like Amp."""
    text = Text()
    
    for line in diff_content.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            # Added line - green text on dark green background
            text.append(line + '\n', style="green on #1a2f1a")
        elif line.startswith('-') and not line.startswith('---'):
            # Removed line - red text on dark red background
            text.append(line + '\n', style="red on #2f1a1a")
        elif line.startswith('@@'):
            # Hunk header - cyan/blue
            text.append(line + '\n', style="cyan")
        else:
            # Context line - dim
            text.append(line + '\n', style="dim")
    
    return text


class ThemedMarkdown(Markdown):
    """Markdown with themed link colors, styled headers, and keyword highlighting"""
    
    LINK_COLOR = "#5dd9c1"
    
    def __init__(self, markup: str, **kwargs):
        super().__init__(markup, hyperlinks=True, **kwargs)
        self.style_stack = []
        self.original_markup = markup
    
    def _set_link_style(self):
        """Override the link style in the style lookup"""
        if hasattr(self, '_style'):
            self._style = self._style.copy()
        
    def __rich_console__(self, console, options):
        from rich.console import Console
        from rich.theme import Theme
        
        # Check if content contains diff code blocks
        diff_pattern = re.compile(r'```diff\n(.*?)```', re.DOTALL)
        
        if diff_pattern.search(self.original_markup):
            # Process content with custom diff rendering
            parts = []
            last_end = 0
            
            for match in diff_pattern.finditer(self.original_markup):
                # Add markdown before the diff block
                before_text = self.original_markup[last_end:match.start()]
                if before_text.strip():
                    parts.append(self._render_markdown(before_text, options))
                
                # Add styled diff block
                diff_content = match.group(1)
                parts.append(render_diff_block(diff_content))
                
                last_end = match.end()
            
            # Add any remaining markdown after last diff block
            after_text = self.original_markup[last_end:]
            if after_text.strip():
                parts.append(self._render_markdown(after_text, options))
            
            for part in parts:
                yield part
        else:
            # No diff blocks, render normally
            yield self._render_markdown(self.original_markup, options)
    
    def _render_markdown(self, content: str, options) -> Text:
        """Render markdown content to Text with Amp/OpenCode-style formatting."""
        from rich.console import Console
        from rich.theme import Theme
        
        # Pre-process: convert headers to custom format, then render
        processed, headers = self._extract_headers(content)
        
        # Theme matching Amp/OpenCode style
        themed_console = Console(
            theme=Theme({
                "markdown.link": f"{CYAN}",
                "markdown.link_url": f"dim {CYAN}",
                "markdown.code": f"{YELLOW}",
                "markdown.code_block": f"{WHITE}",
                "markdown.strong": f"bold {STRONG}",
                "markdown.emph": f"italic {WHITE}",
                "markdown.item.bullet": f"{DIM}",
                "markdown.item.number": f"{DIM}",
                "markdown.block_quote": f"italic {DIM}",
                "markdown.hr": f"{DIM}",
            }),
            force_terminal=True,
            width=options.max_width,
        )
        
        with themed_console.capture() as capture:
            themed_console.print(Markdown(processed, hyperlinks=True, code_theme="monokai"))
        
        result = Text.from_ansi(capture.get())
        
        # Post-process to highlight file paths and restore header colors
        result = self._highlight_paths(result)
        result = self._colorize_headers(result, headers)
        
        return result
    
    def _extract_headers(self, content: str) -> tuple[str, list[str]]:
        """Convert headers to bold text and track them for coloring."""
        lines = content.split('\n')
        result = []
        headers = []
        in_code_block = False
        
        for line in lines:
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                result.append(line)
                continue
            
            if in_code_block:
                result.append(line)
                continue
            
            # Convert headers to bold (will be colored in post-process)
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if header_match:
                text = header_match.group(2)
                headers.append(text)
                if result and result[-1].strip():
                    result.append('')
                result.append(f'**{text}**')
                continue
            
            result.append(line)
        
        return '\n'.join(result), headers
    
    def _colorize_headers(self, text: Text, headers: list[str]) -> Text:
        """Apply header color to extracted headers."""
        plain = text.plain
        for header in headers:
            # Find the header text and apply blue color
            idx = plain.find(header)
            if idx >= 0:
                text.stylize(Style(color=LIGHT_BLUE, bold=True), idx, idx + len(header))
        return text
    
    def _enhance_content(self, content: str) -> str:
        """Pre-process markdown content for cleaner Amp-style display.
        
        Converts headers to bold text (left-aligned) since Rich centers headers.
        """
        lines = content.split('\n')
        result = []
        in_code_block = False
        
        for line in lines:
            # Track code blocks to avoid processing inside them
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                result.append(line)
                continue
            
            if in_code_block:
                result.append(line)
                continue
            
            # Convert markdown headers to styled bold text (left-aligned)
            # This avoids Rich's default centered header rendering
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if header_match:
                level = len(header_match.group(1))
                text = header_match.group(2)
                # Use bold for headers, add blank line before for spacing
                if result and result[-1].strip():
                    result.append('')  # Add spacing before header
                result.append(f'**{text}**')
                continue
            
            result.append(line)
        
        return '\n'.join(result)
    
    def _highlight_paths(self, text: Text) -> Text:
        """Highlight file paths and technical identifiers in rendered text."""
        plain = text.plain
        
        # Pattern for file paths (e.g., src/foo/bar.py, ./file.ts, /absolute/path)
        path_pattern = re.compile(
            r'(?:^|[\s\(\[\{])('
            r'(?:\./|/|[a-zA-Z]:/)?'  # Optional prefix
            r'(?:[a-zA-Z0-9_\-]+/)*'   # Directory components
            r'[a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+'  # filename.ext
            r')(?:[\s\)\]\}:,]|$)'
        )
        
        # Find and highlight paths
        for match in path_pattern.finditer(plain):
            start = match.start(1)
            end = match.end(1)
            text.stylize(Style(color=CYAN), start, end)
        
        return text


def styled_markdown(content: str, link_color: str = "#5dd9c1") -> ThemedMarkdown:
    """Create Markdown with themed link styling"""
    ThemedMarkdown.LINK_COLOR = link_color
    return ThemedMarkdown(content)


from datetime import datetime

from .clipboard import SelectableMixin


class UserMessage(SelectableMixin, Static):
    """User message with clean, minimal styling.
    
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
    
    UserMessage .user-text {
        color: $text;
    }
    
    UserMessage .message-meta {
        margin-top: 0;
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
            yield Static(self.content, classes="user-text")
            yield Static(
                f"[dim]You · {self.timestamp.strftime('%H:%M')}[/dim]",
                classes="message-meta"
            )


class AssistantMessage(SelectableMixin, Static):
    """Assistant message with rich formatting and keyword highlighting.
    
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
        # Use enhanced styled markdown for rich formatting
        yield Static(styled_markdown(self.content))
        meta_parts = [f"[{CYAN}]▣[/]", "[dim]Assistant[/dim]"]
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

    def __init__(self, role: str, content: str, tools_used: list[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.role = role
        self.content = content
        self.tools_used = tools_used or []
        self.set_class(True, role)

    def compose(self):
        if self.role == "user":
            yield Static(self.content)
            yield Static("[dim]You[/dim]", classes="message-meta")
        else:
            yield Static(styled_markdown(self.content))
            meta_parts = ["[#5dd9c1]▣[/]", "[dim]Assistant[/dim]"]
            if self.tools_used:
                tools_str = ", ".join(self.tools_used)
                meta_parts.append(f"[dim]· used {tools_str}[/dim]")
            yield Static(" ".join(meta_parts), classes="message-meta")


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

    def update_lsp_status(self, servers: list[dict]):
        """Update LSP server status"""
        try:
            lsp_widget = self.query_one("#lsp-status", Static)
            if not servers:
                lsp_widget.update("[dim]No active servers[/dim]")
            else:
                lines = []
                for server in servers:
                    status_icon = "[green]●[/]" if server.get("status") == "connected" else "[red]●[/]"
                    lines.append(f"{status_icon} {server.get('name', 'unknown')}")
                lsp_widget.update("\n".join(lines))
        except Exception:
            pass

    def update_file_change(self, change):
        """Update display with file change notification (optional sidebar display)"""
        # File changes are primarily shown via notify(), but sidebar could show recent changes
        # This is a hook for future sidebar file activity display
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
