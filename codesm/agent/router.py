"""Task Router - intelligent routing based on reasoning depth vs speed tradeoff"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TaskComplexity(Enum):
    """Task complexity levels"""
    TRIVIAL = "trivial"      # Simple lookup, one-liner answers
    SIMPLE = "simple"        # Straightforward tasks, single file edits
    MODERATE = "moderate"    # Multi-step tasks, some reasoning needed
    COMPLEX = "complex"      # Deep reasoning, multi-file, architecture decisions
    EXPERT = "expert"        # Requires o1-level reasoning, debugging, planning


class TaskType(Enum):
    """Types of tasks"""
    SEARCH = "search"           # Finding code, searching codebase
    READ = "read"               # Reading/understanding code
    EDIT = "edit"               # Editing files
    CREATE = "create"           # Creating new files/features
    DEBUG = "debug"             # Debugging issues
    REFACTOR = "refactor"       # Refactoring code
    REVIEW = "review"           # Code review
    PLAN = "plan"               # Planning/architecture
    RESEARCH = "research"       # External research
    EXPLAIN = "explain"         # Explaining concepts


@dataclass
class RoutingDecision:
    """Result of task routing analysis"""
    complexity: TaskComplexity
    task_type: TaskType
    recommended_model: str
    recommended_subagent: Optional[str]
    reasoning: str
    estimated_tokens: int  # Rough estimate of tokens needed
    parallelizable: bool   # Can be split into parallel subtasks


# Model tiers by capability
MODEL_TIERS = {
    TaskComplexity.TRIVIAL: {
        "model": "openrouter/google/gemini-2.0-flash-lite-001",
        "name": "Gemini Flash-Lite",
        "cost_per_1k": 0.00001,  # Very cheap
    },
    TaskComplexity.SIMPLE: {
        "model": "openrouter/anthropic/claude-3.5-haiku",
        "name": "Claude Haiku",
        "cost_per_1k": 0.00025,
    },
    TaskComplexity.MODERATE: {
        "model": "openrouter/anthropic/claude-sonnet-4",
        "name": "Claude Sonnet",
        "cost_per_1k": 0.003,
    },
    TaskComplexity.COMPLEX: {
        "model": "openrouter/anthropic/claude-sonnet-4",
        "name": "Claude Sonnet",
        "cost_per_1k": 0.003,
    },
    TaskComplexity.EXPERT: {
        "model": "openrouter/openai/o1",
        "name": "o1",
        "cost_per_1k": 0.015,
    },
}

# Subagent recommendations by task type
SUBAGENT_BY_TYPE = {
    TaskType.SEARCH: "finder",
    TaskType.READ: "researcher",
    TaskType.EDIT: "coder",
    TaskType.CREATE: "coder",
    TaskType.DEBUG: "oracle",
    TaskType.REFACTOR: "coder",
    TaskType.REVIEW: "reviewer",
    TaskType.PLAN: "planner",
    TaskType.RESEARCH: "librarian",
    TaskType.EXPLAIN: None,  # Main agent handles
}

# System prompt for task classification
ROUTER_SYSTEM_PROMPT = """You are a task complexity analyzer. Classify the given task to route it to the optimal model.

# Complexity Levels
- trivial: Simple lookup, yes/no questions, one-liner answers (use Flash-Lite)
- simple: Straightforward single-file edits, simple searches (use Haiku)
- moderate: Multi-step tasks, some reasoning, 2-3 files (use Sonnet)
- complex: Deep reasoning, multi-file changes, architecture decisions (use Sonnet)
- expert: Debugging race conditions, complex algorithms, planning (use o1)

# Task Types
- search: Finding code in codebase
- read: Understanding existing code
- edit: Modifying existing files
- create: Creating new files/features
- debug: Finding and fixing bugs
- refactor: Restructuring code
- review: Reviewing code quality
- plan: Architecture/design planning
- research: External documentation lookup
- explain: Explaining concepts

# Output Format (JSON only)
{
  "complexity": "simple|moderate|complex|expert",
  "task_type": "search|read|edit|create|debug|refactor|review|plan|research|explain",
  "reasoning": "Brief explanation of classification",
  "parallelizable": true/false,
  "estimated_steps": 1-20
}

Respond with ONLY valid JSON."""


class TaskRouter:
    """Routes tasks to optimal models based on complexity analysis"""
    
    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm
        self._cache: dict[str, RoutingDecision] = {}
    
    async def analyze(self, task: str, context: str = "") -> RoutingDecision:
        """Analyze a task and return routing recommendation"""
        # Check cache (simple hash of task)
        cache_key = hash(task[:200])
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        if self.use_llm:
            try:
                decision = await self._analyze_with_llm(task, context)
            except Exception as e:
                logger.warning(f"LLM routing failed, using heuristics: {e}")
                decision = self._analyze_with_heuristics(task)
        else:
            decision = self._analyze_with_heuristics(task)
        
        self._cache[cache_key] = decision
        return decision
    
    async def _analyze_with_llm(self, task: str, context: str) -> RoutingDecision:
        """Use LLM for accurate task classification"""
        import json
        from codesm.provider.base import get_provider
        
        provider = get_provider("router")  # Gemini Flash-Lite for speed
        
        prompt = f"""Classify this task:

Task: {task[:1000]}

