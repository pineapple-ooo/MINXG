"""Web UI Server - premium browser-based interface for AgentHarness.

Provides:
  - Full chat interface with streaming responses
  - Tool call visualization with diff rendering
  - Multi-agent company dashboard with live status
  - Communication bus monitoring
  - Session memory viewer

Runs a local HTTP + WebSocket server. Access via browser at:
  http://localhost:PORT
"""
import json
import logging
from typing import Any, Dict, Optional

# Keep in sync with multiling/constants.py WEB_UI_DEFAULT_PORT
_WEB_PORT = 8080

logger = logging.getLogger(__name__)

# Pure SVG icons - no emoji, crisp on all devices
ICONS = {
    'chat':    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    'company': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>',
    'memory':  '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a4 4 0 0 0-4 4v14a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2V6a4 4 0 0 0-4-4z"/><path d="M8 10h8M8 14h8"/></svg>',
    'settings':'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    'send':    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
    'menu':    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>',
    'close':   '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
}


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>AgentHarness</title>
<style>
:root {
  --bg: #ffffff;
  --bg-elev: #f8f9fb;
  --bg-card: #ffffff;
  --border: #eaecf0;
  --text: #1a1d23;
  --text-dim: #8a8f9c;
  --text-soft: #6b7080;
  --accent: #2563eb;
  --accent-light: #eff4ff;
  --accent-glow: rgba(37, 99, 235, 0.08);
  --green: #16a34a;
  --green-light: #f0fdf4;
  --red: #dc2626;
  --red-light: #fef2f2;
  --yellow: #ca8a04;
  --yellow-light: #fffbeb;
  --radius: 14px;
  --mono: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.03);
  --shadow-md: 0 4px 12px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.04);
}

* { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { height: 100%; }
body {
  background: var(--bg-elev);
  color: var(--text);
  font-family: var(--sans);
  overflow: hidden;
  font-size: 15px;
  -webkit-font-smoothing: antialiased;
}

/* ── Layout ── */
.app {
  position: fixed;
  inset: 0;
  display: flex;
  width: 100%;
}

/* ── Sidebar (off-canvas, push layout) ── */
.sidebar {
  width: 0;
  min-width: 0;
  overflow: hidden;
  background: var(--bg);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transition: width 0.3s cubic-bezier(0.4,0,0.2,1), min-width 0.3s cubic-bezier(0.4,0,0.2,1);
  white-space: nowrap;
  position: relative;
}

.sidebar.open {
  width: 240px;
  min-width: 240px;
}

/* Sidebar close button (fixed position so it never moves with the pushed main panel) */
.sidebar-close {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 5;
  width: 30px;
  height: 30px;
  border: none;
  background: transparent;
  color: var(--text-soft);
  cursor: pointer;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s, background 0.12s;
}
.sidebar.open .sidebar-close { opacity: 1; pointer-events: auto; }
.sidebar-close:hover { background: var(--bg-elev); color: var(--text); }

.sidebar-inner {
  width: 240px;
  min-width: 240px;
  display: flex;
  flex-direction: column;
  height: 100%;
  padding-right: 36px; /* room for the close button */
}

.sidebar-header {
  padding: 24px 20px 16px;
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--text);
  color: var(--bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.5px;
}

.brand-text h1 {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.3px;
}

.brand-text span {
  font-size: 11px;
  color: var(--text-dim);
}

.sidebar-nav {
  flex: 1;
  padding: 10px 12px;
  overflow-y: auto;
}

/* New-chat button */
.new-chat-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  margin: 6px 0 10px;
  border-radius: 10px;
  cursor: pointer;
  color: var(--text);
  font-size: 14px;
  font-weight: 500;
  transition: background 0.15s;
}
.new-chat-btn:hover { background: var(--bg-elev); }
.new-chat-btn svg { flex-shrink: 0; }

/* Section titles in sidebar */
.nav-section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 10px 4px 4px;
}

/* Session list */
.session-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--text-soft);
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background 0.12s, color 0.12s;
}
.session-item:hover { background: var(--bg-elev); color: var(--text); }
.session-item.active { background: var(--bg-elev); color: var(--text); font-weight: 500; }

.session-item .del-btn {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-dim);
  cursor: pointer;
  opacity: 0;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.12s;
}
.session-item:hover .del-btn { opacity: 1; }
.session-item .del-btn:hover { background: var(--red-light); color: var(--red); }

