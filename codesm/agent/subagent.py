"""Subagent - lightweight agent for delegated tasks"""

import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import AsyncIterator

from codesm.provider.base import get_provider, StreamChunk
from codesm.tool.registry import ToolRegistry
from codesm.agent.loop import ReActLoop

logger = logging.getLogger(__name__)


@dataclass
class SubAgentConfig:
    """Configuration for a subagent type"""
    name: str
    description: str
    system_prompt: str
    model: str | None = None  # None = inherit from parent
    max_iterations: int = 25
    allowed_tools: list[str] | None = None  # None = all tools
    denied_tools: list[str] | None = None  # Tools to exclude


# Built-in subagent configurations
SUBAGENT_CONFIGS: dict[str, SubAgentConfig] = {
    "coder": SubAgentConfig(
        name="coder",
        description="Implements code changes across multiple files. Use for complex multi-file edits, refactoring, or feature implementation.",
        system_prompt="""You are a focused coding agent. Your job is to implement the specific task given to you.

# Your Capabilities
- Read, write, and edit files
- Run shell commands (build, test, lint)
- Search the codebase

# Your Constraints
- Focus ONLY on the task described in your prompt
- Do NOT explore beyond what's needed
- Do NOT ask questions - make reasonable decisions
- Do NOT use todo tracking - just do the work
- IMPORTANT: Make at most 10-15 tool calls per turn to avoid API limits

# Workflow
1. Understand the task from your prompt
2. Search/read files to understand context
3. Make the necessary changes
4. Verify changes work (run tests/builds if applicable)
5. Return a summary of what you did

# Output
End with a clear summary of:
- What files you modified
- What changes you made
- Any issues encountered
""",
        max_iterations=30,
        denied_tools=["todo", "task"],  # No nested tasks or todos
    ),
    
    "researcher": SubAgentConfig(
        name="researcher",
        description="Searches and analyzes code without making changes. Use for understanding codebases, finding patterns, or investigating bugs.",
        system_prompt="""You are a code research agent. Your job is to find and analyze code, NOT modify it.

# Your Capabilities
- Read files
- Search with grep, glob, codesearch
- Run read-only commands
- Search the web for documentation

# Your Constraints
- Do NOT write, edit, or delete any files
- Do NOT run commands that modify state
- Focus ONLY on gathering information
- Be thorough - search from multiple angles
- IMPORTANT: Make at most 10-15 tool calls per turn to avoid API limits

# Workflow
1. Understand what information is needed
2. Search the codebase thoroughly (but don't make 100+ parallel calls)
3. Read relevant files
4. Analyze and synthesize findings
5. Return comprehensive findings

# Output
Provide a clear, structured report of:
- What you found
- Relevant file paths and line numbers
- Key insights or patterns
- Answers to specific questions asked
""",
        max_iterations=20,
        allowed_tools=["read", "grep", "glob", "codesearch", "ls", "websearch", "webfetch", "bash"],
        denied_tools=["write", "edit", "multiedit", "patch", "todo", "task"],
    ),
    
    "reviewer": SubAgentConfig(
        name="reviewer",
        description="Reviews code for bugs, security issues, and improvements. Use after making significant changes.",
        system_prompt="""You are a code review agent. Your job is to analyze code quality and find issues.

# Your Focus Areas
- Bugs and logic errors
- Security vulnerabilities  
- Performance issues
- Code style and best practices
- Missing error handling
- Edge cases

# Your Constraints
- Do NOT modify code - only analyze
- Be specific about issues found
- Provide line numbers
- Suggest fixes but don't implement them

# Output Format
For each issue found:
1. **Severity**: Critical / High / Medium / Low
2. **File**: path/to/file.py:123
3. **Issue**: Clear description
4. **Suggestion**: How to fix

End with a summary: X issues found (Y critical, Z high, ...)
""",
        max_iterations=15,
        allowed_tools=["read", "grep", "glob", "codesearch", "ls", "diagnostics"],
        denied_tools=["write", "edit", "multiedit", "patch", "bash", "todo", "task"],
    ),
    
    "planner": SubAgentConfig(
        name="planner",
        description="Creates implementation plans without executing them. Use for complex tasks that need design first.",
        system_prompt="""You are a planning agent. Your job is to create detailed implementation plans.

# Your Approach
1. Understand the requirements
2. Explore the codebase to understand current architecture
3. Identify all files that need changes
4. Design the solution approach
5. Break down into specific, actionable steps

# Your Constraints
- Do NOT implement anything
- Do NOT modify any files
- Focus on creating a clear, detailed plan
- Be specific about file paths and function names

# Output Format
Provide a structured plan:

## Overview
Brief description of the approach

## Files to Modify
- `path/to/file.py` - what changes needed
- ...

## Implementation Steps
1. Step 1 - specific details
2. Step 2 - specific details
...

## Risks & Considerations
- Potential issues to watch for
""",
        max_iterations=15,
        allowed_tools=["read", "grep", "glob", "codesearch", "ls", "websearch", "webfetch"],
        denied_tools=["write", "edit", "multiedit", "patch", "bash", "todo", "task"],
    ),
}


