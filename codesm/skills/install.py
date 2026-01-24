"""
Skill installation - fetch skills from GitHub/skills.sh.
"""

from __future__ import annotations

import shutil
import tempfile
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from .loader import SkillLoader


@dataclass
class InstallResult:
    success: bool
    path: Optional[Path] = None
    error: Optional[str] = None
    skill_name: Optional[str] = None


# Default installation directory
SKILLS_DIR = Path.home() / '.claude' / 'skills'


def install(
    source: str,
    name: Optional[str] = None,
    force: bool = False,
    target_dir: Optional[Path] = None,
) -> InstallResult:
    """
    Install a skill from a source.
    
    Supported formats:
    - owner/repo                     → github.com/owner/repo (discovery mode)
    - owner/repo/skills/skill-name   → specific skill
    - https://github.com/owner/repo  → full URL
    - /path/to/local/skill           → local path
    
    Args:
        source: Source location
        name: Override skill name
        force: Overwrite if exists
        target_dir: Installation directory (default: ~/.claude/skills)
    
    Returns:
        InstallResult with success status and details
    """
    dest_dir = target_dir or SKILLS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Handle local path
    if source.startswith('/') or source.startswith('./'):
        return _install_local(Path(source), dest_dir, name, force)
    
    # Handle Git source
    return _install_git(source, dest_dir, name, force)


def _install_local(
    source_path: Path,
    dest_dir: Path,
    name: Optional[str],
    force: bool,
) -> InstallResult:
    """Install from local path."""
    source_path = source_path.resolve()
    
    # Find SKILL.md
    if source_path.is_file() and source_path.name == 'SKILL.md':
        skill_dir = source_path.parent
    elif source_path.is_dir():
        skill_md = source_path / 'SKILL.md'
        if not skill_md.exists():
            return InstallResult(success=False, error=f"SKILL.md not found in {source_path}")
        skill_dir = source_path
    else:
        return InstallResult(success=False, error=f"Invalid source: {source_path}")
    
    # Parse skill to get name
    try:
        skill = SkillLoader.load(skill_dir / 'SKILL.md')
    except Exception:
        return InstallResult(success=False, error="Failed to parse SKILL.md")
    
    skill_name = name or skill.name
    target = dest_dir / skill_name
    
    # Check existing
    if target.exists() and not force:
        return InstallResult(
            success=False,
            error=f"Skill already exists: {skill_name}. Use --force to overwrite."
        )
    
    # Copy
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(skill_dir, target)
    
    return InstallResult(success=True, path=target, skill_name=skill_name)


def _install_git(
    source: str,
    dest_dir: Path,
    name: Optional[str],
    force: bool,
) -> InstallResult:
    """Install from Git repository."""
    
    # Parse source
    if source.startswith('http'):
        repo_url = source
        skill_path = None
    elif '/' in source:
        parts = source.split('/')
        if len(parts) == 2:
            # owner/repo
            repo_url = f"https://github.com/{source}"
            skill_path = None
        else:
            # owner/repo/path/to/skill
            owner, repo = parts[0], parts[1]
            repo_url = f"https://github.com/{owner}/{repo}"
            skill_path = '/'.join(parts[2:])
    else:
        return InstallResult(success=False, error=f"Invalid source format: {source}")
    
    # Clone to temp directory
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        
        try:
            subprocess.run(
                ['git', 'clone', '--depth', '1', repo_url, str(tmp_path / 'repo')],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            return InstallResult(success=False, error=f"Git clone failed: {e.stderr.decode()}")
        
        repo_dir = tmp_path / 'repo'
        
        # Find skill directory
        if skill_path:
            skill_dir = repo_dir / skill_path
        else:
            # Discovery mode - look for skills/ directory
            skills_dir = repo_dir / 'skills'
            if skills_dir.exists():
                # List available skills
                skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir() and (d / 'SKILL.md').exists()]
                
                if len(skill_dirs) == 0:
                    return InstallResult(success=False, error="No skills found in repository")
                elif len(skill_dirs) == 1:
                    skill_dir = skill_dirs[0]
                else:
                    skill_names = [d.name for d in skill_dirs]
                    return InstallResult(
                        success=False,
                        error=f"Multiple skills found: {', '.join(skill_names)}. Specify with owner/repo/skills/name"
                    )
            else:
                # Check for SKILL.md in root
                if (repo_dir / 'SKILL.md').exists():
                    skill_dir = repo_dir
                else:
                    return InstallResult(success=False, error="No SKILL.md found")
        
        # Verify SKILL.md exists
        if not (skill_dir / 'SKILL.md').exists():
            return InstallResult(success=False, error=f"SKILL.md not found at {skill_path}")
        
        # Install from temp
        return _install_local(skill_dir, dest_dir, name, force)


def uninstall(name: str, target_dir: Optional[Path] = None) -> InstallResult:
    """Uninstall a skill by name."""
    dest_dir = target_dir or SKILLS_DIR
    skill_dir = dest_dir / name
    
    if not skill_dir.exists():
        return InstallResult(success=False, error=f"Skill not found: {name}")
    
    shutil.rmtree(skill_dir)
    return InstallResult(success=True, skill_name=name)


def list_installed(target_dir: Optional[Path] = None) -> list[str]:
    """List all installed skills."""
    dest_dir = target_dir or SKILLS_DIR
    
    if not dest_dir.exists():
        return []
    
    return [
        d.name for d in dest_dir.iterdir()
        if d.is_dir() and (d / 'SKILL.md').exists()
    ]
