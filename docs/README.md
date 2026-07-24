# MINXG Documentation

Welcome to the MINXG documentation. This directory contains comprehensive guides for installation, usage, and development.

## Quick Links

- [Installation Guide](installation.md)
- [Usage Guide](usage.md)
- [CLI Reference](cli.md)
- [API Reference](api.md)
- [Contributing](contributing.md)
- [Changelog](changelog.md)

## Overview

MINXG is an AI orchestration platform designed to manage, deploy, and scale AI agent systems. It provides:

- Multi-language runtime support (Python, Julia, Datalog, Wasm)
- Unified CLI with modern TUI
- Tool calling and function calling framework
- Context compression for multi-agent scenarios
- Gateway for channel integrations (Telegram, Discord, Slack)
- Extension system for custom tools and capabilities

## Requirements

- Python 3.13+ or 3.14
- pip or uv package manager
- Optional: Rust toolchain for native extensions
- Optional: Julia runtime for scientific computing modules

## Installation

```bash
pip install minxg
```

Or from source:

```bash
git clone https://github.com/pineapple-ooo/MINXG.git
cd MINXG
pip install -e .
```

## Getting Started

After installation, verify your setup:

```bash
minxg --version
minxg doctor
```

Then start the interactive chat:

```bash
minxg chat
```

## Documentation Structure

- **installation.md** - Detailed installation instructions for different platforms
- **usage.md** - Getting started guide and basic usage patterns
- **cli.md** - Complete CLI reference with all commands and options
- **api.md** - Python API reference for developers
- **contributing.md** - Development setup, testing, and contribution guidelines
- **changelog.md** - Version history and release notes
