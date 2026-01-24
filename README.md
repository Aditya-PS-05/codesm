# codesm

An AI coding agent built with Python + Rust. Features a TUI interface, multi-provider support (Anthropic, OpenAI, OpenRouter), and a powerful tool system for reading, writing, and executing code.

> Still in early development. Contributions welcome!

![codesm TUI](assets/image.png)

## Mermaid Graph

```mermaid
flowchart TD
    A[User Input] --> B[Agent.stream]
    B --> C[ReAct Loop Execute]
    C --> D[Claude API Call]
    D --> E{Response Type?}
    
    E -->|Text Response| F[Add Assistant Message]
    E -->|Tool Call| G[Extract Tool Call]
    
    G --> H{Tool Type?}
    H -->|MCP Tool| I[MCP Execute Tool]
    H -->|Built-in Tool| J[Direct Tool Execution]
    H -->|Parallel Tasks| PA[Parallel Subagent Spawning]
    
    I --> K[Generate Python Code]
    K --> L[Execute in Subprocess]
    L --> M[MCP Client Call]
    M --> N[MCP Server Process]
    N --> O[Tool Implementation]
    O --> P[Tool Result]
    
    J --> Q[Tool.execute method]
    Q --> R{Tool Category?}
    R -->|File Operations| S[Read/Write/Edit Tools]
    R -->|Search Tools| T[Grep/Glob/CodeSearch]
    R -->|External Tools| U[Bash/Web Tools]
    R -->|Agent Tools| V[Task/Oracle/Subagents]
    
    S --> W[File System Operations]
    T --> X[Search Operations]
    U --> Y[External Process/API]
    V --> Z[Spawn Single Subagent]
    
    W --> P
    X --> P
    Y --> P
    Z --> AA[Subagent Result]
    AA --> P
    
    PA --> PB{Orchestration Type?}
    PB -->|parallel_tasks| PC[Concurrent Execution]
    PB -->|orchestrate| PD[Staged Execution]
    PB -->|pipeline| PE[Sequential Chain]
    
    PC --> PF[asyncio.gather]
    PD --> PG[Stage 1 Parallel] --> PH[Stage 2 Parallel] --> PI[Stage N Parallel]
    PE --> PJ[Step 1] --> PK[Pass Result] --> PL[Step 2]
    
    PF --> PM[Subagent 1]
    PF --> PN[Subagent 2]
    PF --> PO[Subagent N]
    
    PM --> PQ[Aggregate Results]
    PN --> PQ
    PO --> PQ
    PI --> PQ
    PL --> PQ
    PQ --> P
    
    P --> BB[Add Tool Result Message]
    BB --> CC[Update Session State]
    CC --> DD{More Tool Calls?}
    
    DD -->|Yes| G
    DD -->|No| EE[Continue ReAct Loop]
    EE --> D
    
    F --> FF[Session Complete]
    
    CC --> GG[Context Management]
    GG --> HH[Message Storage]
    HH --> II[Session Persistence]
```

## Quick Start

```bash
# Install
uv pip install -e .

# Run
codesm

# Or with uv directly
uv run codesm
```

## Parallel Subagents

codesm supports spawning multiple subagents in parallel for independent tasks. This is inspired by opencode's batch/task pattern and allows faster execution of parallelizable work.

### Using `parallel_tasks` Tool

The `parallel_tasks` tool allows you to run up to 10 subagent tasks concurrently:

```python
# Example: Run multiple research tasks in parallel
{
    "tasks": [
        {
            "subagent_type": "researcher",
            "prompt": "Find all API endpoints in the codebase",
            "description": "Find API endpoints"
        },
        {
            "subagent_type": "researcher", 
            "prompt": "Analyze the authentication flow",
            "description": "Analyze auth flow"
        },
        {
            "subagent_type": "finder",
            "prompt": "Find all test files",
            "description": "Find test files"
        }
    ],
    "fail_fast": false
}
```

### Features

