"""Read file tool - handles text files, delegates images/PDFs to look_at"""

from pathlib import Path
from .base import Tool

# File extensions that need vision/special handling
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
PDF_EXTENSIONS = {".pdf"}
BINARY_EXTENSIONS = {".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".zip", ".tar", ".gz", ".7z", ".rar"}


class ReadTool(Tool):
    name = "read"
    description = "Read file contents. Returns line-numbered output for text files. For images/PDFs, use the look_at tool instead."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line (1-indexed)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line (1-indexed)",
                },
            },
            "required": ["path"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        path = Path(args["path"])
        start = args.get("start_line", 1)
        end = args.get("end_line")
        
        if not path.exists():
            return f"Error: File not found: {path}"
        
        if not path.is_file():
            return f"Error: Not a file: {path}"
        
        ext = path.suffix.lower()
        
        # Check for image files - suggest using look_at
        if ext in IMAGE_EXTENSIONS:
            return f"This is an image file ({ext}). Use the `look_at` tool to analyze images:\n\nlook_at(path=\"{path}\", objective=\"describe the contents\")"
        
        # Check for PDF files - suggest using look_at
        if ext in PDF_EXTENSIONS:
            return f"This is a PDF file. Use the `look_at` tool to analyze PDFs:\n\nlook_at(path=\"{path}\", objective=\"extract and summarize the content\")"
        
        # Check for binary files
        if ext in BINARY_EXTENSIONS:
            return f"Error: Cannot read binary file ({ext}). This file format is not readable as text."
        
        try:
            # Try to read as text
            content = path.read_text()
            lines = content.split("\n")
            
            end = end or len(lines)
            selected = lines[start - 1:end]
            
            numbered = [
                f"{start + i}: {line}" 
                for i, line in enumerate(selected)
            ]
            return "\n".join(numbered)
        except UnicodeDecodeError:
            # Binary file that wasn't in our list
            return f"Error: Cannot read file as text - appears to be binary. File: {path}"
        except Exception as e:
            return f"Error reading file: {e}"
