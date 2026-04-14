"""LLM-based summarization for conversation context"""

import os
import re
import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# The summarizer runs as a one-shot fork that inherits the parent's tool
# schema. Without an explicit "text only" instruction the model sometimes
# wastes its only turn on a tool call, producing no summary text. The
# preamble is placed before the task so it reads as a hard gate and the
# trailer reinforces it right before the model starts generating.
_NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use any tool (no file reads, no searches, no edits).
- All the context you need is in the conversation above.
- Tool calls will be REJECTED and will waste your only turn.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

_NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    "an <analysis> block followed by a <summary> block."
)

# Nine-section structured prompt. The <analysis> block is a drafting
# scratchpad — format_compact_summary() strips it before the summary lands
# in the continuing conversation, so the model gets the benefit of
# chain-of-thought without paying for the tokens downstream.
_BASE_COMPACT_PROMPT = """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary must be thorough enough that a fresh model reading only your summary can continue the work without re-asking the user or re-discovering context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts. In your analysis:

1. Walk the conversation chronologically. For each section identify:
   - The user's explicit requests and intents
   - Your approach to each request
   - Key decisions, technical concepts, and code patterns
   - Specific details: file names, full code snippets, function signatures, edits
   - Errors encountered and how they were fixed
   - Any user feedback, especially corrections or direction changes
2. Double-check technical accuracy and completeness before writing the final summary.

Your summary must include these nine sections:

1. Primary Request and Intent: All of the user's explicit requests and intents, in detail.
2. Key Technical Concepts: Technologies, frameworks, and patterns discussed.
3. Files and Code Sections: Every file examined, modified, or created. Include the reason the file mattered and full code snippets for anything recent or load-bearing.
4. Errors and Fixes: Each error hit and how it was resolved. Call out user feedback on errors explicitly.
5. Problem Solving: Problems solved and any troubleshooting still in flight.
6. All User Messages: Every non-tool-result user message in order. These are critical for tracking shifting intent.
7. Pending Tasks: Tasks the user explicitly asked for that are not yet done.
8. Current Work: Exactly what was being worked on immediately before this summary request, with file names and code snippets.
9. Optional Next Step: The next step directly in line with the most recent request. Include a verbatim quote from the most recent user message showing where work was left off. If the last task concluded cleanly, only list a next step if the user explicitly asked for one.

Structure your output like this:

<analysis>
[Your chronological walk-through and accuracy check]
</analysis>

<summary>
1. Primary Request and Intent:
   [...]

2. Key Technical Concepts:
   - [...]

3. Files and Code Sections:
   - [file]
     - [why it matters]
     - [snippet]

4. Errors and Fixes:
   - [error] -> [fix] -> [user feedback if any]

5. Problem Solving:
   [...]

6. All User Messages:
   - [...]

7. Pending Tasks:
   - [...]

8. Current Work:
   [...]

9. Optional Next Step:
   [...]
</summary>

Be precise and thorough. The next model only sees what you write here."""

SUMMARY_SYSTEM_PROMPT = _NO_TOOLS_PREAMBLE + _BASE_COMPACT_PROMPT + _NO_TOOLS_TRAILER


_ANALYSIS_BLOCK_RE = re.compile(r"<analysis>[\s\S]*?</analysis>", re.IGNORECASE)
_SUMMARY_BLOCK_RE = re.compile(r"<summary>([\s\S]*?)</summary>", re.IGNORECASE)
_COLLAPSE_BLANK_RE = re.compile(r"\n\n+")


def format_compact_summary(summary: str) -> str:
    """Strip the <analysis> scratchpad and unwrap the <summary> block.

    The model is instructed to emit both <analysis> and <summary>. The
    analysis section is chain-of-thought drafting that burns tokens in the
    continuing conversation without adding information once the summary
    exists, so we drop it. If the model forgets the XML wrappers entirely
    we fall back to the raw text.
    """
    if not summary:
        return ""

    cleaned = _ANALYSIS_BLOCK_RE.sub("", summary)

    match = _SUMMARY_BLOCK_RE.search(cleaned)
    if match:
        cleaned = match.group(1)

    cleaned = _COLLAPSE_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def format_messages_for_summary(messages: list[dict]) -> str:
    """Format messages into a compact text representation for summarization."""
    formatted_parts = []
    
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        
        # Handle tool calls
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            tool_names = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls]
            formatted_parts.append(f"[{role}] Called tools: {', '.join(tool_names)}")
            continue
        
        # Handle tool messages
        if role == "tool":
            tool_name = msg.get("name", "unknown")
            # Truncate tool output
            content_preview = str(content)[:500] if content else ""
            if len(str(content)) > 500:
                content_preview += "..."
            formatted_parts.append(f"[tool:{tool_name}] {content_preview}")
            continue
        
        # Handle regular messages with content
        if content:
            # Content can be string or list
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        text_parts.append(item)
                content = " ".join(text_parts)
            
            # Truncate very long content
            content_str = str(content)
            if len(content_str) > 500:
                content_str = content_str[:500] + "..."
            
            formatted_parts.append(f"[{role}] {content_str}")
    
    return "\n\n".join(formatted_parts)


