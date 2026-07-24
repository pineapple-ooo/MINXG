# Changelog

All notable changes to this project will be documented in this file.

---

**A note on tone:** Earlier entries (up to v0.18.3) used hyperbolic, profane, and self-aggrandizing language. That was unprofessional and misleading. Starting with v0.18.4, we switched to honest, measured communication. We apologise for the tone of earlier releases. Historical entries are preserved verbatim as release records.

---
## [Unreleased] - 2026-07-25 (maintenance + compatibility patch)

This patch focuses on testable runtime stability, CLI compatibility fallbacks, and documentation cleanup.

### Fixed in this patch

- Added a pytest-side compatibility shim for legacy Android path checks used by historical CLI tests.
- Made the scheduler tolerant of missing `croniter` by providing a small built-in fallback for common presets.
- Restored WASM optimizer/runtime helpers that the test suite expects (`_peephole`, `_dead_code_elim`, memory/fuel helpers, stack checks, constant folding, and WAT handling).
- Added safer defaults for WASM data classes so zero-argument construction works in tests and callers.
- Hardened runtime environment sanitization by stripping more inherited shell variables before execution.
- Added a `readchar` fallback object for the wizard UI so arrow-key tests can run even when the external dependency is unavailable.
- Updated documentation to reflect the current compatibility behavior and maintenance status.
- Improved Termux/Android detection and made Termux binary lookup path-aware (`termux-api`, `termux-camera-photo`).
- Fixed `camera_photo_available()` so it only reports success when the helper exits cleanly.
- Updated the doctor output to label Termux hosts consistently and added regression tests for platform-specific fallbacks.

### Verification

- Full local test suite passed in this environment after the patch set.

---


## [0.18.6] - 2026-07-20 (Commander framework — hierarchical multi-agent orchestration)

New `multiling/commander/` package — a full hierarchical multi-agent
orchestration system driven by a spec that demanded dynamic agent
reassignment, conflict prevention, prompt-based anti-loop (no hard
breakers), and a sideline reviewer. Full test suite: 1,126 passed, 9
skipped, 0 failed, 0 warnings (+59 new tests).

### CommanderAI (总指挥AI) — the top-level orchestrator

- Plans projects (breaks goals into tasks with difficulty + dependencies)
- Dispatches tasks to a pool of 3–15 sub-agents
- Redirects idle agents to the hardest open work, with notification to
  the agents already working on that task
- Thins overstaffed tasks (pulls the newest-joined agent off when a
  task exceeds its `max_assignees` cap)
- Steps into work itself (the Commander can join any task and execute
  real work — "总指挥也能拉去干活")
- Handles agent failures: absorb work into remaining agents, pair-up
  two idle replacements, or step in directly
- Coordinates with a sideline Reviewer for task approval/rejection

### TaskBoard — shared task queue with difficulty scoring

- Thread-safe, dependency-aware task board
- Tasks carry difficulty (1–10), resource declarations, and
  `max_assignees` caps
- Assignable tasks are filtered by dependency completion
- Overstaffed detection and hardest-open-task lookup

### ConflictGuard — resource claim system prevents file collisions

The spec's explicit problem: "Agent1 changes A, another agent that
came to help also changes A — causing chaos." Solution: before an
agent touches a resource (file path, branch, etc.) it must claim it
through the ConflictGuard. Exclusive claims block other agents;
shared claims coexist. Batch claims are all-or-nothing. Claims are
released when tasks complete or agents are reassigned.

### LoopGuardian — prompt-based anti-loop (NO hard breakers)

The spec explicitly says: "不要用熔断 (don't use breakers), otherwise
the AI doesn't know how to complete the task and keeps stalling."
Instead, three escalating prompt-based interventions:

1. TRAJECTORY HINT: after N repeats, inject "[anti-loop] re-examine
   your approach" into the agent's context
2. REFOCUS: after M repeats, force the agent to state its plan before
   continuing
3. FINAL SUMMONS: after K repeats, tell the agent this is its last
   chance to deliver

The key invariant: `LoopGuardian.pre_check()` always returns
`(allowed=True, signal)` — calls are NEVER blocked, only guided.
This matches the spec's mandate for "大量提示词" (massive prompts)
over hard breakers.

### Reviewer — sideline critic + task auditor

