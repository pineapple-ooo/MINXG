"""
constants.py — Consolidated magic numbers and hardcoded values for AgentHarness v0.18.5.

Rule: only values used in 2+ distinct locations are included.
One-off values (e.g. CSS font-size, hex colours, single-use thresholds)
are left inline.
"""

from __future__ import annotations
from pathlib import Path

# ── Path constants ────────────────────────────────────────────────────────────
AgentHarness_HOME            = Path.home() / ".minxg"
AgentHarness_MEMORY_DIR      = AgentHarness_HOME / "memory"
AgentHarness_LOG_DIR         = AgentHarness_HOME / "logs"
AgentHarness_SESSIONS_DIR    = AgentHarness_HOME / "sessions"
AgentHarness_SESSION_FILE    = AgentHarness_HOME / "session_id"
AgentHarness_MEMORIES_FILE   = AgentHarness_HOME / "memory" / "memories.json"
AgentHarness_MEMDB_FILE      = AgentHarness_HOME / "memory.db"
AgentHarness_FEEDBACK_FILE   = AgentHarness_HOME / "feedback.jsonl"
AgentHarness_UPLOADS_DIR     = AgentHarness_HOME / "uploads"
AgentHarness_CHECKPOINT_FILE = AgentHarness_HOME / "comm_bus_checkpoint.json"

# IPC/SSL paths
IPC_SSL_CERT_DEFAULT  = "/tmp/ipc_server.crt"
IPC_SSL_KEY_DEFAULT   = "/tmp/ipc_server.key"

# ── Network / IPC ports ────────────────────────────────────────────────────────
IPC_DEFAULT_PORT      = 18999   # TCPIPCServer, HTTPGateway (ipc_server.py)
GATEWAY_DEFAULT_PORT  = 18080   # gateway CLI (main.py)
WORKERS_DEFAULT_PORT  = 19001   # workers CLI (main.py)
WEB_UI_DEFAULT_PORT   = 8080    # web UI (main.py, web_ui.py)
OLLAMA_DEFAULT_URL    = "http://localhost:11434/v1"

# ── Agent / LLM defaults ───────────────────────────────────────────────────────
DEFAULT_MAX_TOKENS    = 4096
DEFAULT_TEMPERATURE   = 0.7
DEFAULT_TOP_P         = 0.95
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_MEMORY_CAPACITY = 5000

# ── Context / buffer sizes ─────────────────────────────────────────────────────
DEFAULT_QUERY_LIMIT   = 20
DEFAULT_BUILD_MSGS_LIMIT = 30
DEFAULT_HISTORY_LIMIT  = 30
DEFAULT_MESSAGE_LIMIT  = 50
DEFAULT_MAX_ROUNDS     = 100
DEFAULT_MAX_CYCLES     = 100

# ── Cache / queue sizes ────────────────────────────────────────────────────────
DEFAULT_CACHE_SIZE    = 1000
MAX_SESSIONS          = DEFAULT_CACHE_SIZE   # alias — same value used in analytics
DEFAULT_TTL_SECONDS   = 300.0
DEFAULT_LRU_CAPACITY  = 128

# ── Rate / throttling ─────────────────────────────────────────────────────────
IPC_RATE_WINDOW       = 60
IPC_RATE_LIMIT        = 100
MAX_CONNECTIONS       = 1000

# ── Memory / analytics ─────────────────────────────────────────────────────────
DEFAULT_MAX_POINTS     = 10000
ANALYTICS_EVENT_LIMIT  = 50000
HEALTH_HISTORY_LIMIT   = 1000
HISTOGRAM_TRIM_THRESHOLD = 10000

# ── Token / auth ──────────────────────────────────────────────────────────────
TOKEN_EXPIRY_SECONDS   = 3600
SESSION_MAX_AGE_SECONDS = 86400

# ── IPC internals ─────────────────────────────────────────────────────────────
READ_BUFFER_SIZE      = 65536
MAX_REQUEST_SIZE       = 10 * 1024 * 1024  # 10 MB
IPC_BACKLOG            = 128
HTTP_BACKLOG           = 1024

# ── Terminal / display ─────────────────────────────────────────────────────────
DEFAULT_TERMINAL_WIDTH = 80
WIZARD_BAR_WIDTH       = 32
SWIPE_DEFAULT_PERCENT  = 50

# ── Time shorthands ────────────────────────────────────────────────────────────
HALF_MINUTE            = 30
ONE_MINUTE             = 60
ONE_HOUR               = 3600
ONE_DAY                = 86400
MINUTE                 = ONE_MINUTE    # alias for readability
HOUR                   = ONE_HOUR      # alias
DAY                    = ONE_DAY       # alias
MAX_CACHE_ENTRIES      = DEFAULT_CACHE_SIZE  # alias

# ── Semantic timeouts ─────────────────────────────────────────────────────────
TIMEOUT_HTTP           = 120.0
TIMEOUT_STREAMING      = 180.0
TIMEOUT_AIOHTTP_TOTAL  = 300.0    # aiohttp single-request ceiling
TIMEOUT_AIOHTTP_KEEPALIVE = 30    # aiohttp keep-alive socket

# ── Runtime / tool execution ─────────────────────────────────────────────────
TIMEOUT_SUBPROCESS_QUICK   = 5.0      # version checks, tiny commands
TIMEOUT_SUBPROCESS_NORMAL  = 10.0     # single-shot runtime (cpp/r/julia/go/wasm)
TIMEOUT_SUBPROCESS_TOOL    = 30.0     # ADB, bridge scripts
TIMEOUT_SUBPROCESS_BUILD   = 60.0     # compile/test chains
TIMEOUT_SUBPROCESS_HEAVY   = 180.0    # BAP/OMIT-opt binary tooling
TIMEOUT_SUBPROCESS_INSTALL = 300.0    # installer long-running ops
TIMEOUT_HTTP_SKILL_FETCH   = 10.0     # skill registry urlopen

# ── Memory / cache ─────────────────────────────────────────────────────────────
MAX_MEMORY_FACTS       = 1000
MAX_TOOL_CALLS_PER_TURN = 30
DEFAULT_BATCH_SIZE     = 300
MAX_CONTEXT_CHARS      = 32000   # council.py context compression threshold

# ── Misc ───────────────────────────────────────────────────────────────────────
KiB                    = 1024
MiB                    = 1024 * KiB
UUID_PREFIX_LEN        = 10
AGENT_ID_PREFIX_LEN    = 12
AgentHarness_IPC_VERSION      = "0.17.1"