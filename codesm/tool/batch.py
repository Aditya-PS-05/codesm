"""Batch tool - execute multiple tools in parallel"""

import asyncio
import json
from .base import Tool


DISALLOWED_TOOLS = {"batch"}  # Prevent recursive batch calls


class BatchTool(Tool):
    name = "batch"
    description = "Execute multiple tool calls in parallel for faster execution."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tool_calls": {
                    "type": "array",
                    "description": "Array of tool calls to execute in parallel",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "The name of the tool to execute",
                            },
                            "parameters": {
                                "type": "object",
                                "description": "Parameters for the tool",
                            },
                        },
                        "required": ["tool", "parameters"],
                    },
                    "minItems": 1,
                    "maxItems": 10,
                },
            },
            "required": ["tool_calls"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from .registry import ToolRegistry
        
        tool_calls = args.get("tool_calls", [])
        if not tool_calls:
            return "Error: No tool calls provided"
        
        # Limit to 10 calls
        tool_calls = tool_calls[:10]
        discarded = len(args.get("tool_calls", [])) - len(tool_calls)
        
        registry = context.get("tools")
        if registry is None:
            return "Error: No tool registry in execution context"
        results = []
        
        async def execute_one(call: dict) -> dict:
            tool_name = call.get("tool", "")
            params = call.get("parameters", {})
            
            if tool_name in DISALLOWED_TOOLS:
                return {
                    "tool": tool_name,
                    "success": False,
                    "error": f"Tool '{tool_name}' cannot be used in batch",
                }
            
            tool = registry.get(tool_name)
            if not tool:
                return {
                    "tool": tool_name,
                    "success": False,
                    "error": f"Unknown tool: {tool_name}",
                }
            
            try:
                result = await registry.execute(tool_name, params, context)
                return {
                    "tool": tool_name,
                    "success": not result.startswith(("Error", "Permission denied")),
                    "error": result,
                    "result": result[:500] if len(result) > 500 else result,  # Truncate long results
                }
            except Exception as e:
                return {
                    "tool": tool_name,
                    "success": False,
                    "error": str(e),
                }
        
        # Execute all in parallel
        # Preserve dependencies and the active registry's capability restrictions.
        results = [await execute_one(call) for call in tool_calls]
        
        # Format output
        successful = sum(1 for r in results if r["success"])
        failed = len(results) - successful
        
        output_lines = []
        for r in results:
            if r["success"]:
                output_lines.append(f"✓ {r['tool']}: {r['result'][:100]}...")
            else:
                output_lines.append(f"✗ {r['tool']}: {r['error']}")
        
        summary = f"Batch: {successful}/{len(results)} successful"
        if discarded:
            summary += f" ({discarded} discarded - max 10)"
        
        return f"{summary}\n\n" + "\n".join(output_lines)