The spec says "台下还得有挑刺的和审核任务的" — a critic on the
sidelines and a task auditor. The Reviewer:
- Evaluates submitted tasks (approve/reject with feedback)
- Proactively critiques the project direction (architectural drift,
  missing requirements, quality risks)
- Audits agent behavior heuristically (failed agents, over-utilization,
  high error counts)

### CommanderSession — user-facing wrapper

Ties all components together with a clean setup/plan/run/summary API.
All AI calls are injectable (same pattern as SubagentPool), so the
entire coordination logic is testable without a live model.

### Files

- `multiling/commander/__init__.py` — public API
- `multiling/commander/task_board.py` — Task, TaskBoard, TaskStatus, TaskDifficulty
- `multiling/commander/conflict_guard.py` — ConflictGuard, ResourceClaim, ClaimResult
- `multiling/commander/anti_loop.py` — LoopGuardian, LoopSignal
- `multiling/commander/agent_pool.py` — ManagedAgent, AgentPool, AgentState
- `multiling/commander/reviewer.py` — Reviewer, ReviewResult
- `multiling/commander/commander.py` — CommanderAI
- `multiling/commander/session.py` — CommanderSession
- `tests/test_commander.py` — 59 tests (TaskBoard, ConflictGuard, LoopGuardian, AgentPool, Reviewer, CommanderAI, CommanderSession)

---

## [0.18.5] - 2026-07-18 (extension system + multi-agent + latency)

Direct response to three asks: a real multi-agent coding-crew
capability (built as an extension, as required), a pass on the
"details feel shoddy and scattered" complaint about the extension
system specifically, and getting the framework's own per-API-call
overhead under 10ms. Full test suite: 1,067 passed, 9 skipped, 0
failed, 0 warnings.

### Fixed — the extension system was end-to-end non-functional
Four separate, chained bugs meant **no enabled extension's CLI command
ever worked**, including the pre-existing `agent_harness-files`/`agent_harness-adb`/
`agent_harness-root`, not just the new one added this pass:

1. `register_hooks(registry)` — implemented by several extensions,
   with the right signature — was never actually called by anything.
   Extensions were imported for `agent_harness ext list` metadata, but the
   chat agent's live tool registry never heard from them.
   `multiling/model_tools.py::ensure_tools_discovered` now calls it
   for every *enabled* extension.
2. `register_cli_extensions()`/`dispatch_extension()` — implemented
   correctly, also never called from `multiligua_cli/main.py`. Every
   extension-provided CLI verb failed argparse's "invalid choice"
   before even reaching `handle_command`.
3. Once (2) was wired up, `agent_harness files browse` *still* silently
   launched the interactive chat TUI instead of dispatching — the
   dispatch map was keyed by `EXTENSION_NAME` (e.g. `"agent_harness-files"`),
   but every extension's `register_cli` registers a short, different
   argparse verb (`"files"`). `cmd in ext_map` never matched anything,
   silently falling through to whatever the top-level parser does
   with an unrecognized command. Now keyed by diffing
   `subparsers.choices` before/after each extension's own
   registration, so it always matches whatever verb(s) an extension
   actually adds.
4. `agent_harness ext enable`/`disable` mutated `ext.module.EXTENSION_ENABLED`
   in memory and never persisted it — meaningless across process
   boundaries, i.e. always, since every real `agent_harness` invocation is a
   fresh process. `agent_harness ext list` also read that same never-persisted
   attribute directly instead of the state-file-aware `ext.enabled`,
   so it couldn't even report the (also-broken) state correctly. Both
   now go through the already-existing, already-correct
   `set_extension_enabled()`.
5. `extensions/builtin/hello.py` (the reference example the
   `writing-agent_harness-skills`/extensions docs point to) had two real bugs
   of its own: a missing `EXTENSION_DESCRIPTION` that made
   `register_cli` throw on *every single CLI invocation* — that
   warning has been showing up quietly in the background this whole
   time — and an undefined `greeting` variable that would have crashed
   `handle_command` the moment anyone actually ran it.

New coverage: `tests/test_extension_tool_discovery.py`,
`tests/test_extension_cli_dispatch.py`.

