"""Tool registry"""

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from ..mcp.manager import MCPManager

logger = logging.getLogger(__name__)

# Unknown and external tools are treated as mutating, regardless of their names.
READ_ONLY_TOOLS = frozenset({
    "read", "grep", "glob", "ls", "codesearch", "websearch", "webfetch",
    "diagnostics", "lsp", "look_at", "find_thread", "read_thread", "recall",
})
DELEGATION_TOOLS = frozenset({"task", "parallel_tasks", "pipeline", "orchestrate", "oracle", "finder", "code_review", "refactor", "refactor_apply", "testgen", "bug_localize", "diagram", "look_at", "handoff", "read_thread"})
COMPOSITE_TOOLS = frozenset({"batch", "task", "parallel_tasks", "pipeline", "orchestrate", "oracle", "debug", "handoff"})


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._mcp_manager: "MCPManager | None" = None
        self._writer_lock = asyncio.Lock()
        self._register_defaults()
    
    def _register_defaults(self):
        """Register built-in tools"""
        from .read import ReadTool
        from .write import WriteTool
        from .edit import EditTool
        from .multiedit import MultiEditTool
        from .multifile_edit import MultiFileEditTool
        from .bash import BashTool
        from .git import GitTool
        from .grep import GrepTool
        from .glob import GlobTool
        from .webfetch import WebFetchTool
        from .websearch import WebSearchTool
        from .diagnostics import DiagnosticsTool
        from .lsp import LSPTool
        from .codesearch import CodeSearchTool
        from .todo import TodoTool
        from .ls import ListTool
        from .batch import BatchTool
        from .patch import PatchTool
        from .task import TaskTool, ParallelTaskTool
        from .skill import SkillTool
        from .undo import UndoTool
        from .redo import RedoTool
        from .lookat import LookAtTool
        from .oracle import OracleTool
        from .finder import FinderTool
        from .handoff import HandoffTool
        from .find_thread import FindThreadTool
        from .read_thread import ReadThreadTool
        from .orchestrate import OrchestrateTool, PipelineTool
        from .mermaid import MermaidTool, DiagramGeneratorTool
        from .code_review import CodeReviewTool
        from .testgen import TestGenTool
        from .bug_localize import BugLocalizeTool
        from .refactor import RefactorTool, RefactorApplyTool
        from .mark_uncertain import MarkUncertainTool
        from .debug import DebugTool
        from .recall import RecallTool
        self.register(DebugTool())
        self.register(RecallTool())

        for tool_class in [ReadTool, WriteTool, EditTool, MultiEditTool, MultiFileEditTool, BashTool, GitTool, GrepTool, GlobTool, WebFetchTool, WebSearchTool, DiagnosticsTool, LSPTool, CodeSearchTool, TodoTool, ListTool, BatchTool, PatchTool, SkillTool, UndoTool, RedoTool, LookAtTool, MarkUncertainTool]:
            tool = tool_class()
            self._tools[tool.name] = tool
        
        # Task tool needs special initialization (needs reference to registry)
        task_tool = TaskTool(parent_tools=self)
        self._tools[task_tool.name] = task_tool
        
        # Parallel task tool for concurrent subagent execution
        parallel_task_tool = ParallelTaskTool(parent_tools=self)
        self._tools[parallel_task_tool.name] = parallel_task_tool
        
        # Oracle tool also needs reference to registry
        oracle_tool = OracleTool(parent_tools=self)
        self._tools[oracle_tool.name] = oracle_tool
        
        # Finder tool needs reference to registry for grep/glob access
        finder_tool = FinderTool(parent_tools=self)
        self._tools[finder_tool.name] = finder_tool
        
        # Handoff tool for context transfer to new threads
        handoff_tool = HandoffTool(parent_tools=self)
        self._tools[handoff_tool.name] = handoff_tool
        
        # Thread search tools for cross-thread context
        find_thread_tool = FindThreadTool(parent_tools=self)
        self._tools[find_thread_tool.name] = find_thread_tool
        
        read_thread_tool = ReadThreadTool(parent_tools=self)
        self._tools[read_thread_tool.name] = read_thread_tool
        
        # Orchestration tools for multi-subagent coordination
        orchestrate_tool = OrchestrateTool(parent_tools=self)
        self._tools[orchestrate_tool.name] = orchestrate_tool
        
        pipeline_tool = PipelineTool(parent_tools=self)
        self._tools[pipeline_tool.name] = pipeline_tool
        
        # Mermaid diagram tools
        mermaid_tool = MermaidTool(parent_tools=self)
        self._tools[mermaid_tool.name] = mermaid_tool
        
        diagram_tool = DiagramGeneratorTool(parent_tools=self)
        self._tools[diagram_tool.name] = diagram_tool
        
        # Intelligence layer tools
        code_review_tool = CodeReviewTool(parent_tools=self)
        self._tools[code_review_tool.name] = code_review_tool
        
        testgen_tool = TestGenTool(parent_tools=self)
        self._tools[testgen_tool.name] = testgen_tool
        
        bug_localize_tool = BugLocalizeTool(parent_tools=self)
        self._tools[bug_localize_tool.name] = bug_localize_tool
        
        # Refactoring suggestion tools
        refactor_tool = RefactorTool(parent_tools=self)
        self._tools[refactor_tool.name] = refactor_tool
        
        refactor_apply_tool = RefactorApplyTool(parent_tools=self)
        self._tools[refactor_apply_tool.name] = refactor_apply_tool
    
    def register(self, tool: Tool):
        """Register a tool"""
        self._tools[tool.name] = tool
    
    def set_mcp_manager(self, manager: "MCPManager", workspace_dir=None):
        """Set the MCP manager for MCP tool integration"""
        self._mcp_manager = manager
        
        # Register code execution tools for efficient MCP usage
        from .mcp_execute import MCPExecuteTool, MCPToolsListTool, MCPSkillsTool
        
        mcp_execute = MCPExecuteTool(mcp_manager=manager, workspace_dir=workspace_dir)
        mcp_tools = MCPToolsListTool(mcp_manager=manager)
        mcp_skills = MCPSkillsTool(workspace_dir=workspace_dir)
        
        self._tools[mcp_execute.name] = mcp_execute
        self._tools[mcp_tools.name] = mcp_tools
        self._tools[mcp_skills.name] = mcp_skills
        
        logger.info("Registered MCP code execution tools: mcp_execute, mcp_tools, mcp_skills")
    
    def get(self, name: str) -> Tool | None:
        """Get a tool by name (includes MCP tools)"""
        # Check built-in tools first
        tool = self._tools.get(name)
        if tool:
            return tool
        
        # Check MCP tools
        if self._mcp_manager:
            return self._mcp_manager.get_tool(name)
        
        return None
    
    def get_schemas(self) -> list[dict]:
        """Get all tool schemas for LLM (includes MCP tools)"""
        schemas = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.get_parameters_schema(),
            }
            for tool in self._tools.values()
        ]
        
        # Add MCP tool schemas
        if self._mcp_manager:
            for tool in self._mcp_manager.get_tools():
                schemas.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.get_parameters_schema(),
                })
        
        from codesm.agent.execution import current_context
        context = current_context.get() or {}
        return [schema for schema in schemas if self._policy_error(schema["name"], context) is None]

    @staticmethod
    def _policy_error(name: str, context: dict) -> str | None:
        config = context.get("config")
        if config and config.delegation == "single" and name in DELEGATION_TOOLS:
            return f"Permission denied: {name} is unavailable in single-agent mode"
        if context.get("read_only") and name not in READ_ONLY_TOOLS and name != "batch":
            return f"Permission denied: {name} is unavailable in a read-only task"
        if name in context.get("denied_tools", ()):
            return f"Permission denied: {name} is disabled for this task"
        allowed = context.get("allowed_tools")
        if allowed is not None and name not in allowed:
            return f"Permission denied: {name} is not enabled for this task"
        return None
    
    async def execute(self, name: str, args: dict, context: dict) -> str:
        """Execute a tool by name (includes MCP tools)"""
        from codesm.agent.execution import current_context

        if not isinstance(args, dict):
            return "Error: Tool arguments must be an object"
        error = self._policy_error(name, context)
        if error:
            return error
        session = context.get("session")
        state = context.get("debug_state") or (session.debug_state if session else {})
        if state and name not in READ_ONLY_TOOLS | {"debug", "oracle"} and not context.get("debug_check"):
            if name not in COMPOSITE_TOOLS and state.get("reproduction", {}).get("exit_code") in (None, 0, -1):
                return "Error: Before modifying code or running shell commands, capture a failing reproduction with debug(action='reproduce', command=...). Use read/grep to inspect files first."
            if name == "bash" and args.get("command") == state.get("command"):
                # Rerunning the recorded check always consumes a verification attempt.
                return await self.execute("debug", {"action": "verify"}, context)
            if state.get("attempts", 0) >= state.get("max_attempts", 3):
                return "Permission denied: Debugging attempt limit reached; report the unresolved failure"
            if state.get("status") == "verified":
                state["status"] = "unverified"
                context.get("save_session", session.save if session else lambda: None)()
        # Apply existing guarded-path policy to every direct file-edit entry point.
        if name in {"write", "edit", "multiedit", "multifile_edit"}:
            from pathlib import Path
            from codesm.permission import check_path_permission, PathBlockedError
            root = Path(context.get("cwd", ".")).resolve()
            paths = [edit.get("path") for edit in args.get("edits", []) if isinstance(edit, dict)] if name == "multifile_edit" else [args.get("path")]
            try:
                for path in paths:
                    if not isinstance(path, str) or not path:
                        return "Error: A file path is required"
                    check_path_permission(root / Path(path).expanduser(), working_dir=root)
            except PathBlockedError as error:
                return f"Permission denied: {error}"
        # Try built-in tools first
        tool = self._tools.get(name)
        
        # Try MCP tools if not found
        if not tool and self._mcp_manager:
            tool = self._mcp_manager.get_tool(name)
        
        if not tool:
            return f"Error: Unknown tool '{name}'"
        
        token = current_context.set(context)
        from codesm.agent.execution import emit
        emit(context, "tool_started", tool=name, args=args)
        result = "Interrupted: outcome unknown; verify current state before retrying."
        try:
            if name in READ_ONLY_TOOLS or name in COMPOSITE_TOOLS or context.get("owns_writer"):
                result = await tool.execute(args, context)
            else:
                async with self._writer_lock:
                    result = await tool.execute(args, context)
        except Exception as e:
            result = f"Error executing {name}: {e}"
        finally:
            emit(context, "tool_finished", tool=name, result=result)
            current_context.reset(token)
        if name in {"read", "write", "edit", "multiedit", "multifile_edit"} and not result.startswith(("Error", "Permission denied")):
            from pathlib import Path
            files = context.get("file_state")
            if files is not None:
                paths = [edit.get("path") for edit in args.get("edits", []) if isinstance(edit, dict)] if name == "multifile_edit" else [args.get("path")]
                for filename in paths:
                    if not isinstance(filename, str):
                        continue
                    path = (Path(context.get("cwd", ".")) / Path(filename).expanduser()).resolve()
                    try:
                        stat = path.stat()
                        files[str(path)] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
                    except OSError:
                        pass
                context.get("save_session", lambda: None)()
        return result
    
    async def execute_parallel(
        self, 
        tool_calls: list[tuple[str, str, dict]], 
        context: dict
    ) -> list[tuple[str, str, str]]:
        """Execute multiple tools in parallel.
        
        Args:
            tool_calls: List of (tool_call_id, tool_name, args)
            context: Execution context
            
        Returns:
            List of (tool_call_id, tool_name, result)
        """
        async def execute_one(call_id: str, name: str, args: dict) -> tuple[str, str, str]:
            result = await self.execute(name, args, context)
            callback = context.get("on_tool_result")
            if callback:
                callback(call_id, name, result)
            return (call_id, name, result)
        
        # Only consecutive independent reads overlap. Every other call is a barrier.
        results = []
        reads = []
        for call in tool_calls:
            if call[1] in READ_ONLY_TOOLS:
                reads.append(call)
                continue
            if reads:
                results.extend(await asyncio.gather(*(execute_one(*c) for c in reads)))
                reads.clear()
            results.append(await execute_one(*call))
        if reads:
            results.extend(await asyncio.gather(*(execute_one(*c) for c in reads)))
        return results
