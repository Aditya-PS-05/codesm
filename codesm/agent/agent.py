"""Main agent - orchestrates LLM calls and tool execution"""

import logging
import sqlite3
from pathlib import Path
from typing import AsyncIterator

from codesm.provider.base import get_provider, StreamChunk
from codesm.tool.registry import ToolRegistry
from codesm.session.session import Session
from codesm.agent.prompt import SYSTEM_PROMPT, build_system_prompt, format_available_skills
from codesm.agent.loop import ReActLoop
from codesm.agent.event_log import EventLogger
from codesm.mcp import MCPManager, load_mcp_config
from codesm.skills import SkillManager
from codesm.rules import RulesDiscovery

logger = logging.getLogger(__name__)


class Agent:
    """AI coding agent that can read, write, and execute code"""

    def __init__(
        self,
        directory: Path,
        model: str | None = None,
        session: Session | None = None,
        max_iterations: int = 0,  # 0 = unlimited
        mcp_config_path: Path | str | None = None,
        config=None,
    ):
        self.directory = Path(session.directory if session else directory).resolve()
        from codesm.config import Config
        from codesm.agent.optimizer import CostLatencyOptimizer, Budget
        self.config = config or Config.load(directory=self.directory)
        self.profile = self.config.agents.get("main")
        self._chat_active = False
        self._retired_providers = []
        self._model = model or (self.profile.model if self.profile else None) or self.config.model
        self.budget = CostLatencyOptimizer(budget=Budget(
            session_limit=self.config.budget_usd if self.config.budget_usd is not None else 5.0,
            daily_limit=float("inf"), hard_limit=self.config.budget_usd is not None))
        self.budget.prices = {key: value.model_dump() for key, value in self.config.model_prices.items()}
        self.budget.max_requests = self.config.max_requests
        self.budget.max_task_tokens = self.config.max_task_tokens
        self.session = session or Session.create(self.directory)
        from codesm.memory.history import HistoryStore
        try:
            HistoryStore().import_project(self.directory)
        except (OSError, sqlite3.Error) as error:
            logger.warning("History index unavailable; continuing with saved session: %s", error)
        self._budget_session = None
        self._restore_budget()
        self.max_iterations = max_iterations or (self.profile.max_iterations if self.profile else 0)
        self.tools = ToolRegistry()
        self.provider = get_provider(self._model, self.config)
        self._configure_provider()
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

        # Optional eval instrumentation hooks. When the eval runner sets
        # these, chat() propagates them into the ReAct loop context so the
        # loop can record compaction, tool errors, and token usage.
        self._eval_events: list[dict] | None = None
        self._eval_usage: dict | None = None

        # Per session failure mode event log. Writes JSONL to
        # ~/.local/share/codesm/events/<session_id>.jsonl on every
        # compaction, tool error, permission denial, malformed tool
        # call, and max iteration cutoff. Used for real run analysis
        # and also surfaced in eval reports.
        self._event_logger: EventLogger = EventLogger(session_id=self.session.id)

    @property
    def model(self) -> str:
        """Get current model"""
        return self._model

    @model.setter
    def model(self, value: str):
        """Set model and recreate provider"""
        if self._chat_active:
            raise RuntimeError("Finish or cancel the active task before switching models")
        provider = get_provider(value, self.config)
        previous = self._model
        self._model = value
        self._retired_providers.append(self.provider)
        self.provider = provider
        self._configure_provider()
        self.session.save()
        self._event_logger.emit("model_changed", previous=previous, model=value)

    def _configure_provider(self):
        provider_id = getattr(self.provider, "model_key", self._model).split("/", 1)[0]
        settings = self.config.providers.get(provider_id)
        self.provider.options = dict(settings.options) if settings else {}
        if self.profile:
            self.provider.options.update(self.profile.model_dump(include={"reasoning_effort", "max_output_tokens"}, exclude_none=True))
    
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
    
    async def chat(self, message: str) -> AsyncIterator[StreamChunk]:
        from contextlib import aclosing
        if self._chat_active:
            raise RuntimeError("This agent already has an active task")
        self._chat_active = True
        session = self.session
        try:
            for provider in self._retired_providers:
                if hasattr(provider, "close"):
                    await provider.close()
            self._retired_providers.clear()
            async with aclosing(self._chat(message)) as stream:
                async for chunk in stream:
                    if chunk.type == "run_status":
                        session.run_state["status"] = chunk.content
                    yield chunk
        except BaseException as error:
            import asyncio
            status = "interrupted" if isinstance(error, (asyncio.CancelledError, GeneratorExit)) else "failed"
            session.run_state.update(status=status, error=str(error))
            raise
        finally:
            try:
                session.commit_pending_response()
                session.save()
            finally:
                self._chat_active = False

    async def _chat(self, message: str) -> AsyncIterator[StreamChunk]:
        self._restore_budget()
        session = self.session
        if message == "/debug off":
            session.debug_state = {}
            session.save()
            yield StreamChunk(type="text", content="Debugging workflow disabled.")
            return
        if message.startswith("/debug "):
            message = message.removeprefix("/debug ").strip()
            session.debug_state = {"status": "unverified", "attempts": 0, "max_attempts": 3, "hypotheses": []}
        
        # Add user message to session (saved immediately)
        session.add_message(role="user", content=message)
        # Save the request before external initialization can fail.
        await self._init_mcp()
        
        # Get conversation history
        messages = session.get_messages()
        
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
        if self.profile and self.profile.prompt:
            system_prompt += "\n\n" + self.profile.prompt
        system_prompt += f"\nDelegation mode: {self.config.delegation}."
        if self.config.delegation == "specialists":
            system_prompt += " Choose an explicit specialist role when delegating; automatic routing is disabled."
        if session.debug_state:
            from codesm.tool.debug import DEBUG_PROMPT
            system_prompt += "\n\n" + DEBUG_PROMPT + "\nCurrent debugging record:\n" + str(session.debug_state)
        from codesm.memory.continuation import continuation_context
        system_prompt += "\n\n" + continuation_context(session, message)
        session.last_model = self._model
        session.run_state = {"status": "running", "model": self._model, "request": message}
        session.save()
        
        # Ensure the event logger matches the current session (the Agent
        # can be mutated to a new session via new_session()).
        if self._event_logger.session_id != session.id:
            self._event_logger = EventLogger(session_id=session.id)

        # Build context for tools
        context = {
            "session": session,
            "session_id": session.id,
            "agent_runs": session.agent_runs,
            "usage_records": session.usage_records,
            "save_session": session.save,
            "cwd": self.directory,
            "workspace_dir": str(self.directory),
            "tools": self.tools,
            "model": self._model,
            "config": self.config,
            "budget": self.budget,
            "read_only": self.config.read_only,
            "denied_tools": self.config.denied_tools + ([name for name, enabled in self.profile.tools.items() if not enabled] if self.profile else []),
            "allowed_tools": ({name for name, enabled in self.profile.tools.items() if enabled} or None) if self.profile else None,
            "context_tokens": self.profile.context_tokens if self.profile else 128000,
            "rules": custom_rules,
            "debug_state": session.debug_state,
            "file_state": session.file_state,
            "run_id": session.id,
            "task_constraints": "\n".join(m.get("content", "") for m in messages if m.get("role") == "user"),
            "skills": self.skills,  # Add skills manager to context
            "event_logger": self._event_logger,
        }

        # Propagate eval instrumentation hooks if the runner attached any.
        if self._eval_events is not None:
            context["eval_events"] = self._eval_events
        if self._eval_usage is not None:
            context["eval_usage"] = self._eval_usage
        
        import asyncio
        from contextlib import aclosing
        queue = asyncio.Queue()
        context["event_queue"] = queue

        async def produce():
            try:
                async with aclosing(self.react_loop.execute(
                    provider=self.provider, system_prompt=system_prompt,
                    messages=messages, tools=self.tools, context=context,
                )) as stream:
                    async for chunk in stream:
                        queue.put_nowait(chunk)
            except BaseException as error:
                queue.put_nowait(error)
            finally:
                queue.put_nowait(None)

        producer = asyncio.create_task(produce())
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if isinstance(chunk, BaseException):
                    raise chunk
                if chunk.type == "tool_result":
                    session.add_message(role="tool_display", content=chunk.content,
                        tool_name=chunk.name, tool_call_id=chunk.id)
                yield chunk
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    def _restore_budget(self):
        if self._budget_session == self.session.id:
            return
        from codesm.agent.optimizer import UsageRecord
        self.budget.reset_session()
        for fields in self.session.usage_records:
            record = UsageRecord(**fields)
            self.budget._session_usage.append(record)
            self.budget._session_cost += record.cost
            self.budget._model_stats[record.model].add(record)
            self.budget._requests += 1
            self.budget._tokens += record.input_tokens + record.output_tokens
        self._budget_session = self.session.id

    def new_session(self):
        """Start a new session"""
        if self._chat_active:
            raise RuntimeError("Cancel or finish the active task before starting a new session")
        self.session = Session.create(self.directory)
        self._restore_budget()
        self.skills.clear()  # Clear loaded skills for new session
    
    async def cleanup(self):
        """Cleanup resources (disconnect MCP servers, etc.)"""
        for provider in [self.provider, *self._retired_providers]:
            if hasattr(provider, "close"):
                await provider.close()
        self._retired_providers.clear()
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()
            self._mcp_manager = None
            self._mcp_initialized = False
    
    def get_mcp_tools(self) -> list[dict]:
        """Get list of available MCP tools"""
        if self._mcp_manager:
            return self._mcp_manager.list_all_tools()
        return []
