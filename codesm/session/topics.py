"""Topics/Indexing - thread categorization using Gemini 2.5 Flash-Lite"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from codesm.storage.storage import Storage

logger = logging.getLogger(__name__)

# Predefined topic categories
TOPIC_CATEGORIES = [
    "feature",      # New feature implementation
    "bugfix",       # Bug fixing and debugging
    "refactor",     # Code refactoring
    "docs",         # Documentation
    "testing",      # Writing or fixing tests
    "config",       # Configuration and setup
    "review",       # Code review
    "research",     # Research and exploration
    "planning",     # Planning and architecture
    "devops",       # CI/CD, deployment, infrastructure
    "performance",  # Performance optimization
    "security",     # Security-related work
    "ui",           # UI/UX work
    "api",          # API development
    "database",     # Database work
    "other",        # Uncategorized
]

# System prompt for topic extraction
TOPICS_SYSTEM_PROMPT = """You are a thread categorization assistant. Analyze conversation content and assign relevant topics.

# Your Task
Given a conversation summary, extract:
1. Primary topic (most relevant category)
2. Secondary topics (other relevant categories, max 3)
3. Keywords (3-5 specific terms describing the work)

# Available Categories
- feature: New feature implementation
- bugfix: Bug fixing and debugging
- refactor: Code refactoring
- docs: Documentation
- testing: Writing or fixing tests
- config: Configuration and setup
- review: Code review
- research: Research and exploration
- planning: Planning and architecture
- devops: CI/CD, deployment, infrastructure
- performance: Performance optimization
- security: Security-related work
- ui: UI/UX work
- api: API development
- database: Database work
- other: Uncategorized

# Output Format (JSON)
{
  "primary": "feature",
  "secondary": ["api", "testing"],
  "keywords": ["authentication", "jwt", "middleware"]
}

Respond with ONLY valid JSON, no explanation."""


@dataclass
class TopicInfo:
    """Topic information for a session"""
    primary: str = "other"
    secondary: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    indexed_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        return {
            "primary": self.primary,
            "secondary": self.secondary,
            "keywords": self.keywords,
            "indexed_at": self.indexed_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TopicInfo":
        return cls(
            primary=data.get("primary", "other"),
            secondary=data.get("secondary", []),
            keywords=data.get("keywords", []),
            indexed_at=datetime.fromisoformat(data["indexed_at"]) if data.get("indexed_at") else datetime.now(),
        )


class TopicIndex:
    """Manages topic indexing for sessions"""
    
    def __init__(self):
        self._cache: dict[str, TopicInfo] = {}
    
    def get_topics(self, session_id: str) -> TopicInfo | None:
        """Get topics for a session"""
        # Check cache first
        if session_id in self._cache:
            return self._cache[session_id]
        
        # Load from storage
        data = Storage.read(["topics", session_id])
        if data:
            info = TopicInfo.from_dict(data)
            self._cache[session_id] = info
            return info
        
        return None
    
    def save_topics(self, session_id: str, topics: TopicInfo):
        """Save topics for a session"""
        self._cache[session_id] = topics
        Storage.write(["topics", session_id], topics.to_dict())
    
    def delete_topics(self, session_id: str):
        """Delete topics for a session"""
        self._cache.pop(session_id, None)
        try:
            Storage.delete(["topics", session_id])
        except Exception:
            pass
    
    def list_by_topic(self, topic: str) -> list[str]:
        """List all session IDs with a given topic"""
        keys = Storage.list(["topics"])
        matching = []
        
        for key in keys:
            data = Storage.read(key)
            if data:
                if data.get("primary") == topic or topic in data.get("secondary", []):
                    session_id = key[-1]  # Last part of key is session_id
                    matching.append(session_id)
        
        return matching
    
    def search_by_keyword(self, keyword: str) -> list[str]:
        """Search sessions by keyword"""
        keyword = keyword.lower()
        keys = Storage.list(["topics"])
        matching = []
        
        for key in keys:
            data = Storage.read(key)
            if data:
                keywords = [k.lower() for k in data.get("keywords", [])]
                if any(keyword in k for k in keywords):
                    session_id = key[-1]
                    matching.append(session_id)
        
        return matching
    
    def get_all_topics_summary(self) -> dict[str, int]:
        """Get count of sessions per topic"""
        keys = Storage.list(["topics"])
        counts: dict[str, int] = {}
        
        for key in keys:
            data = Storage.read(key)
            if data:
                primary = data.get("primary", "other")
                counts[primary] = counts.get(primary, 0) + 1
        
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))
    
    async def index_session(self, session_id: str, force: bool = False) -> TopicInfo:
        """Index a session using LLM to extract topics"""
        from codesm.session.session import Session
        
        # Check if already indexed
        if not force:
            existing = self.get_topics(session_id)
            if existing:
                return existing
        
        # Load session
        session = Session.load(session_id)
        if not session:
            return TopicInfo()
        
        # Build summary from messages
        summary = self._build_session_summary(session)
        
        if not summary.strip():
            # No content to analyze
            info = TopicInfo()
            self.save_topics(session_id, info)
            return info
        
        # Use Gemini Flash-Lite for fast categorization
        try:
            from codesm.provider.base import get_provider
            import json
            
            provider = get_provider("topics")  # Gemini 2.5 Flash-Lite
            
            user_prompt = f"""Categorize this conversation:

