"""Skill system for defining custom agent behaviors"""

from .loader import SkillLoader, Skill
from .manager import SkillManager, SkillSummary, SkillMatch
from .install import install, uninstall, list_installed, InstallResult

__all__ = [
    "SkillManager",
    "Skill", 
    "SkillSummary",
    "SkillMatch",
    "SkillLoader",
    "install",
    "uninstall", 
    "list_installed",
    "InstallResult",
]
