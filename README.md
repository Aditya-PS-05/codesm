# codesm

An AI coding agent built with Python + Rust. Features a TUI interface, multi-provider support (Anthropic, OpenAI, OpenRouter), and a powerful tool system for reading, writing, and executing code.

> Still in early development. Contributions welcome!

![codesm TUI](assets/image.png)

## Quick Start

```bash
# Install
uv pip install -e .

# Run
codesm

# Or with uv directly
uv run codesm
```

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
- [x] **Tools**: Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Diagnostics, CodeSearch, Todo, Ls, Batch

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
- [ ] Permission system
- [ ] Web search tool improvements
- [ ] Rust core performance
- [X] Web Search

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
- [ ] **Oracle** - GPT-5/o1 for complex reasoning, planning, debugging
- [ ] **Finder/Search** - Gemini 3 Flash for high-speed codebase retrieval
- [ ] **Librarian** - Claude Sonnet 4.5 for multi-repo research & external code

**Workflow Management**
- [ ] **Handoff System** - Gemini 2.5 Flash for context analysis & task continuation
- [ ] **Topics/Indexing** - Gemini 2.5 Flash-Lite for thread categorization
- [ ] **Task Router** - Route tasks based on reasoning depth vs speed tradeoff

**Infrastructure**
- [x] Multi-provider model registry (Anthropic, OpenAI, OpenRouter, Google)
- [x] Model selection logic per task type
- [ ] Subagent spawning and orchestration
- [ ] Context passing between agents
- [ ] Cost/latency optimization layer

#### Other Smart Features
- [ ] Mermaid diagram generation
- [ ] Thread search & cross-thread context
- [ ] Auto todo planning & tracking during tasks
- [ ] File citations with clickable links
- [x] Web page reading (WebFetch tool)
- [x] Parallel tool execution optimization

---

## How to Make codesm Better

### vs Claude Code
- **Open source & extensible** - Full transparency, community-driven
- **Multi-provider** - Not locked to Anthropic; use any LLM
- **Rust core** - Performance-critical ops in Rust (grep, file ops)
- **Self-hostable** - No cloud dependency, run fully local
- **Custom skills** - User-defined agent behaviors

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