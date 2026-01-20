"""Look At tool - Analyzes images, PDFs, and media files using Gemini Flash via OpenRouter"""

import os
import base64
import logging
from pathlib import Path
from typing import Optional

import httpx

from .base import Tool

logger = logging.getLogger(__name__)

# Model configuration - uses Gemini Flash for speed and vision capabilities
VISION_MODEL = "google/gemini-2.0-flash-001"  # Gemini 2.0 Flash with vision
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Supported file types
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
PDF_EXTENSIONS = {".pdf"}
DOCUMENT_EXTENSIONS = {".txt", ".md", ".rst", ".json", ".yaml", ".yml", ".toml", ".xml", ".csv"}

# MIME type mapping
MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}


class LookAtTool(Tool):
    """Analyze images, PDFs, and media files using vision AI."""
    
    name = "look_at"
    description = "Analyze images, PDFs, screenshots, diagrams, or any visual content. Extract text, describe contents, or answer questions about the file."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file (image, PDF, screenshot, diagram)",
                },
                "objective": {
                    "type": "string",
                    "description": "What to analyze or extract (e.g., 'describe the UI layout', 'extract text', 'identify the error message')",
                },
                "context": {
                    "type": "string",
                    "description": "Optional context about why you're analyzing this file",
                },
            },
            "required": ["path", "objective"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        path = Path(args["path"])
        objective = args["objective"]
        analysis_context = args.get("context", "")
        
        if not path.exists():
            return f"Error: File not found: {path}"
        
        if not path.is_file():
            return f"Error: Not a file: {path}"
        
        ext = path.suffix.lower()
        
        # Check if we have an API key
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return "Error: OPENROUTER_API_KEY not set. Required for image/PDF analysis."
        
        # Handle different file types
        if ext in IMAGE_EXTENSIONS:
            return await self._analyze_image(path, objective, analysis_context, api_key)
        elif ext in PDF_EXTENSIONS:
            return await self._analyze_pdf(path, objective, analysis_context, api_key)
        elif ext in DOCUMENT_EXTENSIONS:
            # For text documents, just read and summarize
            return await self._analyze_document(path, objective, analysis_context, api_key)
        else:
            return f"Error: Unsupported file type: {ext}. Supported: {', '.join(IMAGE_EXTENSIONS | PDF_EXTENSIONS | DOCUMENT_EXTENSIONS)}"
    
    async def _analyze_image(self, path: Path, objective: str, context: str, api_key: str) -> str:
        """Analyze an image file using Gemini Flash vision."""
        try:
            # Read and encode the image
            image_data = path.read_bytes()
            base64_image = base64.b64encode(image_data).decode("utf-8")
            mime_type = MIME_TYPES.get(path.suffix.lower(), "image/png")
            
            # Build the prompt
            prompt = self._build_analysis_prompt(objective, context, path.name)
            
            # Call OpenRouter with vision
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/codesm",
                        "X-Title": "codesm",
                    },
                    json={
                        "model": VISION_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": prompt,
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime_type};base64,{base64_image}",
                                        },
                                    },
                                ],
                            },
                        ],
                        "temperature": 0.2,
                        "max_tokens": 4096,
                    },
                )
                
                if response.status_code != 200:
                    logger.error(f"Vision API error: {response.status_code} - {response.text}")
                    return f"Error: Vision API returned {response.status_code}"
                
                data = response.json()
                result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                return f"**Analysis of {path.name}:**\n\n{result}"
                
        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return f"Error analyzing image: {e}"
    
    async def _analyze_pdf(self, path: Path, objective: str, context: str, api_key: str) -> str:
        """Analyze a PDF file.
        
        For PDFs, we first try to extract text, then use vision for complex layouts.
        """
        try:
            # Try to extract text from PDF first
            text_content = await self._extract_pdf_text(path)
            
            if text_content and len(text_content.strip()) > 100:
                # If we got meaningful text, analyze it
                return await self._analyze_text_content(
                    text_content, objective, context, path.name, api_key
                )
            else:
                # For image-heavy PDFs, convert first page to image and analyze
                # This requires pdf2image or similar - fallback to text extraction error
                return f"Error: PDF appears to be image-based or empty. Text extraction returned: {text_content[:200] if text_content else 'nothing'}"
                
        except Exception as e:
            logger.error(f"PDF analysis failed: {e}")
            return f"Error analyzing PDF: {e}"
    
    async def _extract_pdf_text(self, path: Path) -> str:
        """Extract text from a PDF file."""
        try:
            # Try pypdf first (lightweight)
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                text_parts = []
                for page in reader.pages[:10]:  # Limit to first 10 pages
                    text_parts.append(page.extract_text() or "")
                return "\n\n".join(text_parts)
            except ImportError:
                pass
            
            # Try pdfplumber (better extraction)
            try:
                import pdfplumber
                text_parts = []
                with pdfplumber.open(str(path)) as pdf:
                    for page in pdf.pages[:10]:
                        text_parts.append(page.extract_text() or "")
                return "\n\n".join(text_parts)
            except ImportError:
                pass
            
            # Fallback: use system pdftotext if available
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                "pdftotext", "-layout", str(path), "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode("utf-8", errors="replace")
            
            return ""
            
        except Exception as e:
            logger.debug(f"PDF text extraction failed: {e}")
            return ""
    
    async def _analyze_document(self, path: Path, objective: str, context: str, api_key: str) -> str:
        """Analyze a text document."""
        try:
            content = path.read_text(errors="replace")
            
            # Truncate if too long
            if len(content) > 50000:
                content = content[:50000] + "\n\n[... truncated ...]"
            
            return await self._analyze_text_content(
                content, objective, context, path.name, api_key
            )
            
        except Exception as e:
            return f"Error reading document: {e}"
    
    async def _analyze_text_content(
        self, content: str, objective: str, context: str, filename: str, api_key: str
    ) -> str:
        """Analyze text content using LLM."""
        prompt = f"""Analyze the following document content.

**File:** {filename}
**Objective:** {objective}
{f"**Context:** {context}" if context else ""}

**Document Content:**
```
{content[:30000]}
```

Provide a focused analysis based on the objective. Be concise and actionable."""

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/codesm",
                        "X-Title": "codesm",
                    },
                    json={
                        "model": VISION_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "max_tokens": 4096,
                    },
                )
                
                if response.status_code != 200:
                    return f"Error: API returned {response.status_code}"
                
                data = response.json()
                result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                return f"**Analysis of {filename}:**\n\n{result}"
                
        except Exception as e:
            return f"Error analyzing content: {e}"
    
    def _build_analysis_prompt(self, objective: str, context: str, filename: str) -> str:
        """Build the analysis prompt for vision model."""
        prompt = f"""Analyze this image carefully.

**File:** {filename}
**Objective:** {objective}
{f"**Context:** {context}" if context else ""}

Provide a detailed analysis based on the objective. Include:
- Relevant details visible in the image
- Any text content you can read
- Layout and structure information if relevant
- Specific answers to the objective

Be concise but thorough."""
        return prompt