- **Up to 10 concurrent tasks** - Prevent resource exhaustion
- **Auto-routing** - Use `subagent_type: "auto"` to let the router pick the best agent
- **Fail-fast mode** - Cancel remaining tasks on first failure with `fail_fast: true`
- **Progress tracking** - Per-task timing and success/failure indicators
- **Result aggregation** - Combined output with truncation for long results

### Subagent Types

| Type | Best For | Model |
|------|----------|-------|
| `coder` | Multi-file edits, features | Claude Sonnet |
| `researcher` | Code analysis (read-only) | Claude Sonnet |
| `reviewer` | Bug detection, security | Claude Sonnet |
| `planner` | Implementation plans | Claude Sonnet |
| `finder` | Fast code search | Gemini Flash |
| `oracle` | Deep reasoning | o1 |
| `librarian` | Multi-repo research | Claude Sonnet |
| `auto` | Router picks best | Varies |

### Advanced Orchestration

For complex multi-stage workflows, use the `orchestrate` tool with stages:

```python
# Example: Staged execution (stages run sequentially, tasks within stages run in parallel)
{
    "stages": [
        [  # Stage 1: Research (parallel)
            {"subagent_type": "researcher", "prompt": "Analyze current auth system", "description": "Research auth"},
            {"subagent_type": "finder", "prompt": "Find all auth-related files", "description": "Find auth files"}
        ],
        [  # Stage 2: Planning (after research completes)
            {"subagent_type": "planner", "prompt": "Plan auth improvements based on research", "description": "Plan improvements"}
        ],
        [  # Stage 3: Implementation (after planning)
            {"subagent_type": "coder", "prompt": "Implement planned auth improvements", "description": "Implement changes"},
            {"subagent_type": "coder", "prompt": "Add tests for new auth code", "description": "Add tests"}
        ]
    ],
    "fail_fast": true
}
```

### Pipeline Tool

For sequential workflows where each step receives the previous step's output:

```python
{
    "steps": [
        {"subagent_type": "researcher", "prompt_template": "Find all TODO comments in the codebase", "description": "Find TODOs"},
        {"subagent_type": "planner", "prompt_template": "Prioritize these TODOs: {previous_result}", "description": "Prioritize"},
        {"subagent_type": "coder", "prompt_template": "Fix the top priority TODO: {previous_result}", "description": "Fix top TODO"}
    ],
    "initial_context": ""
}
```

### Programmatic Usage

```python
from codesm.agent.orchestrator import spawn_parallel_subagents, SubAgentOrchestrator, OrchestrationPlan

# Simple parallel execution
results = await spawn_parallel_subagents(
    tasks=[
        ("researcher", "Find all database queries", "Find DB queries"),
        ("coder", "Add input validation to user.py", "Add validation"),
    ],
    directory=Path("."),
    parent_tools=tool_registry,
    max_concurrent=5,
)

for task in results:
    print(f"{task.description}: {task.status}")

# Advanced: Staged orchestration
orchestrator = SubAgentOrchestrator(directory=Path("."), parent_tools=registry)
plan = OrchestrationPlan.staged([
    [orchestrator.create_task("researcher", "Research phase 1", "Research")],
    [orchestrator.create_task("coder", "Implement based on research", "Implement")],
])
await orchestrator.execute_plan(plan)
```

---

## Agent Modes

codesm supports two agent modes for different task types:

### Smart Mode (Default)
- Uses powerful models (Claude Sonnet 4, GPT-4o)
- Best for complex tasks, architecture decisions, debugging
- Full capability, thorough reasoning

### Rush Mode
- Uses fast models (Claude Haiku 3.5, GPT-4o-mini)
- **67% cheaper, 50% faster**
- Best for simple, well-defined tasks: quick edits, small fixes, simple features

**Switch modes:**
- Press `tab` to toggle between modes
- Use `/rush` or `/smart` commands
- Use `/mode` to open mode selector

## Providers

### Supported Providers
- **Anthropic** - Claude models (Sonnet, Opus, Haiku)
- **OpenAI** - GPT-4o, GPT-4-turbo, o1
- **OpenRouter** - Access 100+ models with one API key
- **Ollama** - Local models (Llama, Qwen, DeepSeek, Mistral)
- **Google** - Gemini models (coming soon)

