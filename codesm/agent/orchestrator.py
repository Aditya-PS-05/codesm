"""Subagent Orchestrator - manages spawning, lifecycle, and coordination of subagents"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from codesm.provider.base import StreamChunk
from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)


class SubAgentStatus(Enum):
    """Status of a subagent"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SubAgentTask:
    """A task assigned to a subagent"""
    id: str
    subagent_type: str
    prompt: str
    description: str
    status: SubAgentStatus = SubAgentStatus.PENDING
    result: str = ""
    error: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tools_used: list[str] = field(default_factory=list)
    token_count: int = 0
    
    @property
    def duration_seconds(self) -> float:
        """Get task duration in seconds"""
        if not self.started_at:
            return 0
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()


@dataclass
class OrchestrationPlan:
    """A plan for executing multiple tasks"""
    tasks: list[SubAgentTask]
    parallel_groups: list[list[str]]  # Groups of task IDs that can run in parallel
    dependencies: dict[str, list[str]]  # task_id -> list of dependency task_ids
    
    @classmethod
    def sequential(cls, tasks: list[SubAgentTask]) -> "OrchestrationPlan":
        """Create a sequential execution plan"""
        return cls(
            tasks=tasks,
            parallel_groups=[[t.id] for t in tasks],
            dependencies={tasks[i].id: [tasks[i-1].id] for i in range(1, len(tasks))},
        )
    
    @classmethod
    def parallel(cls, tasks: list[SubAgentTask]) -> "OrchestrationPlan":
        """Create a fully parallel execution plan"""
        return cls(
            tasks=tasks,
            parallel_groups=[[t.id for t in tasks]],
            dependencies={},
        )
    
    @classmethod
    def staged(cls, stages: list[list[SubAgentTask]]) -> "OrchestrationPlan":
        """Create a staged plan where each stage runs in parallel, but stages are sequential"""
        all_tasks = []
        parallel_groups = []
        dependencies = {}
        
        prev_stage_ids = []
        for stage in stages:
            stage_ids = [t.id for t in stage]
            parallel_groups.append(stage_ids)
            all_tasks.extend(stage)
            
            # Each task in this stage depends on all tasks from previous stage
            for task_id in stage_ids:
                if prev_stage_ids:
                    dependencies[task_id] = prev_stage_ids.copy()
            
            prev_stage_ids = stage_ids
        
        return cls(tasks=all_tasks, parallel_groups=parallel_groups, dependencies=dependencies)