/* Sidebar footer (current model) */
.sidebar-footer {
  padding: 12px 20px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.4;
}
.sidebar-footer strong { color: var(--text); font-weight: 500; }

.nav-item { display: none; }

/* ── Main area (column: topbar, scrollable views, input) ── */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--bg);
  position: relative;
  overflow: hidden;
}

.topbar {
  height: 52px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  background: var(--bg);
  flex-shrink: 0;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.menu-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-dim);
  padding: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: background 0.15s;
}

.menu-btn:hover { background: var(--bg-elev); }

.menu-btn svg {
  width: 22px;
  height: 22px;
}

.topbar h2 {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.2px;
}

/* ── Content views ── */
.views {
  flex: 1;
  overflow: hidden;
  min-height: 0;
  position: relative;
}

.view { display: none; }
.view.active { display: flex; flex-direction: column; min-height: 0; }

/* ── Chat scroll area ── */
.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
}

/* ── Chat bubbles (auto-grow with content) ── */
.msg {
  margin-bottom: 14px;
  display: flex;
  flex-direction: column;
}

.msg.user { align-items: flex-end; }
.msg.assistant { align-items: flex-start; }

.msg-meta {
  font-size: 10px;
  color: var(--text-dim);
  margin-bottom: 4px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  padding: 0 12px;
}

.msg-bubble {
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  max-width: 82%;
  width: fit-content;
  min-width: 30px;
  max-height: 60vh;
  overflow-y: auto;
  position: relative;
  transition: background 0.15s, opacity 0.25s, box-shadow 0.2s;
}

/* User bubble: light blue, auto-grow */
.msg.user .msg-bubble {
  background: #93c5fd;  /* light blue #3b82f6 / 45% */
  color: #1e293b;
  border-bottom-right-radius: 4px;
}

/* AI bubble: no background, plain text (reference UI style) */
.msg.assistant .msg-bubble {
  background: transparent;
  border: none;
  color: var(--text);
  padding: 6px 4px;
}

/* Stale assistant message (invalidated by edit-resend) — faded */
.msg.assistant.stale .msg-bubble {
  opacity: 0.35;
}
.msg.stale {
  opacity: 0.5;
}

/* ── "Modify Input" card (slide up from bottom, like reference UI) ── */
.edit-card {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--bg);
  border-top: 1px solid var(--border);
  border-radius: 16px 16px 0 0;
  padding: 18px 18px 14px;
  box-shadow: 0 -4px 20px rgba(0,0,0,0.08);
  z-index: 200;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.edit-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.edit-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.edit-close {
  background: none;
  border: none;
  font-size: 22px;
  color: var(--text-dim);
  cursor: pointer;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
}
.edit-close:hover { background: var(--bg-elev); }

.edit-textarea {
  width: 100%;
  min-height: 80px;
  max-height: 140px;
  padding: 12px 14px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--text);
  font-size: 14px;
  font-family: var(--sans);
  resize: none;
  outline: none;
  line-height: 1.5;
}
.edit-textarea:focus {
  border-color: var(--accent);
}

.edit-card-footer {
  display: flex;
  justify-content: flex-end;
}

/* ── Welcome screen ── */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  color: var(--text-dim);
}
.welcome-sub {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text-dim);
}

/* Tool calls */
.tool-call {
  margin: 10px 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--accent-light);
  border-left: 3px solid var(--accent);
  font-size: 12px;
  font-family: var(--mono);
}

.tool-name {
  color: var(--accent);
  font-weight: 600;
  font-size: 12px;
}

.tool-args {
  color: var(--text-soft);
  margin-left: 6px;
}

.tool-result {
  margin: 6px 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--green-light);
  border-left: 3px solid var(--green);
  font-size: 11px;
  font-family: var(--mono);
  max-height: 250px;
  overflow-y: auto;
}

.diff-add { color: var(--green); }
.diff-del { color: var(--red); }
.diff-hunk { color: var(--accent); }

/* ── Input ── */
.input-area {
  padding: 14px 20px;
  border-top: 1px solid var(--border);
  background: var(--bg);
  display: flex !important;
  gap: 10px;
  align-items: center;
  flex-shrink: 0;
  z-index: 10;
  position: relative;
  min-height: 80px;
}

.input-box {
  flex: 1;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 12px 16px;
  color: var(--text);
  font-size: 14px;
  font-family: var(--sans);
  resize: none;
  min-height: 46px;
  max-height: 140px;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
  line-height: 1.5;
}

.input-box:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}

.input-box::placeholder { color: var(--text-dim); }