### Connect a Provider
1. Press `Ctrl+A` or use `/connect` command
2. Select provider
3. Enter API key

## Feature Progress

### Implemented

- [x] TUI interface (Textual-based)
- [x] Session management & persistence
- [x] Multi-provider support (Anthropic, OpenAI, OpenRouter)
- [x] **Rush Mode** - Fast/cheap mode for simple tasks
- [x] Agent loop with tool execution
- [x] Command palette (`Ctrl+P` or `/`)
- [x] Sidebar with session list
- [x] **Tools**: Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Diagnostics, CodeSearch, Todo, Ls, Batch, ParallelTasks, Orchestrate, Pipeline

### To Be Implemented

#### Core Features (from opencode)
- [X] LSP integration
- [x] Code search (semantic search)
- [x] Multi-edit (batch file edits)
- [x] Patch tool
- [x] Task/sub-agent spawning
- [x] Todo tracking for agent
- [x] MCP (Model Context Protocol) support
- [x] Skill/plugin system
- [x] Snapshot/undo system
- [x] Permission system
- [ ] Web search tool improvements
- [ ] Rust core performance
- [X] Web Search

#### CLI Features (from amp)
- [ ] **`codesm login`** - Authenticate with API key or OAuth
- [ ] **`codesm logout`** - Remove stored credentials
- [ ] **`codesm update`** - Self-update CLI
- [ ] **Thread Management CLI**:
  - [ ] `codesm threads list` - List all threads/sessions
  - [ ] `codesm threads new` - Create new thread
  - [ ] `codesm threads continue` - Continue existing thread
  - [ ] `codesm threads search` - Search threads
  - [ ] `codesm threads share` - Share a thread (generate URL)
  - [ ] `codesm threads rename` - Rename a thread
  - [ ] `codesm threads archive` - Archive a thread
  - [ ] `codesm threads delete` - Delete a thread
  - [ ] `codesm threads handoff` - Create handoff thread from existing
  - [ ] `codesm threads markdown` - Export thread as markdown
  - [ ] `codesm threads replay` - Replay a thread
- [ ] **Tool Management CLI**:
  - [ ] `codesm tools list` - List all active tools (including MCP)
  - [ ] `codesm tools show` - Show tool details
  - [ ] `codesm tools make` - Create skeleton tool in toolbox
  - [ ] `codesm tools use` - Invoke a tool directly from CLI
- [x] **Skill Management CLI**:
  - [x] `skill browse` - Browse skills.sh registry (vercel-labs/agent-skills)
  - [x] `skill fetch <name>` - Fetch skill from skills.sh
  - [x] `skill suggest` - Auto-suggest skills based on file context
  - [x] `skill list` - List local project skills
  - [x] `skill load/unload` - Load/unload local skills
- [ ] **Permissions CLI**:
  - [ ] `codesm permissions list` - List permission rules
  - [ ] `codesm permissions test` - Test permissions
  - [ ] `codesm permissions edit` - Edit permissions
  - [ ] `codesm permissions add` - Add permission rule
- [ ] **MCP OAuth**:
  - [ ] `codesm mcp oauth login` - Register OAuth for MCP server
  - [ ] `codesm mcp oauth logout` - Remove OAuth for MCP server
  - [ ] `codesm mcp oauth status` - Show OAuth status
  - [ ] `codesm mcp add` - Add MCP server configuration
  - [ ] `codesm mcp remove` - Remove MCP server configuration
  - [ ] `codesm mcp doctor` - Check MCP server health

#### Execute Mode (from amp)
- [ ] **`-x, --execute`** - Non-interactive execute mode (run prompt, print last message, exit)
- [ ] **`--stream-json`** - Output in Claude Code-compatible stream JSON format
- [ ] **`--stream-json-thinking`** - Include thinking blocks in stream JSON
- [ ] **`--stream-json-input`** - Read JSON Lines from stdin
- [ ] **`-l, --label`** - Add labels to threads
- [ ] **Stdin piping** - `echo "message" | codesm` 
- [ ] **Stdout redirect detection** - Auto-enable execute mode when redirecting

