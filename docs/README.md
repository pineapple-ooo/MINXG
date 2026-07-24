# AgentHarness Documentation

Comprehensive documentation for AgentHarness — Enterprise AI Orchestration Platform.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [CLI Reference](#cli-reference)
5. [MCP Integration](#mcp-integration)
6. [Workers](#workers)
7. [Memory System](#memory-system)
8. [API Reference](#api-reference)
9. [Troubleshooting](#troubleshooting)

## Getting Started

AgentHarness is an enterprise-grade AI orchestration platform with:

- **70+ Workers** — File I/O, network, crypto, math, text, data, system, AI
- **32+ AI Providers** — OpenAI, Anthropic, Google, DeepSeek, and more
- **MCP Server** — Expose workers as MCP tools for Claude Code, Cursor, ChatGPT
- **Web UI** — Full-featured browser interface
- **Memory System** — Multi-tier memory with compression and visualization
- **Cost Tracking** — Real-time token usage and cost estimation
- **Theme System** — 8 built-in themes
- **Model Comparison** — Multi-model side-by-side comparison

### Quick Start

```bash
# Install
pip install agent_harness-beta

# Setup
agent_harness setup

# Start chat
agent_harness

# Start MCP server
python -m agent_harness.mcp_server

# Start web UI
agent_harness web
```

## Installation

### From PyPI

```bash
pip install agent_harness-beta
```

### From Source

```bash
git clone https://github.com/pineapple-ooo/AgentHarness-Beta
cd AgentHarness-Beta
pip install -e .
```

### Dependencies

- Python 3.8+
- fastmcp (optional, for MCP server)
- rich (optional, for TUI)
- PIL/Pillow (optional, for image tools)
- pypdf (optional, for PDF tools)

## Configuration

Configuration is stored in `~/.agent_harness/config.yaml`.

```yaml
ai:
  provider: openai
  model: gpt-4o
  base_url: https://api.openai.com/v1
  api_key: sk-...
  temperature: 0.3
  max_tokens: 4096

gateway:
  port: 18080
  api_key: your-gateway-key

memory:
  enabled: true
  auto_learn: true
  decay_days: 90

workers:
  port: 19001
```

## CLI Reference

### Core Commands

| Command | Description |
|---------|-------------|
| `agent_harness` | Start TUI chat |
| `agent_harness setup` | Run setup wizard |
| `agent_harness config` | Show configuration |
| `agent_harness status` | Show system status |
| `agent_harness tools` | List available tools |
| `agent_harness model [name]` | Set/view model |
| `agent_harness api <url>` | Set API base URL |
| `agent_harness key <key>` | Set API key |
| `agent_harness lang [code]` | Switch language |
| `agent_harness gateway` | API gateway |
| `agent_harness doctor` | Self-diagnostic |
| `agent_harness memory` | Memory dashboard |
| `agent_harness cost` | Cost tracking |
| `agent_harness compare` | Model comparison |
| `agent_harness web` | Start web UI |
| `agent_harness features` | Feature showcase |
| `agent_harness themes` | Theme management |
| `agent_harness export` | Export memories |
| `agent_harness import` | Import memories |

### MCP Integration

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "agent_harness": {
      "command": "python",
      "args": ["-m", "agent_harness.mcp_server"],
      "env": {
        "AgentHarness_HOME": "/path/to/.agent_harness"
      }
    }
  }
}
```

## Workers

AgentHarness has 70+ workers organized into categories:

### File I/O
- `fs_io` — Read/write files
- `fs_copy` — Copy/move files
- `fs_search` — Search files
- `fs_diff` — File comparison

### Network
- `network` — HTTP requests
- `dns` — DNS lookup
- `ping` — Network ping
- `whois` — WHOIS lookup

### Crypto
- `encrypt` — AES encryption
- `decrypt` — AES decryption
- `hash` — Hash functions
- `sign` — Digital signatures

### Math
- `calculator` — Basic math
- `statistics` — Statistical analysis
- `linear_algebra` — Matrix operations
- `calculus` — Derivatives/integrals

### Text
- `text_process` — Text processing
- `summarize` — Summarization
- `translate` — Translation
- `sentiment` — Sentiment analysis

### Data
- `csv_tools` — CSV processing
- `json_tools` — JSON processing
- `xml_tools` — XML processing
- `yaml_tools` — YAML processing

### System
- `system_info` — System information
- `process` — Process management
- `disk` — Disk usage
- `memory_info` — Memory usage

### AI
- `chat` — AI chat
- `embeddings` — Embedding generation
- `classify` — Text classification
- `extract` — Information extraction

## Memory System

The memory system provides persistent storage for conversations and learned patterns.

### Memory Tiers

- **Working** — Current session only
- **Short-term** — Last 24 hours
- **Long-term** — Persistent storage

### Memory Categories

- `fact` — Factual information
- `preference` — User preferences
- `summary` — Conversation summaries
- `conversation` — Raw conversation turns
- `skill` — Learned skills/patterns
- `context` — Contextual information

### Usage

```python
from multiligua_cli.memory_system import get_memory_engine

engine = get_memory_engine()

# Add memory
engine.add(
    content="User prefers Python over JavaScript",
    category="preference",
    tier="long",
    tags=["coding", "language"],
    importance=0.8,
)

# Search
results = engine.search("python", limit=10)

# Stats
stats = engine.get_stats()

# Export
json_data = engine.export(format="json")
md_data = engine.export(format="markdown")
```

## API Reference

AgentHarness provides an OpenAI-compatible API at `http://localhost:18080/v1`.

### Endpoints

- `GET /v1/models` — List available models
- `POST /v1/chat/completions` — Chat completion
- `POST /v1/embeddings` — Generate embeddings

### Example

```bash
curl http://localhost:18080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Troubleshooting

### Common Issues

**Issue: `agent_harness` command not found**

```bash
# Ensure pip bin directory is in PATH
export PATH="$HOME/.local/bin:$PATH"
```

**Issue: MCP server not connecting**

```bash
# Check if fastmcp is installed
pip install fastmcp

# Test MCP server
python -m agent_harness.mcp_server
```

**Issue: Memory not persisting**

```bash
# Check memory directory
ls -la ~/.agent_harness/memory/

# Ensure proper permissions
chmod 755 ~/.agent_harness/memory/
```

### Getting Help

- GitHub Issues: https://github.com/pineapple-ooo/AgentHarness-Beta/issues
- Documentation: https://agent_harness.nousresearch.com/docs