.send-btn {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  border: none;
  background: var(--accent);
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
  flex-shrink: 0;
}

.send-btn:hover { background: #1d4ed8; }
.send-btn:active { transform: scale(0.95); }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; background: var(--text-dim); }

/* ── Dashboard views ── */
.dashboard {
  padding: 24px 20px;
  max-width: 800px;
  margin: 0 auto;
  width: 100%;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 14px;
}

.dash-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 28px;
}

.group-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  box-shadow: var(--shadow-sm);
}

.group-name {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}

.group-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--text-soft);
}

.group-stats span { display: flex; align-items: center; gap: 5px; }

.stat-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.stat-dot.working { background: var(--green); }
.stat-dot.idle { background: var(--yellow); }
.stat-dot.dead { background: var(--red); }

/* Bus messages */
.bus-messages {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.bus-msg {
  padding: 10px 12px;
  background: var(--bg-elev);
  border-radius: 10px;
  font-size: 12px;
  font-family: var(--mono);
  border-left: 3px solid var(--accent);
}

.bus-msg .sender {
  color: var(--accent);
  font-weight: 600;
  margin-right: 6px;
}

.bus-msg .type {
  color: var(--text-dim);
  font-size: 10px;
  margin-right: 6px;
  text-transform: uppercase;
}

/* ── Typing indicator ── */
.typing {
  display: inline-flex;
  gap: 4px;
  padding: 6px 0;
}

.typing span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-dim);
  animation: typing 1.4s infinite ease-in-out;
}

.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: scale(0.8); }
  30% { opacity: 1; transform: scale(1); }
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-dim); }

/* ── Welcome message ── */
.welcome {
  text-align: center;
  padding: 40px 20px;
}

.welcome-mark {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 22px;
  margin: 0 auto 16px;
  color: var(--text);
}

.welcome h3 {
  font-size: 17px;
  font-weight: 600;
  margin-bottom: 8px;
}

.welcome p {
  font-size: 13px;
  color: var(--text-dim);
  max-width: 320px;
  margin: 0 auto;
  line-height: 1.6;
}

.welcome-cmd {
  display: inline-block;
  margin-top: 16px;
  padding: 6px 12px;
  background: var(--accent-light);
  color: var(--accent);
  border-radius: 8px;
  font-size: 12px;
  font-family: var(--mono);
  font-weight: 600;
}

