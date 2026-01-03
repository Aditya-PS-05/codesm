"""Main agent - orchestrates LLM calls and tool execution"""

from pathlib import Path
from typing import AsyncIterator

from codesm.provider.base import get_provider, StreamChunk
from codesm.tool.registry import ToolRegistry
from codesm.session.session import Session
from codesm.agent.prompt import SYSTEM_PROMPT
from codesm.agent.loop import ReActLoop


class Agent:
    """AI coding agent that can read, write, and execute code"""

    def __init__(
        self,
        directory: Path,
        model: str,
        session: Session | None = None,
        max_iterations: int = 15
    ):
        self.directory = Path(directory).resolve()
        self._model = model
        self.session = session or Session.create(self.directory)
        self.max_iterations = max_iterations
        self.tools = ToolRegistry()
        self.provider = get_provider(self._model)
        self.react_loop = ReActLoop(max_iterations=self.max_iterations)

    @property
    def model(self) -> str:
        """Get current model"""
        return self._model

    @model.setter
    def model(self, value: str):
        """Set model and recreate provider"""
        self._model = value
        self.provider = get_provider(value)
    
    async def chat(self, message: str) -> AsyncIterator[str]:
        """Send a message and stream the response"""
        # Add user message to session
        self.session.add_message(role="user", content=message)
        
        # Get conversation history
        messages = self.session.get_messages()
        
        # Build context for tools
        context = {
            "session": self.session,
            "cwd": self.directory,
        }
        
        # Run ReAct loop
        full_response = ""
        async for chunk in self.react_loop.execute(
            provider=self.provider,
            system_prompt=SYSTEM_PROMPT.format(cwd=self.directory),
            messages=messages,
            tools=self.tools,
            context=context,
        ):
            if chunk.type == "text":
                full_response += chunk.content
                yield chunk
            elif chunk.type == "tool_call":
                yield chunk
            elif chunk.type == "tool_result":
                yield chunk
        
        # Save final assistant response
        if full_response:
            self.session.add_message(role="assistant", content=full_response)
    
    def new_session(self):
        """Start a new session"""
        self.session = Session.create(self.directory)
