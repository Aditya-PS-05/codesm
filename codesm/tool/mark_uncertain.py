"""Mark uncertain tool.

Lets the model self report that it is not fully confident in a recent
change, decision, or interpretation. Every call is recorded in the
per session event log (H4 failure mode instrumentation) so eval runs
can correlate self reported uncertainty against actual assertion
failures. This is the research hook for asking "is a model's
uncertainty signal calibrated against its real error rate."
"""

from .base import Tool


VALID_SEVERITIES = {"low", "medium", "high"}


class MarkUncertainTool(Tool):
    name = "mark_uncertain"
    description = (
        "Record that you are uncertain about a recent change, decision, "
        "or interpretation. Use this when you would like a human to "
        "double check something before trusting it, or when you had to "
        "guess in the absence of clear evidence. Every call is logged "
        "to the session event stream and surfaced in eval reports."
    )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Short natural language description of what you "
                        "are uncertain about."
                    ),
                },
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": (
                        "How much the downstream task depends on being "
                        "right about this. low = cosmetic guess, "
                        "medium = plausible but unverified, "
                        "high = could break the change if wrong."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Optional file path the uncertainty applies to, "
                        "when it is scoped to a specific file."
                    ),
                },
                "suggested_verification": {
                    "type": "string",
                    "description": (
                        "Optional one line hint for how a human could "
                        "verify the guess (e.g. 'run pytest tests/test_x.py')."
                    ),
                },
            },
            "required": ["description", "severity"],
        }

    async def execute(self, args: dict, context: dict) -> str:
        description = (args.get("description") or "").strip()
        severity = (args.get("severity") or "").strip().lower()
        file_path = (args.get("file_path") or "").strip() or None
        suggested_verification = (args.get("suggested_verification") or "").strip() or None

        if not description:
            return "Error: description is required"
        if severity not in VALID_SEVERITIES:
            return f"Error: severity must be one of {sorted(VALID_SEVERITIES)}"

        event_logger = context.get("event_logger")
        eval_events = context.get("eval_events")

        payload = {
            "severity": severity,
            "description": description[:500],
        }
        if file_path:
            payload["file_path"] = file_path
        if suggested_verification:
            payload["suggested_verification"] = suggested_verification[:300]

        if event_logger is not None:
            event_logger.emit("mark_uncertain", **payload)
        if isinstance(eval_events, list):
            record = {"type": "mark_uncertain"}
            record.update(payload)
            eval_events.append(record)

        parts = [f"Uncertainty recorded (severity={severity}): {description}"]
        if file_path:
            parts.append(f"file: {file_path}")
        if suggested_verification:
            parts.append(f"verify: {suggested_verification}")
        return "\n".join(parts)