/* ── Mobile ── */
@media (max-width: 700px) {
  .menu-btn { display: flex; }

  .chat-area { padding: 14px; }
  .input-area { padding: 10px 12px; }
  .input-box { min-height: 42px; padding: 10px 14px; }
  .send-btn { width: 42px; height: 42px; }
  .msg-bubble { max-width: 92%; font-size: 14px; }
  .dash-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
</head>
<body>
  <div class="app">
    <!-- Sidebar (push layout) -->
    <aside class="sidebar" id="sidebar">
          <div class="sidebar-inner">
            <div class="sidebar-header">
              <div class="brand">
                <div class="brand-mark">M</div>
                <div class="brand-text">
                  <h1>AgentHarness</h1>
                  <span>AI</span>
                </div>
              </div>
            </div>
            <div class="new-chat-btn" id="new-chat-btn" title="New chat">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              <span>New chat</span>
            </div>
            <div class="sidebar-nav">
              <div class="nav-section-title">Chats</div>
              <div class="session-list" id="session-list"></div>
            </div>
            <div class="sidebar-footer">
              <span id="model-label">...</span>
            </div>
          </div>
        </aside>

    <!-- Main -->
    <div class="main" id="main">
      <div class="topbar">
        <div class="topbar-left">
          <button class="menu-btn" id="menu-btn">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="3" y1="7" x2="20" y2="7"/>
              <line x1="3" y1="15" x2="14" y2="15"/>
            </svg>
          </button>
        </div>
      </div>

      <div class="views">
        <!-- Chat view -->
        <div class="view active" id="chat-view">
          <div class="chat-messages">
            <div class="welcome">
              <div class="welcome-mark">M</div>
              <h3>Welcome to AgentHarness</h3>
              <p>Send a message to start, or use /plan for multi-agent collaboration.</p>
              <span class="welcome-cmd">/plan build something</span>
            </div>
          </div>
        </div>

        <!-- Agents view -->
        <div class="view dashboard" id="agents-view">
          <div class="section-title">Agent Groups</div>
          <div class="dash-grid" id="group-grid"></div>
          <div class="section-title">Communication Bus</div>
          <div class="bus-messages" id="bus-messages"></div>
        </div>

        <!-- Memory view -->
        <div class="view dashboard" id="memory-view">
          <div class="section-title">Memory Stats</div>
          <div id="memory-stats"></div>
          <div class="section-title" style="margin-top:24px">Recent Turns</div>
          <div id="memory-content" style="font-family:var(--mono);font-size:12px;"></div>
        </div>

        <!-- Settings view -->
        <div class="view dashboard" id="settings-view">
          <div class="section-title">Configuration</div>
          <div id="settings-content"></div>
        </div>
      </div>

      <!-- Input (only visible in chat view) -->
      <div class="input-area" id="input-area">
        <textarea class="input-box" id="input" placeholder="Send a message..." rows="1"></textarea>
        <button class="send-btn" id="send">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          </button>
      </div>
    </div>
  </div>

<script>
  // ── WebSocket ──
  let ws = null;
  try {
    ws = new WebSocket('ws://' + location.host + '/ws');
    ws.onclose = () => {};
  } catch(e) {
    ws = null;
  }
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send');
  const chatView = document.getElementById('chat-view');
  const chatMessages = document.querySelector('#chat-view .chat-messages');
  const sidebar = document.getElementById('sidebar');
  const modelLabel = document.getElementById('model-label');

  // ════════════════════════════════════════════════════════════
  //  Multi-session memory (localStorage — survives restarts)
  // ════════════════════════════════════════════════════════════
  const STORE_KEY = 'minxg-sessions';
  function loadStore() {
    try { const s = localStorage.getItem(STORE_KEY); if (s) return JSON.parse(s); } catch {}
    return { active: null, sessions: {} };
  }
  function saveStore() { localStorage.setItem(STORE_KEY, JSON.stringify(store)); }
  function genId() { return 'sess_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

  let store = loadStore();
  if (!store.sessions[store.active]) {
    store.active = genId();
    store.sessions[store.active] = { history: [] };
    saveStore();
  }

  function curHist() {
    return store.sessions[store.active] ? (store.sessions[store.active].history || []) : [];
  }
  function deriveTitle(h) {
    for (const m of h) { if (m.role === 'user' && m.content) return m.content.slice(0, 40); }
    return 'Untitled';
  }
  function refreshTitle() {
    store.sessions[store.active].title = deriveTitle(curHist());
    saveStore();
    renderSessionList();
  }

  // ── Session list UI ──
  function renderSessionList() {
    const list = document.getElementById('session-list');
    list.innerHTML = '';
    const entries = Object.entries(store.sessions).sort((a, b) => {
      const ta = a[1] && a[1]._updated ? a[1]._updated : 0;
      const tb = b[1] && b[1]._updated ? b[1]._updated : 0;
      return tb - ta;
    });
    for (const [id, s] of entries) {
      const item = document.createElement('div');
      item.className = 'session-item' + (id === store.active ? ' active' : '');
      const title = s.title || deriveTitle(s.history || []);
      const titleEl = document.createElement('span');
      titleEl.textContent = title;
      titleEl.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      item.appendChild(titleEl);
      const del = document.createElement('button');
      del.className = 'del-btn';
      del.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
      del.onclick = (e) => { e.stopPropagation(); deleteSession(id); };
      item.appendChild(del);
      item.onclick = () => switchSession(id);
      list.appendChild(item);
    }
  }

  function switchSession(id) {
    if (isStreaming) return;
    store.active = id;
    saveStore();
    renderSessionList();
    renderHistory();
    closeSidebar();
  }

  function newChat() {
    if (isStreaming) return;
    store.active = genId();
    store.sessions[store.active] = { history: [] };
    saveStore();
    renderSessionList();
    renderHistory();
    closeSidebar();
  }

  function deleteSession(id) {
    if (Object.keys(store.sessions).length <= 1) return;
    delete store.sessions[id];
    if (store.active === id) {
      store.active = Object.keys(store.sessions)[0];
    }
    saveStore();
    renderSessionList();
    renderHistory();
  }

  // ── State ──
  let isStreaming = false;
  let currentMsgEl = null;

  // ── Helpers ──
  function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  // ── Render chat bubbles ──
  function addMsg(role, content) {
    const welcome = chatView.querySelector('.welcome');
    if (welcome) welcome.remove();
    const wrap = document.createElement('div');
    wrap.className = 'msg ' + role;
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = role === 'user' ? 'You' : 'AgentHarness';
    wrap.appendChild(meta);
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.textContent = content;
    wrap.appendChild(bubble);
    chatMessages.appendChild(wrap);
    scrollChat();
    return wrap;
  }

  function renderHistory() {
    chatMessages.innerHTML = '';
    const h = curHist();
    if (h.length === 0) {
      const welcome = document.createElement('div');
      welcome.className = 'welcome';
      welcome.innerHTML = '<div class="welcome-mark">M</div><h3>Welcome to AgentHarness</h3><p>Send a message to start, or use /plan for multi-agent collaboration.</p><span class="welcome-cmd">/plan build something</span>';
      chatMessages.appendChild(welcome);
      return;
    }
    h.forEach((m, i) => {
      const wrap = document.createElement('div');
      wrap.className = 'msg ' + m.role + (m.stale ? ' stale' : '');
      wrap.dataset.histIdx = i;
      const meta = document.createElement('div');
      meta.className = 'msg-meta';
      meta.textContent = m.role === 'user' ? 'You' : 'AgentHarness';
      wrap.appendChild(meta);
      const bubble = document.createElement('div');
      bubble.className = 'msg-bubble';
      bubble.textContent = m.content || '';
      wrap.appendChild(bubble);
      chatMessages.appendChild(wrap);
    });
    scrollChat();
  }

  function dismissWelcome() {
    const w = chatView.querySelector('.welcome');
    if (w) w.remove();
  }

  function scrollChat() { chatMessages.scrollTop = chatMessages.scrollHeight; }

  function appendTool(msgEl, name, args) {
    const el = document.createElement('div');
    el.className = 'tool-call';
    el.innerHTML = '<span class="tool-name">[ ' + esc(name) + ' ]</span><span class="tool-args">' + esc(JSON.stringify(args).slice(0, 80)) + '</span>';
    msgEl.querySelector('.msg-bubble').appendChild(el);
    scrollChat();
  }

  function appendResult(msgEl, name, result, diff) {
    const el = document.createElement('div');
    el.className = 'tool-result';
    if (diff) {
      el.innerHTML = result.split('\n').map(l => {
        if (l.startsWith('+') && !l.startsWith('+++')) return '<div class="diff-add">' + esc(l) + '</div>';
        if (l.startsWith('-') && !l.startsWith('---')) return '<div class="diff-del">' + esc(l) + '</div>';
        if (l.startsWith('@@')) return '<div class="diff-hunk">' + esc(l) + '</div>';
        return '<div>' + esc(l) + '</div>';
      }).join('');
    } else {
      el.textContent = (result||'').slice(0, 500);
    }
    msgEl.querySelector('.msg-bubble').appendChild(el);
    scrollChat();
  }

  // ════════════════════════════════════════════════════════════
  //  Edit-resend ("Modify Input" panel)
  // ════════════════════════════════════════════════════════════
  function openEditPanel(targetIdx) {
    const history = curHist();
    const original = (history[targetIdx] || {}).content || '';
    const card = document.createElement('div');
    card.className = 'edit-card';
    card.id = 'edit-card';
    card.innerHTML =
      '<div class="edit-card-header">' +
        '<span class="edit-card-title">Modify Input</span>' +
        '<button class="edit-close" id="edit-close">&times;</button>' +
      '</div>' +
      '<textarea class="edit-textarea" id="edit-textarea">' + esc(original) + '</textarea>' +
      '<div class="edit-card-footer">' +
        '<button class="send-btn" id="edit-send">' +
          '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>' +
        '</button>' +
      '</div>';
    document.body.appendChild(card);
    const ta = card.querySelector('#edit-textarea');
    ta.focus();
    ta.selectionStart = ta.selectionEnd = ta.value.length;
    function sendEdit() {
      const newText = ta.value.trim();
      if (!newText) return;
      card.remove();
      isStreaming = true;
      sendBtn.disabled = true;
      // Update DOM
      chatMessages.querySelectorAll('.msg.user').forEach(el => {
        if (parseInt(el.dataset.histIdx, 10) === targetIdx) {
          el.querySelector('.msg-bubble').textContent = newText;
        }
      });
      chatMessages.querySelectorAll('.msg.assistant').forEach(el => {
        if (parseInt(el.dataset.histIdx, 10) > targetIdx) el.classList.add('stale');
      });
      // Update history
      history[targetIdx].content = newText;
      for (let k = history.length - 1; k > targetIdx; k--) {
        if (history[k].role === 'assistant') history.splice(k, 1);
      }
      history.push({ role: 'user', content: newText, stale: false });
      saveStore();
      refreshTitle();
      currentMsgEl = addMsg('assistant', '');
      currentMsgEl.querySelector('.msg-bubble').innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
      ws.send(JSON.stringify({ type: 'chat', message: newText, history: curHist() }));
    }
    function cancel() { card.remove(); }
    card.querySelector('#edit-close').onclick = cancel;
    card.querySelector('#edit-send').onclick = sendEdit;
    ta.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendEdit(); }
      if (e.key === 'Escape') cancel();
    };
  }

  // ════════════════════════════════════════════════════════════
  //  Send (normal)
  // ════════════════════════════════════════════════════════════
  function sendMessage() {
    const text = input.value.trim();
    if (!text || isStreaming) return;
    dismissWelcome();
    const history = curHist();
    const userIdx = history.length;
    const userEl = addMsg('user', text);
    userEl.dataset.histIdx = userIdx;
    history.push({ role: 'user', content: text, stale: false });
    saveStore();
    refreshTitle();
    input.value = '';
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    isStreaming = true;
    sendBtn.disabled = true;
    currentMsgEl = addMsg('assistant', '');
    currentMsgEl.querySelector('.msg-bubble').innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
    ws.send(JSON.stringify({ type: 'chat', message: text, history: curHist() }));
  }

  sendBtn.onclick = sendMessage;
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  });

  // ── Sidebar toggle ──
  function openSidebar() { sidebar.classList.add('open'); }
  function closeSidebar() { sidebar.classList.remove('open'); }
  document.getElementById('menu-btn').onclick = (e) => { e.stopPropagation(); openSidebar(); };
  document.getElementById('new-chat-btn').onclick = (e) => { e.stopPropagation(); newChat(); };
  document.addEventListener('click', (e) => {
    if (!sidebar.classList.contains('open')) return;
    if (sidebar.contains(e.target)) return;
    if (e.target.closest('.menu-btn')) return;
    closeSidebar();
  }, true);

  // ── Bubble click → edit ──
  chatMessages.addEventListener('click', (e) => {
    if (e.target.closest('.edit-card, .send-btn, .del-btn, #edit-send, #edit-close')) return;
    const bubble = e.target.closest('.msg-bubble');
    if (!bubble) return;
    const wrap = bubble.closest('.msg');
    if (!wrap || !wrap.classList.contains('user')) return;
    const idx = parseInt(wrap.dataset.histIdx, 10);
    if (isNaN(idx)) return;
    openEditPanel(idx);
  });

  // ── WebSocket ──
  if (ws) {
    ws.onopen = () => ws.send(JSON.stringify({ type: 'init' }));
    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);
      switch (data.type) {
        case 'init':
          modelLabel.textContent = data.model || 'unknown';
          break;
        case 'text':
          if (!currentMsgEl) currentMsgEl = addMsg('assistant', '');
          currentMsgEl.querySelector('.msg-bubble').textContent += data.content;
          scrollChat();
          break;
        case 'tool_call':
          appendTool(currentMsgEl, data.name, data.args);
          break;
        case 'tool_result':
          appendResult(currentMsgEl, data.name, data.result, data.diff);
          break;
        case 'done': {
          isStreaming = false;
          sendBtn.disabled = false;
          const finalText = data.final_text || '';
          const hist = curHist();
          hist.push({ role: 'assistant', content: finalText, stale: false });
          saveStore();
          if (currentMsgEl) currentMsgEl.dataset.histIdx = hist.length - 1;
          currentMsgEl = null;
          refreshTitle();
          break;
        }
        case 'error':
          addMsg('assistant', 'Error: ' + data.message);
          isStreaming = false;
          sendBtn.disabled = false;
          currentMsgEl = null;
          break;
        case 'agent_status':
          updateAgents(data);
          break;
        case 'bus_message':
          appendBus(data);
          break;
        case 'memory':
          updateMemory(data);
          break;
        case 'settings':
          updateSettings(data);
          break;
      }
    };
  }

  // ── Initial render ──
  renderSessionList();
  renderHistory();

  // ── Dashboard updates ──
  function updateAgents(data) {
    const grid = document.getElementById('group-grid');
    grid.innerHTML = '';
    for (const [key, g] of Object.entries(data.groups || {})) {
      const card = document.createElement('div');
      card.className = 'group-card';
      card.innerHTML =
        '<div class="group-name">' + esc(g.name) + '</div>' +
        '<div class="group-stats">' +
          '<span><div class="stat-dot working"></div>' + (g.working||0) + '</span>' +
          '<span><div class="stat-dot idle"></div>' + (g.idle||0) + '</span>' +
          '<span><div class="stat-dot dead"></div>' + (g.dead||0) + '</span>' +
        '</div>' +
        '<div style="margin-top:8px;font-size:12px;color:var(--text-dim)">Model: ' + esc(g.model||'default') + '</div>';
      grid.appendChild(card);
    }
  }

  function appendBus(data) {
    const c = document.getElementById('bus-messages');
    const el = document.createElement('div');
    el.className = 'bus-msg';
    el.innerHTML = '<span class="sender">' + esc(data.sender) + '</span><span class="type">[' + esc(data.msg_type) + ']</span> ' + esc(data.content);
    c.prepend(el);
  }

  function updateMemory(data) {
    document.getElementById('memory-stats').innerHTML =
      '<div class="dash-grid">' +
        '<div class="group-card"><div class="group-name">Session</div><div style="font-size:12px;color:var(--text-dim)">' + esc(data.session_id||'N/A') + '</div></div>' +
        '<div class="group-card"><div class="group-name">Working</div><div style="font-size:22px;font-weight:600">' + (data.working||0) + '</div></div>' +
        '<div class="group-card"><div class="group-name">Episodic</div><div style="font-size:22px;font-weight:600">' + (data.episodic||0) + '</div></div>' +
        '<div class="group-card"><div class="group-name">Semantic</div><div style="font-size:22px;font-weight:600">' + (data.semantic||0) + '</div></div>' +
      '</div>';
    document.getElementById('memory-content').innerHTML = (data.recent_turns||[]).map(t =>
      '<div style="margin-bottom:8px;padding:8px 10px;background:var(--bg-elev);border-radius:8px">' +
        '<span style="color:var(--accent);font-weight:600">' + esc(t.role) + ':</span> ' + esc(t.content.slice(0,200)) +
      '</div>'
    ).join('');
  }

  function updateSettings(data) {
    document.getElementById('settings-content').innerHTML =
      '<div class="dash-grid">' +
        '<div class="group-card"><div class="group-name">Provider</div><div style="font-size:13px;color:var(--text-dim)">' + esc(data.provider||'N/A') + '</div></div>' +
        '<div class="group-card"><div class="group-name">Model</div><div style="font-size:13px;color:var(--text-dim)">' + esc(data.model||'N/A') + '</div></div>' +
        '<div class="group-card"><div class="group-name">Base URL</div><div style="font-size:13px;color:var(--text-dim)">' + esc(data.base_url||'N/A') + '</div></div>' +
        '<div class="group-card"><div class="group-name">Tools</div><div style="font-size:13px;color:var(--text-dim)">' + esc((data.tools||[]).join(', ')) + '</div></div>' +
      '</div>';
  }
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════
#  Web UI Server
# ═══════════════════════════════════════════════════════════════════


