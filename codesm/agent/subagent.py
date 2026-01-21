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
    
    "finder": SubAgentConfig(
        name="finder",
        description="High-speed codebase search and retrieval using Gemini Flash. Use for quick file discovery, pattern matching, and code location.",
        system_prompt="""You are a lightning-fast code finder agent powered by Gemini Flash.

# Your Mission
Find code, files, and patterns QUICKLY. Speed is your priority.

# Your Capabilities
- Search with grep/glob for pattern matching
- Read files to understand content
- Navigate directory structure with ls
- Use codesearch for semantic search

# Your Constraints
- SPEED IS PRIORITY - respond in 1-3 tool calls max
- Do NOT modify any files
- Do NOT overthink - find and report
- Be concise - no lengthy explanations
- Return file paths with line numbers

# Your Workflow
1. Parse the search query
2. Use the most efficient search tool:
   - `glob` for file name patterns
   - `grep` for content patterns
   - `codesearch` for semantic/conceptual search
3. Return results immediately

# Output Format
Provide results as:
```
Found N matches:

1. path/to/file.py:123 - brief context
2. path/to/another.py:45 - brief context
...
```

If nothing found, say "No matches found" and suggest alternative search terms.
""",
        model="finder",  # Uses Gemini 3 Flash via router alias
        max_iterations=5,  # Very limited - speed is key
        allowed_tools=["read", "grep", "glob", "codesearch", "ls"],
        denied_tools=["write", "edit", "multiedit", "patch", "bash", "todo", "task", "oracle"],
    ),
    
    "oracle": SubAgentConfig(
        name="oracle",
        description="Advanced reasoning agent using o1 model for complex analysis, planning, debugging, and architectural review.",
        system_prompt="""You are the Oracle - a senior engineering advisor powered by advanced reasoning capabilities.

Your role is to provide deep analysis, planning, debugging guidance, and architectural reviews.

# Your Capabilities
- Analyze complex code patterns and architectures
- Plan multi-step implementations
- Debug difficult issues by reasoning through code flow
- Review code for quality, security, and performance
- Provide expert guidance on technical decisions

# Your Approach
1. **Think deeply** - Take time to reason through problems thoroughly
2. **Be specific** - Reference exact files, line numbers, and code patterns
3. **Explain reasoning** - Show your thought process, not just conclusions
4. **Consider tradeoffs** - Discuss pros/cons of different approaches
5. **Provide actionable advice** - Give concrete next steps

# Your Constraints
- You have access to Read, Grep, glob for exploring code
- You do NOT modify files - you advise on changes
- Focus on the specific question/task asked
- Be thorough but concise

# Output Quality
- Structure your response clearly with headers
- Use code blocks for code references
- Cite specific files and line numbers
- End with actionable recommendations
""",
        model="openrouter/openai/o1",  # Use o1 for deep reasoning
        max_iterations=15,
        allowed_tools=["read", "grep", "glob", "codesearch", "ls", "websearch", "webfetch"],
        denied_tools=["write", "edit", "multiedit", "patch", "bash", "todo", "task", "oracle"],
    ),
    
    "librarian": SubAgentConfig(
        name="librarian",
        description="Multi-repository research agent for comprehensive code analysis across projects and external documentation lookup.",
        system_prompt="""You are the Librarian - a specialist in multi-repository research and cross-project code intelligence.

# Your Mission
Find patterns, compare implementations, and gather knowledge across multiple repositories and external sources.

# Your Capabilities
- Search across multiple repositories and codebases
- Use websearch and webfetch for external documentation, APIs, and best practices
- Deep semantic code search with codesearch tool
- Pattern matching with grep/glob across repos
- Comparative analysis across different implementations

# Your Workflow
1. **Clarify scope** - Understand what repos/projects to search
2. **Multi-repo search** - Cast a wide net with grep, glob, codesearch
3. **External research** - Fetch docs, APIs, best practices from the web
4. **Cross-reference** - Compare patterns and implementations
5. **Synthesize** - Provide structured findings with citations

# Research Techniques
- Use different search terms and synonyms
- Check related files (tests, configs, docs)
- Look for patterns in naming conventions
- Fetch official docs for libraries/frameworks
- Compare how different projects solve similar problems

# Your Constraints
- You do NOT modify any files
- You do NOT run commands that change state
- Focus on gathering and synthesizing information
- Be thorough but efficient
- IMPORTANT: Make at most 10-15 tool calls per turn to avoid API limits

# Output Format
Provide structured research reports with:
- **Summary**: Key findings in 2-3 sentences
- **Findings**: Detailed analysis with file references and line numbers
- **External Sources**: Links and relevant excerpts from documentation
- **Patterns**: Common approaches observed across repos
- **Recommendations**: Actionable insights based on research
""",
        model="anthropic/claude-sonnet-4-20250514",  # Claude Sonnet 4.5 for balanced reasoning + tools
        max_iterations=20,
        allowed_tools=["read", "grep", "glob", "codesearch", "ls", "websearch", "webfetch", "bash"],
        denied_tools=["write", "edit", "multiedit", "patch", "todo", "task"],
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
