"""Orchestrate tool - advanced subagent spawning and coordination"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)


class OrchestrateTool(Tool):
    """Orchestrate multiple subagents with dependency management"""
    
    name = "orchestrate"
    description = "Spawn and coordinate multiple subagents with dependencies. Use for complex multi-stage tasks."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def set_parent(self, tools: "ToolRegistry"):
        self._parent_tools = tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "stages": {
                    "type": "array",
                    "description": "List of stages. Each stage is a list of tasks that run in parallel. Stages run sequentially.",
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "subagent_type": {
                                    "type": "string",
                                    "enum": ["coder", "researcher", "reviewer", "planner", "oracle", "finder", "librarian", "auto"],
                                    "description": "Type of subagent or 'auto' for automatic routing",
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Task prompt for the subagent",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Short description of the task",
                                },
                            },
                            "required": ["subagent_type", "prompt", "description"],
                        },
                    },
                },
                "fail_fast": {
                    "type": "boolean",
                    "description": "Stop all tasks if one fails (default: false)",
                    "default": False,
                },
            },
            "required": ["stages"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.agent.orchestrator import SubAgentOrchestrator, OrchestrationPlan, SubAgentTask, SubAgentStatus
        
        stages_data = args.get("stages", [])
        fail_fast = args.get("fail_fast", False)
        
        if not stages_data:
            return "Error: stages is required - provide at least one stage with tasks"
        
        # Validate total tasks
        total_tasks = sum(len(stage) for stage in stages_data)
        if total_tasks > 10:
            return f"Error: Maximum 10 total tasks allowed, got {total_tasks}"
        
        workspace_dir = context.get("workspace_dir") or context.get("cwd")
        parent_tools = context.get("tools") or self._parent_tools
        parent_model = context.get("model", "anthropic/claude-sonnet-4-20250514")
        
        if not workspace_dir or not parent_tools:
            return "Error: Missing workspace or tools context"
        
        # Create orchestrator
        orchestrator = SubAgentOrchestrator(
            directory=Path(workspace_dir),
            parent_tools=parent_tools,
            parent_model=parent_model,
            max_concurrent=5,
        )
        
        # Build stages
        stages = []
        for stage_data in stages_data:
            stage_tasks = []
            for task_data in stage_data:
                task = orchestrator.create_task(
                    subagent_type=task_data["subagent_type"],
                    prompt=task_data["prompt"],
                    description=task_data.get("description", ""),
                )
                stage_tasks.append(task)
            stages.append(stage_tasks)
        
        # Create and execute plan
        plan = OrchestrationPlan.staged(stages)
        
        logger.info(f"Executing orchestration plan: {len(stages)} stages, {total_tasks} tasks")
        
        try:
            await orchestrator.execute_plan(plan, fail_fast=fail_fast)
        except Exception as e:
            logger.exception("Orchestration failed")
        
        # Build result
        summary = orchestrator.get_summary()
        
        output_parts = [
            f"## Orchestration Complete",
            f"",
            f"**Summary:** {summary['completed']} completed, {summary['failed']} failed, {summary['cancelled']} cancelled",
            f"**Total Duration:** {summary['total_duration']:.1f}s",
            "",
        ]
        
        # Add results by stage
        task_index = 0
        for stage_num, stage in enumerate(stages, 1):
            output_parts.append(f"### Stage {stage_num}")
            output_parts.append("")
            
            for task in stage:
                status_icon = {
                    SubAgentStatus.COMPLETED: "✓",
                    SubAgentStatus.FAILED: "✗",
                    SubAgentStatus.CANCELLED: "⊘",
                }.get(task.status, "?")
                
                output_parts.append(f"**{status_icon} {task.description}** @{task.subagent_type}")
                
                if task.status == SubAgentStatus.COMPLETED:
                    # Truncate long results
                    result = task.result
                    if len(result) > 1500:
                        result = result[:1500] + "... (truncated)"
                    output_parts.append(result)
                elif task.status == SubAgentStatus.FAILED:
                    output_parts.append(f"_Error: {task.error}_")
                
                output_parts.append("")
                task_index += 1
        
        return "\n".join(output_parts)


class PipelineTool(Tool):
    """Run a pipeline of subagents where each passes context to the next"""
    
    name = "pipeline"
    description = "Run subagents in sequence, passing results from one to the next. Use for chained workflows."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def set_parent(self, tools: "ToolRegistry"):
        self._parent_tools = tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Pipeline steps. Each step receives the result of the previous step as context.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subagent_type": {
                                "type": "string",
                                "enum": ["coder", "researcher", "reviewer", "planner", "oracle", "finder", "librarian", "auto"],
                            },
                            "prompt_template": {
                                "type": "string",
                                "description": "Prompt template. Use {previous_result} to inject the previous step's output.",
                            },
                            "description": {
                                "type": "string",
                            },
                        },
                        "required": ["subagent_type", "prompt_template", "description"],
                    },
                },
                "initial_context": {
                    "type": "string",
                    "description": "Initial context passed to the first step",
                },
            },
            "required": ["steps"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.agent.orchestrator import SubAgentOrchestrator, SubAgentStatus
        
        steps = args.get("steps", [])
        initial_context = args.get("initial_context", "")
        
        if not steps:
            return "Error: steps is required"
        
        if len(steps) > 5:
            return "Error: Maximum 5 pipeline steps allowed"
        
        workspace_dir = context.get("workspace_dir") or context.get("cwd")
        parent_tools = context.get("tools") or self._parent_tools
        parent_model = context.get("model", "anthropic/claude-sonnet-4-20250514")
        
        if not workspace_dir or not parent_tools:
            return "Error: Missing workspace or tools context"
        
        orchestrator = SubAgentOrchestrator(
            directory=Path(workspace_dir),
            parent_tools=parent_tools,
            parent_model=parent_model,
        )
        
        # Execute pipeline
        previous_result = initial_context
        results = []
        
        for i, step in enumerate(steps, 1):
            # Build prompt with previous result
            prompt = step["prompt_template"].replace("{previous_result}", previous_result)
            
            task = orchestrator.create_task(
                subagent_type=step["subagent_type"],
                prompt=prompt,
                description=step.get("description", f"Step {i}"),
            )
            
            try:
                await orchestrator.spawn(task)
                previous_result = task.result
                results.append((step["description"], task))
            except Exception as e:
                results.append((step["description"], task))
                break  # Stop pipeline on failure
        
        # Build output
        output_parts = [
            "## Pipeline Complete",
            "",
        ]
        
        for i, (desc, task) in enumerate(results, 1):
            status_icon = "✓" if task.status == SubAgentStatus.COMPLETED else "✗"
            output_parts.append(f"### Step {i}: {status_icon} {desc}")
            output_parts.append("")
            
            if task.status == SubAgentStatus.COMPLETED:
                result = task.result
                if len(result) > 2000:
                    result = result[:2000] + "... (truncated)"
                output_parts.append(result)
            else:
                output_parts.append(f"_Failed: {task.error}_")
            
            output_parts.append("")
        
        return "\n".join(output_parts)