class WebUIServer:
    """Local HTTP + WebSocket server for browser-based AgentHarness interface."""

    def __init__(self, orchestrator, host="0.0.0.0", port=_WEB_PORT):
        self.orch = orchestrator
        self.host = host
        self.port = port
        self.clients = set()
        self._app = None

    def _build_app(self):
        try:
            from aiohttp import web, WSMsgType
        except ImportError:
            raise RuntimeError("aiohttp not installed. Run: pip install aiohttp")

        async def index_handler(request):
            return web.Response(text=INDEX_HTML, content_type="text/html")

        async def ws_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.clients.add(ws)

            await ws.send_json({
                "type": "init",
                "model": getattr(self.orch, "ai_model", "unknown"),
            })

            try:
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        await self._handle_message(ws, data)
                    elif msg.type == WSMsgType.ERROR:
                        logger.error("WS error: %s", ws.exception())
            finally:
                self.clients.discard(ws)
            return ws

        app = web.Application()
        app.router.add_get("/", index_handler)
        app.router.add_get("/ws", ws_handler)
        return app

    async def _handle_message(self, ws, data):
        msg_type = data.get("type", "")
        if msg_type == "chat":
            await self._handle_chat(ws, data.get("message", ""), data.get("history", []))
        elif msg_type == "get_agents":
            await self._send_agent_status(ws)
        elif msg_type == "get_memory":
            await self._send_memory(ws)
        elif msg_type == "get_settings":
            await self._send_settings(ws)

    async def _handle_chat(self, ws, message, history):
        """Stream chat. Client owns history — no server-side memory."""
        if not message:
            return
        if message.startswith("/"):
            if message.strip() in ("/exit", "/quit"):
                await ws.send_json({"type": "text", "content": "Session ended."})
                await ws.send_json({"type": "done"})
                return
            if message.startswith("/plan"):
                goal = message[5:].strip() or "Analyze the current project"
                await self._handle_plan(ws, goal)
                return

        messages = list(history) + [{"role": "user", "content": message}]
        final_text = ""
        try:
            async for event in self.orch.chat_stream_with_history(messages):
                et = event.get("type", "")
                if et == "text":
                    final_text += event.get("content", "")
                    await ws.send_json({"type": "text", "content": event.get("content", "")})
                elif et == "thinking":
                    await ws.send_json({"type": "thinking"})
                elif et == "tool_call":
                    await ws.send_json({
                        "type": "tool_call",
                        "name": event.get("name", "?"),
                        "args": event.get("args", {}),
                    })
                elif et == "tool_result":
                    name = event.get("name", "?")
                    result = str(event.get("result", ""))
                    await ws.send_json({
                        "type": "tool_result",
                        "name": name,
                        "result": result[:5000],
                        "diff": name in ("file", "patch", "git"),
                    })
                elif et == "done":
                    await ws.send_json({"type": "done", "final_text": final_text})
                elif et == "error":
                    await ws.send_json({"type": "error", "message": event.get("message", "Unknown error")})
                    await ws.send_json({"type": "done"})
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            await ws.send_json({"type": "done"})
        finally:
            await self._broadcast_agent_status()

    async def _handle_plan(self, ws, goal):
        """Run company-mode multi-agent collaboration."""
        try:
            from multiling.commander.council import CompanyOrchestrator

            await ws.send_json({
                "type": "text",
                "content": f"Starting company-mode collaboration for: {goal}\n\n",
            })

            orch = CompanyOrchestrator(
                llm_complete=self.orch.llm_complete,
                llm_with_model=self.orch.llm_complete_with_model,
                fetch_models_fn=self.orch.fetch_models,
            )

            result = orch.run(goal)
            await ws.send_json({
                "type": "text",
                "content": result.get("completion", "Done."),
            })
            await ws.send_json({"type": "done"})
            await self._broadcast_agent_status()

        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            await ws.send_json({"type": "done"})

    async def _send_agent_status(self, ws):
        """Send current agent dashboard data."""
        try:
            from multiling.commander.comm_bus import get_bus
            bus = get_bus()
            snapshot = bus.get_latest_status_snapshot()
            await ws.send_json({"type": "agent_status", "groups": snapshot.get("groups", {})})
        except Exception:
            await ws.send_json({"type": "agent_status", "groups": {}})

    async def _broadcast_agent_status(self):
        """Broadcast agent status to all clients."""
        for ws in list(self.clients):
            try:
                await self._send_agent_status(ws)
            except Exception:
                pass

    async def _send_memory(self, ws):
        """Send memory stats."""
        try:
            ctx = await self.orch._get_context()
            stats = ctx.get_stats()
            recent = ctx._working_memory[-10:]
            await ws.send_json({
                "type": "memory",
                "session_id": stats.get("session_id", "N/A"),
                "working": stats.get("working_memory_size", 0),
                "episodic": stats.get("episodic_turns", 0),
                "semantic": stats.get("semantic_facts", 0),
                "recent_turns": [
                    {"role": m.get("role", ""), "content": m.get("content", "")}
                    for m in recent
                ],
            })
        except Exception as e:
            await ws.send_json({"type": "memory", "error": str(e)})

    async def _send_settings(self, ws):
        """Send settings data."""
        await ws.send_json({
            "type": "settings",
            "provider": getattr(self.orch, "ai_provider", "N/A"),
            "model": getattr(self.orch, "ai_model", "N/A"),
            "base_url": getattr(self.orch, "ai_base_url", "N/A"),
            "tools": getattr(self.orch, "enabled_toolsets", []),
        })

    def run(self):
        """Start the server (blocking)."""
        from aiohttp import web
        self._app = self._build_app()
        print(f"\n  AgentHarness Web UI: http://localhost:{self.port}\n")
        web.run_app(self._app, host=self.host, port=self.port, print=None)


def launch_web_ui(orchestrator, host="0.0.0.0", port=_WEB_PORT):
    """Launch the web UI server."""
    server = WebUIServer(orchestrator, host=host, port=port)
    server.run()