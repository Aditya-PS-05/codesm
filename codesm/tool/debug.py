"""A persistent reproduction and verification record for debugging tasks."""

from pathlib import Path

from .base import Tool


DEBUG_PROMPT = """Debugging workflow:
1. Use debug(action="reproduce", command=...) to capture the original failing check before changing code.
2. Record a concrete hypothesis and its supporting evidence with debug(action="hypothesis", hypothesis=...).
3. Inspect callers and fix the cause. Preserve existing tests; add a focused regression test when needed.
4. Use debug(action="verify") to rerun the exact original command. A different passing command does not verify this bug.
5. If an attempt fails, use the evidence to revise the hypothesis or use debug(action="consult", reason=...) for one Oracle consultation.
Respect the attempt limit. If the issue cannot be reproduced or verification is unavailable, use
debug(action="unverified", reason=...) and report the limitation. Never claim a verified fix without a passing rerun.
"""


class DebugTool(Tool):
    name = "debug"
    description = "Record a failing reproduction, hypotheses, and verification. Verification reruns the original command and has a bounded attempt limit."

    def get_parameters_schema(self):
        return {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["reproduce", "hypothesis", "verify", "status", "unverified", "consult"]},
            "command": {"type": "string", "description": "Original reproduction command; only valid for reproduce."},
            "hypothesis": {"type": "string", "description": "Possible cause and supporting evidence."},
            "reason": {"type": "string", "description": "Why verification cannot be completed."},
        }, "required": ["action"]}

    async def execute(self, args, context):
        from codesm.agent.execution import emit
        session = context.get("session")
        if session is None:
            return "Error: Debugging requires a session"
        state = session.debug_state
        if not state:
            state.update(status="unverified", attempts=0, max_attempts=3, hypotheses=[])
        action = args.get("action")
        if action == "status":
            return str(state)
        if action == "hypothesis":
            hypothesis = args.get("hypothesis", "").strip()
            if not hypothesis:
                return "Error: A hypothesis and supporting evidence are required"
            state["hypotheses"].append(hypothesis)
        elif action == "consult":
            reason = args.get("reason", "").strip()
            if not reason:
                return "Error: Explain the observed stall or the question requiring Oracle"
            if state.get("consulted"):
                return "Error: Oracle consultation limit reached for this debugging task"
            state["consulted"] = True
            session.save()
            advice = await context["tools"].execute("oracle", {
                "task": reason + "\nDebugging evidence:\n" + str(state),
            }, context)
            state["oracle_advice"] = advice
            session.save()
            return advice
        elif action == "unverified":
            reason = args.get("reason", "").strip()
            if not reason:
                return "Error: Explain why the result cannot be verified"
            state.update(status="unverified", reason=reason)
        elif action in ("reproduce", "verify"):
            if action == "reproduce":
                if state.get("reproduction"):
                    return "Error: Reproduction is already recorded. Verify the original check or start a new /debug task."
                command = args.get("command", "").strip()
                if not command:
                    return "Error: A reproduction command is required"
                cwd = str(Path(context["cwd"]).resolve())
            else:
                if not state.get("reproduction") or state["reproduction"]["exit_code"] in (0, -1, None):
                    return "Error: No failing reproduction has been captured; the fix is unverified"
                if state["attempts"] >= state["max_attempts"]:
                    return "Error: Verification attempt limit reached; report the unresolved failure"
                command, cwd = state["command"], state["cwd"]
                if args.get("command") and args["command"] != command:
                    return "Error: Verification must use the original reproduction command"
                state["attempts"] += 1
            session.save()
            check_context = {**context, "debug_check": True}
            check_context.pop("command_result", None)
            result = await context["tools"].execute("bash", {"command": command, "cwd": cwd}, check_context)
            evidence = check_context.get("command_result")
            if evidence is None:
                state.update(status="unverified", reason=result)
            elif action == "reproduce":
                state.update(command=command, cwd=cwd, reproduction=evidence,
                             status="reproduced" if evidence["exit_code"] not in (0, -1, None) else "unreproduced")
            else:
                state.update(verification=evidence,
                             status="verified" if evidence["exit_code"] == 0 else "unverified")
            session.save()
            emit(context, "debug_check", action=action, status=state["status"], evidence=evidence)
            return f"Debugging: {state['status']} (verification attempts {state['attempts']}/{state['max_attempts']})\n{result}"
        else:
            return "Error: Unknown debugging action"
        session.save()
        emit(context, "debug_state", action=action, status=state["status"])
        return f"Debugging: {state['status']}"
