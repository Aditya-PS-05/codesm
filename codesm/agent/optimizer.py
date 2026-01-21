"""Cost/Latency Optimization Layer - intelligent resource management"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable
from collections import defaultdict

from codesm.storage.storage import Storage

logger = logging.getLogger(__name__)


class OptimizationMode(Enum):
    """Optimization strategy"""
    BALANCED = "balanced"       # Balance cost and speed
    COST_OPTIMIZED = "cost"     # Minimize cost, accept slower
    SPEED_OPTIMIZED = "speed"   # Minimize latency, accept higher cost
    QUALITY_OPTIMIZED = "quality"  # Best models regardless of cost


# Comprehensive model pricing (input/output per 1M tokens)
MODEL_PRICING = {
    # Anthropic
    "anthropic/claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "name": "Claude Sonnet 4"},
    "anthropic/claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0, "name": "Claude Sonnet 4.5"},
    "anthropic/claude-opus-4-5-20251101": {"input": 15.0, "output": 75.0, "name": "Claude Opus 4.5"},
    "anthropic/claude-3.5-haiku": {"input": 0.25, "output": 1.25, "name": "Claude Haiku"},
    
    # OpenAI
    "openai/gpt-4o": {"input": 2.5, "output": 10.0, "name": "GPT-4o"},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60, "name": "GPT-4o Mini"},
    "openai/gpt-4-turbo": {"input": 10.0, "output": 30.0, "name": "GPT-4 Turbo"},
    "openai/o1": {"input": 15.0, "output": 60.0, "name": "o1"},
    "openai/o1-mini": {"input": 3.0, "output": 12.0, "name": "o1 Mini"},
    
    # OpenRouter (via OpenRouter pricing)
    "openrouter/anthropic/claude-sonnet-4": {"input": 3.0, "output": 15.0, "name": "Claude Sonnet 4"},
    "openrouter/anthropic/claude-3.5-haiku": {"input": 0.80, "output": 4.0, "name": "Claude Haiku"},
    "openrouter/openai/gpt-4o": {"input": 2.5, "output": 10.0, "name": "GPT-4o"},
    "openrouter/openai/gpt-4o-mini": {"input": 0.15, "output": 0.60, "name": "GPT-4o Mini"},
    "openrouter/openai/o1": {"input": 15.0, "output": 60.0, "name": "o1"},
    "openrouter/openai/o1-mini": {"input": 3.0, "output": 12.0, "name": "o1 Mini"},
    "openrouter/google/gemini-2.5-flash-preview": {"input": 0.15, "output": 0.60, "name": "Gemini 2.5 Flash"},
    "openrouter/google/gemini-2.0-flash-lite-001": {"input": 0.075, "output": 0.30, "name": "Gemini Flash-Lite"},
    "openrouter/google/gemini-flash-1.5": {"input": 0.075, "output": 0.30, "name": "Gemini Flash 1.5"},
    "openrouter/google/gemini-pro-1.5": {"input": 1.25, "output": 5.0, "name": "Gemini Pro 1.5"},
    "openrouter/deepseek/deepseek-chat": {"input": 0.14, "output": 0.28, "name": "DeepSeek Chat"},
    "openrouter/meta-llama/llama-3.1-70b-instruct": {"input": 0.52, "output": 0.75, "name": "Llama 3.1 70B"},
}

# Average latency per model (ms for first token)
MODEL_LATENCY = {
    "openrouter/google/gemini-2.0-flash-lite-001": 150,
    "openrouter/google/gemini-2.5-flash-preview": 200,
    "openrouter/anthropic/claude-3.5-haiku": 300,
    "openrouter/openai/gpt-4o-mini": 350,
    "openrouter/anthropic/claude-sonnet-4": 500,
    "openrouter/openai/gpt-4o": 600,
    "openrouter/openai/o1-mini": 1500,
    "openrouter/openai/o1": 3000,
}


@dataclass
class UsageRecord:
    """Record of a single API call"""
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost: float
    timestamp: datetime = field(default_factory=datetime.now)
    task_type: str = ""
    success: bool = True


@dataclass
class UsageStats:
    """Aggregated usage statistics"""
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    error_count: int = 0
    
    def add(self, record: UsageRecord):
        self.total_requests += 1
        self.total_input_tokens += record.input_tokens
        self.total_output_tokens += record.output_tokens
        self.total_cost += record.cost
        self.total_latency_ms += record.latency_ms
        self.avg_latency_ms = self.total_latency_ms / self.total_requests
        if not record.success:
            self.error_count += 1


@dataclass
class Budget:
    """Budget configuration"""
    daily_limit: float = 10.0       # Daily spend limit in USD
    session_limit: float = 5.0      # Per-session limit
    alert_threshold: float = 0.8    # Alert at 80% of limit
    hard_limit: bool = False        # If true, block requests over limit


class CostLatencyOptimizer:
    """Optimizes model selection and tracks usage for cost/latency management"""
    
    def __init__(
        self,
        mode: OptimizationMode = OptimizationMode.BALANCED,
        budget: Optional[Budget] = None,
    ):
        self.mode = mode
        self.budget = budget or Budget()
        
        # Usage tracking
        self._session_usage: list[UsageRecord] = []
        self._model_stats: dict[str, UsageStats] = defaultdict(UsageStats)
        self._daily_cost: float = 0.0
        self._session_cost: float = 0.0
        
        # Latency tracking (rolling average)
        self._latency_samples: dict[str, list[float]] = defaultdict(list)
        self._max_samples = 50
        
        # Callbacks
        self._on_budget_alert: Optional[Callable[[float, float], None]] = None
        self._on_budget_exceeded: Optional[Callable[[float, float], None]] = None
        
        # Load daily usage from storage
        self._load_daily_usage()
    
    def _load_daily_usage(self):
        """Load today's usage from storage"""
        today = datetime.now().strftime("%Y-%m-%d")
        data = Storage.read(["usage", today])
        if data:
            self._daily_cost = data.get("total_cost", 0.0)
    
    def _save_daily_usage(self):
        """Save today's usage to storage"""
        today = datetime.now().strftime("%Y-%m-%d")
        Storage.write(["usage", today], {
            "date": today,
            "total_cost": self._daily_cost,
            "total_requests": len([r for r in self._session_usage if r.timestamp.strftime("%Y-%m-%d") == today]),
        })
    
    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Estimate cost for a request"""
        pricing = MODEL_PRICING.get(model)
        if not pricing:
            # Try to find partial match
            for key, val in MODEL_PRICING.items():
                if model in key or key in model:
                    pricing = val
                    break
        
        if not pricing:
            # Default fallback pricing
            pricing = {"input": 3.0, "output": 15.0}
        
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        
        return input_cost + output_cost
    
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count from text (rough approximation)"""
        # ~4 chars per token for English text
        return max(1, len(text) // 4)
    
    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        task_type: str = "",
        success: bool = True,
    ) -> UsageRecord:
        """Record API usage"""
        cost = self.estimate_cost(model, input_tokens, output_tokens)
        
        record = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost=cost,
            task_type=task_type,
            success=success,
        )
        
        # Update tracking
        self._session_usage.append(record)
        self._model_stats[model].add(record)
        self._session_cost += cost
        self._daily_cost += cost
        
        # Update latency samples
        if success:
            samples = self._latency_samples[model]
            samples.append(latency_ms)
            if len(samples) > self._max_samples:
                samples.pop(0)
        
        # Check budget
        self._check_budget()
        
        # Save to storage
        self._save_daily_usage()
        
        return record
    
    def _check_budget(self):
        """Check budget limits and trigger alerts"""
        # Daily limit check
        if self._daily_cost >= self.budget.daily_limit * self.budget.alert_threshold:
            if self._on_budget_alert:
                self._on_budget_alert(self._daily_cost, self.budget.daily_limit)
        
        if self._daily_cost >= self.budget.daily_limit:
            if self._on_budget_exceeded:
                self._on_budget_exceeded(self._daily_cost, self.budget.daily_limit)
        
        # Session limit check
        if self._session_cost >= self.budget.session_limit * self.budget.alert_threshold:
            if self._on_budget_alert:
                self._on_budget_alert(self._session_cost, self.budget.session_limit)
    
    def can_proceed(self) -> tuple[bool, str]:
        """Check if we can proceed with a request based on budget"""
        if not self.budget.hard_limit:
            return True, ""
        
        if self._daily_cost >= self.budget.daily_limit:
            return False, f"Daily budget exceeded (${self._daily_cost:.2f} / ${self.budget.daily_limit:.2f})"
        
        if self._session_cost >= self.budget.session_limit:
            return False, f"Session budget exceeded (${self._session_cost:.2f} / ${self.budget.session_limit:.2f})"
        
        return True, ""
    
    def get_optimal_model(
        self,
        candidates: list[str],
        estimated_input_tokens: int = 1000,
        estimated_output_tokens: int = 500,
        max_cost: Optional[float] = None,
        max_latency_ms: Optional[float] = None,
    ) -> str:
        """Select optimal model from candidates based on optimization mode"""
        if not candidates:
            return candidates[0] if candidates else ""
        
        scored = []
        for model in candidates:
            cost = self.estimate_cost(model, estimated_input_tokens, estimated_output_tokens)
            latency = self._get_avg_latency(model)
            
            # Skip if exceeds constraints
            if max_cost and cost > max_cost:
                continue
            if max_latency_ms and latency > max_latency_ms:
                continue
            
            # Score based on mode
            if self.mode == OptimizationMode.COST_OPTIMIZED:
                score = -cost  # Lower cost = higher score
            elif self.mode == OptimizationMode.SPEED_OPTIMIZED:
                score = -latency  # Lower latency = higher score
            elif self.mode == OptimizationMode.QUALITY_OPTIMIZED:
                # Prefer expensive models (usually higher quality)
                score = cost
            else:  # BALANCED
                # Normalize and combine
                max_c = max(self.estimate_cost(m, estimated_input_tokens, estimated_output_tokens) for m in candidates) or 1
                max_l = max(self._get_avg_latency(m) for m in candidates) or 1
                norm_cost = cost / max_c
                norm_latency = latency / max_l
                score = -(norm_cost * 0.5 + norm_latency * 0.5)
            
            scored.append((model, score))
        
        if not scored:
            return candidates[0]
        
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]
    
    def _get_avg_latency(self, model: str) -> float:
        """Get average latency for a model"""
        samples = self._latency_samples.get(model, [])
        if samples:
            return sum(samples) / len(samples)
        # Fall back to default estimates
        return MODEL_LATENCY.get(model, 500)
    
    def get_model_recommendation(
        self,
        task_complexity: str,
        prefer_speed: bool = False,
        prefer_cost: bool = False,
    ) -> str:
        """Get model recommendation based on task and preferences"""
        from codesm.agent.router import TaskComplexity, MODEL_TIERS
        
        try:
            complexity = TaskComplexity(task_complexity)
        except ValueError:
            complexity = TaskComplexity.MODERATE
        
        base_model = MODEL_TIERS[complexity]["model"]
        
        # Adjust based on preferences
        if prefer_cost:
            # Downgrade to cheaper model if possible
            cheaper_tiers = {
                TaskComplexity.EXPERT: TaskComplexity.COMPLEX,
                TaskComplexity.COMPLEX: TaskComplexity.MODERATE,
                TaskComplexity.MODERATE: TaskComplexity.SIMPLE,
                TaskComplexity.SIMPLE: TaskComplexity.TRIVIAL,
            }
            if complexity in cheaper_tiers:
                complexity = cheaper_tiers[complexity]
                base_model = MODEL_TIERS[complexity]["model"]
        
        elif prefer_speed:
            # Use faster models
            speed_models = {
                TaskComplexity.EXPERT: "openrouter/openai/o1-mini",  # Faster than o1
                TaskComplexity.COMPLEX: "openrouter/anthropic/claude-3.5-haiku",
                TaskComplexity.MODERATE: "openrouter/google/gemini-2.5-flash-preview",
            }
            if complexity in speed_models:
                base_model = speed_models[complexity]
        
        return base_model
    
    def get_session_stats(self) -> UsageStats:
        """Get session usage statistics"""
        stats = UsageStats()
        for record in self._session_usage:
            stats.add(record)
        return stats
    
    def get_model_stats(self, model: str) -> UsageStats:
        """Get usage statistics for a specific model"""
        return self._model_stats.get(model, UsageStats())
    
    def get_daily_stats(self) -> dict:
        """Get daily usage summary"""
        today = datetime.now().strftime("%Y-%m-%d")
        today_records = [r for r in self._session_usage if r.timestamp.strftime("%Y-%m-%d") == today]
        
        return {
            "date": today,
            "total_cost": self._daily_cost,
            "budget_remaining": max(0, self.budget.daily_limit - self._daily_cost),
            "budget_used_pct": (self._daily_cost / self.budget.daily_limit * 100) if self.budget.daily_limit > 0 else 0,
            "total_requests": len(today_records),
            "total_tokens": sum(r.input_tokens + r.output_tokens for r in today_records),
        }
    
    def get_cost_breakdown(self) -> dict[str, float]:
        """Get cost breakdown by model"""
        breakdown = {}
        for model, stats in self._model_stats.items():
            breakdown[model] = stats.total_cost
        return dict(sorted(breakdown.items(), key=lambda x: x[1], reverse=True))
    
    def reset_session(self):
        """Reset session usage tracking"""
        self._session_usage = []
        self._session_cost = 0.0
    
    def on_budget_alert(self, callback: Callable[[float, float], None]):
        """Register callback for budget alerts"""
        self._on_budget_alert = callback
    
    def on_budget_exceeded(self, callback: Callable[[float, float], None]):
        """Register callback for budget exceeded"""
        self._on_budget_exceeded = callback
    
    def set_mode(self, mode: OptimizationMode):
        """Change optimization mode"""
        self.mode = mode
        logger.info(f"Optimization mode set to: {mode.value}")
    
    def set_budget(self, daily_limit: float = None, session_limit: float = None):
        """Update budget limits"""
        if daily_limit is not None:
            self.budget.daily_limit = daily_limit
        if session_limit is not None:
            self.budget.session_limit = session_limit
    
    def format_cost(self, cost: float) -> str:
        """Format cost for display"""
        if cost < 0.01:
            return f"${cost:.4f}"
        elif cost < 1:
            return f"${cost:.3f}"
        else:
            return f"${cost:.2f}"


# Global optimizer instance
_optimizer: CostLatencyOptimizer | None = None


def get_optimizer() -> CostLatencyOptimizer:
    """Get the global optimizer instance"""
    global _optimizer
    if _optimizer is None:
        _optimizer = CostLatencyOptimizer()
    return _optimizer


def record_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    task_type: str = "",
    success: bool = True,
) -> UsageRecord:
    """Convenience function to record usage"""
    return get_optimizer().record_usage(
        model, input_tokens, output_tokens, latency_ms, task_type, success
    )


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Convenience function to estimate cost"""
    return get_optimizer().estimate_cost(model, input_tokens, output_tokens)


def get_daily_stats() -> dict:
    """Convenience function to get daily stats"""
    return get_optimizer().get_daily_stats()
