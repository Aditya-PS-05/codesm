"""Main agent - orchestrates LLM calls and tool execution"""

import logging
from pathlib import Path
from typing import AsyncIterator

from codesm.provider.base import get_provider, StreamChunk
from codesm.tool.registry import ToolRegistry
from codesm.session.session import Session
from codesm.agent.prompt import SYSTEM_PROMPT, build_system_prompt, format_available_skills
from codesm.agent.loop import ReActLoop
from codesm.mcp import MCPManager, load_mcp_config
from codesm.skills import SkillManager
from codesm.rules import RulesDiscovery

logger = logging.getLogger(__name__)


class Agent:
    """AI coding agent that can read, write, and execute code"""

    def __init__(
        self,
        directory: Path,
        model: str,
        session: Session | None = None,
        max_iterations: int = 0,  # 0 = unlimited
        mcp_config_path: Path | str | None = None,
    ):
        self.directory = Path(directory).resolve()
        self._model = model
        self.session = session or Session.create(self.directory)
        self.max_iterations = max_iterations
        self.tools = ToolRegistry()
        self.provider = get_provider(self._model)
        self.react_loop = ReActLoop(max_iterations=self.max_iterations)
        
        # MCP support - will be initialized on first chat
        self._mcp_manager: MCPManager | None = None
        self._mcp_config_path = mcp_config_path
        self._mcp_initialized = False
        
        # Skill system
        self.skills = SkillManager(
            workspace_dir=self.directory,
            auto_triggers_enabled=True,
        )
        
        # Rules discovery (AGENTS.md, CLAUDE.md, etc.)
        self.rules = RulesDiscovery(workspace=self.directory)

    @property
    def model(self) -> str:
        """Get current model"""
        return self._model

    @model.setter
    def model(self, value: str):
        """Set model and recreate provider"""
        self._model = value
        self.provider = get_provider(value)
    
    async def _init_mcp(self):
        """Initialize MCP servers if configured"""
        if self._mcp_initialized:
            return
        
        self._mcp_initialized = True
        
        # Load MCP config - search in working directory first
        config_path = self._mcp_config_path
        if not config_path:
            # Try common locations relative to working directory
            for candidate in [
                self.directory / "mcp-servers.json",
                self.directory / ".mcp" / "servers.json",
                self.directory / "codesm.json",
            ]:
                if candidate.exists():
                    config_path = candidate
                    break
        
        servers = load_mcp_config(config_path)
        if not servers:
            logger.debug(f"No MCP servers configured (searched {config_path or 'default locations'})")
            return
        
        # Create manager and connect
        self._mcp_manager = MCPManager()
        for name, config in servers.items():
            self._mcp_manager.add_server(config)
        
        logger.info(f"Connecting to {len(servers)} MCP servers...")
        results = await self._mcp_manager.connect_all()
        
        connected = sum(1 for v in results.values() if v)
        if connected > 0:
            # Register MCP manager with tool registry (includes code execution tools)
            self.tools.set_mcp_manager(self._mcp_manager, workspace_dir=self.directory)
            logger.info(f"Connected to {connected} MCP servers, {len(self._mcp_manager.get_tools())} MCP tools + code execution available")
    
    async def chat(self, message: str) -> AsyncIterator[str]:
        """Send a message and stream the response"""
        # Initialize MCP on first chat
        await self._init_mcp()
        
        # Add user message to session (saved immediately)
        self.session.add_message(role="user", content=message)
        
        # Generate title asynchronously using Claude Haiku via OpenRouter
        # This runs in background and doesn't block the chat
        import asyncio
        asyncio.create_task(self.session.generate_title_from_message(message))
        
        # Get conversation history
        messages = self.session.get_messages()
        
        # Auto-load skills based on message triggers
        auto_loaded = self.skills.auto_load_for_message(message)
        if auto_loaded:
            logger.info(f"Auto-loaded skills: {auto_loaded}")
        
        # Build system prompt with skills and rules
        skills_block = self.skills.render_active_for_prompt()
        available_skills_summary = format_available_skills(self.skills.list())
        custom_rules = self.rules.get_combined_rules()
        system_prompt = build_system_prompt(
            cwd=str(self.directory),
            skills_block=skills_block,
            available_skills_summary=available_skills_summary,
            custom_rules=custom_rules,
        )
        
        # Build context for tools
        context = {
            "session": self.session,
            "session_id": self.session.id,
            "cwd": self.directory,
            "workspace_dir": str(self.directory),
            "tools": self.tools,
            "model": self._model,
            "skills": self.skills,  # Add skills manager to context
        }
        
        # Run ReAct loop
        full_response = ""
        async for chunk in self.react_loop.execute(
            provider=self.provider,
            system_prompt=system_prompt,
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
                # Save tool results immediately for session recovery
                if chunk.name in ["edit", "write", "bash", "grep", "glob", "todo"]:
                    self.session.add_message(
                        role="tool_display",
                        content=chunk.content,
                        tool_name=chunk.name,
                        tool_call_id=chunk.id,
                    )
                yield chunk
        
        # Save final assistant response
        if full_response:
            self.session.add_message(role="assistant", content=full_response)
    
    def new_session(self):
        """Start a new session"""
        self.session = Session.create(self.directory)
        self.skills.clear()  # Clear loaded skills for new session
    
    async def cleanup(self):
        """Cleanup resources (disconnect MCP servers, etc.)"""
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()
            self._mcp_manager = None
            self._mcp_initialized = False
    
    def get_mcp_tools(self) -> list[dict]:
        """Get list of available MCP tools"""
        if self._mcp_manager:
            return self._mcp_manager.list_all_tools()
        return []