class SubAgentOrchestrator:
    """Orchestrates multiple subagents with lifecycle management"""
    
    def __init__(
        self,
        directory: Path,
        parent_tools: ToolRegistry,
        parent_model: str = "anthropic/claude-sonnet-4-20250514",
        max_concurrent: int = 5,
    ):
        self.directory = Path(directory).resolve()
        self.parent_tools = parent_tools
        self.parent_model = parent_model
        self.max_concurrent = max_concurrent
        
        # Track active tasks
        self._tasks: dict[str, SubAgentTask] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        # Event callbacks
        self._on_task_start: Optional[Callable[[SubAgentTask], None]] = None
        self._on_task_complete: Optional[Callable[[SubAgentTask], None]] = None
        self._on_task_error: Optional[Callable[[SubAgentTask, Exception], None]] = None
    
    def create_task(
        self,
        subagent_type: str,
        prompt: str,
        description: str = "",
    ) -> SubAgentTask:
        """Create a new subagent task"""
        task = SubAgentTask(
            id=f"task_{uuid.uuid4().hex[:8]}",
            subagent_type=subagent_type,
            prompt=prompt,
            description=description or prompt[:50],
        )
        self._tasks[task.id] = task
        return task
    
    async def spawn(self, task: SubAgentTask) -> str:
        """Spawn a single subagent and wait for result"""
        from codesm.agent.subagent import SubAgent, get_subagent_config
        from codesm.agent.router import route_task
        
        task.status = SubAgentStatus.RUNNING
        task.started_at = datetime.now()
        
        if self._on_task_start:
            self._on_task_start(task)
        
        try:
            # Auto-route if needed
            subagent_type = task.subagent_type
            if subagent_type == "auto":
                try:
                    decision = await route_task(task.prompt)
                    subagent_type = decision.recommended_subagent or "coder"
                    logger.info(f"Auto-routed task {task.id} to {subagent_type}")
                except Exception:
                    subagent_type = "coder"
            
            config = get_subagent_config(subagent_type)
            if not config:
                raise ValueError(f"Unknown subagent type: {subagent_type}")
            
            subagent = SubAgent(
                config=config,
                directory=self.directory,
                parent_model=self.parent_model,
                parent_tools=self.parent_tools,
            )
            
            # Run with semaphore to limit concurrency
            async with self._semaphore:
                result = await subagent.run(task.prompt)
            
            task.result = result
            task.status = SubAgentStatus.COMPLETED
            task.completed_at = datetime.now()
            
            if self._on_task_complete:
                self._on_task_complete(task)
            
            return result
            
        except asyncio.CancelledError:
            task.status = SubAgentStatus.CANCELLED
            task.completed_at = datetime.now()
            raise
            
        except Exception as e:
            task.status = SubAgentStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now()
            
            if self._on_task_error:
                self._on_task_error(task, e)
            
            logger.exception(f"Task {task.id} failed")
            raise
    
    async def spawn_streaming(self, task: SubAgentTask) -> AsyncIterator[StreamChunk]:
        """Spawn a subagent with streaming output"""
        from codesm.agent.subagent import SubAgent, get_subagent_config
        from codesm.agent.router import route_task
        
        task.status = SubAgentStatus.RUNNING
        task.started_at = datetime.now()
        
        if self._on_task_start:
            self._on_task_start(task)
        
        try:
            subagent_type = task.subagent_type
            if subagent_type == "auto":
                try:
                    decision = await route_task(task.prompt)
                    subagent_type = decision.recommended_subagent or "coder"
                except Exception:
                    subagent_type = "coder"
            
            config = get_subagent_config(subagent_type)
            if not config:
                raise ValueError(f"Unknown subagent type: {subagent_type}")
            
            subagent = SubAgent(
                config=config,
                directory=self.directory,
                parent_model=self.parent_model,
                parent_tools=self.parent_tools,
            )
            
            full_response = ""
            async with self._semaphore:
                async for chunk in subagent.run_streaming(task.prompt):
                    if chunk.type == "text":
                        full_response += chunk.content
                    elif chunk.type == "tool_result":
                        task.tools_used.append(chunk.name)
                    yield chunk
            
            task.result = full_response
            task.status = SubAgentStatus.COMPLETED
            task.completed_at = datetime.now()
            
            if self._on_task_complete:
                self._on_task_complete(task)
                
        except asyncio.CancelledError:
            task.status = SubAgentStatus.CANCELLED
            task.completed_at = datetime.now()
            raise
            
        except Exception as e:
            task.status = SubAgentStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now()
            
            if self._on_task_error:
                self._on_task_error(task, e)
            raise
    
    async def spawn_parallel(
        self,
        tasks: list[SubAgentTask],
        fail_fast: bool = False,
    ) -> list[SubAgentTask]:
        """Spawn multiple subagents in parallel"""
        if not tasks:
            return []
        
        async def run_one(task: SubAgentTask) -> SubAgentTask:
            try:
                await self.spawn(task)
            except Exception:
                if fail_fast:
                    raise
            return task
        
        # Run all tasks concurrently
        await asyncio.gather(*[run_one(t) for t in tasks], return_exceptions=not fail_fast)
        
        return tasks
    
    async def execute_plan(
        self,
        plan: OrchestrationPlan,
        fail_fast: bool = False,
    ) -> list[SubAgentTask]:
        """Execute an orchestration plan with dependency tracking"""
        completed: set[str] = set()
        
        for group in plan.parallel_groups:
            # Wait for dependencies
            group_tasks = [t for t in plan.tasks if t.id in group]
            
            # Check dependencies are met
            for task in group_tasks:
                deps = plan.dependencies.get(task.id, [])
                for dep_id in deps:
                    if dep_id not in completed:
                        # Wait for dependency (shouldn't happen with proper plan)
                        logger.warning(f"Task {task.id} waiting for unmet dependency {dep_id}")
            
            # Execute group in parallel
            await self.spawn_parallel(group_tasks, fail_fast=fail_fast)
            
            # Mark completed
            for task in group_tasks:
                if task.status == SubAgentStatus.COMPLETED:
                    completed.add(task.id)
        
        return plan.tasks
    
    async def spawn_background(self, task: SubAgentTask) -> asyncio.Task:
        """Spawn a subagent in the background, returning immediately"""
        async_task = asyncio.create_task(self.spawn(task))
        self._running_tasks[task.id] = async_task
        return async_task
    
    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task"""
        if task_id in self._running_tasks:
            async_task = self._running_tasks[task_id]
            async_task.cancel()
            try:
                await async_task
            except asyncio.CancelledError:
                pass
            del self._running_tasks[task_id]
            
            if task_id in self._tasks:
                self._tasks[task_id].status = SubAgentStatus.CANCELLED
            
            return True
        return False
    
    async def cancel_all(self):
        """Cancel all running tasks"""
        for task_id in list(self._running_tasks.keys()):
            await self.cancel(task_id)
    
    def get_task(self, task_id: str) -> Optional[SubAgentTask]:
        """Get a task by ID"""
        return self._tasks.get(task_id)
    
    def get_all_tasks(self) -> list[SubAgentTask]:
        """Get all tasks"""
        return list(self._tasks.values())
    
    def get_running_tasks(self) -> list[SubAgentTask]:
        """Get currently running tasks"""
        return [t for t in self._tasks.values() if t.status == SubAgentStatus.RUNNING]
    
    def get_completed_tasks(self) -> list[SubAgentTask]:
        """Get completed tasks"""
        return [t for t in self._tasks.values() if t.status == SubAgentStatus.COMPLETED]
    
    def get_failed_tasks(self) -> list[SubAgentTask]:
        """Get failed tasks"""
        return [t for t in self._tasks.values() if t.status == SubAgentStatus.FAILED]
    
    def clear_completed(self):
        """Clear completed tasks from memory"""
        self._tasks = {
            tid: task for tid, task in self._tasks.items()
            if task.status in [SubAgentStatus.PENDING, SubAgentStatus.RUNNING]
        }
    
    def on_task_start(self, callback: Callable[[SubAgentTask], None]):
        """Register callback for task start"""
        self._on_task_start = callback
    
    def on_task_complete(self, callback: Callable[[SubAgentTask], None]):
        """Register callback for task completion"""
        self._on_task_complete = callback
    
    def on_task_error(self, callback: Callable[[SubAgentTask, Exception], None]):
        """Register callback for task error"""
        self._on_task_error = callback
    
    def get_summary(self) -> dict:
        """Get orchestrator summary"""
        tasks = list(self._tasks.values())
        return {
            "total": len(tasks),
            "pending": sum(1 for t in tasks if t.status == SubAgentStatus.PENDING),
            "running": sum(1 for t in tasks if t.status == SubAgentStatus.RUNNING),
            "completed": sum(1 for t in tasks if t.status == SubAgentStatus.COMPLETED),
            "failed": sum(1 for t in tasks if t.status == SubAgentStatus.FAILED),
            "cancelled": sum(1 for t in tasks if t.status == SubAgentStatus.CANCELLED),
            "total_duration": sum(t.duration_seconds for t in tasks if t.completed_at),
        }


# Convenience functions
async def spawn_subagent(
    subagent_type: str,
    prompt: str,
    directory: Path,
    parent_tools: ToolRegistry,
    parent_model: str = "anthropic/claude-sonnet-4-20250514",
) -> str:
    """Convenience function to spawn a single subagent"""
    orchestrator = SubAgentOrchestrator(
        directory=directory,
        parent_tools=parent_tools,
        parent_model=parent_model,
    )
    task = orchestrator.create_task(subagent_type, prompt)
    return await orchestrator.spawn(task)


async def spawn_parallel_subagents(
    tasks: list[tuple[str, str, str]],  # (subagent_type, prompt, description)
    directory: Path,
    parent_tools: ToolRegistry,
    parent_model: str = "anthropic/claude-sonnet-4-20250514",
    max_concurrent: int = 5,
) -> list[SubAgentTask]:
    """Convenience function to spawn multiple subagents in parallel"""
    orchestrator = SubAgentOrchestrator(
        directory=directory,
        parent_tools=parent_tools,
        parent_model=parent_model,
        max_concurrent=max_concurrent,
    )
    
    subagent_tasks = [
        orchestrator.create_task(stype, prompt, desc)
        for stype, prompt, desc in tasks
    ]
    
    return await orchestrator.spawn_parallel(subagent_tasks)
