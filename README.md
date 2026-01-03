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
- [x] **Tools**: Read, Write, Edit, Bash, Glob, Grep, Web

### To Be Implemented

#### Core Features (from opencode)
- [ ] LSP integration
- [ ] Code search (semantic search)
- [ ] Multi-edit (batch file edits)
- [ ] Patch tool
- [ ] Task/sub-agent spawning
- [ ] Todo tracking for agent
- [ ] MCP (Model Context Protocol) support
- [ ] Skill/plugin system
- [ ] Snapshot/undo system
- [ ] Permission system
- [ ] IDE integrations
- [ ] Web search tool improvements
- [ ] Rust core performance modules

#### Smart Features (inspired by Amp)
- [ ] Oracle - reasoning model advisor for planning, review, debugging
- [ ] Finder - intelligent semantic code search agent
- [ ] Librarian - codebase understanding agent (multi-repo analysis)
- [ ] Mermaid diagram generation
- [ ] Thread search & cross-thread context
- [ ] Auto todo planning & tracking during tasks
- [ ] File citations with clickable links
- [ ] Web page reading with objective extraction
- [ ] Parallel tool execution optimization

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
