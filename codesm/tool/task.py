"""Task tool - spawn subagents for delegated work"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)


# Maximum parallel subagents to prevent resource exhaustion
MAX_PARALLEL_TASKS = 10


class TaskTool(Tool):
    name = "task"
    description = "Launch a subagent to handle a complex task autonomously. Use auto_route=true to let the router pick the best subagent."
    
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
                    "enum": ["coder", "researcher", "reviewer", "planner", "oracle", "finder", "librarian", "auto"],
                    "description": "Type of subagent. Use 'auto' to let the router pick the best one based on task complexity.",
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
        from codesm.agent.router import route_task, TaskComplexity
        
        subagent_type = args.get("subagent_type", "")
        prompt = args.get("prompt", "")
        description = args.get("description", "Task")
        
        if not subagent_type:
            available = ", ".join(c.name for c in list_subagent_configs())
            return f"Error: subagent_type is required. Available: {available}"
        
        if not prompt:
            return "Error: prompt is required - describe what the subagent should do"
        
        # Auto-routing: let the router pick the best subagent
        routing_info = ""
        if subagent_type == "auto":
            try:
                decision = await route_task(prompt)
                if decision.recommended_subagent:
                    subagent_type = decision.recommended_subagent
                else:
                    # Default to coder for tasks without specific subagent
                    subagent_type = "coder"
                routing_info = f"\n_Routed: {decision.task_type.value} ({decision.complexity.value}) → {subagent_type}_"
                logger.info(f"Auto-routed task to {subagent_type}: {decision.reasoning}")
            except Exception as e:
                logger.warning(f"Auto-routing failed, defaulting to coder: {e}")
                subagent_type = "coder"
        
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
            
            return self._format_result(description, subagent_type, result) + routing_info
            
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


def _load_parallel_tasks_description() -> str:
    """Load description from parallel_tasks.txt"""
    import os
    txt_path = os.path.join(os.path.dirname(__file__), "parallel_tasks.txt")
    try:
        with open(txt_path) as f:
            return f.read()
    except FileNotFoundError:
        return "Launch multiple subagents to run in parallel for independent tasks. Use for executing 2-10 independent pieces of work concurrently."


class ParallelTaskTool(Tool):
    """Execute multiple subagent tasks in parallel with progress tracking.
    
    Similar to opencode's batch tool and task tool patterns, this allows spawning
    multiple independent subagents concurrently for faster execution of parallelizable work.
    """
    name = "parallel_tasks"
    description = _load_parallel_tasks_description()
    
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
                "tasks": {
                    "type": "array",
                    "description": "List of tasks to run in parallel (max 10)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subagent_type": {
                                "type": "string",
                                "enum": ["coder", "researcher", "reviewer", "planner", "oracle", "finder", "librarian", "auto"],
                                "description": "Type of subagent. 'auto' uses router to pick best type.",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Detailed task instructions for the subagent",
                            },
                            "description": {
                                "type": "string",
                                "description": "Short 3-5 word summary (e.g., 'Fix auth bug')",
                            },
                        },
                        "required": ["subagent_type", "prompt", "description"],
                    },
                    "minItems": 1,
                    "maxItems": 10,
                },
                "fail_fast": {
                    "type": "boolean",
                    "description": "If true, cancel remaining tasks on first failure. Default: false",
                    "default": False,
                },
            },
            "required": ["tasks"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.agent.subagent import SubAgent, get_subagent_config
        
        tasks = args.get("tasks", [])
        fail_fast = args.get("fail_fast", False)
        
        if not tasks:
            return "Error: No tasks provided"
        
        # Enforce maximum parallel tasks
        if len(tasks) > MAX_PARALLEL_TASKS:
            tasks = tasks[:MAX_PARALLEL_TASKS]
            logger.warning(f"Truncated to {MAX_PARALLEL_TASKS} parallel tasks")
        
        workspace_dir = context.get("workspace_dir") or context.get("cwd")
        parent_tools = context.get("tools") or self._parent_tools
        parent_model = context.get("model") or self._parent_model
        
        if not workspace_dir:
            return "Error: Missing workspace directory in context"
        
        if not parent_tools:
            return "Error: Missing tools context - ensure parent registry is set"
        
        # Track results with timing
        @dataclass
        class TaskResult:
            description: str
            subagent_type: str
            result: str = ""
            error: str = ""
            success: bool = False
            duration_ms: int = 0
            tools_used: list[str] = field(default_factory=list)
        
        results: list[TaskResult] = []
        semaphore = asyncio.Semaphore(MAX_PARALLEL_TASKS)
        cancel_event = asyncio.Event() if fail_fast else None
        
        async def run_task(task: dict) -> TaskResult:
            """Run a single subagent task with timing and error handling"""
            from codesm.agent.router import route_task
            
            task_result = TaskResult(
                description=task.get("description", "Unknown"),
                subagent_type=task.get("subagent_type", "auto"),
            )
            
            # Check for cancellation (fail_fast mode)
            if cancel_event and cancel_event.is_set():
                task_result.error = "Cancelled (fail_fast)"
                return task_result
            
            start_time = time.time()
            
            try:
                async with semaphore:
                    subagent_type = task["subagent_type"]
                    
                    # Auto-routing support
                    if subagent_type == "auto":
                        try:
                            decision = await route_task(task["prompt"])
                            subagent_type = decision.recommended_subagent or "coder"
                            task_result.subagent_type = subagent_type
                        except Exception:
                            subagent_type = "coder"
                            task_result.subagent_type = subagent_type
                    
                    config = get_subagent_config(subagent_type)
                    if not config:
                        task_result.error = f"Unknown subagent type: {subagent_type}"
                        return task_result
                    
                    subagent = SubAgent(
                        config=config,
                        directory=Path(workspace_dir),
                        parent_model=parent_model,
                        parent_tools=parent_tools,
                    )
                    
                    result = await subagent.run(task["prompt"])
                    
                    task_result.result = result
                    task_result.success = True
                    task_result.duration_ms = int((time.time() - start_time) * 1000)
                    
            except asyncio.CancelledError:
                task_result.error = "Cancelled"
            except Exception as e:
                task_result.error = str(e)
                task_result.duration_ms = int((time.time() - start_time) * 1000)
                logger.exception(f"Parallel task failed: {task_result.description}")
                
                # Signal cancellation in fail_fast mode
                if cancel_event:
                    cancel_event.set()
            
            return task_result
        
        # Run all tasks in parallel with asyncio.gather
        logger.info(f"Starting {len(tasks)} parallel subagent tasks")
        start_time = time.time()
        
        results = await asyncio.gather(
            *[run_task(t) for t in tasks],
            return_exceptions=False  # Exceptions handled within run_task
        )
        
        total_duration_ms = int((time.time() - start_time) * 1000)
        
        # Compute summary stats
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful
        
        # Format output
        output_parts = []
        
        # Header with summary
        status_label = "[OK]" if failed == 0 else "[PARTIAL]" if successful > 0 else "[FAIL]"
        output_parts.append(
            f"{status_label} **Parallel Tasks Complete**: "
            f"{successful}/{len(results)} succeeded in {total_duration_ms}ms"
        )

        if failed > 0:
            output_parts.append(f"\n[WARN] {failed} task(s) failed")
        
        output_parts.append("\n")
        
        # Individual results
        for i, r in enumerate(results, 1):
            marker = "[OK]" if r.success else "[FAIL]"
            header = f"\n---\n### {i}. {marker} {r.description} @{r.subagent_type} ({r.duration_ms}ms)"
            output_parts.append(header)
            
            if r.success:
                # Truncate long results
                result_text = r.result
                if len(result_text) > 3000:
                    result_text = result_text[:3000] + f"\n... _(truncated, {len(r.result) - 3000} chars omitted)_"
                output_parts.append(f"\n{result_text}")
            else:
                output_parts.append(f"\n**Error:** {r.error}")
        
        return "\n".join(output_parts)
