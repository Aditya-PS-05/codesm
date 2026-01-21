"""Tool registry"""

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from ..mcp.manager import MCPManager

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._mcp_manager: "MCPManager | None" = None
        self._register_defaults()
    
    def _register_defaults(self):
        """Register built-in tools"""
        from .read import ReadTool
        from .write import WriteTool
        from .edit import EditTool
        from .multiedit import MultiEditTool
        from .bash import BashTool
        from .grep import GrepTool
        from .glob import GlobTool
        from .webfetch import WebFetchTool
        from .websearch import WebSearchTool
        from .diagnostics import DiagnosticsTool
        from .codesearch import CodeSearchTool
        from .todo import TodoTool
        from .ls import ListTool
        from .batch import BatchTool
        from .patch import PatchTool
        from .task import TaskTool
        from .skill import SkillTool
        from .undo import UndoTool
        from .lookat import LookAtTool
        from .oracle import OracleTool
        from .finder import FinderTool
        from .handoff import HandoffTool
        from .orchestrate import OrchestrateTool, PipelineTool

        for tool_class in [ReadTool, WriteTool, EditTool, MultiEditTool, BashTool, GrepTool, GlobTool, WebFetchTool, WebSearchTool, DiagnosticsTool, CodeSearchTool, TodoTool, ListTool, BatchTool, PatchTool, SkillTool, UndoTool, LookAtTool]:
            tool = tool_class()
            self._tools[tool.name] = tool
        
        # Task tool needs special initialization (needs reference to registry)
        task_tool = TaskTool(parent_tools=self)
        self._tools[task_tool.name] = task_tool
        
        # Oracle tool also needs reference to registry
        oracle_tool = OracleTool(parent_tools=self)
        self._tools[oracle_tool.name] = oracle_tool
        
        # Finder tool needs reference to registry for grep/glob access
        finder_tool = FinderTool(parent_tools=self)
        self._tools[finder_tool.name] = finder_tool
        
        # Handoff tool for context transfer to new threads
        handoff_tool = HandoffTool(parent_tools=self)
        self._tools[handoff_tool.name] = handoff_tool
        
        # Orchestration tools for multi-subagent coordination
        orchestrate_tool = OrchestrateTool(parent_tools=self)
        self._tools[orchestrate_tool.name] = orchestrate_tool
        
        pipeline_tool = PipelineTool(parent_tools=self)
        self._tools[pipeline_tool.name] = pipeline_tool
    
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
        
        return schemas
    
    async def execute(self, name: str, args: dict, context: dict) -> str:
        """Execute a tool by name (includes MCP tools)"""
        # Try built-in tools first
        tool = self._tools.get(name)
        
        # Try MCP tools if not found
        if not tool and self._mcp_manager:
            tool = self._mcp_manager.get_tool(name)
        
        if not tool:
            return f"Error: Unknown tool '{name}'"
        
        try:
            return await tool.execute(args, context)
        except Exception as e:
            return f"Error executing {name}: {e}"
    
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
            return (call_id, name, result)
        
        tasks = [
            execute_one(call_id, name, args) 
            for call_id, name, args in tool_calls
        ]
        
        return await asyncio.gather(*tasks)
