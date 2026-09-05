"""Handoff tool - seamless context transfer to new threads using Gemini 2.5 Flash"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)

# System prompt for context analysis
HANDOFF_SYSTEM_PROMPT = """You are a context analyzer for AI agent handoffs. Your job is to extract and summarize the essential context from a conversation so a new agent can continue the work seamlessly.

# Your Task
Analyze the conversation and extract:
1. **Goal**: What is the user trying to accomplish?
2. **Progress**: What has been done so far?
3. **Current State**: What files were modified? What's the current status?
4. **Next Steps**: What needs to be done next?
5. **Key Context**: Important decisions, constraints, or patterns discovered

# Output Format
Provide a structured handoff document that a new agent can use to immediately continue work:

## Goal
[1-2 sentences describing the overall objective]

## Progress Summary
- [Bullet points of completed work]

## Files Modified
- [List of files changed with brief description]

## Current State
[Description of where we are now]

## Next Steps
1. [Immediate next action]
2. [Following actions...]

## Key Context
- [Important decisions or constraints]
- [Patterns or conventions discovered]
- [Any blockers or issues]

Be CONCISE but COMPLETE. The new agent should be able to continue without re-reading the entire conversation."""


class HandoffTool(Tool):
    """Hand off work to a new thread with summarized context using Gemini 2.5 Flash"""
    
    name = "handoff"
    description = "Hand off work to a new thread. Use when context is getting long or you need a fresh start while preserving important context."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def set_parent(self, tools: "ToolRegistry"):
        """Set parent context"""
        self._parent_tools = tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Brief description of what should continue in the new thread (1-2 sentences)",
                },
                "follow": {
                    "type": "boolean",
                    "description": "If true, switch to the new thread immediately. If false, continue in current thread.",
                    "default": True,
                },
                "include_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of file paths that are important for the continuation",
                },
            },
            "required": ["goal"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        """Execute the handoff - analyze context and create new session"""
        from codesm.provider.base import complete
        from codesm.session.session import Session
        
        goal = args.get("goal", "")
        follow = args.get("follow", True)
        include_files = args.get("include_files", [])
        
        if not goal:
            return "Error: goal is required - describe what should continue in the new thread"
        
        # Get current session and messages from context
        session = context.get("session")
        messages = context.get("messages", [])
        workspace_dir = context.get("workspace_dir") or context.get("cwd", ".")
        
        # Build conversation summary for analysis
        conversation_text = self._format_conversation(messages)
        
        # Use Gemini 2.5 Flash for fast context analysis
        try:
            
            user_prompt = f"""Analyze this conversation and create a handoff document for a new agent.

## Continuation Goal
{goal}

## Files to Include
{', '.join(include_files) if include_files else 'None specified'}

## Conversation History
{conversation_text}

Create a concise handoff document that will allow a new agent to continue this work seamlessly."""

            # Collect response
            handoff_summary = await complete(HANDOFF_SYSTEM_PROMPT, user_prompt, model="handoff")

        except Exception as e:
            logger.warning(f"Handoff LLM failed, using basic summary: {e}")
            handoff_summary = self._basic_summary(goal, messages, include_files)
        
        # Create new session
        try:
            new_session = Session.create(
                directory=Path(workspace_dir),
                is_child=True,
            )
            if session:
                from copy import deepcopy
                new_session.debug_state = deepcopy(session.debug_state)
                new_session.usage_records = deepcopy(session.usage_records)
                new_session.parent_id = session.id
                new_session.file_state = deepcopy(session.file_state)
                new_session.agent_runs = deepcopy(session.agent_runs)
                new_session.last_model = session.last_model
                from codesm.storage.storage import Storage
                todos = Storage.read(["todo", session.id]) or []
                Storage.write(["todo", new_session.id], [{**todo, "session_id": new_session.id} for todo in todos])
                new_session.save()

            
            # Set title based on goal
            new_session.set_title(f"Continuation: {goal[:50]}...")
            
            # Add handoff context as first message
            handoff_message = f"""# Handoff from Previous Thread

{handoff_summary}

---

**Continue with:** {goal}"""
            
            new_session.add_message("user", handoff_message)
            
            # Build response
            result = f"""## Handoff Created

**New Thread:** `{new_session.id}`
**Goal:** {goal}

### Context Summary
{handoff_summary[:1000]}{'...' if len(handoff_summary) > 1000 else ''}

"""
            
            if follow:
                result += f"\n→ Switching to new thread..."
                # Signal the agent/TUI to switch sessions
                context["_handoff_session_id"] = new_session.id
                context["_handoff_follow"] = True
            else:
                result += f"\n→ New thread created. Use `/session {new_session.id}` to continue there."
            
            return result
            
        except Exception as e:
            logger.exception("Failed to create handoff session")
            return f"Error creating handoff session: {e}"
    
    def _format_conversation(self, messages: list[dict], max_chars: int = 15000) -> str:
        """Format conversation for analysis"""
        parts = []
        total_chars = 0
        
        for msg in reversed(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if not content or role == "tool":
                continue
            
            # Truncate long messages
            if len(content) > 2000:
                content = content[:2000] + "... [truncated]"
            
            formatted = f"**{role.upper()}:** {content}"
            
            if total_chars + len(formatted) > max_chars:
                parts.append("... [earlier messages truncated for brevity]")
                break
            
            parts.append(formatted)
            total_chars += len(formatted)
        
        return "\n\n".join(reversed(parts))  # Retain the latest context in chronological order.
    
    def _basic_summary(self, goal: str, messages: list[dict], include_files: list[str]) -> str:
        """Create basic summary without LLM"""
        # Count messages
        user_msgs = sum(1 for m in messages if m.get("role") == "user")
        assistant_msgs = sum(1 for m in messages if m.get("role") == "assistant")
        
        # Extract any mentioned files from messages
        mentioned_files = set(include_files)
        for msg in messages:
            content = msg.get("content", "")
            # Simple heuristic: look for file paths
            for word in content.split():
                if "/" in word and "." in word.split("/")[-1]:
                    clean = word.strip("\"'`,;:()[]{}").rstrip(".")
                    if len(clean) < 200:
                        mentioned_files.add(clean)
        
        return f"""## Goal
{goal}

## Progress Summary
- Conversation had {user_msgs} user messages and {assistant_msgs} assistant responses
- Context was getting long, requiring handoff

## Files Mentioned
{chr(10).join(f'- {f}' for f in list(mentioned_files)[:20]) if mentioned_files else '- None identified'}

## Next Steps
1. Continue with: {goal}

## Key Context
- This is a continuation from a previous thread
- Review the goal and proceed with implementation"""
