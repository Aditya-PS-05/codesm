# codesm

An AI coding agent built with Python + Rust. Features a TUI interface, multi-provider support (Anthropic Claude, OpenAI GPT), and a powerful tool system for reading, writing, and executing code.

> Still in early development. Contributions welcome!

![codesm TUI](assets/image.png)

## Feature Progress

### Implemented

- [x] TUI interface (Textual-based)
- [x] Session management & persistence
- [x] Multi-provider support (Anthropic, OpenAI)
- [x] Agent loop with tool execution
- [x] Command palette
- [x] Sidebar with session list
- [x] **Tools**: Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Diagnostics, CodeSearch, Todo, Ls, Batch

### To Be Implemented

#### Core Features (from opencode)
- [X] LSP integration
- [x] Code search (semantic search)
- [x] Multi-edit (batch file edits)
- [x] Patch tool
- [ ] Task/sub-agent spawning
- [x] Todo tracking for agent
- [ ] MCP (Model Context Protocol) support
- [ ] Skill/plugin system
- [ ] Snapshot/undo system
- [ ] Permission system
- [ ] IDE integrations
- [ ] Web search tool improvements
- [ ] Rust core performance 
- [X] Web Search

#### Smart Multi-Model Architecture

The system uses task-specialized models across three tiers:

**Tier 1: Agent Modes** (Primary interaction)
- [ ] **Smart Mode** - Claude Opus 4.5 for unconstrained, state-of-the-art reasoning
- [ ] **Rush Mode** - Claude Haiku 4.5 for fast, cost-effective small tasks
- [ ] Mode switching logic based on task complexity

**Tier 2: Feature Models** (Low-latency UI/UX tasks)
- [ ] **Tab Completion** - Custom fine-tuned model for autocomplete/next-action
- [ ] **Code Review** - Gemini 3 Pro for bug detection and review assistance
- [ ] **Titling** - Claude Haiku 4.5 for fast thread title generation
- [ ] **Look At** - Gemini 3 Flash for image/PDF/media analysis

**Tier 3: Specialized Subagents** (Background processing)
- [ ] **Oracle** - GPT-5/o1 for complex reasoning, planning, debugging
- [ ] **Finder/Search** - Gemini 3 Flash for high-speed codebase retrieval
- [ ] **Librarian** - Claude Sonnet 4.5 for multi-repo research & external code

**Workflow Management**
- [ ] **Handoff System** - Gemini 2.5 Flash for context analysis & task continuation
- [ ] **Topics/Indexing** - Gemini 2.5 Flash-Lite for thread categorization
- [ ] **Task Router** - Route tasks based on reasoning depth vs speed tradeoff

**Infrastructure**
- [ ] Multi-provider model registry (Anthropic, OpenAI, Google)
- [ ] Model selection logic per task type
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