"""Task tool - spawn subagents for delegated work"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)


class TaskTool(Tool):
    name = "task"
    description = "Launch a subagent to handle a complex task autonomously."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None, parent_model: str = "anthropic/claude-sonnet-4-20250514"):
        super().__init__()
        self._parent_tools = parent_tools
        self._parent_model = parent_model
    
    def set_parent(self, tools: "ToolRegistry", model: str):
        """Set parent context (called by Agent after init)"""
        self._parent_tools = tools
        self._parent_model = model
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "enum": ["coder", "researcher", "reviewer", "planner"],
                    "description": "Type of subagent: coder (implements code), researcher (analyzes without changes), reviewer (finds issues), planner (creates plans)",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed task description including context, file hints, and expected output",
                },
                "description": {
                    "type": "string",
                    "description": "Short 3-5 word summary for display (e.g., 'Add user authentication')",
                },
            },
            "required": ["subagent_type", "prompt", "description"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.agent.subagent import SubAgent, get_subagent_config, list_subagent_configs
        
        subagent_type = args.get("subagent_type", "")
        prompt = args.get("prompt", "")
        description = args.get("description", "Task")
        
        if not subagent_type:
            available = ", ".join(c.name for c in list_subagent_configs())
            return f"Error: subagent_type is required. Available: {available}"
        
        if not prompt:
            return "Error: prompt is required - describe what the subagent should do"
        
        # Get subagent config
        config = get_subagent_config(subagent_type)
        if not config:
            available = ", ".join(c.name for c in list_subagent_configs())
            return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"
        
        # Get workspace directory
        workspace_dir = context.get("workspace_dir") or context.get("cwd")
        if not workspace_dir:
            return "Error: No workspace directory in context"
        
        # Get parent tools - try from context first, then instance
        parent_tools = context.get("tools") or self._parent_tools
        if not parent_tools:
            return "Error: No parent tools available for subagent"
        
        parent_model = context.get("model") or self._parent_model
        
        # Create and run subagent
        logger.info(f"Spawning {subagent_type} subagent: {description}")
        
        try:
            subagent = SubAgent(
                config=config,
                directory=Path(workspace_dir),
                parent_model=parent_model,
                parent_tools=parent_tools,
            )
            
            result = await subagent.run(prompt)
            
            return self._format_result(description, subagent_type, result)
            
        except Exception as e:
            logger.exception(f"Subagent {subagent_type} failed")
            return f"**Task Failed** ({description})\n\nError: {e}"
    
    def _format_result(self, description: str, subagent_type: str, result: str) -> str:
        """Format the subagent result for display"""
        # Truncate very long results
        max_length = 8000
        if len(result) > max_length:
            result = result[:max_length] + f"\n\n... (truncated, {len(result) - max_length} chars omitted)"
        
        return f"**Task Complete** ({description}) @{subagent_type}\n\n{result}"


class ParallelTaskTool(Tool):
    """Execute multiple tasks in parallel"""
    name = "parallel_tasks"
    description = "Launch multiple subagents to run in parallel for independent tasks."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "List of tasks to run in parallel",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subagent_type": {
                                "type": "string",
                                "enum": ["coder", "researcher", "reviewer", "planner"],
                            },
                            "prompt": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["subagent_type", "prompt", "description"],
                    },
                },
            },
            "required": ["tasks"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.agent.subagent import SubAgent, get_subagent_config
        
        tasks = args.get("tasks", [])
        if not tasks:
            return "Error: No tasks provided"
        
        if len(tasks) > 5:
            return "Error: Maximum 5 parallel tasks allowed"
        
        workspace_dir = context.get("workspace_dir") or context.get("cwd")
        parent_tools = context.get("tools")
        parent_model = context.get("model", "anthropic/claude-sonnet-4-20250514")
        
        if not workspace_dir or not parent_tools:
            return "Error: Missing workspace or tools context"
        
        async def run_task(task: dict) -> tuple[str, str]:
            """Run a single task and return (description, result)"""
            config = get_subagent_config(task["subagent_type"])
            if not config:
                return (task["description"], f"Error: Unknown subagent type")
            
            try:
                subagent = SubAgent(
                    config=config,
                    directory=Path(workspace_dir),
                    parent_model=parent_model,
                    parent_tools=parent_tools,
                )
                result = await subagent.run(task["prompt"])
                return (task["description"], result)
            except Exception as e:
                return (task["description"], f"Error: {e}")
        
        # Run all tasks in parallel
        logger.info(f"Running {len(tasks)} tasks in parallel")
        results = await asyncio.gather(*[run_task(t) for t in tasks])
        
        # Format combined results
        output_parts = [f"**Parallel Tasks Complete** ({len(tasks)} tasks)\n"]
        
        for i, (desc, result) in enumerate(results, 1):
            # Truncate individual results
            if len(result) > 2000:
                result = result[:2000] + "... (truncated)"
            output_parts.append(f"\n---\n### {i}. {desc}\n{result}")
        
        return "\n".join(output_parts)
