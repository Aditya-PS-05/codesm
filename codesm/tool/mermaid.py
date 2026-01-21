"""Mermaid tool - generate diagrams from code"""

import base64
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MermaidTool(Tool):
    """Generate Mermaid diagrams for visualization"""
    
    name = "mermaid"
    description = "Generate Mermaid diagrams (flowcharts, sequences, class diagrams, etc.) for visualizing architecture and flows."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def set_parent(self, tools: "ToolRegistry"):
        self._parent_tools = tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Mermaid diagram code (e.g., 'flowchart LR\\n  A --> B')",
                },
                "diagram_type": {
                    "type": "string",
                    "enum": ["flowchart", "sequence", "class", "state", "er", "gantt", "pie", "mindmap", "timeline", "auto"],
                    "description": "Type of diagram. Use 'auto' to detect from code.",
                    "default": "auto",
                },
                "title": {
                    "type": "string",
                    "description": "Optional title for the diagram",
                },
                "save_path": {
                    "type": "string",
                    "description": "Optional path to save the diagram as SVG/PNG",
                },
                "citations": {
                    "type": "object",
                    "description": "Map of node IDs to file paths for clickable links",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["code"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        code = args.get("code", "")
        diagram_type = args.get("diagram_type", "auto")
        title = args.get("title", "")
        save_path = args.get("save_path")
        citations = args.get("citations", {})
        
        if not code:
            return "Error: code is required - provide Mermaid diagram code"
        
        # Clean up the code
        code = self._clean_code(code)
        
        # Validate syntax
        validation = self._validate_mermaid(code)
        if validation:
            return f"Error: Invalid Mermaid syntax - {validation}"
        
        # Detect diagram type if auto
        if diagram_type == "auto":
            diagram_type = self._detect_type(code)
        
        # Build the full diagram with title
        full_code = code
        if title:
            full_code = f"---\ntitle: {title}\n---\n{code}"
        
        # Try to render if save_path specified
        rendered_path = None
        if save_path:
            rendered_path = await self._render_diagram(full_code, save_path, context)
        
        # Format output for display
        output = self._format_output(code, diagram_type, title, citations, rendered_path)
        
        return output
    
    def _clean_code(self, code: str) -> str:
        """Clean and normalize Mermaid code"""
        # Remove markdown code fences if present
        code = code.strip()
        if code.startswith("```mermaid"):
            code = code[10:]
        elif code.startswith("```"):
            code = code[3:]
        if code.endswith("```"):
            code = code[:-3]
        return code.strip()
    
    def _validate_mermaid(self, code: str) -> str | None:
        """Basic validation of Mermaid syntax"""
        if not code:
            return "Empty diagram code"
        
        lines = code.strip().split("\n")
        first_line = lines[0].strip().lower()
        
        valid_starts = [
            "flowchart", "graph", "sequencediagram", "sequence",
            "classDiagram", "class", "statediagram", "state",
            "erdiagram", "er", "gantt", "pie", "mindmap",
            "timeline", "gitgraph", "journey", "quadrantchart",
            "---",  # YAML frontmatter
        ]
        
        # Check if starts with valid keyword
        if not any(first_line.startswith(start.lower()) for start in valid_starts):
            return f"Unknown diagram type. Code starts with: {first_line[:30]}"
        
        return None
    
    def _detect_type(self, code: str) -> str:
        """Detect diagram type from code"""
        first_line = code.strip().split("\n")[0].lower()
        
        if first_line.startswith("flowchart") or first_line.startswith("graph"):
            return "flowchart"
        elif "sequencediagram" in first_line or first_line.startswith("sequence"):
            return "sequence"
        elif "classdiagram" in first_line or first_line.startswith("class"):
            return "class"
        elif "statediagram" in first_line or first_line.startswith("state"):
            return "state"
        elif "erdiagram" in first_line:
            return "er"
        elif first_line.startswith("gantt"):
            return "gantt"
        elif first_line.startswith("pie"):
            return "pie"
        elif first_line.startswith("mindmap"):
            return "mindmap"
        elif first_line.startswith("timeline"):
            return "timeline"
        
        return "flowchart"
    
    async def _render_diagram(self, code: str, save_path: str, context: dict) -> str | None:
        """Render diagram to file using mmdc CLI if available"""
        workspace_dir = context.get("workspace_dir") or context.get("cwd", ".")
        
        # Resolve save path
        output_path = Path(save_path)
        if not output_path.is_absolute():
            output_path = Path(workspace_dir) / output_path
        
        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Determine format from extension
        ext = output_path.suffix.lower()
        if ext not in [".svg", ".png", ".pdf"]:
            output_path = output_path.with_suffix(".svg")
        
        # Try to use mermaid-cli (mmdc)
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".mmd", delete=False) as f:
                f.write(code)
                temp_input = f.name
            
            result = subprocess.run(
                ["mmdc", "-i", temp_input, "-o", str(output_path), "-b", "transparent"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            Path(temp_input).unlink(missing_ok=True)
            
            if result.returncode == 0 and output_path.exists():
                logger.info(f"Rendered diagram to {output_path}")
                return str(output_path)
            else:
                logger.warning(f"mmdc failed: {result.stderr}")
                
        except FileNotFoundError:
            logger.debug("mmdc not found, skipping render")
        except subprocess.TimeoutExpired:
            logger.warning("mmdc timed out")
        except Exception as e:
            logger.warning(f"Failed to render diagram: {e}")
        
        return None
    
    def _generate_mermaid_live_url(self, code: str) -> str:
        """Generate a mermaid.live edit URL from diagram code"""
        state = {"code": code}
        state_json = json.dumps(state, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(state_json.encode()).decode()
        return f"https://mermaid.live/edit#base64:{encoded}"

    def _format_output(
        self,
        code: str,
        diagram_type: str,
        title: str,
        citations: dict,
        rendered_path: str | None,
    ) -> str:
        """Format diagram output for display"""
        parts = []
        
        if title:
            parts.append(f"## {title}")
            parts.append("")
        
        parts.append(f"**Diagram Type:** {diagram_type}")
        parts.append("")
        
        # Generate mermaid.live link
        live_url = self._generate_mermaid_live_url(code)
        parts.append(f"**[View/Edit on Mermaid Live]({live_url})**")
        parts.append("")
        
        # Add the mermaid code block
        parts.append("```mermaid")
        parts.append(code)
        parts.append("```")
        parts.append("")
        
        # Add citations if present
        if citations:
            parts.append("**Code References:**")
            for node_id, file_path in citations.items():
                parts.append(f"- `{node_id}` → [{file_path}]({file_path})")
            parts.append("")
        
        # Add render info
        if rendered_path:
            parts.append(f"**Saved to:** [{rendered_path}](file://{rendered_path})")
        
        return "\n".join(parts)


class DiagramGeneratorTool(Tool):
    """Generate diagrams from code analysis"""
    
    name = "diagram"
    description = "Analyze code and generate architecture, flow, or class diagrams automatically."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def set_parent(self, tools: "ToolRegistry"):
        self._parent_tools = tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["architecture", "flow", "class", "sequence", "dependency"],
                    "description": "Type of diagram to generate",
                },
                "scope": {
                    "type": "string",
                    "description": "What to diagram (file path, directory, or description)",
                },
                "include_details": {
                    "type": "boolean",
                    "description": "Include detailed elements (methods, parameters)",
                    "default": False,
                },
            },
            "required": ["type", "scope"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm.provider.base import get_provider
        
        diagram_type = args.get("type", "architecture")
        scope = args.get("scope", "")
        include_details = args.get("include_details", False)
        
        if not scope:
            return "Error: scope is required - specify what to diagram"
        
        workspace_dir = context.get("workspace_dir") or context.get("cwd", ".")
        
        # Gather context based on scope
        code_context = await self._gather_context(scope, workspace_dir, context)
        
        if not code_context:
            return f"Error: Could not find code for scope: {scope}"
        
        # Use LLM to generate diagram
        try:
            provider = get_provider("diagram")  # Gemini Flash for speed
            
            system_prompt = self._get_system_prompt(diagram_type, include_details)
            user_prompt = f"""Generate a Mermaid {diagram_type} diagram for the following code:

Scope: {scope}

Code Context:
{code_context[:8000]}

Generate ONLY the Mermaid code, no explanation. Include file:// links in comments for key nodes."""
            
            response_text = ""
            async for chunk in provider.stream(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=None,
            ):
                if chunk.type == "text":
                    response_text += chunk.content
            
            # Clean and format the response
            mermaid_code = self._extract_mermaid(response_text)
            
            return f"""## Auto-Generated {diagram_type.title()} Diagram

**Scope:** {scope}

```mermaid
{mermaid_code}
```

_Generated from code analysis. Edit as needed._"""
            
        except Exception as e:
            logger.exception("Failed to generate diagram")
            return f"Error generating diagram: {e}"
    
    async def _gather_context(self, scope: str, workspace_dir: str, context: dict) -> str:
        """Gather code context for diagram generation"""
        parts = []
        
        path = Path(workspace_dir) / scope
        
        if path.is_file():
            try:
                content = path.read_text()
                parts.append(f"# {scope}\n{content}")
            except Exception:
                pass
        
        elif path.is_dir():
            # Get key files from directory
            for ext in ["*.py", "*.ts", "*.js", "*.go", "*.rs"]:
                for f in list(path.glob(ext))[:10]:
                    try:
                        content = f.read_text()
                        parts.append(f"# {f.relative_to(workspace_dir)}\n{content[:2000]}")
                    except Exception:
                        pass
        
        else:
            # Treat as description, use grep/finder to locate
            if self._parent_tools:
                grep = self._parent_tools.get("grep")
                if grep:
                    try:
                        result = await grep.execute(
                            {"pattern": scope, "path": workspace_dir, "max_matches": 20},
                            context,
                        )
                        parts.append(f"# Search results for '{scope}'\n{result}")
                    except Exception:
                        pass
        
        return "\n\n".join(parts)
    
    def _get_system_prompt(self, diagram_type: str, include_details: bool) -> str:
        """Get system prompt for diagram generation"""
        detail_instruction = "Include method names and parameters." if include_details else "Keep it high-level, focus on main components."
        
        prompts = {
            "architecture": f"""You generate Mermaid architecture diagrams from code.
Create flowchart diagrams showing system components and their relationships.
Use subgraphs for modules/packages.
{detail_instruction}
Use descriptive node labels.
Output ONLY valid Mermaid code.""",

            "flow": f"""You generate Mermaid flowchart diagrams showing program flow.
Create flowcharts showing how data/control flows through the code.
Use decision nodes for conditionals.
{detail_instruction}
Output ONLY valid Mermaid code.""",

            "class": f"""You generate Mermaid class diagrams from code.
Show classes, their relationships (inheritance, composition, association).
{detail_instruction}
Use proper UML notation.
Output ONLY valid Mermaid code.""",

            "sequence": f"""You generate Mermaid sequence diagrams from code.
Show how components interact over time.
Include key method calls and responses.
{detail_instruction}
Output ONLY valid Mermaid code.""",

            "dependency": f"""You generate Mermaid dependency diagrams from code.
Show module/package dependencies.
Use flowchart with import relationships.
{detail_instruction}
Output ONLY valid Mermaid code.""",
        }
        
        return prompts.get(diagram_type, prompts["architecture"])
    
    def _extract_mermaid(self, response: str) -> str:
        """Extract Mermaid code from LLM response"""
        response = response.strip()
        
        # Remove markdown code fences
        if "```mermaid" in response:
            start = response.find("```mermaid") + 10
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()
        
        if "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()
        
        return response