#### Global Options (from amp)
- [ ] **`--visibility`** - Set thread visibility (private, public, workspace, group)
- [ ] **`--notifications / --no-notifications`** - Sound notifications toggle
- [ ] **`--settings-file`** - Custom settings file path
- [ ] **`--log-level`** - Set log level (error, warn, info, debug, audit)
- [ ] **`--log-file`** - Set log file location
- [ ] **`--dangerously-allow-all`** - Disable all confirmation prompts
- [ ] **`--mcp-config`** - JSON config or file path for MCP servers
- [ ] **`-m, --mode`** - Set agent mode (free, rush, smart)

#### Settings System (from amp)
- [ ] **JSON settings file** - `~/.config/codesm/settings.json`
- [ ] **Settings reference**:
  - [ ] `codesm.notifications.enabled` - Sound notifications
  - [ ] `codesm.notifications.system.enabled` - System notifications when terminal unfocused
  - [ ] `codesm.mcpServers` - MCP server configurations
  - [ ] `codesm.tools.disable` - Array of tools to disable
  - [ ] `codesm.tools.enable` - Glob patterns for tools to enable
  - [ ] `codesm.network.timeout` - Network request timeout
  - [ ] `codesm.permissions` - Permission rules
  - [ ] `codesm.guardedFiles.allowlist` - File patterns allowed without confirmation
  - [ ] `codesm.dangerouslyAllowAll` - Disable all prompts
  - [ ] `codesm.fuzzy.alwaysIncludePaths` - Paths to always include in search
  - [ ] `codesm.skills.path` - Additional skill directories
  - [ ] `codesm.toolbox.path` - Toolbox scripts directory
  - [ ] `codesm.git.commit.coauthor.enabled` - Add codesm as co-author
  - [ ] `codesm.proxy` - Proxy URL for requests
  - [ ] `codesm.updates.mode` - Update checking behavior
  - [ ] `codesm.showCosts` - Show cost information

#### Smart Multi-Model Architecture

The system uses task-specialized models across three tiers:

**Tier 1: Agent Modes** (Primary interaction)
- [x] **Smart Mode** - Claude Sonnet 4 / GPT-4o for complex reasoning
- [x] **Rush Mode** - Claude Haiku 3.5 / GPT-4o-mini for fast, cheap tasks
- [x] Mode switching via `tab` key or `/mode` command

**Tier 2: Feature Models** (Low-latency UI/UX tasks)
- [x] **Tab Completion** - Custom fine-tuned model for autocomplete/next-action
- [x] **Code Review** - Gemini 2.5 Pro (via OpenRouter) for bug detection and review assistance
- [x] **Titling** - Claude 3.5 Haiku (via OpenRouter) for fast thread title generation
- [x] **Look At** - Gemini 2.0 Flash (via OpenRouter) for image/PDF/media analysis

**Tier 3: Specialized Subagents** (Background processing)
- [x] **Oracle** - GPT-5/o1 for complex reasoning, planning, debugging
- [x] **Finder/Search** - Gemini 2.5 Flash for high-speed codebase retrieval
- [x] **Librarian** - Claude Sonnet 4 for multi-repo research & external code

**Workflow Management**
- [x] **Handoff System** - Gemini 2.5 Flash for context analysis & task continuation
- [x] **Topics/Indexing** - Gemini 2.0 Flash-Lite for thread categorization
- [x] **Task Router** - Route tasks based on reasoning depth vs speed tradeoff

**Infrastructure**
- [x] Multi-provider model registry (Anthropic, OpenAI, OpenRouter, Google)
- [x] Model selection logic per task type
- [x] Subagent spawning and orchestration
- [x] Context passing between agents
- [x] Cost/latency optimization layer

#### Other Smart Features
- [x] Mermaid diagram generation
- [x] Thread search & cross-thread context
- [ ] Auto todo planning & tracking during tasks
- [x] File citations with clickable links
- [x] Web page reading (WebFetch tool)
- [x] Parallel tool execution optimization

## Roadmap: Competitive Feature

