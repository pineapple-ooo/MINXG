# AgentHarness — AI Orchestration Platform

**Version:** 0.18.6  
**Status:** Under active development  
**License:** MIT

---

## What is AgentHarness?

AgentHarness is a modular AI orchestration platform written in Python. It provides:

- A **chat CLI** with streaming, tool calling, and multi-provider model support
- An **OpenAI-compatible `/v1` API gateway** (MCP server mode included)
- A **worker system** with 59 worker classes exposing 600+ tools across file I/O, networking, crypto, math, text, system, AI, data, image/audio/video/PDF processing
- **7 mathematical pillars**: Geometric Algebra, Category Theory, Information Geometry, Topology, Chaos Theory, Fiber Bundles, Symbolic Differential Algebra
- **Polyglot bridges** to C/C++, Rust, Go, Java, Julia, R, WebAssembly, and Datalog
- **Multi-channel support** for Telegram, Discord, Slack (opt-in, long-polling/websocket)
- A **skill system** for agent instruction packs (markdown-based, no code execution)
- **Self-evolution** and **reverse engineering** tooling (academic/interop scope)

Platforms tested: Linux, macOS, Docker. Windows and Android/Termux are supported on a best-effort basis — see the platform notes below.

### Recent maintenance notes

- CLI history and compatibility tests now include a small fallback for legacy Android path checks.
- The scheduler can still start when `croniter` is not installed, using a limited built-in preset parser.
- WASM runtime helpers and optimizer hooks have been restored so the runtime and its tests agree on the public surface.
- The wizard UI now exposes a `readchar`-compatible fallback object when the optional dependency is absent.
- Runtime execution sanitization removes more inherited shell environment variables before launching isolated work.

---


## Honest Comparison

We previously positioned AgentHarness against other tools with inflated claims and unnecessarily aggressive language. That was a mistake. Here's a sober look:

| Capability | AgentHarness | Notes |
|------------|-------|-------|
| Tool breadth | 600+ tools across 59 workers | Wide but shallow in some categories |
| MCP server | ✅ Yes | Exposes all workers as MCP tools |
| OpenAI-compatible API | ✅ Yes | `/v1/chat/completions`, streaming |
| Mathematical operators | 300+ (7 pillars) | Niche strength; not general-purpose |
| Polyglot bridges | 8 runtimes | C/C++, Rust, Go, Java, Julia, R, WASM, Datalog |
| Multi-channel (Telegram/Discord/Slack) | Opt-in | Long-polling / websocket, no public port |
| Agent framework | ReAct, planning, multi-agent | Functional but not battle-hardened |
| RAG system | Vector + hybrid search | Basic implementation |
| Workflow engine | DAG with branching/loops | Early stage |
| Guardrails | Input/output validation, PII, toxicity | Minimal; not production-grade |
| Windows support | Best-effort | CI uses mocked paths; real-device testing needed |
| Android/Termux support | Best-effort | Degraded networking without `termux-api` |

We are **not** "better than everyone." We have genuine depth in math/science operators and polyglot bridges, but we lag on messaging-platform reach, community skill breadth, and production hardening compared to established projects like OpenClaw or LangChain. If you need a wide messaging ecosystem or a mature plugin marketplace, those are better choices today.

---

## Quick Start

```bash
# The easy way
pip install agent_harness-beta
Successfully installed agent_harness-beta-0.18.6

# The "from source" way
git clone https://github.com/pineapple-ooo/AgentHarness-Beta.git
cd AgentHarness-Beta
pip install -e .

# Verify installation
agent_harness doctor
```

Run `agent_harness` with no arguments to start the chat CLI. Use `agent_harness gateway --mcp` for the MCP server.

---

## Core Features

### Chat CLI (`agent_harness`)
- Streaming responses with tool-call visualization
- 32+ model providers (OpenAI, Anthropic, Google, DeepSeek, xAI, local via Ollama, etc.)
- Mid-conversation model switching (`/model`)
- Multi-language UI (12 languages, live-switchable with `/lang`)
- Theme system (8 built-in themes)

### API Gateway (`agent_harness gateway`)
- OpenAI-compatible `/v1/chat/completions` endpoint
- MCP server mode: `agent_harness gateway --mcp`
- All 59 workers exposed as callable tools
- Cost tracking, rate limiting, request logging

### Worker System
59 worker classes organized in 8 categories:
- **I/O**: file, network, database, web, archive, cloud, media
- **Dispatch**: system, shell, process, platform, ADB, root, notify, security
- **Transform**: state machines, persistence, events, rules, hot-reload, AI tools
- **Scalar**: text, datetime, math, string, version, color, markdown
- **Aggregate**: encoding, crypto, data, templates, i18n, ML, benchmarks
- **DevTools**: Android Forge, QuadForge, DevShell, ReverseStudio, AuditWorker, SelfEvolutionWorker
- **Polyglot**: Julia, R, Datalog, WASM workers
- **Math Pillar**: GeometryWorker (driver-geometry glue)