def get_subagent_config(name: str) -> SubAgentConfig | None:
    """Get a subagent configuration by name"""
    return SUBAGENT_CONFIGS.get(name)


def list_subagent_configs() -> list[SubAgentConfig]:
    """List all available subagent configurations"""
    return list(SUBAGENT_CONFIGS.values())


class SubAgent:
    """A lightweight agent for delegated tasks"""
    
    def __init__(
        self,
        config: SubAgentConfig,
        directory: Path,
        parent_model: str,
        parent_tools: ToolRegistry,
    ):
        self.config = config
        self.directory = Path(directory).resolve()
        self.parent_tools = parent_tools
        
        # Use config model or inherit from parent
        model = config.model or parent_model
        self.provider = get_provider(model)
        self.model = model
        
        # Create filtered tool registry
        self.tools = self._create_filtered_tools()
        
        # ReAct loop with config iterations
        self.react_loop = ReActLoop(max_iterations=config.max_iterations)
    
    def _create_filtered_tools(self) -> ToolRegistry:
        """Create a tool registry with only allowed tools"""
        # Start with a fresh registry
        filtered = ToolRegistry()
        
        # Get all tools from parent
        all_tools = self.parent_tools._tools.copy()
        
        # Apply filters
        if self.config.allowed_tools:
            # Only include explicitly allowed tools
            all_tools = {
                name: tool for name, tool in all_tools.items()
                if name in self.config.allowed_tools
            }
        
        if self.config.denied_tools:
            # Remove denied tools
            for name in self.config.denied_tools:
                all_tools.pop(name, None)
        
        # Replace the internal tools dict
        filtered._tools = all_tools
        
        return filtered
    
    async def run(self, prompt: str) -> str:
        """Run the subagent with the given prompt and return the result"""
        messages = [{"role": "user", "content": prompt}]
        
        context = {
            "workspace_dir": str(self.directory),
            "cwd": self.directory,
            "subagent": True,
            "subagent_type": self.config.name,
        }
        
        # Build system prompt
        system = self.config.system_prompt + f"\n\n# Environment\nWorking directory: {self.directory}"
        
        # Run the ReAct loop and collect response
        full_response = ""
        tool_summaries = []
        
        async for chunk in self.react_loop.execute(
            provider=self.provider,
            system_prompt=system,
            messages=messages,
            tools=self.tools,
            context=context,
        ):
            if chunk.type == "text":
                full_response += chunk.content
            elif chunk.type == "tool_result":
                # Collect tool execution summaries
                tool_summaries.append(f"✓ {chunk.name}")
        
        # Build result with metadata
        result = full_response
        
        if tool_summaries:
            result += f"\n\n---\n_Tools used: {', '.join(tool_summaries)}_"
        
        return result
    
    async def run_streaming(self, prompt: str) -> AsyncIterator[StreamChunk]:
        """Run the subagent with streaming output"""
        messages = [{"role": "user", "content": prompt}]
        
        context = {
            "workspace_dir": str(self.directory),
            "cwd": self.directory,
            "subagent": True,
            "subagent_type": self.config.name,
        }
        
        system = self.config.system_prompt + f"\n\n# Environment\nWorking directory: {self.directory}"
        
        async for chunk in self.react_loop.execute(
            provider=self.provider,
            system_prompt=system,
            messages=messages,
            tools=self.tools,
            context=context,
        ):
            yield chunk