### Critical (To match Amp/Claude Code/OpenCode)

#### Agent Architecture
- [x] **Parallel Subagent Spawning** - Run multiple subagents concurrently for independent tasks
- [ ] **Context Window Management** - Smart context compression, summarization, and handoff
- [ ] **Automatic Thread Handoff** - When context gets long, seamlessly continue in new thread
- [x] **Task Decomposition Engine** - Break complex tasks into parallelizable subtasks (via orchestrate/pipeline tools)
- [ ] **Agent Self-Correction** - Detect and retry failed tool calls with different approaches

#### Tool System
- [x] **Undo/Redo System** - Revert any file edit with full history
- [x] **File Watcher** - React to file changes in real-time
- [x] **Diff Preview** - Show unified diff before applying edits
- [x] **Format on Save** - Auto-format files after edits (ruff, prettier, gofmt, rustfmt)
- [x] **Multi-file Atomic Edits** - Transactional edits across files

#### Context & Memory
- [x] **AGENTS.md Support** - Read project-specific instructions automatically
- [x] **Cross-Session Memory** - Remember user preferences and past solutions
- [x] **Codebase Indexing** - Pre-index repos for instant semantic search
- [x] **Git Integration** - Auto-commit, branch management, PR creation
- [x] **Conversation Branching** - Fork conversations to explore alternatives

### Important (Competitive Advantages)

#### Intelligence Layer
- [x] **Code Review Agent** - Automatic PR review with actionable feedback
- [x] **Test Generation** - Auto-generate tests for new/changed code
- [x] **Bug Localization** - Given an error, find the root cause automatically
- [ ] **Refactoring Suggestions** - Proactive code improvement recommendations

#### Developer Experience
- [ ] **Streaming Tool Output** - Real-time output for long-running tools (tests, builds)
- [ ] **Progress Indicators** - Show what the agent is doing at each step
- [ ] **Keyboard-First Navigation** - Vim-style motions throughout TUI
- [ ] **Split Pane View** - Code preview alongside chat
- [ ] **File Tree Browser** - Navigate and select files visually
- [ ] **Syntax Highlighted Diffs** - Beautiful, readable code changes

#### Safety & Permissions
- [ ] **Sandboxed Execution** - Run bash commands in isolated environment
- [x] **Permission Prompts** - Ask before destructive operations
- [x] **Allowlist/Blocklist** - Configure which commands/paths are allowed
- [x] **Audit Log** - Track all agent actions for review
- [x] **Dry Run Mode** - Preview all changes without applying

### Nice to Have (Killer Features)

#### Advanced Capabilities
- [ ] **Mermaid Diagram Generation** - Auto-generate architecture/flow diagrams
- [ ] **Image/Screenshot Analysis** - Describe UI, extract text from images
- [ ] **PDF/Document Reading** - Extract and summarize document contents
- [ ] **Browser Automation** - Navigate and interact with web pages
- [ ] **API Testing** - Make HTTP requests and validate responses

#### Local-First Features
- [x] **Ollama Integration** - Run fully local with open models
- [ ] **Embedding Cache** - Local vector DB for semantic search (ChromaDB/LanceDB)
- [ ] **Offline Fallback** - Graceful degradation when no internet
- [ ] **Model Switching Mid-Task** - Hot-swap models during execution
- [ ] **Cost Tracking Dashboard** - Monitor API spend in real-time

#### Collaboration
- [ ] **Session Sharing** - Share conversations via URL
- [ ] **Team Workspaces** - Shared sessions, shared memory
- [ ] **Real-time Collaboration** - Multiple users in same session
- [ ] **Export Formats** - Export to Markdown, JSON, or executable scripts

#### IDE Integration
- [ ] **VS Code Extension** - Native integration with file syncing
- [ ] **Neovim Plugin** - Lua-based plugin for Neovim users  
- [ ] **JetBrains Plugin** - Support for IntelliJ-based IDEs
- [ ] **Language Server** - Act as an LSP for AI-powered completions

