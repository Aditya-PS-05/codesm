"""Skill manager - discovers, loads, and manages markdown-based skills"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loader import SkillLoader, Skill

logger = logging.getLogger(__name__)


# File pattern mappings for context-aware matching
FILE_PATTERN_KEYWORDS = {
    'react': {'.tsx', '.jsx', 'react'},
    'typescript': {'.ts', '.tsx', 'typescript'},
    'javascript': {'.js', '.jsx', 'javascript'},
    'python': {'.py', 'python'},
    'rust': {'.rs', 'rust', 'cargo'},
    'go': {'.go', 'golang'},
    'css': {'.css', '.scss', '.sass', 'tailwind'},
    'html': {'.html', '.htm'},
    'sql': {'.sql', 'database', 'postgres', 'mysql'},
    'docker': {'dockerfile', 'docker', 'container'},
    'kubernetes': {'k8s', 'kubernetes', 'helm'},
    'terraform': {'.tf', 'terraform'},
    'markdown': {'.md', 'markdown'},
    'json': {'.json'},
    'yaml': {'.yaml', '.yml'},
    'nextjs': {'next.js', 'nextjs', 'next'},
    'svelte': {'.svelte', 'svelte'},
    'vue': {'.vue', 'vue'},
    'pdf': {'.pdf', 'pdf'},
}

# Stopwords to filter from keyword matching
STOPWORDS = {
    'the', 'and', 'for', 'with', 'this', 'that', 'when', 'use', 'using',
    'how', 'what', 'why', 'can', 'could', 'would', 'should', 'please',
    'help', 'need', 'want', 'like', 'make', 'create', 'file', 'code',
}


@dataclass
class SkillSummary:
    """Lightweight skill info for listing"""
    name: str
    description: str
    triggers: list[str]
    path: Path
    is_active: bool = False


@dataclass
class SkillMatch:
    """Result from skill matching"""
    skill: Skill
    score: float
    match_type: str  # 'trigger', 'keyword', 'file'


class SkillManager:
    """
    Manages markdown-based skills that inject instructions into the agent prompt.
    
    Skills are discovered from:
    - {workspace}/.codesm/skills/**/SKILL.md
    - {workspace}/skills/**/SKILL.md
    - ~/.claude/skills/**/SKILL.md (global, skills.sh compatible)
    - ~/.codesm/skills/**/SKILL.md (global)
    
    Workspace skills take precedence over global skills.
    
    Features:
    - Inverted keyword index for O(1) matching
    - File-context awareness (detects file types → relevant skills)
    - Trigger-based auto-loading
    """
    
    # Max total size of injected skill content (prevent prompt bloat)
    MAX_INJECTED_SIZE = 40_000
    
    # Global skill directories (skills.sh compatible)
    GLOBAL_SKILL_DIRS = [
        Path.home() / '.claude' / 'skills',      # Claude/skills.sh format
        Path.home() / '.opencode' / 'skills',    # OpenCode format  
        Path.home() / '.codesm' / 'skills',      # codesm format
    ]
    
    def __init__(
        self,
        workspace_dir: Path,
        skills_dirs: list[str] | None = None,
        auto_triggers_enabled: bool = True,
        include_global: bool = True,
    ):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.skills_dirs = skills_dirs or [".codesm/skills", ".claude/skills", "skills", "examples/skills"]
        self.auto_triggers_enabled = auto_triggers_enabled
        self.include_global = include_global
        
        self._discovered: dict[str, Skill] = {}
        self._active: dict[str, Skill] = {}
        self._triggered_this_session: set[str] = set()
        
        # Inverted indexes for fast O(1) lookup
        self._keyword_index: dict[str, set[str]] = defaultdict(set)
        self._file_pattern_index: dict[str, set[str]] = defaultdict(set)
        
        # Discover on init
        self.discover()
    
    def discover(self) -> dict[str, Skill]:
        """Scan skill directories and discover all SKILL.md files"""
        self._discovered.clear()
        self._keyword_index.clear()
        self._file_pattern_index.clear()
        
        # Load global skills first (lower priority)
        if self.include_global:
            for global_dir in self.GLOBAL_SKILL_DIRS:
                self._load_skills_from_dir(global_dir)
        
        # Load project skills (higher priority - can override global)
        for rel_dir in self.skills_dirs:
            skills_path = self.workspace_dir / rel_dir
            self._load_skills_from_dir(skills_path)
        
        logger.info(f"Discovered {len(self._discovered)} skills")
        return self._discovered
    
    def _load_skills_from_dir(self, skills_path: Path) -> None:
        """Load all SKILL.md files from a directory"""
        if not skills_path.exists():
            return
        
        for skill_file in skills_path.rglob("SKILL.md"):
            try:
                skill = SkillLoader.load(skill_file)
                
                # Handle name collisions (later dirs win)
                if skill.name in self._discovered:
                    logger.debug(f"Skill '{skill.name}' overridden by {skill_file}")
                
                self._add_skill(skill)
                logger.debug(f"Discovered skill: {skill.name} at {skill_file}")
                
            except Exception as e:
                logger.warning(f"Failed to load skill from {skill_file}: {e}")
    
    def _add_skill(self, skill: Skill) -> None:
        """Add a skill and update inverted indexes"""
        self._discovered[skill.name] = skill
        
        # Extract keywords from name + description
        text = f"{skill.name} {skill.description}".lower()
        keywords = set(re.findall(r'\b[a-z]{3,}\b', text)) - STOPWORDS
        
        for keyword in keywords:
            self._keyword_index[keyword].add(skill.name)
        
        # Extract file patterns
        full_text = f"{skill.name} {skill.description} {skill.content}".lower()
        for category, patterns in FILE_PATTERN_KEYWORDS.items():
            if any(p in full_text for p in patterns):
                for pattern in patterns:
                    self._file_pattern_index[pattern].add(skill.name)
    
    def list(self) -> list[SkillSummary]:
        """List all discovered skills"""
        return [
            SkillSummary(
                name=skill.name,
                description=skill.description,
                triggers=skill.triggers,
                path=skill.path,
                is_active=skill.name in self._active,
            )
            for skill in self._discovered.values()
        ]
    
    def get(self, name: str) -> Skill | None:
        """Get a skill by name"""
        return self._discovered.get(name)
    
    def lookup_by_keyword(self, keyword: str) -> set[str]:
        """O(1) lookup: keyword → skill names"""
        return self._keyword_index.get(keyword.lower(), set())
    
    def lookup_by_file(self, file_path: str) -> set[str]:
        """O(1) lookup: file extension/name → skill names"""
        path = Path(file_path)
        ext = path.suffix.lower()
        name = path.name.lower()
        
        matches = set()
        matches.update(self._file_pattern_index.get(ext, set()))
        matches.update(self._file_pattern_index.get(name, set()))
        
        # Special cases
        if 'dockerfile' in name:
            matches.update(self._file_pattern_index.get('dockerfile', set()))
        
        return matches
    
    def match(
        self,
        query: str,
        context_files: list[str] | None = None,
        max_skills: int = 3,
        min_score: float = 0.2,
    ) -> list[SkillMatch]:
        """
        Match skills based on query and file context.
        Uses inverted index for O(1) keyword lookup.
        
        Args:
            query: User message or search query
            context_files: List of file paths for context-aware matching
            max_skills: Maximum number of skills to return
            min_score: Minimum score threshold
        
        Returns:
            List of SkillMatch sorted by score (highest first)
        """
        scores: dict[str, float] = {}
        match_types: dict[str, str] = {}
        
        # Strategy 1: Keyword matching from query
        keywords = self._extract_keywords(query)
        for keyword in keywords:
            matching_skills = self.lookup_by_keyword(keyword)
            for skill_name in matching_skills:
                scores[skill_name] = scores.get(skill_name, 0) + 1.0
                match_types[skill_name] = 'keyword'
        
        # Strategy 2: File-context matching
        if context_files:
            for file_path in context_files:
                matching_skills = self.lookup_by_file(file_path)
                for skill_name in matching_skills:
                    scores[skill_name] = scores.get(skill_name, 0) + 0.5
                    if skill_name not in match_types:
                        match_types[skill_name] = 'file'
        
        # Normalize scores
        if keywords:
            max_possible = len(keywords)
            for name in scores:
                scores[name] = scores[name] / max_possible
        
        # Filter and sort
        results = []
        for skill_name, score in scores.items():
            if score >= min_score:
                skill = self.get(skill_name)
                if skill:
                    results.append(SkillMatch(
                        skill=skill,
                        score=score,
                        match_type=match_types.get(skill_name, 'keyword'),
                    ))
        
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:max_skills]
    
    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text"""
        text = text.lower()
        words = re.findall(r'\b[a-z]{3,}\b', text)
        return [w for w in words if w not in STOPWORDS]
    
    def load(self, name: str) -> Skill | None:
        """Load a skill by name (add to active set)"""
        skill = self._discovered.get(name)
        if not skill:
            return None
        
        self._active[name] = skill
        logger.info(f"Loaded skill: {name}")
        return skill
    
    def unload(self, name: str) -> bool:
        """Unload a skill by name"""
        if name in self._active:
            del self._active[name]
            # Also remove from triggered set so it can trigger again
            self._triggered_this_session.discard(name)
            logger.info(f"Unloaded skill: {name}")
            return True
        return False
    
    def active(self) -> list[Skill]:
        """Get list of currently active skills"""
        return list(self._active.values())
    
    def is_active(self, name: str) -> bool:
        """Check if a skill is active"""
        return name in self._active
    
    def auto_load_for_message(self, user_message: str) -> list[str]:
        """
        Check triggers and auto-load matching skills.
        Returns list of newly loaded skill names.
        """
        if not self.auto_triggers_enabled:
            return []
        
        newly_loaded = []
        
        for skill in self._discovered.values():
            # Skip if already active or already triggered this session
            if skill.name in self._active:
                continue
            if skill.name in self._triggered_this_session:
                continue
            
            # Check triggers
            for pattern in skill.triggers:
                try:
                    if re.search(pattern, user_message, re.IGNORECASE):
                        self._active[skill.name] = skill
                        self._triggered_this_session.add(skill.name)
                        newly_loaded.append(skill.name)
                        logger.info(f"Auto-loaded skill '{skill.name}' (trigger: {pattern})")
                        break
                except re.error as e:
                    logger.warning(f"Invalid trigger pattern in skill {skill.name}: {pattern} - {e}")
        
        return newly_loaded
    
    def render_active_for_prompt(self) -> str:
        """Render all active skills as a prompt block"""
        if not self._active:
            return ""
        
        parts = ["# Loaded Skills"]
        total_size = 0
        truncated = False
        
        for skill in self._active.values():
            skill_block = self._render_skill(skill)
            
            if total_size + len(skill_block) > self.MAX_INJECTED_SIZE:
                truncated = True
                break
            
            parts.append(skill_block)
            total_size += len(skill_block)
        
        if truncated:
            parts.append("\n[Warning: Some skills truncated due to size limit]")
        
        return "\n\n".join(parts)
    
    def _render_skill(self, skill: Skill) -> str:
        """Render a single skill for prompt injection"""
        lines = [
            f"<loaded_skill name=\"{skill.name}\">",
        ]
        
        if skill.description:
            lines.append(f"Description: {skill.description}")
            lines.append("")
        
        lines.append(skill.content)
        
        if skill.resources:
            lines.append("")
            lines.append("Resources available in skill folder:")
            for res in skill.resources[:10]:  # Limit shown resources
                lines.append(f"  - {res}")
            if len(skill.resources) > 10:
                lines.append(f"  ... and {len(skill.resources) - 10} more")
        
        lines.append("</loaded_skill>")
        
        return "\n".join(lines)
    
    def clear(self):
        """Clear all active skills"""
        self._active.clear()
        self._triggered_this_session.clear()
    
    def get_resource_path(self, skill_name: str, resource: str) -> Path | None:
        """
        Get the full path to a skill resource.
        Returns None if skill not found or resource path is invalid.
        """
        skill = self._discovered.get(skill_name)
        if not skill:
            return None
        
        # Resolve the resource path
        resource_path = (skill.root_dir / resource).resolve()
        
        # Security: ensure it's within the skill directory
        try:
            resource_path.relative_to(skill.root_dir)
        except ValueError:
            logger.warning(f"Attempted path traversal in skill {skill_name}: {resource}")
            return None
        
        if not resource_path.exists():
            return None
        
        return resource_path
