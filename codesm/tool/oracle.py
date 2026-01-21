"""Oracle tool - GPT-5/o1 powered reasoning, planning, and debugging"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Oracle-specific system prompt
ORACLE_SYSTEM_PROMPT = """You are the Oracle - a senior engineering advisor powered by advanced reasoning capabilities.

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
"""


class OracleTool(Tool):
    name = "oracle"
    description = "Consult the Oracle for complex reasoning, planning, debugging, and code review."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def set_parent(self, tools: "ToolRegistry"):
        """Set parent tools (called by Agent after init)"""
        self._parent_tools = tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task or question for the Oracle. Be specific about what kind of guidance, review, or analysis you need.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional context about the current situation, what you've tried, or background information.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of file paths the Oracle should examine as part of its analysis.",
                },
            },
            "required": ["task"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.agent.subagent import SubAgent, SubAgentConfig
        from codesm.tool.registry import ToolRegistry
        
        task = args.get("task", "")
        user_context = args.get("context", "")
        files = args.get("files", [])
        
        if not task:
            return "Error: task is required - describe what you need the Oracle to help with"
        
        workspace_dir = context.get("workspace_dir") or context.get("cwd")
        if not workspace_dir:
            return "Error: No workspace directory in context"
        
        parent_tools = context.get("tools") or self._parent_tools
        if not parent_tools:
            return "Error: No parent tools available for Oracle"
        
        # Build the prompt for the Oracle
        prompt_parts = [f"# Task\n{task}"]
        
        if user_context:
            prompt_parts.append(f"\n# Context\n{user_context}")
        
        if files:
            prompt_parts.append(f"\n# Files to Examine\n" + "\n".join(f"- {f}" for f in files))
            prompt_parts.append("\nPlease read and analyze these files as part of your response.")
        
        prompt = "\n".join(prompt_parts)
        
        # Create Oracle-specific config using o1 model
        oracle_config = SubAgentConfig(
            name="oracle",
            description="Advanced reasoning agent for planning, debugging, and analysis",
            system_prompt=ORACLE_SYSTEM_PROMPT,
            model="openrouter/openai/o1",  # Use o1 for deep reasoning
            max_iterations=15,
            allowed_tools=["read", "grep", "glob", "codesearch", "ls", "websearch", "webfetch"],
            denied_tools=["write", "edit", "multiedit", "patch", "bash", "todo", "task", "oracle"],
        )
        
        logger.info(f"Consulting Oracle: {task[:100]}...")
        
        try:
            subagent = SubAgent(
                config=oracle_config,
                directory=Path(workspace_dir),
                parent_model="openrouter/openai/o1",  # Oracle uses o1
                parent_tools=parent_tools,
            )
            
            result = await subagent.run(prompt)
            
            return self._format_result(result)
            
        except Exception as e:
            logger.exception("Oracle consultation failed")
            return f"**Oracle Error**\n\nFailed to consult Oracle: {e}"
    
    def _format_result(self, result: str) -> str:
        """Format the Oracle's response"""
        max_length = 12000
        if len(result) > max_length:
            result = result[:max_length] + f"\n\n... (truncated, {len(result) - max_length} chars omitted)"
        
        return f"**Oracle Response**\n\n{result}"