#### Voice & Accessibility
- [ ] **Voice Input** - Whisper-based speech-to-text
- [ ] **Voice Output** - TTS for responses (optional)
- [ ] **Screen Reader Support** - Full accessibility compliance
- [ ] **High Contrast Themes** - Accessibility-focused UI themes

### Experimental (Moonshots)

- [ ] **Self-Improving Agent** - Learn from user corrections
- [ ] **Codebase-Specific Fine-tuning** - LoRA adapters for your repos
- [ ] **Multi-Agent Debates** - Multiple agents argue to find best solution
- [ ] **Autonomous Mode** - Run overnight, wake user for decisions
- [x] **Plugin Marketplace** - skills.sh registry integration (vercel-labs/agent-skills)
- [ ] **Natural Language Git** - "Undo my last 3 commits" → executes git commands
- [ ] **Code Generation Streaming** - Token-by-token code preview as it's written
- [ ] **Predictive Actions** - Suggest next action before user asks

---

## How to Make codesm Better

### vs Claude Code
- **Open source & extensible** - Full transparency, community-driven
- **Multi-provider** - Not locked to Anthropic; use any LLM
- **Rust core** - Performance-critical ops in Rust (grep, file ops)
- **Self-hostable** - No cloud dependency, run fully local
- **Custom skills** - User-defined agent behaviors + skills.sh registry

### vs Opencode
- **Python ecosystem** - Easier contribution, rich ML/AI libraries
- **Simpler architecture** - Less overhead, faster iteration
- **Better defaults** - Opinionated but sensible out-of-box experience

### Unique Differentiators to Build
1. **Hybrid Python+Rust** - Python for flexibility, Rust for speed
2. **Local-first AI** - First-class support for Ollama, llama.cpp
3. **Agent memory** - Long-term context across sessions
4. **Code understanding graphs** - AST-based code intelligence
5. **Collaborative mode** - Multiple users, shared sessions
6. **Voice interface** - Speech-to-code capabilities
7. **Custom model fine-tuning** - Train on your codebase patterns
8. **Offline mode** - Full functionality without internet

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `tab` | Toggle Smart/Rush mode |
| `Ctrl+P` | Command palette |
| `Ctrl+A` | Connect provider |
| `Ctrl+N` | New session |
| `Ctrl+T` | Toggle theme |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+C` | Quit |
| `Escape` | Cancel current operation |

## Commands

| Command | Description |
|---------|-------------|
| `/mode` | Open mode selector |
| `/rush` | Switch to Rush mode |
| `/smart` | Switch to Smart mode |
| `/models` | Select model |
| `/connect` | Connect a provider |
| `/session` | Browse sessions |
| `/new` | New session |
| `/theme` | Change theme |
| `/status` | Show current status |
| `/help` | Show help |

---

## Post-Development Roadmap

### Phase 1: Polish & Reliability (1-2 weeks)
**Goal: Make it daily-drivable**
- [x] **Undo/Redo system** - Critical for trust
- [x] **Permission prompts** - Ask before destructive ops
- [x] **AGENTS.md support** - Match Amp/Claude Code behavior
- [ ] **Streaming tool output** - Real-time feedback for bash/tests

### Phase 2: Differentiation (2-4 weeks)
**Goal: Create your unique angle**
- [x] **Ollama/local models** - First-class offline support (big differentiator)
- [ ] **Cost tracking dashboard** - Show users their spend in real-time
- [ ] **Git integration** - Auto-commit, branch, PR creation
- [ ] **Cross-session memory** - Remember preferences/past solutions

### Phase 3: Community & Growth (4-8 weeks)
**Goal: Build traction**
- [ ] **Plugin marketplace/skills** - Let community extend
- [ ] **VS Code extension** - Meet devs where they are
- [ ] **Session sharing** - Viral loop for growth
- [ ] **Better docs + demo videos**

### Recommended Focus Order
1. **Undo system** → builds trust
2. **Ollama support** → unique selling point vs Cursor/Claude Code
3. **Cost tracking** → users love transparency
4. **Git integration** → makes it a complete workflow

> **Strategic Note:** Ship Ollama support early—"fully local AI coding agent" is a strong positioning no major player owns yet.