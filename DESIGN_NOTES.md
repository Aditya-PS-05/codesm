# Codesm Design Notes

Four war stories from building Codesm. Each is a design decision I had to
make after hitting a concrete failure mode in a real run, not a theoretical
one. These are the notes I would hand to someone asking "why did you
build it this way?"

---

## 1. Context overflow in ReAct loops

**What the problem was.** A ReAct loop that reads three files, runs a grep,
and reads two more files can burn 30K tokens in four iterations. By
iteration ten, you are at the provider's hard limit and the next call
fails with a 400. There is no graceful degradation: the loop either
finishes this turn or the whole session dies.

**Why it happens.** ReAct is quadratic in conversation length because
every turn re-sends the full history plus new tool outputs. Coding tool
outputs in particular are unbounded: a `read` on a 2000-line file or a
`grep` across a repo can each return 10K tokens. Models do not know
their own context budget and will happily fan out reads even when the
window is already 80 percent full. This is a blind spot, not a
capability gap.

**How I solved it.** `ContextManager` in `codesm/session/context.py`
owns compaction. It runs a token estimator (`tiktoken` with the
`cl100k_base` encoding, words-times-1.3 fallback) on every message and
trips at `max_tokens * compact_trigger_ratio` (default 128K and 0.75).
Compaction is three phases, in order: (1) prune old tool outputs to
`[OUTPUT PRUNED: N chars]` while keeping the `tool_call_id` and role
intact so the provider does not see orphaned tool calls; (2) select
recent messages within a separate 40 percent budget walking backward;
(3) pass the middle section to an optional async LLM summarizer
callback. The summary becomes a single system message with
`_context_summary: True` so the next pass can distinguish it from user
system prompts. System messages are always preserved at the top.

**What I would do differently.** Two things. First, the trigger ratio
should be per provider, not global. Anthropic's 200K window behaves
very differently from OpenAI's 128K in terms of when cost and latency
degrade, and a single 0.75 cannot capture both. Second, I would
instrument a "cheap read" vs "expensive read" distinction in the tool
layer, so the model sees hints like "this file is 18K tokens, consider
`grep` first." The current design reacts to the explosion; the next
version should shape the model's choices before the explosion happens.

---

## 2. Streaming text and tool calls displayed in the wrong order

**What the problem was.** Commit `f024ac2`. In the TUI, if the model
produced "let me check one thing" followed by a `read` tool call
followed by "found it, here is the fix", the user would see the text
and the tool call in a jumbled order. Sometimes the second text block
would appear above the tool call. Sometimes text would vanish entirely
when the tool call was rendered. The chunks themselves were arriving
in order from the provider. The bug was in my code.

**Why it happens.** Streaming text and `tool_use` blocks are two
different content types interleaved in a single provider stream, but
they have different lifecycles in a UI. Text grows token by token into
one container widget; a tool call is a discrete atom that gets its
own widget. My first version tried to "pause" the text widget when a
tool call came in by removing it from the container and then re-mount
it when more text arrived. That mount-unmount-remount dance made the
widget's position in the DOM unstable: after re-mounting, it ended up
at the bottom of the container, below later tool calls.

**How I solved it.** The fix is to treat each contiguous text run as a
distinct, sealed widget. When the stream yields a `tool_call` chunk,
the current streaming widget is `mark_complete()`'d and the reference
is set to `None`. The next `text` chunk creates a brand new
`StreamingTextWidget` with a fresh UUID and mounts it at the current
end of the container. A turn that says "hmm let me look (tool read)
found it (tool edit) done" becomes five widgets, not three, all
mounted in arrival order. Net diff was minus nine lines: simpler
beats clever.

**What I would do differently.** I would have designed the stream
consumer as a state machine with explicit transitions
`text -> tool -> text -> tool` rather than trying to reuse widgets
across transitions. I learned this the slow way.

---

## 3. Permission boundaries for destructive operations

**What the problem was.** The `bash` tool would happily run
`rm -rf ./build` in the user's home directory if the model asked for
it. The `write` tool would overwrite `~/.ssh/config` without asking.
The `edit` tool could silently rewrite a git-tracked file with a test
suite next to it. The model was not malicious; it was just following
instructions. My tool layer had no concept of "this is the kind of
action that should pause and check with a human."