### Fixed — `delegate_task`/`delegate_batch` were fabricating success
`tools/delegate_tool.py`'s `_create_subagent_handler` — the function
that actually runs a sub-agent's work — was a stub: it accepted an
`orchestrator_ref` parameter, never used it, and unconditionally
returned a fabricated `"status": "completed"` message without calling
a model or executing a single tool. Both `delegate_task` and
`delegate_batch` are already-registered, chat-agent-callable tools;
every call to either was reporting fabricated success. Now builds a
real, isolated `NexusOrchestrator` per sub-agent task (own session, own
toolset restriction — SubagentPool runs tasks concurrently on a thread
pool, so a *shared* mutable orchestrator would leak one task's toolset
restriction into another's) and actually drives a real conversation via
`.chat()`. New coverage: `tests/test_delegate_tool.py` (15 tests).

### Added — multi-agent coding crew (`agent_harness-multiagent` extension)
`extensions/builtin/multiagent_ext/` — Planner sub-agent breaks a goal
into subtasks, Coder sub-agents implement them in parallel, a Reviewer
sub-agent checks the combined result, flagged subtasks get
re-delegated with the review feedback attached (bounded rounds). Built
entirely on the now-real `delegate_tool.py` primitive above — this is
orchestration *policy*, not a second execution engine. Ships disabled
by default (`agent_harness ext add agent_harness-multiagent` or `agent_harness ext enable
agent_harness-multiagent` to opt in, same convention as adb/root — this spins
up multiple real, potentially costly AI calls once a provider is
configured).

Two ways to reach it: `agent_harness ext multiagent run "<goal>"` (standalone
CLI) and `multi_agent_code_task` (chat-agent tool, reachable
mid-conversation once enabled — reachable at all only because of the
extension-system fixes above). The coordination logic (planner/coder/
reviewer/bounded-revise-loop, tolerant JSON extraction from model
output) is fully unit-tested against an injected fake handler
(`tests/test_multiagent_ext.py`, 27 tests) — what isn't and can't be
tested from this sandbox is real output quality against an actual
model, since no AI provider is reachable here. Try it against a real,
configured provider before leaning on it for anything important.

### Changed — framework-side latency
Profiled the actual code path between "the agent decides to call the
AI provider" and "the HTTP request is dispatched" (not network RTT,
not the AI's own generation time) and fixed what was actually costing
something real:
- `multiling/orchestrator.py` `_run_with_upstream_ai` and
  `_stream_conversation` both opened a **fresh** `aiohttp.ClientSession()`
  — and therefore a fresh TCP/TLS handshake to the AI provider — on
  *every single round-trip* within one multi-turn tool-calling
  conversation, discarding connection reuse for no reason. Both now
  share one session (with a keep-alive connector) across a
  conversation's round-trips. Verified against a real local aiohttp
  server (not a mock of aiohttp itself) that multi-round tool-calling
  conversations now land on exactly one underlying connection, for
  both the streaming and non-streaming code paths
  (`tests/test_orchestrator_session_reuse.py`).
- `tools/registry.py` `dispatch()` re-ran `inspect.signature()` on
  every single tool call to figure out which kwargs a handler accepts
  — a static property of the function that never changes. Now cached
  per-handler, permanently (unlike the existing TTL'd `check_fn`
  cache, there's no reason for this one to expire).
- Measured end-to-end: a full `chat()` round-trip against a local
  server — event loop setup, connector, payload construction,
  dispatch, response parsing, everything this framework's own code is
  responsible for — averages **~1.3ms** (p95 ~1.6ms) in this
  environment, comfortably under the 10ms target. This number is
  local-server RTT, not a real provider's network latency or
  generation time — those aren't something the framework controls
  either way, which is the same distinction the ask itself drew.

A real bug-fix, safety-hardening, and consolidation pass — every item
below was reproduced, fixed, and covered by a passing regression test.
Full test suite: 1,005 passed, 9 skipped, 0 failed, 0 warnings (was
884 passed / 15 failed / several silent warnings at the start of this
pass).

### Added — a real skill ecosystem (`agent_harness skill ...`)
`agent_harness skill` was listed in `CORE_COMMANDS` and documented in the
README, but was never actually registered with argparse — running it
failed with "invalid choice: 'skill'". The LLM-facing implementation
behind it (`tools/skill_manager_tool.py`) pointed at a `skills/`
directory that didn't exist in the shipped package, so even the
in-chat version silently returned nothing. Built the real thing:

- `agent_harness/core_ops/skill_registry.py` — the shared engine: parse/validate
  `SKILL.md` frontmatter, install from a local path / git URL (`git
  clone`, tested against a real throwaway repo, not mocked) / raw URL /
  catalog name, a content-hashed local lockfile, a courtesy secret-leak
  scan for `agent_harness skill publish`, and a scaffold generator
  (`agent_harness skill new`).
- `multiligua_cli/skill_cli.py` + real argparse wiring for
  `agent_harness skill {list,view,search,install,new,remove,publish}` — fixes
  the missing-subcommand bug above.
- `skill_search` / `skill_install` / `skill_new` added as chat-agent
  tools too (`tools/skill_manager_tool.py`), same engine as the CLI.
  Installing always requires an explicit `confirm`/`--yes` after the
  content has been previewed — nothing is written silently, from either
  surface.
- Three real starter skills now ship in `skills/` (previously the
  directory didn't exist at all), plus `skills/catalog.json` as a
  working example catalog. Catalogs are just JSON — self-hostable via
  any raw file URL, no server required, matching the local-first shape
  of the rest of the project. See `ARCHITECTURE.md` and the
  `writing-agent_harness-skills` skill for the full design rationale, including
  why skills (markdown, not code) are a deliberately lower-risk
  ecosystem surface than extensions (executable Python).
- 33 new tests in `tests/test_skill_registry.py`.
- **Caught by actually building the wheel, not assumed**: `skills/`
  wasn't a Python package and wasn't in `pyproject.toml`'s package
  list, so none of the above shipped through `pip install agent_harness-beta`
  at all — only a git clone happened to have it. Verified by building
  a real wheel, installing it into a clean virtualenv, and confirming
  `agent_harness skill list` found the bundled skills there before calling
  this done. `skills/` is now a proper package
  (`skills/__init__.py` + `package-data` glob for `**/*.md`/`*.json`)
  and its old `.gitignore` entry (a leftover from when it was a
  project-local scratch dir, not shipped content) is gone.

### Added — `ARCHITECTURE.md`
A full physical reorg of the ~440-file tree (`agent_harness/` vs `multiling/`
vs `multiligua_cli/` vs `tools/` vs `agent/` vs `extensions/` vs
`skills/` vs `src/ai/`) was considered for this pass and deliberately
cut — the blast radius of updating every import in lockstep outweighed
the payoff given the time available, and a botched reorg is worse than
a confusing-but-documented one. Wrote up what's actually where and why
instead, including the "two tool registries" question every new
contributor asks first.

### Fixed — found while building the above
- `multiligua_cli/utils.py` `_escape_markup()` escaped both `[` and
  `]` before every rich-console print; rich only treats `\[` as a real
  escape sequence, so every message containing a `]` rendered with a
  visible stray backslash right before it (`[category]` came out as
  `[category\]`). Fixed to only escape `[`, which is what rich's own
  docs say to do. Existing tests in `test_cli_tui.py` had encoded the
  buggy behavior as correct (asserting `\]` was present); corrected
  them and added an end-to-end render check against real rich output.
- `agent_harness/core_ops/skill_registry.py` `parse_skill_md()` — an empty
  YAML frontmatter value (e.g. `author:` with nothing after it) parses
  to `None`, and `dict.get(key, default)` only falls back to `default`
  when the key is *missing*, not when it's present-but-`None` — so
  `str(frontmatter.get("author", ""))` was leaking the literal string
  `"None"` into manifests whenever a field was left blank. Fixed with
  `frontmatter.get(key) or default`.

### Fixed — real bugs, not just style
- `multiligua_cli/main.py` — two redundant local `import sys` statements
  inside `main()` shadowed the module-level import, making Python treat
  `sys` as local for the *entire* function and raising
  `UnboundLocalError` on `agent_harness help` and other early-exit branches.
- `multiligua_cli/features.py` — the entire EXPERIMENTAL surface
  (`Spinner`, `SilentFeatures`, `SessionManager`, `get_silent`, etc.)
  was deleted wholesale during the 0.18.3 cleanup while its tests were
  left in place; restored from git history and merged with the feature
  showcase. This also fixes `agent_harness features` itself, which crashed with
  `ImportError: cannot import name 'print_features'` on every call —
  a flagship, README-advertised command that had been broken since the
  0.18.3 release.
- `agent_harness/rust_bridge.py` — missing `Dict`/`Any` import broke module
  collection entirely (`NameError` at import time), taking the whole
  `test_rust_bridge.py` file down with it.
- `tests/test_twin.py` — `_rustc_available()` used
  `subprocess.run(["command", "-v", "rustc"], shell=True)`; with
  `shell=True` and a list, only the first list element becomes the
  shell command and the rest become the shell's own positional
  params — so it never actually checked for `rustc` and silently
  reported "available" on boxes without it, breaking a test that
  should have skipped instead of failing. Fixed with `shutil.which`,
  which also makes it work on Windows (the old code assumed `/bin/sh`).
- `tests/test_cli_gateway.py` — two `run_open()` tests mocked
  `asyncio.run` but not `start_api_server` itself, so the real
  coroutine object got constructed, handed to the mock, and never
  awaited or closed — an orphaned coroutine that surfaced as a
  `RuntimeWarning` in a *different*, unrelated test once garbage
  collection finally got to it.
- `tests/test_experimental_cli.py` — `TopLevelSubsystemsTests.setUp()`
  did `del sys.modules[name] for name starting with "agent_harness"` before
  every test, wiping out unrelated already-imported submodules (e.g.
  `agent_harness.core_ops.*`). The next `import agent_harness` doesn't reattach
  previously-cached submodules to the freshly created module object,
  splitting module identity and silently breaking
  `monkeypatch.setattr("agent_harness.x.y", ...)` in *other* test files for the
  rest of the session. Switched to `importlib.reload(agent_harness)`, which
  re-runs the same promotion logic in place without the collateral
  damage.
- `tests/test_all.py` — `TestModelCompare.test_comparison_result` called
  the `async def compare()` method without `await`, so the assertion
  was checking a coroutine object (always truthy) instead of the actual
  result, and the coroutine's body never ran at all (confirmed by the
  "never awaited" warning). The call also passed the wrong argument
  shapes entirely (pre-computed results instead of a prompt + model
  list). Rewritten to actually await it, use the real signature, mock
  out the network-calling `_query_model`, and assert on the real
  aggregation logic (`fastest`/`cheapest`/`longest`).
- `tests/test_cli_commands.py::TestGateway::test_gateway_no_subcommand_runs_foreground`
  — used to start a real, unmockable aiohttp server and block until an
  internal connection-retry loop eventually gave up (~60s), which is
  how it "passed": there was no deterministic way to stop it. Under
  extra load that retry window could blow past pytest's timeout and
  hang instead of failing. Rewritten to mock `gateway.runner.run_gateway`
  the same way `test_cli_gateway.py` already does it correctly — now
  deterministic and takes 0.02s instead of up to 120s+.
- `agent_harness/workers/network/network_workers.py` `PingWorker` — on
  `FileNotFoundError` (no `ping` binary — the common case on a bare
  Termux/Android install or a minimal container) it returned a bare
  `{"error": ...}` with no `host` key. Now falls back to a TCP-connect
  reachability probe and always returns a structured result.
- `tools/system_tools.py` `_handle_process_list` — the no-psutil
  fallback used `Path` without ever importing it (latent `NameError`
  on any box that actually needs the fallback), and only tried
  `/proc`, silently returning an empty list on Windows. Added a
  `tasklist /FO CSV` fallback for Windows and fixed the import.
- **Real, previously-undetected security gap**: `is_blocked_path()`'s
  original "resolve the symlink, compare to a fixed list" approach
  never actually caught `/dev/stdin`, `/dev/stdout`, `/dev/stderr`, or
  `/dev/fd/{0,1,2}` — those resolve through a *different, ephemeral*
  `/proc/<pid>/fd/pipe:[N]` target on every single call, which can
  never equal a fixed string. Fixed by checking the un-resolved,
  literal path first, and added a `stat()`-based check (character
  device / FIFO / socket) as depth-of-defense that doesn't depend on
  naming at all.

### Changed — Windows
- `tools/terminal_tool.py` `execute_command` unconditionally used
  `os.environ.get("SHELL", "/bin/sh")` as the subprocess `executable`
  — a path that doesn't exist on Windows, so every shell command
  issued by the chat agent failed there outright. Now platform-aware:
  lets `subprocess` use the platform default shell.
- `tools/terminal_tool.py`'s dangerous-command detector only recognised
  POSIX destructive commands; added the Windows/PowerShell equivalents
  (`format`, `diskpart`, `Remove-Item -Recurse -Force`, `reg delete`,
  `Stop-Computer`, `vssadmin`, etc.).
- `agent_harness/workers/system/system_workers.py` `ProcessWorker.kill_force`
  referenced `signal.SIGKILL`, which doesn't exist on Windows at all
  (silently swallowed by a bare `except Exception`, so force-kill just
  quietly did nothing there). Both `kill`/`kill_force` now use
  `taskkill` on Windows.

### Changed — tool consolidation ("merge tools")
- AgentHarness had two independent file-operation implementations: one behind
  the chat-agent's function-calling registry (`tools/file_tools.py`,
  safety-hardened) and one behind the MCP worker protocol
  (`agent_harness/workers/file/file_workers.py`, which had *none* of those
  guards — no blocked-path check, no size cap, no binary-file
  rejection). Extracted the safety logic into a single shared module,
  `agent_harness/core_ops/file_safety.py`, that both surfaces now import, so a
  fix only has to happen once and both entry points get the same
  protection level.

### Added — real multi-channel messaging (closing the gap with OpenClaw)
The README has claimed "Multi-Channel: Telegram, Discord, Slack
integration" since before this pass, but `gateway/channels.py` only
ever implemented `memory` and `http` adapters — any config naming
`telegram`/`discord`/`slack` silently fell into the "unknown adapter,
skip" branch. Implemented all three for real, against each platform's
public API docs, all outbound-only (no public webhook/open port
needed, matching the local-first shape of the rest of the gateway):
  - `gateway/channel_telegram.py` — long-polling Bot API.
  - `gateway/channel_discord.py` — Gateway websocket
    (Identify/Heartbeat/Dispatch) + REST for sending.
  - `gateway/channel_slack.py` — Socket Mode websocket + Web API.

Each ships with a unit-tested (mocked HTTP/websocket) implementation —
see each module's "Honesty note" docstring: none of the three have
been exercised against the real Telegram/Discord/Slack servers, since
this sandbox's outbound network is allowlisted to package registries
only. Smoke-test with real credentials before relying on these.

### Docs
- Doc/version drift: `agent_harness/_version.py`, `CHANGELOG.md`, `DEVELOPER.md`
  and the README install line had each drifted to a different version
  string (0.18.2 vs 0.18.3 in different places); re-synced to 0.18.4 as
  the single source of truth, per `tests/test_version_lock.py`.
- README numbers reconciled against what the code actually reports at
  runtime (`agent_harness doctor`, `WorkerRegistry().load()`) instead of
  hand-maintained counts that had drifted from reality.
- Fixed the README's own "Any MCP Client" code sample — it called
  `WorkerRegistry().list_all()`, a method that doesn't exist on that
  class.

## [0.18.3] - 2026-07-16 (shitshow cleanup)

### Added — Because Apparently We Didn't Have Enough Shit Already

#### Agent Framework (4 new tools)
- `react_agent` — ReAct agent with reasoning traces, because single-step thinking is for amateurs
- `planning_agent` — Task decomposition and planning, for when you need to think before you act
- `multi_agent_system` — Multi-agent orchestration, because one agent wasn't enough chaos
- `self_reflective_agent` — Self-reflective agent that learns from its own damn mistakes

#### RAG System (3 new tools)
- `vector_store` — In-memory vector store with cosine similarity search
- `text_splitter` — Text chunking for embedding, because context windows aren't infinite
- `rag_pipeline` — Complete RAG pipeline with hybrid search (keyword + semantic)

#### Workflow Engine (2 new tools)
- `workflow_engine` — DAG-based workflow execution with conditional branching, loops, retries
- `workflow_builder` — Fluent API for building workflows without losing your mind

#### Function Calling (3 new tools)
- `function_registry` — Function registry with OpenAI-compatible format
- `structured_output` — Pydantic-based structured output generation
- `tool_calling_agent` — Agent that automatically calls tools based on LLM responses

#### Streaming (4 new tools)
- `streaming_response` — Server-Sent Events (SSE) streaming
- `token_stream` — Token-by-token streaming with callbacks
- `chunk_aggregator` — Aggregate streaming chunks into structured output
- `stream_parser` — Parse and process streaming responses

#### Guardrails (3 new tools)
- `input_guardrail` — Input validation with injection detection, PII redaction, toxicity filtering
- `output_guardrail` — Output validation with hallucination detection, repetition checks
- `guardrails` — Combined input/output guardrail system

#### Caching (3 new tools)
- `semantic_cache` — Semantic cache using embedding similarity
- `tiered_cache` — Multi-level caching (L1 memory, L2 disk, L3 semantic)
- `cache_middleware` — Automatic caching middleware for API calls

#### Monitoring (4 new tools)
- `metrics_collector` — Real-time metrics collection (counters, gauges, histograms)
- `request_tracker` — Request latency tracking and token usage monitoring
- `alert_manager` — Alert rules with webhook notifications
- `health_checker` — System health monitoring and checks

#### CLI Commands (15 new commands)
- `agent_harness memory` — Memory system dashboard
- `agent_harness cost` — Cost tracking panel
- `agent_harness compare` — Multi-model comparison
- `agent_harness web` — Launch web UI
- `agent_harness features` — Feature showcase
- `agent_harness themes` — Theme management
- `agent_harness export` — Export memories
- `agent_harness import` — Import memories
- `agent_harness ext` — Extension management
- `agent_harness screen` — Screen operations
- `agent_harness update` — Update check
- `agent_harness skill` — Skill management
- `agent_harness bench` — Performance benchmark
- `agent_harness replay` — Session replay
- `agent_harness doctor` — System diagnosis

#### Worker Tools (69 new tools)
- **File workers (9):** file_read, file_write, file_copy, file_move, file_delete, file_search, file_diff, file_hash, file_stat
- **Network workers (9):** http_get, http_post, dns_lookup, ping, port_scan, whois, url_parse, ssl_lookup, tcp_socket
- **Crypto workers (9):** aes_encrypt, aes_decrypt, hash, hmac, pbkdf2, sign, verify, keygen, random_bytes
- **Math workers (7):** calculator, statistics, linear_algebra, calculus, fft, primes, geometry
- **Text workers (9):** text_process, summarize, translate, sentiment, keywords, entities, regex, diff, plagiarism
- **System workers (9):** system_info, process, disk, memory, cpu, network_interfaces, environment, uptime, file_descriptors
- **AI workers (10):** ai_chat, embeddings, classify, extract, ocr, speech_to_text, text_to_speech, summarize_long, question_answer, image_tools
- **Image tools (17):** format_convert, resize, thumbnail, metadata, compress, crop, rotate, filters, grayscale, histogram, batch_convert, watermark, collage, gif_create, exif_extract, color_analysis, image_compare
- **Audio tools (10):** format_convert, metadata, trim, merge, volume, silence_removal, speed_change, audio_extract, normalize, fade
- **Video tools (14):** format_convert, metadata, trim, merge, resize, frame_extract, video_from_frames, audio_extract, add_audio, compress, speed_change, thumbnail, video_to_gif, subtitles
- **PDF tools (11):** merge, split, extract_pages, extract_text, extract_images, add_watermark, rotate, compress, pdf_to_images, get_info
- **Data tools (17):** csv_read, csv_write, csv_to_json, csv_stats, json_read, json_write, json_query, json_merge, yaml_read, yaml_write, yaml_to_json, xml_read, xml_to_json, validate_json, validate_yaml, filter_rows, sort_data

#### Other Additions
- `SELLING_POINTS.md` — Complete list of 190+ selling points with competitor comparison
- `docs/README.md` — Comprehensive documentation
- `examples/README.md` — Example code directory
- `.github/workflows/ci.yml` — GitHub Actions CI/CD
- `scripts/benchmark.py` — Performance benchmark script
- `scripts/setup.sh` — Setup script
- `CHANGELOG.md` — This changelog
- `CONTRIBUTING.md` — Contributing guide

### Changed
- README rewritten with professional tone
- All documentation updated to match the new standard

### Fixed
- All known issues at time of release

### Removed
- Previous unprofessional tone from documentation

---

## [0.18.2] - 2026-07-15

### Added
- Memory system with multi-tier storage
- Cost tracking functionality
- Theme system with 8 themes
- Model comparison feature
- Web UI
- Export/import functionality

### Changed
- MCP server implementation
- README rewritten (again)

### Fixed
- Various bugs that we're not going to talk about

---

## [0.18.1] - 2026-07-14

### Added
- Initial MCP server support
- 70+ worker tools
- Basic CLI commands

### Changed
- Project structure reorganization

### Fixed
- Critical bugs that prevented the thing from running

---

## [0.18.0] - 2026-07-13

### Added
- Initial release
- 8 pillars of AgentHarness
- Basic worker framework
- CLI interface

### Notes
- This was the beginning of our collective breakdown
- We thought 50 tools was enough. We were wrong.

---

*Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).*
*This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), mostly.*
