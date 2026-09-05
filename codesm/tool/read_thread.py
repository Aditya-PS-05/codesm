"""Read thread tool - extract relevant context from a past thread"""

import logging
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)

# System prompt for context extraction
EXTRACT_SYSTEM_PROMPT = """You are a context extraction assistant. Your job is to read a conversation thread and extract only the information relevant to the user's goal.

# Your Task
Given a conversation history and a specific goal, extract:
1. Only information directly relevant to the goal
2. Code snippets, patterns, or solutions that apply
3. Key decisions or approaches taken
4. Any file paths or configurations mentioned

# Output Format
Provide a focused summary that answers the user's goal:
- Start with a brief "Summary" (1-2 sentences)
- Include "Relevant Code/Patterns" if applicable
- Include "Key Decisions" if applicable
- Include "Files Mentioned" if applicable

Be CONCISE. Only include what's directly relevant to the goal. Skip pleasantries and meta-discussion."""


class ReadThreadTool(Tool):
    """Read and extract relevant context from a past conversation thread"""
    
    name = "read_thread"
    description = "Read a past thread and extract information relevant to a specific goal."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "The session ID of the thread to read (format: session_YYYYMMDD_HHMMSS_ffffff)",
                },
                "goal": {
                    "type": "string",
                    "description": "What information to extract from the thread. Be specific about what you need.",
                },
            },
            "required": ["thread_id", "goal"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.session.session import Session
        from codesm.provider.base import complete
        
        thread_id = args.get("thread_id", "")
        goal = args.get("goal", "")
        
        if not thread_id:
            return "Error: thread_id is required"
        if not goal:
            return "Error: goal is required - describe what information you need from the thread"
        
        # Load the session
        session = Session.load(thread_id)
        if not session:
            return f"Error: Thread '{thread_id}' not found. Use find_thread to search for valid thread IDs."
        
        # Build conversation summary
        conversation_text = self._format_conversation(session.messages)
        
        if not conversation_text.strip():
            return f"Thread '{thread_id}' ({session.title}) has no content to extract."
        
        # Use Gemini Flash for fast extraction
        try:
            
            user_prompt = f"""# Thread: {session.title}
Thread ID: {thread_id}
Updated: {session.updated_at.strftime("%Y-%m-%d %H:%M")}

# Goal
{goal}

# Conversation History
{conversation_text}

Extract only the information relevant to the goal above."""

            response_text = await complete(EXTRACT_SYSTEM_PROMPT, user_prompt, model="finder")

            return f"## Context from: {session.title}\n\n{response_text}"
            
        except Exception as e:
            logger.warning(f"LLM extraction failed, returning raw summary: {e}")
            return self._basic_summary(session, goal)
    
    def _format_conversation(self, messages: list[dict], max_chars: int = 20000) -> str:
        """Format conversation for analysis"""
        parts = []
        total_chars = 0
        
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if not content or role == "tool":
                continue
            
            # Truncate long messages
            if len(content) > 3000:
                content = content[:3000] + "... [truncated]"
            
            formatted = f"**{role.upper()}:** {content}"
            
            if total_chars + len(formatted) > max_chars:
                parts.append("... [earlier messages truncated]")
                break
            
            parts.append(formatted)
            total_chars += len(formatted)
        
        return "\n\n".join(parts)
    
    def _basic_summary(self, session, goal: str) -> str:
        """Create basic summary without LLM"""
        messages = session.messages
        user_msgs = [m for m in messages if m.get("role") == "user" and m.get("content")]
        assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("content")]
        
        # Extract first and last user messages
        first_msg = user_msgs[0].get("content", "")[:500] if user_msgs else ""
        last_msg = user_msgs[-1].get("content", "")[:500] if len(user_msgs) > 1 else ""
        
        return f"""## Context from: {session.title}

**Goal:** {goal}

**Thread Summary:**
- {len(user_msgs)} user messages, {len(assistant_msgs)} assistant responses
- Started: {session.created_at.strftime("%Y-%m-%d %H:%M")}
- Last updated: {session.updated_at.strftime("%Y-%m-%d %H:%M")}

**First Request:**
{first_msg}

{"**Last Request:**" + chr(10) + last_msg if last_msg else ""}

*Note: LLM extraction failed. For full context, review the thread manually.*"""