### Mathematical Pillars (7 + Geometry)
- **Geometric Algebra** — multivector calculus
- **Category Theory** — functors, monads, morphisms
- **Information Geometry** — Fisher metric, natural gradient
- **Topology** — homology, cohomology, spectral sequences
- **Chaos Theory** — Lyapunov exponents, bifurcation, fractals
- **Fiber Bundles** — connections, curvature, parallel transport
- **Symbolic Differential Algebra** — jets, Lie brackets, differential ideals
- **GeometryWorker** — driver-geometry integration layer

### Polyglot Bridges (8 runtimes)
| Runtime | Bridge | Notes |
|---------|--------|-------|
| C/C++ | `c_core/`, `cpp_core/` | FFI via cffi / pybind11 |
| Rust | `rust_core/` | `agent_harness_rust_core` crate, ctypes |
| Go | `go_core/` | JSON-RPC over stdio |
| Java | `java_core/` | JVM daemon + vector engine |
| Julia | `julia_worker.py` | `agent_harness.contracts.runtime.julia` |
| R | `r_worker.py` + `r_scripts/` | Statistical computing |
| WASM | `wasm_worker.py` | Wasmtime sandbox |
| Datalog | `datalog_worker.py` | Clingo / pyDatalog |

### Skill System (`agent_harness skill ...`)
- Markdown `SKILL.md` files with YAML frontmatter
- Local or remote catalogs (JSON file, raw GitHub URL works)
- `agent_harness skill search / install / new / publish`
- Also exposed to the chat agent as `skill_search`, `skill_install`, `skill_new` tools
- **No code execution on install** — skills are instruction packs, not plugins

### Multi-Channel Gateway
- Telegram (long-polling), Discord (websocket), Slack (websocket)
- Disabled by default; enable in `config/gateway.yaml`
- No public port required — all outbound
- See `gateway/channel_*.py` for per-channel status and known gaps

---

## CLI Reference

```bash
agent_harness                    # Start chat CLI (default)
agent_harness setup              # Run setup wizard
agent_harness config             # Show current configuration
agent_harness status             # Runtime status
agent_harness tools              # List available tools
agent_harness model [name]       # Set or view model
agent_harness api <url>          # Quick-set API base URL
agent_harness key <key>          # Quick-set API key
agent_harness lang [code]        # Switch display language
agent_harness gateway [--detach] # API gateway (foreground default)
agent_harness gateway --mcp      # MCP server mode
agent_harness doctor             # Self-check (config + tools + extensions)
agent_harness ext <sub>          # Extension management
agent_harness skill <sub>        # Skill management
agent_harness features           # Feature showcase
agent_harness themes             # Theme management
agent_harness export             # Export memories (json/markdown)
agent_harness import <file>      # Import memories
agent_harness help               # Show this cheatsheet
```

---

## MCP Server Setup

### Claude Desktop
Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "agent_harness": {
      "command": "agent_harness",
      "args": ["gateway", "--mcp"]
    }
  }
}
```

### Cursor / Windsurf / Any MCP Client
Same configuration; the `--mcp` flag exposes all workers as MCP tools.

---

## Platform Notes

### Windows
- Install Python 3.10+, then `pip install agent_harness-beta`
- Run in PowerShell or `cmd.exe`
- `agent_harness doctor` reports missing capabilities
- Command execution routes through `cmd.exe` correctly
- **Not tested on real hardware in this release** — CI mocks the Windows code paths. Please report issues.

### Android (Termux)
- Install [Termux](https://termux.dev) from F-Droid
- `pkg install python git && pip install agent_harness-beta`
- Notifications use `termux-api` when available; fallback to log lines
- Networking tools degrade gracefully without `inetutils`
- **Not tested on real device in this release** — CI mocks Termux paths. Please report issues.

### Linux / macOS / Docker
- Fully tested in CI
- Dockerfile provided for containerized deployment

---

## Contributing

1. Fork the repository
2. Read `ARCHITECTURE.md` for the module layout rationale
3. Make your changes
4. Run `pytest tests/` — all tests must pass
5. Submit a PR

See `CONTRIBUTING.md` for details.

---

## License

MIT. Use freely. We take no responsibility for what your AI does.

---

## A Note on Tone

Earlier versions of this README (up to 0.18.3) used hyperbolic, aggressive, and profane language. That was unprofessional and misleading. We apologize. This rewrite aims for honesty about what AgentHarness does well, where it falls short, and what you can actually expect. If you find remaining instances of the old tone, please open an issue or PR.

---

*Version 0.18.6 — built with care, tested honestly.*