Title: {session.title}

Content Summary:
{summary[:3000]}

Respond with JSON only."""

            response_text = ""
            async for chunk in provider.stream(
                system=TOPICS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                tools=None,
            ):
                if chunk.type == "text":
                    response_text += chunk.content
            
            # Parse JSON response
            response_text = response_text.strip()
            # Handle markdown code blocks
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1])
            
            data = json.loads(response_text)
            
            info = TopicInfo(
                primary=data.get("primary", "other"),
                secondary=data.get("secondary", [])[:3],
                keywords=data.get("keywords", [])[:5],
            )
            
            # Validate primary topic
            if info.primary not in TOPIC_CATEGORIES:
                info.primary = "other"
            
            # Validate secondary topics
            info.secondary = [t for t in info.secondary if t in TOPIC_CATEGORIES]
            
            self.save_topics(session_id, info)
            return info
            
        except Exception as e:
            logger.warning(f"Failed to index session {session_id}: {e}")
            # Fallback to basic indexing
            info = self._basic_index(session)
            self.save_topics(session_id, info)
            return info
    
    def _build_session_summary(self, session) -> str:
        """Build a summary of session content for indexing"""
        parts = []
        
        for msg in session.messages[:20]:  # Limit to first 20 messages
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role in ("user", "assistant") and content:
                # Truncate long messages
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(f"{role}: {content}")
        
        return "\n\n".join(parts)
    
    def _basic_index(self, session) -> TopicInfo:
        """Basic keyword-based indexing without LLM"""
        content = " ".join(
            msg.get("content", "") 
            for msg in session.messages 
            if msg.get("role") in ("user", "assistant")
        ).lower()
        
        # Simple keyword detection
        topic_keywords = {
            "feature": ["implement", "add", "create", "new feature", "build"],
            "bugfix": ["fix", "bug", "error", "issue", "broken", "debug"],
            "refactor": ["refactor", "cleanup", "restructure", "reorganize"],
            "docs": ["document", "readme", "docs", "comment", "explain"],
            "testing": ["test", "spec", "coverage", "unittest", "pytest"],
            "config": ["config", "setup", "install", "configure", "environment"],
            "review": ["review", "check", "audit", "analyze"],
            "research": ["research", "explore", "investigate", "understand"],
            "planning": ["plan", "design", "architect", "structure"],
            "devops": ["deploy", "ci", "cd", "docker", "kubernetes", "pipeline"],
            "performance": ["performance", "optimize", "speed", "cache", "slow"],
            "security": ["security", "auth", "permission", "encrypt", "token"],
            "ui": ["ui", "ux", "frontend", "css", "style", "component"],
            "api": ["api", "endpoint", "rest", "graphql", "route"],
            "database": ["database", "sql", "query", "schema", "migration"],
        }
        
        scores: dict[str, int] = {}
        for topic, keywords in topic_keywords.items():
            score = sum(1 for kw in keywords if kw in content)
            if score > 0:
                scores[topic] = score
        
        if not scores:
            return TopicInfo(primary="other")
        
        sorted_topics = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        primary = sorted_topics[0][0]
        secondary = [t[0] for t in sorted_topics[1:4] if t[1] > 0]
        
        # Extract some keywords from title
        keywords = [w for w in session.title.split() if len(w) > 3][:5]
        
        return TopicInfo(primary=primary, secondary=secondary, keywords=keywords)


# Global instance
_topic_index: TopicIndex | None = None


def get_topic_index() -> TopicIndex:
    """Get the global topic index instance"""
    global _topic_index
    if _topic_index is None:
        _topic_index = TopicIndex()
    return _topic_index


async def index_session(session_id: str, force: bool = False) -> TopicInfo:
    """Convenience function to index a session"""
    return await get_topic_index().index_session(session_id, force)


async def index_all_sessions(force: bool = False) -> dict[str, TopicInfo]:
    """Index all sessions"""
    from codesm.session.session import Session
    
    sessions = Session.list_sessions()
    index = get_topic_index()
    results = {}
    
    for session_data in sessions:
        session_id = session_data["id"]
        try:
            info = await index.index_session(session_id, force)
            results[session_id] = info
        except Exception as e:
            logger.warning(f"Failed to index {session_id}: {e}")
    
    return results
