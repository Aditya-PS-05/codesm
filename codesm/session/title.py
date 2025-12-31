"""Session title generation - creates meaningful names from user messages"""

import re
from datetime import datetime


TITLE_PROMPT = """Generate a short, descriptive title for this conversation based on the user's message.

Rules:
- Single line only, maximum 50 characters
- Focus on the main topic for retrievability
- Use -ing verbs for actions (Debugging, Implementing, Refactoring)
- Keep technical terms, numbers, filenames exact
- Remove articles (the, this, my, a, an)
- Never assume tech stack
- Always output something, even for minimal input

Examples:
- "debug 500 errors in production" → "Debugging production 500 errors"
- "refactor user service" → "Refactoring user service"
- "why is app.js failing" → "Analyzing app.js failure"
- "implement rate limiting" → "Implementing rate limiting"
- "how do I connect postgres to my API" → "Connecting Postgres to API"
- "best practices for React hooks" → "React hooks best practices"
- "hi" → "Quick check-in"
- "help me with this code" → "Code assistance"

User message:
{message}

Title (max 50 chars, single line):"""


def create_default_title(is_child: bool = False) -> str:
    """Create a default timestamp-based title"""
    prefix = "Child session - " if is_child else "New session - "
    return prefix + datetime.now().strftime("%Y-%m-%d %H:%M")


def is_default_title(title: str) -> bool:
    """Check if a title is a default timestamp title"""
    return bool(re.match(
        r"^(New session - |Child session - )\d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
        title
    ))


def generate_title_sync(message: str) -> str:
    """Generate a title synchronously using simple heuristics (no LLM)"""
    if not message or len(message.strip()) < 3:
        return "Quick check-in"
    
    msg = message.strip()
    
    # Remove common greetings
    greetings = ["hi", "hello", "hey", "yo", "sup"]
    if msg.lower() in greetings:
        return "Quick check-in"
    
    # Extract main action/topic
    msg_lower = msg.lower()
    
    # Common action patterns
    action_patterns = [
        (r"^(fix|debug|solve)\s+(.+)", lambda m: f"Debugging {m.group(2)[:40]}"),
        (r"^(implement|add|create|build|make)\s+(.+)", lambda m: f"Implementing {m.group(2)[:37]}"),
        (r"^(refactor|improve|optimize)\s+(.+)", lambda m: f"Refactoring {m.group(2)[:38]}"),
        (r"^(explain|what is|how does)\s+(.+)", lambda m: f"Understanding {m.group(2)[:36]}"),
        (r"^how\s+(do i|to|can i)\s+(.+)", lambda m: f"How to {m.group(2)[:42]}"),
        (r"^why\s+(is|does|did|are)\s+(.+)", lambda m: f"Analyzing {m.group(2)[:40]}"),
        (r"^(write|generate)\s+(.+)", lambda m: f"Writing {m.group(2)[:42]}"),
        (r"^(update|change|modify)\s+(.+)", lambda m: f"Updating {m.group(2)[:41]}"),
        (r"^(test|testing)\s+(.+)", lambda m: f"Testing {m.group(2)[:42]}"),
        (r"^(review|check)\s+(.+)", lambda m: f"Reviewing {m.group(2)[:40]}"),
        (r"^(setup|set up|configure)\s+(.+)", lambda m: f"Setting up {m.group(2)[:38]}"),
        (r"^(help|can you help|please help)\s*(me\s*)?(with\s*)?(.+)", lambda m: f"Help with {m.group(4)[:39]}"),
    ]
    
    for pattern, formatter in action_patterns:
        match = re.match(pattern, msg_lower)
        if match:
            title = formatter(match).strip()
            # Capitalize first letter
            return title[0].upper() + title[1:] if title else "Code assistance"
    
    # Remove common filler words
    words = msg.split()
    filler = {"the", "a", "an", "this", "my", "please", "can", "you", "i", "want", "to", "need"}
    meaningful = [w for w in words if w.lower() not in filler]
    
    if meaningful:
        # Take first few meaningful words
        title = " ".join(meaningful[:6])
        # Truncate to 50 chars
        if len(title) > 50:
            title = title[:47] + "..."
        return title.capitalize()
    
    return "Code assistance"


async def generate_title_async(message: str, provider=None) -> str:
    """Generate a title using LLM if available, fallback to sync"""
    if not provider:
        return generate_title_sync(message)
    
    try:
        # Use a small/fast model call for title generation
        prompt = TITLE_PROMPT.format(message=message[:500])
        
        title = ""
        async for chunk in provider.stream(
            system="You generate short, descriptive titles. Output only the title, nothing else.",
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        ):
            if chunk.type == "text":
                title += chunk.content
        
        # Clean up the title
        title = title.strip().strip('"\'')
        
        # Enforce max length
        if len(title) > 50:
            title = title[:47] + "..."
        
        return title if title else generate_title_sync(message)
    except Exception:
        return generate_title_sync(message)