def create_summary_message(summary_text: str) -> dict:
    """Create a properly formatted summary message dict.

    The input may still contain the model's <analysis>/<summary> XML
    wrappers; format_compact_summary() strips them so the scratchpad never
    lands back in the continuing conversation.
    """
    cleaned = format_compact_summary(summary_text)
    return {
        "role": "system",
        "content": f"## Previous Conversation Summary\n\n{cleaned}",
        "_context_summary": True,
        "_summary_timestamp": datetime.now().isoformat(),
    }


async def get_summary_provider() -> tuple:
    """Try to get a cheap model for summarization.
    
    Returns:
        (provider_type, model_id) tuple where provider_type is 'openrouter', 'anthropic', or 'openai'
    """
    # Priority 1: OpenRouter with Claude Haiku
    if os.environ.get("OPENROUTER_API_KEY"):
        return ("openrouter", "anthropic/claude-3-haiku-20240307")
    
    # Priority 2: OpenRouter with Gemini Flash (if key exists but haiku not preferred)
    # This is same as priority 1, just different model
    if os.environ.get("OPENROUTER_API_KEY"):
        return ("openrouter", "google/gemini-flash-1.5")
    
    # Priority 3: Direct Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic", "claude-3-haiku-20240307")
    
    # Priority 4: OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", "gpt-4o-mini")
    
    return (None, None)


async def _summarize_with_openrouter(formatted_text: str, model: str, max_tokens: int) -> str:
    """Summarize using OpenRouter API."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Aditya-PS-05",
                "X-Title": "codesm",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Summarize this conversation:\n\n{formatted_text}"},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
        )
        
        if response.status_code != 200:
            raise Exception(f"OpenRouter API error: {response.status_code}")
        
        data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")


async def _summarize_with_anthropic(formatted_text: str, model: str, max_tokens: int) -> str:
    """Summarize using Anthropic API directly."""
    from ..provider.anthropic import AnthropicProvider
    
    provider = AnthropicProvider(model)
    result = ""
    
    async for chunk in provider.stream(
        system=SUMMARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Summarize this conversation:\n\n{formatted_text}"}],
        tools=None,
    ):
        if chunk.type == "text":
            result += chunk.content
    
    return result


async def _summarize_with_openai(formatted_text: str, model: str, max_tokens: int) -> str:
    """Summarize using OpenAI API directly."""
    from ..provider.openai import OpenAIProvider
    
    provider = OpenAIProvider(model)
    result = ""
    
    async for chunk in provider.stream(
        system=SUMMARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Summarize this conversation:\n\n{formatted_text}"}],
        tools=None,
    ):
        if chunk.type == "text":
            result += chunk.content
    
    return result


def _create_fallback_summary(messages: list[dict]) -> str:
    """Create a basic fallback summary when LLM summarization fails."""
    parts = ["Summary generation failed. Message overview:"]
    
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])
        
        if tool_calls:
            tool_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
            parts.append(f"- [{role}] Called: {', '.join(tool_names)}")
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            parts.append(f"- [tool:{tool_name}] (result)")
        else:
            # Get first 100 chars of content
            if isinstance(content, list):
                content = str(content)
            preview = str(content)[:100].replace("\n", " ")
            if len(str(content)) > 100:
                preview += "..."
            parts.append(f"- [{role}] {preview}")
        
        if i >= 20:
            parts.append(f"- ... and {len(messages) - 20} more messages")
            break
    
    return "\n".join(parts)


async def summarize_messages(
    messages: list[dict],
    provider=None,
    model: str | None = None,
    max_summary_tokens: int = 1500,
) -> str:
    """Summarize a list of messages for context compression.
    
    Args:
        messages: List of message dicts to summarize
        provider: Optional provider instance to use
        model: Optional model override
        max_summary_tokens: Maximum tokens for the summary output
    
    Returns:
        Summary text string
    """
    if not messages:
        return ""
    
    # Format messages for summarization
    formatted_text = format_messages_for_summary(messages)
    
    if not formatted_text.strip():
        return ""
    
    try:
        # If provider is passed, use it directly
        if provider:
            result = ""
            async for chunk in provider.stream(
                system=SUMMARY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Summarize this conversation:\n\n{formatted_text}"}],
                tools=None,
            ):
                if chunk.type == "text":
                    result += chunk.content
            return format_compact_summary(result) or _create_fallback_summary(messages)

        # Otherwise, find a cheap provider
        provider_type, model_id = await get_summary_provider()

        if model:
            # Override model if specified
            model_id = model

        if provider_type == "openrouter":
            result = await _summarize_with_openrouter(formatted_text, model_id, max_summary_tokens)
        elif provider_type == "anthropic":
            result = await _summarize_with_anthropic(formatted_text, model_id, max_summary_tokens)
        elif provider_type == "openai":
            result = await _summarize_with_openai(formatted_text, model_id, max_summary_tokens)
        else:
            # No provider available, return fallback
            return _create_fallback_summary(messages)

        return format_compact_summary(result) or _create_fallback_summary(messages)

    except Exception as e:
        logger.warning(f"Summarization failed: {e}")
        return _create_fallback_summary(messages)
