"""Skill tool - load and manage agent skills"""

import httpx
import logging
import re
import hashlib
from pathlib import Path
from .base import Tool

logger = logging.getLogger(__name__)

# Cache directory for fetched remote skills
SKILL_CACHE_DIR = Path.home() / ".cache" / "codesm" / "skills"

# GitHub API endpoints
GITHUB_RAW_URL = "https://raw.githubusercontent.com"
GITHUB_API_URL = "https://api.github.com"

# skills.sh primary registry (Vercel Labs)
SKILLS_SH_REGISTRY = "vercel-labs/agent-skills"
SKILLS_SH_BRANCH = "main"


class SkillTool(Tool):
    """Tool for loading and managing skills that enhance agent behavior"""
    
    name = "skill"
    description = """Load specialized skills that provide domain-specific instructions and best practices.

**IMPORTANT: Auto-fetch skills when relevant to the task!**
When working on React/Next.js code, fetch "react-best-practices".
When doing web design, fetch "web-design-guidelines".
Call `suggest` to get recommended skills based on context.

## Actions

- **suggest**: Get recommended skills based on file context (CALL THIS AUTOMATICALLY!)
- **browse**: List available skills from skills.sh registry
- **fetch**: Fetch a skill by name (e.g., "react-best-practices")
- **list**: Show local project skills
- **load/unload**: Load or unload local skills
- **active/show**: View skill status and content

## Auto-Skill Triggers

Call skill suggest or fetch AUTOMATICALLY when:
- Working on React/Next.js files → fetch "react-best-practices"
- Doing UI/CSS work → fetch "web-design-guidelines" 
- Starting a new coding task → call "suggest" with files context

## Examples

Auto-suggest based on context:
```json
{"action": "suggest", "files": ["src/App.tsx", "components/Button.tsx"]}
```

Fetch React best practices:
```json
{"action": "fetch", "name": "react-best-practices"}
```
"""
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["suggest", "browse", "fetch", "list", "load", "unload", "active", "show"],
                    "description": "Action to perform. Use 'suggest' automatically at start of tasks!",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name from skills.sh registry (for fetch) or local skill name (for load/show)",
                },
                "source": {
                    "type": "string",
                    "description": "Custom GitHub source: owner/repo or owner/repo/path (for fetch)",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths for context-based suggestion (for suggest action)",
                },
            },
            "required": ["action"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        action = args.get("action", "suggest")
        name = args.get("name")
        source = args.get("source")
        files = args.get("files", [])
        
        # Get skill manager from context (for local skills)
        skills = context.get("skills")
        
        if action == "suggest":
            return await self._suggest_skills(files, context)
        
        elif action == "browse":
            return await self._browse_registry()
        
        elif action == "fetch":
            if source:
                return await self._fetch_from_source(source)
            elif name:
                return await self._fetch_from_registry(name)
            else:
                return "Error: 'name' or 'source' parameter required for fetch action"
        
        elif action == "list":
            if not skills:
                return "No local skill manager initialized"
            return self._list_skills(skills)
        
        elif action == "load":
            if not skills:
                return "No local skill manager initialized"
            if not name:
                return "Error: 'name' parameter required for load action"
            return self._load_skill(skills, name)
        
        elif action == "unload":
            if not skills:
                return "No local skill manager initialized"
            if not name:
                return "Error: 'name' parameter required for unload action"
            return self._unload_skill(skills, name)
        
        elif action == "active":
            if not skills:
                return "No local skill manager initialized"
            return self._active_skills(skills)
        
        elif action == "show":
            if not skills:
                return "No local skill manager initialized"
            if not name:
                return "Error: 'name' parameter required for show action"
            return self._show_skill(skills, name)
        
        return f"Error: Unknown action '{action}'"
    
    async def _suggest_skills(self, files: list, context: dict) -> str:
        """Suggest skills based on file context"""
        # Define skill triggers based on file patterns
        SKILL_TRIGGERS = {
            "react-best-practices": {
                "extensions": [".tsx", ".jsx"],
                "keywords": ["react", "next", "component", "useState", "useEffect"],
                "paths": ["components/", "pages/", "app/", "src/"],
            },
            "web-design-guidelines": {
                "extensions": [".css", ".scss", ".sass", ".less"],
                "keywords": ["css", "style", "design", "layout", "ui", "ux"],
                "paths": ["styles/", "css/"],
            },
            "claude.ai": {
                "extensions": [],
                "keywords": ["claude", "anthropic", "llm", "prompt"],
                "paths": [],
            },
        }
        
        suggested = set()
        reasons = {}
        
        # Check file extensions and paths
        for file_path in files:
            file_lower = file_path.lower()
            for skill_name, triggers in SKILL_TRIGGERS.items():
                # Check extensions
                for ext in triggers["extensions"]:
                    if file_lower.endswith(ext):
                        suggested.add(skill_name)
                        reasons[skill_name] = f"Working with {ext} files"
                        break
                # Check paths
                for path in triggers["paths"]:
                    if path in file_lower:
                        suggested.add(skill_name)
                        reasons[skill_name] = f"Working in {path}"
                        break
        
        # Check context for keywords (e.g., from user message)
        user_message = context.get("user_message", "").lower()
        for skill_name, triggers in SKILL_TRIGGERS.items():
            for kw in triggers["keywords"]:
                if kw in user_message:
                    suggested.add(skill_name)
                    reasons[skill_name] = f"Mentioned '{kw}'"
                    break
        
        if not suggested:
            return "No skill suggestions for current context. Use `skill browse` to see available skills."
        
        lines = ["# Suggested Skills", ""]
        for skill in suggested:
            reason = reasons.get(skill, "Context match")
            lines.append(f"- **{skill}** - {reason}")
            lines.append(f"  → `skill fetch {skill}`")
            lines.append("")
        
        lines.append("Fetch these skills to get best practices for your current task.")
        
        # Auto-fetch the first suggested skill
        if suggested:
            first_skill = list(suggested)[0]
            lines.append(f"\n---\n\n**Auto-fetching: {first_skill}**\n")
            content = await self._fetch_from_registry(first_skill)
            lines.append(content)
        
        return "\n".join(lines)
    
    async def _browse_registry(self) -> str:
        """Browse skills.sh registry (vercel-labs/agent-skills)"""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Get skills directory listing
                response = await client.get(
                    f"{GITHUB_API_URL}/repos/{SKILLS_SH_REGISTRY}/contents/skills",
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                response.raise_for_status()
                items = response.json()
        except httpx.HTTPError as e:
            return f"Error fetching skills.sh registry: {e}"
        
        lines = [
            "# skills.sh Registry",
            "",
            f"Source: {SKILLS_SH_REGISTRY}",
            "",
            "## Available Skills",
            "",
        ]
        
        skill_dirs = [item for item in items if item.get("type") == "dir"]
        
        for item in skill_dirs:
            name = item.get("name", "")
            lines.append(f"- **{name}**")
            lines.append(f"  Fetch: `skill fetch {name}`")
            lines.append("")
        
        lines.extend([
            "---",
            "Use `skill fetch <name>` to load a skill's instructions.",
            "Use `skill fetch --source owner/repo/path` for custom repositories.",
        ])
        
        return "\n".join(lines)
    
    async def _fetch_from_registry(self, name: str) -> str:
        """Fetch a skill from the skills.sh registry by name"""
        # Build URL to SKILL.md in vercel-labs/agent-skills
        source = f"{SKILLS_SH_REGISTRY}/skills/{name}"
        return await self._fetch_from_source(source)
    
    async def _fetch_from_source(self, source: str) -> str:
        """Fetch a skill from a GitHub source"""
        # Parse source (owner/repo or owner/repo/path)
        parts = source.strip("/").split("/")
        if len(parts) < 2:
            return f"Invalid source format: {source}. Use owner/repo or owner/repo/path"
        
        owner = parts[0]
        repo = parts[1]
        skill_path = "/".join(parts[2:]) if len(parts) > 2 else ""
        
        # Check cache first
        cache_key = hashlib.md5(source.encode()).hexdigest()
        cache_file = SKILL_CACHE_DIR / f"{cache_key}.md"
        
        if cache_file.exists():
            content = cache_file.read_text(encoding="utf-8")
            return self._format_skill_content(source, content, cached=True)
        
        # Try to fetch SKILL.md
        skill_urls = []
        if skill_path:
            skill_urls.append(f"{GITHUB_RAW_URL}/{owner}/{repo}/main/{skill_path}/SKILL.md")
            skill_urls.append(f"{GITHUB_RAW_URL}/{owner}/{repo}/master/{skill_path}/SKILL.md")
        else:
            skill_urls.extend([
                f"{GITHUB_RAW_URL}/{owner}/{repo}/main/SKILL.md",
                f"{GITHUB_RAW_URL}/{owner}/{repo}/master/SKILL.md",
            ])
        
        content = None
        async with httpx.AsyncClient(timeout=30) as client:
            for url in skill_urls:
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        content = response.text
                        break
                except httpx.HTTPError:
                    continue
        
        if not content:
            return f"Could not find SKILL.md in {source}. Check the path or try `skill browse` to see available skills."
        
        # Cache the content
        SKILL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(content, encoding="utf-8")
        
        return self._format_skill_content(source, content, cached=False)
    
    def _format_skill_content(self, source: str, content: str, cached: bool = False) -> str:
        """Format fetched skill content for display"""
        # Parse frontmatter
        frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        skill_name = source.split("/")[-1]
        description = ""
        body = content
        
        if frontmatter_match:
            fm_text = frontmatter_match.group(1)
            body = content[frontmatter_match.end():]
            
            name_match = re.search(r'^name:\s*(.+)$', fm_text, re.MULTILINE)
            if name_match:
                skill_name = name_match.group(1).strip().strip('"\'')
            
            desc_match = re.search(r'^description:\s*(.+)$', fm_text, re.MULTILINE)
            if desc_match:
                description = desc_match.group(1).strip().strip('"\'')
        
        lines = [
            f"# Skill: {skill_name}",
            f"Source: {source}",
        ]
        if cached:
            lines.append("(Cached)")
        if description:
            lines.append(f"\n**Description:** {description}")
        lines.extend([
            "",
            "---",
            "",
            body.strip(),
        ])
        
        return "\n".join(lines)
    
    def _list_skills(self, skills) -> str:
        """List all discovered skills"""
        skill_list = skills.list()
        
        if not skill_list:
            return "No skills found.\n\nTo add skills, create SKILL.md files in:\n- .codesm/skills/\n- skills/"
        
        lines = ["# Available Skills", ""]
        
        for s in skill_list:
            status = "✓ loaded" if s.is_active else ""
            lines.append(f"## {s.name} {status}")
            
            if s.description:
                lines.append(f"{s.description}")
            
            if s.triggers:
                lines.append(f"Triggers: {', '.join(s.triggers)}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def _load_skill(self, skills, name: str) -> str:
        """Load a skill"""
        if skills.is_active(name):
            return f"Skill '{name}' is already loaded"
        
        skill = skills.load(name)
        if not skill:
            available = [s.name for s in skills.list()]
            return f"Skill '{name}' not found. Available: {', '.join(available)}"
        
        return f"✓ Loaded skill: {name}\n\n{skill.description or 'No description'}"
    
    def _unload_skill(self, skills, name: str) -> str:
        """Unload a skill"""
        if skills.unload(name):
            return f"✓ Unloaded skill: {name}"
        return f"Skill '{name}' is not currently loaded"
    
    def _active_skills(self, skills) -> str:
        """List active skills"""
        active = skills.active()
        
        if not active:
            return "No skills currently loaded. Use `skill load <name>` to load one."
        
        lines = ["# Active Skills", ""]
        
        for s in active:
            lines.append(f"- **{s.name}**: {s.description or 'No description'}")
        
        return "\n".join(lines)
    
    def _show_skill(self, skills, name: str) -> str:
        """Show skill content without loading"""
        skill = skills.get(name)
        if not skill:
            return f"Skill '{name}' not found"
        
        lines = [
            f"# Skill: {skill.name}",
            "",
            f"**Description:** {skill.description or 'None'}",
            f"**Triggers:** {', '.join(skill.triggers) or 'None'}",
            f"**Path:** {skill.path}",
            "",
            "## Content",
            "",
            skill.content,
        ]
        
        return "\n".join(lines)
    
    def _list_resources(self, skills, name: str) -> str:
        """List skill resources"""
        skill = skills.get(name)
        if not skill:
            return f"Skill '{name}' not found"
        
        if not skill.resources:
            return f"Skill '{name}' has no resources"
        
        lines = [f"# Resources for {name}", ""]
        
        for res in skill.resources:
            lines.append(f"- {res}")
        
        return "\n".join(lines)
    
    def _read_resource(self, skills, name: str, resource: str) -> str:
        """Read a skill resource file"""
        resource_path = skills.get_resource_path(name, resource)
        
        if not resource_path:
            skill = skills.get(name)
            if not skill:
                return f"Skill '{name}' not found"
            return f"Resource '{resource}' not found in skill '{name}'"
        
        try:
            content = resource_path.read_text(encoding="utf-8")
            return f"# {resource}\n\n```\n{content}\n```"
        except Exception as e:
            return f"Error reading resource: {e}"