**Why it happens.** Coding agents live in two worlds at once: an
exploration world where broad tool access is fine, and an action
world where one wrong command can destroy hours of work. Most
implementations pick one setting and stick to it. Either every tool
call stops for approval, which is unusable, or nothing stops, which is
dangerous. There is no middle tier without explicit design work.

**How I solved it.** `codesm/permission/permission.py` defines three
response types (`ALLOW_ONCE`, `ALLOW_ALWAYS`, `DENY`) and a
`Permission` manager that holds per-session approval state. The system
is layered: (1) `is_command_blocked()` and `is_path_allowed()` enforce
hard blocks on patterns like `rm -rf /` and `~/.ssh/*` that no
interactive approval can override; (2) `requires_permission()` pattern
matches git, gh, and dangerous commands and flips them to the ask
path; (3) `Permission.ask()` creates a `PermissionRequest`, fires a
callback into the TUI modal, awaits a future, and raises
`PermissionDeniedError` on deny. Every request and response is written
to a JSONL audit log at `~/.local/share/codesm/audit.jsonl` via the
`AuditLog` singleton in `codesm/audit/audit.py`.

**What I would do differently.** The todo note said "permissions should
be per command, not per session" but that is wrong in practice. The
real rule is: grant at the smallest scope that does not make the user
hit Enter twenty times. I ended up with a type-plus-pattern approval
cache (`git:commit`, `bash:cargo build`) that promotes to
session-wide after a deliberate `ALLOW_ALWAYS`. If I were designing
this again, I would add a time decay: approvals expire after N minutes
of inactivity. An approval you granted forty minutes ago for a
different sub-task is not consent for what the agent is doing now.

---

## 4. Parallel vs pipeline vs staged orchestration

**What the problem was.** When a user says "review the auth module and
suggest improvements," is that one task, two, or five? A naive agent
runs one big sequential thread. A greedy agent fans out five
subagents in parallel. Both are wrong, and they are wrong for
different reasons.

**Why it happens.** Work decomposition is not a binary choice. Some
sub-tasks are genuinely independent (read file A, read file B, grep
for symbol C) and parallelize cleanly. Some are strictly ordered
(plan, implement, test, review) and cannot. Many are in between (two
explorers fan out, then one synthesizer reads both). If the agent only
has `run_one_subagent` or `run_many_subagents`, it will force
everything into the wrong shape, and you will see duplicated work or
missed dependencies.

**How I solved it.** Three distinct tools, each with a different
execution model. `parallel_tasks` (`codesm/tool/task.py:146`) runs up
to ten independent subagents via `asyncio.Semaphore` and
`asyncio.gather`, with optional `fail_fast`. `pipeline`
(`codesm/tool/orchestrate.py:160`) runs up to five steps strictly in
sequence; each step's prompt template can reference
`{previous_result}` to inject the prior step's output; the chain stops
on the first failure. `orchestrate`
(`codesm/tool/orchestrate.py:15`) is the staged DAG version: a list
of stages, each stage a list of parallel tasks, stages run one after
another. The model picks based on the shape of the work, not a
preset.

**What I would do differently.** The split into three tools is too
tool-centric. What the model actually needs is a single declaration
of intent ("here are the tasks, here are the dependencies between
them") and the runtime should figure out parallel vs sequential from
the dependency graph. The current design makes the model do the
graph analysis in its head and then pick the matching tool, which is
an extra cognitive hop that coding models frequently get wrong. The
next version should accept a task DAG directly and schedule it.

---

## Reading order for the interview

If an interviewer is going to open one file to see how I think,
the order I would point them at is: (1) `codesm/session/context.py`
for compaction logic and the three-phase design; (2)
`codesm/permission/permission.py` plus `codesm/audit/audit.py` for the
safety layer; (3) `codesm/tool/task.py` and `codesm/tool/orchestrate.py`
for the work decomposition story; (4) the diff of commit `f024ac2`
for the streaming bug fix, because it is the clearest example of
"the simpler version was the right version."
