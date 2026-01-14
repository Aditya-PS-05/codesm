"""Todo tracking tool for agent task management"""

from .base import Tool
from codesm.session.todo import TodoList


class TodoTool(Tool):
    name = "todo"
    description = "Manage your task list. Use this to track what needs to be done, what's in progress, and what's completed."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "start", "done", "cancel", "delete", "update", "clear_done"],
                    "description": "Action to perform: add (create todo), list (show todos), start (mark in progress), done (mark complete), cancel (mark cancelled), delete (remove), update (change content), clear_done (remove completed)",
                },
                "content": {
                    "type": "string",
                    "description": "Todo content (for add/update)",
                },
                "id": {
                    "type": "string",
                    "description": "Todo ID (for start/done/cancel/delete/update)",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority level (higher = more important, default 0)",
                },
                "include_done": {
                    "type": "boolean",
                    "description": "Include completed todos in list (default false)",
                },
            },
            "required": ["action"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        action = args["action"]
        session = context.get("session")
        
        if not session:
            return "Error: No session context available"
        
        todo_list = TodoList(session.id)
        
        if action == "add":
            content = args.get("content")
            if not content:
                return "Error: 'content' is required for add action"
            priority = args.get("priority", 0)
            todo = todo_list.add(content, priority=priority)
            return f"Added: {todo.id} - {todo.content}"
        
        elif action == "list":
            include_done = args.get("include_done", False)
            formatted = todo_list.format_list(include_done=include_done)
            summary = todo_list.summary()
            return f"{formatted}\n\n(Pending: {summary['pending']}, In Progress: {summary['in_progress']}, Done: {summary['done']})"
        
        elif action == "start":
            todo_id = args.get("id")
            if not todo_id:
                return "Error: 'id' is required for start action"
            todo = todo_list.update_status(todo_id, "in_progress")
            if todo:
                return f"Started: {todo.id} - {todo.content}"
            return f"Error: Todo not found: {todo_id}"
        
        elif action == "done":
            todo_id = args.get("id")
            if not todo_id:
                return "Error: 'id' is required for done action"
            todo = todo_list.update_status(todo_id, "done")
            if todo:
                return f"Completed: {todo.id} - {todo.content}"
            return f"Error: Todo not found: {todo_id}"
        
        elif action == "cancel":
            todo_id = args.get("id")
            if not todo_id:
                return "Error: 'id' is required for cancel action"
            todo = todo_list.update_status(todo_id, "cancelled")
            if todo:
                return f"Cancelled: {todo.id} - {todo.content}"
            return f"Error: Todo not found: {todo_id}"
        
        elif action == "delete":
            todo_id = args.get("id")
            if not todo_id:
                return "Error: 'id' is required for delete action"
            if todo_list.delete(todo_id):
                return f"Deleted: {todo_id}"
            return f"Error: Todo not found: {todo_id}"
        
        elif action == "update":
            todo_id = args.get("id")
            content = args.get("content")
            if not todo_id:
                return "Error: 'id' is required for update action"
            if not content:
                return "Error: 'content' is required for update action"
            todo = todo_list.update_content(todo_id, content)
            if todo:
                return f"Updated: {todo.id} - {todo.content}"
            return f"Error: Todo not found: {todo_id}"
        
        elif action == "clear_done":
            count = todo_list.clear_done()
            return f"Cleared {count} completed todos"
        
        else:
            return f"Error: Unknown action '{action}'"
