# AgentHarness User Guide

Quick start: run `agent_harness` from a terminal and type a prompt. That's it.

## A taste

```python
import agent_harness

print(agent_harness.VERSION)           # "0.18.2"
print(agent_harness.detect_platform())  # 'linux', 'termux', 'windows', etc.

fs = agent_harness.FsIoWorker()
result = await fs.list_directory(path="/tmp")
```

## Where to dig next

- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — how the whole thing fits together
- **[DEVELOPER.md](../../DEVELOPER.md#5-worker-base-class)** — how to write a worker
- **`agent_harness --help`** — built-in command reference
- **`/help`** in the REPL — slash commands available at runtime

## The slash command cheat sheet

Hit `//` then type:

| Command | What it does |
|---------|-------------|
| `//help` | This list |
| `//tools` | All available tools (capped by platform) |
| `//status` | Runtime stats: provider · model · depth · cost |
| `//config` | Active config in full |
| `//memory` | Memory tier snapshot (L0/L1/L2) |
| `//doctor` | Self-check: config + tools + extensions |
| `//clear` | Wipe screen, repaint banner + status bar |
| `//history` | Last N turns in this session |
| `//setup` | Re-run the setup wizard (keeps current config) |
| `//provider [slug]` | Switch provider (interactive if no arg) |
| `//model [name]` | Switch model (interactive picker if no arg) |
| `//exit` | Quit (Ctrl-D also works) |

Anything **without** `//` at the start is sent directly to the AI. No tricks.