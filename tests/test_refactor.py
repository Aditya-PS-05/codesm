"""Tests for refactoring suggestions feature"""

import pytest
from pathlib import Path

from codesm.review.refactor import (
    RefactorAnalyzer,
    RefactorAnalysis,
    RefactorSuggestion,
    RefactorCategory,
)
from codesm.tool.refactor import RefactorTool, RefactorApplyTool


class TestRefactorSuggestion:
    """Test RefactorSuggestion dataclass"""
    
    def test_create_suggestion(self):
        suggestion = RefactorSuggestion(
            category=RefactorCategory.SIMPLIFICATION,
            priority="high",
            file="test.py",
            start_line=10,
            end_line=20,
            title="Extract method",
            description="This function is too long",
            effort="low",
            impact="high",
        )
        
        assert suggestion.category == RefactorCategory.SIMPLIFICATION
        assert suggestion.priority == "high"
        assert suggestion.effort == "low"


class TestRefactorAnalysis:
    """Test RefactorAnalysis dataclass"""
    
    def test_empty_analysis(self):
        analysis = RefactorAnalysis()
        assert analysis.suggestions == []
        assert analysis.high_priority_count == 0
        assert analysis.quick_wins == []
    
    def test_high_priority_count(self):
        analysis = RefactorAnalysis(
            suggestions=[
                RefactorSuggestion(
                    category=RefactorCategory.STRUCTURE,
                    priority="high",
                    file="a.py",
                    start_line=1,
                    end_line=10,
                    title="Test 1",
                    description="Desc 1",
                ),
                RefactorSuggestion(
                    category=RefactorCategory.PERFORMANCE,
                    priority="low",
                    file="b.py",
                    start_line=1,
                    end_line=10,
                    title="Test 2",
                    description="Desc 2",
                ),
                RefactorSuggestion(
                    category=RefactorCategory.SAFETY,
                    priority="high",
                    file="c.py",
                    start_line=1,
                    end_line=10,
                    title="Test 3",
                    description="Desc 3",
                ),
            ]
        )
        assert analysis.high_priority_count == 2
    
    def test_quick_wins(self):
        analysis = RefactorAnalysis(
            suggestions=[
                RefactorSuggestion(
                    category=RefactorCategory.SIMPLIFICATION,
                    priority="medium",
                    file="a.py",
                    start_line=1,
                    end_line=10,
                    title="Quick win",
                    description="Easy fix",
                    effort="low",
                    impact="high",
                ),
                RefactorSuggestion(
                    category=RefactorCategory.STRUCTURE,
                    priority="high",
                    file="b.py",
                    start_line=1,
                    end_line=10,
                    title="Hard work",
                    description="Complex refactor",
                    effort="high",
                    impact="high",
                ),
            ]
        )
        quick_wins = analysis.quick_wins
        assert len(quick_wins) == 1
        assert quick_wins[0].title == "Quick win"
    
    def test_format_for_display_empty(self):
        analysis = RefactorAnalysis(files_analyzed=["test.py"])
        output = analysis.format_for_display()
        assert "No refactoring suggestions" in output
    
    def test_format_for_display_with_suggestions(self):
        analysis = RefactorAnalysis(
            suggestions=[
                RefactorSuggestion(
                    category=RefactorCategory.PERFORMANCE,
                    priority="high",
                    file="slow.py",
                    start_line=50,
                    end_line=60,
                    title="Use list comprehension",
                    description="Replace loop with list comprehension",
                    before_snippet="result = []\\nfor x in items:\\n    result.append(x)",
                    after_snippet="result = [x for x in items]",
                    effort="low",
                    impact="medium",
                ),
            ],
            files_analyzed=["slow.py"],
            summary="Found one performance improvement",
        )
        output = analysis.format_for_display()
        assert "Refactoring Suggestions" in output
        assert "Performance" in output
        assert "Use list comprehension" in output


class TestRefactorTool:
    """Test RefactorTool"""
    
    def test_parameters_schema(self):
        tool = RefactorTool()
        schema = tool.get_parameters_schema()
        
        assert schema["type"] == "object"
        assert "path" in schema["properties"]
        assert "paths" in schema["properties"]
        assert "directory" in schema["properties"]
        assert "code" in schema["properties"]
        assert "focus" in schema["properties"]
    
    def test_tool_name_and_description(self):
        tool = RefactorTool()
        assert tool.name == "refactor"
        assert "refactoring" in tool.description.lower()


class TestRefactorApplyTool:
    """Test RefactorApplyTool"""
    
    def test_parameters_schema(self):
        tool = RefactorApplyTool()
        schema = tool.get_parameters_schema()
        
        assert schema["type"] == "object"
        assert "path" in schema["properties"]
        assert "suggestion" in schema["properties"]
        assert "dry_run" in schema["properties"]
        assert schema["required"] == ["path", "suggestion"]


class TestRefactorAnalyzer:
    """Test RefactorAnalyzer parsing"""
    
    def test_parse_empty_response(self):
        analyzer = RefactorAnalyzer(api_key="test")
        result = analyzer._parse_response("", ["test.py"])
        
        assert result.suggestions == []
        assert result.files_analyzed == ["test.py"]
    
    def test_parse_no_issues(self):
        analyzer = RefactorAnalyzer(api_key="test")
        response = """SUGGESTIONS:
No significant refactoring needed.

METRICS:
complexity_score: 3
maintainability_score: 8

SUMMARY: The code is well-structured and follows best practices."""
        
        result = analyzer._parse_response(response, ["good.py"])
        assert result.summary == "The code is well-structured and follows best practices."
        assert result.metrics.get("complexity_score") == 3
    
    def test_parse_with_suggestions(self):
        analyzer = RefactorAnalyzer(api_key="test")
        response = """SUGGESTIONS:
---
category: simplification
priority: high
file: utils.py
lines: 10-25
title: Extract duplicate code
description: The same logic is repeated in three places
before: repeated_code()
after: extracted_helper()
effort: low
impact: high
---
category: performance
priority: medium
file: utils.py
lines: 50-55
title: Use set for lookup
description: Replace list with set for O(1) lookup
before: if x in list_items
after: if x in set_items
effort: low
impact: medium
---

METRICS:
complexity_score: 6
maintainability_score: 5

SUMMARY: Found 2 refactoring opportunities."""
        
        result = analyzer._parse_response(response, ["utils.py"])
        
        assert len(result.suggestions) == 2
        assert result.suggestions[0].category == RefactorCategory.SIMPLIFICATION
        assert result.suggestions[0].priority == "high"
        assert result.suggestions[0].title == "Extract duplicate code"
        assert result.suggestions[0].start_line == 10
        assert result.suggestions[0].end_line == 25
        
        assert result.suggestions[1].category == RefactorCategory.PERFORMANCE
        assert result.suggestions[1].priority == "medium"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
