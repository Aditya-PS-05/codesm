"""Chat view for the TUI"""

from textual.widgets import Static, Input
from textual.containers import VerticalScroll
from textual import events
from rich.markdown import Markdown


class ChatMessage(Static):
    """A single chat message"""

    CSS = """
    ChatMessage {
        height: auto;
        margin: 0 0 1 0;
    }
    
    ChatMessage.user {
        color: #5dd9c1;
        text-style: bold;
    }
    
    ChatMessage.assistant {
        color: #ffffff;
    }
    """

    def __init__(self, role: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self.role = role
        self.content = content
        self.set_class(True, role)

    def render(self) -> str:
        if self.role == "user":
            return f"[bold cyan]You:[/bold cyan] {self.content}"
        else:
            # For assistant, just return the content (it can be markdown)
            return f"[bold green]Assistant:[/bold green]\n{self.content}"


class ChatView(VerticalScroll):
    """Chat conversation view"""

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
    """Input field for chat messages"""

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