Context: {context[:500] if context else 'None'}"""
        
        response_text = ""
        async for chunk in provider.stream(
            system=ROUTER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        ):
            if chunk.type == "text":
                response_text += chunk.content
        
        # Parse JSON response
        response_text = response_text.strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])
        
        data = json.loads(response_text)
        
        # Map to enums
        complexity_str = data.get("complexity", "moderate")
        try:
            complexity = TaskComplexity(complexity_str)
        except ValueError:
            complexity = TaskComplexity.MODERATE
        
        task_type_str = data.get("task_type", "edit")
        try:
            task_type = TaskType(task_type_str)
        except ValueError:
            task_type = TaskType.EDIT
        
        # Get model recommendation
        model_info = MODEL_TIERS.get(complexity, MODEL_TIERS[TaskComplexity.MODERATE])
        subagent = SUBAGENT_BY_TYPE.get(task_type)
        
        # Estimate tokens based on steps
        estimated_steps = data.get("estimated_steps", 5)
        estimated_tokens = estimated_steps * 2000  # Rough estimate
        
        return RoutingDecision(
            complexity=complexity,
            task_type=task_type,
            recommended_model=model_info["model"],
            recommended_subagent=subagent,
            reasoning=data.get("reasoning", ""),
            estimated_tokens=estimated_tokens,
            parallelizable=data.get("parallelizable", False),
        )
    
    def _analyze_with_heuristics(self, task: str) -> RoutingDecision:
        """Fast heuristic-based classification"""
        task_lower = task.lower()
        
        # Detect task type from keywords (order matters - more specific first)
        task_type = TaskType.EDIT  # Default
        
        # Debug/fix detection first (most specific)
        if any(kw in task_lower for kw in ["debug", "fix bug", "fix the bug", "error", "failing", "broken", "why is", "race condition", "crash"]):
            task_type = TaskType.DEBUG
        elif any(kw in task_lower for kw in ["find", "search", "where", "locate", "which file"]):
            task_type = TaskType.SEARCH
        elif any(kw in task_lower for kw in ["read", "understand", "explain", "how does", "what is"]):
            task_type = TaskType.READ if "read" in task_lower else TaskType.EXPLAIN
        elif any(kw in task_lower for kw in ["create", "new file", "implement", "add feature", "build"]):
            task_type = TaskType.CREATE
        elif any(kw in task_lower for kw in ["refactor", "restructure", "reorganize", "cleanup"]):
            task_type = TaskType.REFACTOR
        elif any(kw in task_lower for kw in ["review", "check", "audit", "analyze"]):
            task_type = TaskType.REVIEW
        elif any(kw in task_lower for kw in ["plan", "design", "architect", "strategy"]):
            task_type = TaskType.PLAN
        elif any(kw in task_lower for kw in ["research", "documentation", "docs", "library"]):
            task_type = TaskType.RESEARCH
        
        # Detect complexity from indicators
        complexity = TaskComplexity.MODERATE  # Default
        
        # Trivial indicators (but not if it's a debug/fix task)
        if task_type not in [TaskType.DEBUG, TaskType.CREATE, TaskType.REFACTOR]:
            if len(task) < 50 or any(kw in task_lower for kw in ["what is", "yes or no", "true or false"]):
                complexity = TaskComplexity.TRIVIAL
        
        # Simple indicators
        elif len(task) < 150 and any(kw in task_lower for kw in ["simple", "quick", "just", "only"]):
            complexity = TaskComplexity.SIMPLE
        
        # Expert indicators
        elif any(kw in task_lower for kw in [
            "race condition", "deadlock", "memory leak", "performance issue",
            "architecture", "design pattern", "complex algorithm", "security vulnerability",
            "why is this happening", "root cause", "deep analysis"
        ]):
            complexity = TaskComplexity.EXPERT
        
        # Complex indicators
        elif any(kw in task_lower for kw in [
            "multiple files", "across the codebase", "refactor entire",
            "redesign", "migrate", "major change"
        ]):
            complexity = TaskComplexity.COMPLEX
        
        # Get recommendations
        model_info = MODEL_TIERS.get(complexity, MODEL_TIERS[TaskComplexity.MODERATE])
        subagent = SUBAGENT_BY_TYPE.get(task_type)
        
        # Estimate tokens
        token_estimates = {
            TaskComplexity.TRIVIAL: 500,
            TaskComplexity.SIMPLE: 2000,
            TaskComplexity.MODERATE: 5000,
            TaskComplexity.COMPLEX: 10000,
            TaskComplexity.EXPERT: 20000,
        }
        
        return RoutingDecision(
            complexity=complexity,
            task_type=task_type,
            recommended_model=model_info["model"],
            recommended_subagent=subagent,
            reasoning=f"Heuristic: {task_type.value} task with {complexity.value} complexity",
            estimated_tokens=token_estimates[complexity],
            parallelizable=task_type in [TaskType.SEARCH, TaskType.READ, TaskType.RESEARCH],
        )
    
    def get_model_for_complexity(self, complexity: TaskComplexity) -> str:
        """Get recommended model for a complexity level"""
        return MODEL_TIERS.get(complexity, MODEL_TIERS[TaskComplexity.MODERATE])["model"]
    
    def estimate_cost(self, decision: RoutingDecision) -> float:
        """Estimate cost in dollars for a task"""
        model_info = MODEL_TIERS.get(decision.complexity, MODEL_TIERS[TaskComplexity.MODERATE])
        cost_per_1k = model_info["cost_per_1k"]
        return (decision.estimated_tokens / 1000) * cost_per_1k


# Global router instance
_router: TaskRouter | None = None


def get_router() -> TaskRouter:
    """Get the global task router instance"""
    global _router
    if _router is None:
        _router = TaskRouter()
    return _router


async def route_task(task: str, context: str = "") -> RoutingDecision:
    """Convenience function to route a task"""
    return await get_router().analyze(task, context)


def route_task_sync(task: str) -> RoutingDecision:
    """Synchronous routing using heuristics only"""
    router = TaskRouter(use_llm=False)
    import asyncio
    return asyncio.get_event_loop().run_until_complete(router.analyze(task))
