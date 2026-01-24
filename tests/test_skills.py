"""Tests for the skill system"""

import pytest
import tempfile
from pathlib import Path

from codesm.skills import SkillManager, SkillLoader, Skill


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with skill directories"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        yield workspace


@pytest.fixture
def skill_file(temp_workspace):
    """Create a sample skill file"""
    skill_dir = temp_workspace / ".codesm" / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: test-skill
description: A test skill for unit testing
triggers:
  - "(?i)test"
  - "(?i)testing"
---

# Test Skill

This is the skill content.

## Instructions

Do the test things.
""")
    
    # Add a resource file
    resource_file = skill_dir / "template.txt"
    resource_file.write_text("This is a resource template.")
    
    return skill_md


class TestSkillLoader:
    """Tests for SkillLoader"""
    
    def test_load_skill_basic(self, skill_file):
        """Test loading a basic skill"""
        skill = SkillLoader.load(skill_file)
        
        assert skill.name == "test-skill"
        assert skill.description == "A test skill for unit testing"
        assert skill.triggers == ["(?i)test", "(?i)testing"]
        assert "# Test Skill" in skill.content
        assert skill.path == skill_file
        assert skill.root_dir == skill_file.parent
    
    def test_load_skill_with_resources(self, skill_file):
        """Test that resources are discovered"""
        skill = SkillLoader.load(skill_file)
        
        assert "template.txt" in skill.resources
    
    def test_load_skill_fallback_name(self, temp_workspace):
        """Test fallback to directory name when no name in frontmatter"""
        skill_dir = temp_workspace / "skills" / "my-fallback-skill"
        skill_dir.mkdir(parents=True)
        
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
description: No name field
---

Content here.
""")
        
        skill = SkillLoader.load(skill_md)
        assert skill.name == "my-fallback-skill"
    
    def test_load_skill_no_frontmatter(self, temp_workspace):
        """Test loading skill without frontmatter"""
        skill_dir = temp_workspace / "skills" / "plain-skill"
        skill_dir.mkdir(parents=True)
        
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Just Content\n\nNo frontmatter here.")
        
        skill = SkillLoader.load(skill_md)
        assert skill.name == "plain-skill"
        assert skill.description == ""
        assert skill.triggers == []
        assert "# Just Content" in skill.content


class TestSkillManager:
    """Tests for SkillManager"""
    
    def test_discover_skills(self, skill_file, temp_workspace):
        """Test skill discovery"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        assert "test-skill" in manager._discovered
        skill = manager.get("test-skill")
        assert skill is not None
        assert skill.name == "test-skill"
    
    def test_list_skills(self, skill_file, temp_workspace):
        """Test listing skills"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        skill_list = manager.list()
        assert len(skill_list) == 1
        assert skill_list[0].name == "test-skill"
        assert skill_list[0].is_active is False
    
    def test_load_unload_skill(self, skill_file, temp_workspace):
        """Test loading and unloading skills"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        # Load
        skill = manager.load("test-skill")
        assert skill is not None
        assert manager.is_active("test-skill")
        assert len(manager.active()) == 1
        
        # Unload
        result = manager.unload("test-skill")
        assert result is True
        assert not manager.is_active("test-skill")
        assert len(manager.active()) == 0
    
    def test_load_nonexistent(self, temp_workspace):
        """Test loading a skill that doesn't exist"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        skill = manager.load("nonexistent")
        assert skill is None
    
    def test_auto_load_triggers(self, skill_file, temp_workspace):
        """Test auto-loading based on triggers"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        # Should trigger on "test"
        loaded = manager.auto_load_for_message("Can you test this code?")
        assert "test-skill" in loaded
        assert manager.is_active("test-skill")
        
        # Should not re-trigger
        loaded_again = manager.auto_load_for_message("test again")
        assert loaded_again == []
    
    def test_auto_load_disabled(self, skill_file, temp_workspace):
        """Test that auto-load can be disabled"""
        manager = SkillManager(
            workspace_dir=temp_workspace,
            auto_triggers_enabled=False,
            include_global=False
        )
        
        loaded = manager.auto_load_for_message("test this")
        assert loaded == []
        assert not manager.is_active("test-skill")
    
    def test_render_active_prompt(self, skill_file, temp_workspace):
        """Test rendering active skills for prompt"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        manager.load("test-skill")
        
        prompt = manager.render_active_for_prompt()
        
        assert "# Loaded Skills" in prompt
        assert 'loaded_skill name="test-skill"' in prompt
        assert "# Test Skill" in prompt
    
    def test_render_empty_prompt(self, temp_workspace):
        """Test rendering when no skills active"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        prompt = manager.render_active_for_prompt()
        assert prompt == ""
    
    def test_get_resource_path(self, skill_file, temp_workspace):
        """Test getting resource path"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        path = manager.get_resource_path("test-skill", "template.txt")
        assert path is not None
        assert path.exists()
        assert path.read_text() == "This is a resource template."
    
    def test_get_resource_path_traversal(self, skill_file, temp_workspace):
        """Test that path traversal is blocked"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        # Try to escape skill directory
        path = manager.get_resource_path("test-skill", "../../../etc/passwd")
        assert path is None
    
    def test_clear(self, skill_file, temp_workspace):
        """Test clearing all active skills"""
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        manager.load("test-skill")
        
        assert len(manager.active()) == 1
        
        manager.clear()
        assert len(manager.active()) == 0
    
    def test_keyword_matching(self, temp_workspace):
        """Test keyword-based skill matching"""
        # Create a skill with specific keywords
        skill_dir = temp_workspace / ".codesm" / "skills" / "react-skill"
        skill_dir.mkdir(parents=True)
        
        (skill_dir / "SKILL.md").write_text("""---
name: react-performance
description: React performance optimization tips
---

# React Performance

Optimize React components.
""")
        
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        # Should match on 'react' and 'performance'
        matches = manager.match("help me with react performance")
        assert len(matches) >= 1
        assert matches[0].skill.name == "react-performance"
        assert matches[0].match_type == "keyword"
    
    def test_file_context_matching(self, temp_workspace):
        """Test file-context skill matching"""
        skill_dir = temp_workspace / ".codesm" / "skills" / "ts-skill"
        skill_dir.mkdir(parents=True)
        
        (skill_dir / "SKILL.md").write_text("""---
name: typescript-guide
description: TypeScript best practices
---

# TypeScript Guide

Write better TypeScript.
""")
        
        manager = SkillManager(workspace_dir=temp_workspace, include_global=False)
        
        # Should match on .ts file context
        matches = manager.match("help", context_files=["src/app.ts"])
        assert len(matches) >= 1
        assert matches[0].skill.name == "typescript-guide"


class TestSimpleYamlParser:
    """Tests for the simple YAML parser in SkillLoader"""
    
    def test_parse_inline_list(self):
        """Test parsing inline list format"""
        text = 'triggers: [one, two, three]'
        result = SkillLoader._parse_simple_yaml(text)
        assert result["triggers"] == ["one", "two", "three"]
    
    def test_parse_multiline_list(self):
        """Test parsing multiline list format"""
        text = """triggers:
  - one
  - two
  - three"""
        result = SkillLoader._parse_simple_yaml(text)
        assert result["triggers"] == ["one", "two", "three"]
    
    def test_parse_quoted_values(self):
        """Test parsing quoted values"""
        text = '''name: "my-skill"
description: 'A skill with quotes'
triggers:
  - "(?i)test"'''
        result = SkillLoader._parse_simple_yaml(text)
        assert result["name"] == "my-skill"
        assert result["description"] == "A skill with quotes"
        assert result["triggers"] == ["(?i)test"]
