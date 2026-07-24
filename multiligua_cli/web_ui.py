"""
multiligua_cli/web_ui.py — AgentHarness Web Interface

A lightweight web UI for AgentHarness that runs in the browser.
Accessible via `minxg web` command.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# ═══════════════════════════════════════════════════════════════════
#  HTML Template (single-file app)
# ═══════════════════════════════════════════════════════════════════

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AgentHarness — Enterprise AI Orchestration</title>
    <style>
        :root {
            --bg-deep: #0a1e50;
            --bg-panel: #102a69;
            --bg-msg-user: #002800;
            --bg-msg-ai: #001428;
            --accent: #00d4ff;
            --secondary: #0066cc;
            --gold: #ffd700;
            --text: #e0e0e0;
            --text-dim: #888;
            --error: #ff4444;
            --success: #44ff44;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
            background: var(--bg-deep);
            color: var(--text);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        /* Header */
        header {
            background: var(--bg-panel);
            border-bottom: 2px solid var(--secondary);
            padding: 12px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-shrink: 0;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .brand-icon {
            font-size: 24px;
            color: var(--gold);
        }
        .brand h1 {
            font-size: 20px;
            font-weight: 700;
            color: var(--gold);
        }
        .brand .subtitle {
            font-size: 11px;
            color: var(--text-dim);
            margin-top: 2px;
        }
        .status-bar {
            display: flex;
            gap: 16px;
            font-size: 12px;
            color: var(--text-dim);
        }
        .status-bar span {
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        /* Messages */
        #messages {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        .message {
            max-width: 80%;
            padding: 12px 16px;
            border-radius: 12px;
            line-height: 1.6;
            animation: fadeIn 0.3s ease;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message.user {
            align-self: flex-end;
            background: var(--bg-msg-user);
            border: 1px solid #00aa00;
            border-radius: 12px 12px 2px 12px;
        }
        .message.assistant {
            align-self: flex-start;
            background: var(--bg-msg-ai);
            border: 1px solid var(--secondary);
            border-radius: 12px 12px 12px 2px;
        }
        .message.system {
            align-self: center;
            background: transparent;
            border: 1px dashed var(--text-dim);
            color: var(--text-dim);
            font-size: 12px;
            text-align: center;
            max-width: 60%;
        }
        .message .meta {
            font-size: 10px;
            color: var(--text-dim);
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
        }
        .message pre {
            background: rgba(0,0,0,0.3);
            padding: 8px 12px;
            border-radius: 6px;
            overflow-x: auto;
            margin: 8px 0;
            font-size: 13px;
        }
        /* Typing indicator */
        .typing {
            display: flex;
            gap: 4px;
            padding: 4px 0;
        }
        .typing span {
            width: 8px;
            height: 8px;
            background: var(--accent);
            border-radius: 50%;
            animation: typingBounce 1.4s infinite ease-in-out;
        }
        .typing span:nth-child(1) { animation-delay: 0s; }
        .typing span:nth-child(2) { animation-delay: 0.2s; }
        .typing span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typingBounce {
            0%, 80%, 100% { transform: translateY(0); }
            40% { transform: translateY(-8px); }
        }
        /* Input */
        #input-area {
            background: var(--bg-panel);
            border-top: 2px solid var(--secondary);
            padding: 16px 24px;
            display: flex;
            gap: 12px;
            flex-shrink: 0;
        }
        #input-area textarea {
            flex: 1;
            background: rgba(0,0,0,0.3);
            border: 1px solid var(--secondary);
            border-radius: 8px;
            color: var(--text);
            padding: 12px 16px;
            font-size: 14px;
            font-family: inherit;
            resize: none;
            height: 48px;
            outline: none;
            transition: border-color 0.2s;
        }
        #input-area textarea:focus {
            border-color: var(--accent);
        }
        #input-area textarea::placeholder {
            color: var(--text-dim);
        }
        .btn {
            background: var(--secondary);
            color: white;
            border: none;
            border-radius: 8px;
            padding: 0 24px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        .btn:hover {
            background: var(--accent);
            color: var(--bg-deep);
        }
        .btn:active {
            transform: scale(0.96);
        }
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        /* Toolbar */
        .toolbar {
            display: flex;
            gap: 8px;
            padding: 8px 24px;
            background: rgba(0,0,0,0.2);
            flex-shrink: 0;
            flex-wrap: wrap;
        }
        .toolbar button {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            color: var(--text-dim);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .toolbar button:hover {
            background: rgba(255,255,255,0.1);
            color: var(--text);
            border-color: var(--accent);
        }
        /* Scrollbar */
        #messages::-webkit-scrollbar { width: 6px; }
        #messages::-webkit-scrollbar-track { background: transparent; }
        #messages::-webkit-scrollbar-thumb {
            background: var(--secondary);
            border-radius: 3px;
        }
        /* Responsive */
        @media (max-width: 600px) {
            header { padding: 8px 12px; }
            .status-bar { display: none; }
            #messages { padding: 12px; }
            .message { max-width: 90%; }
            #input-area { padding: 12px; }
        }
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <span class="brand-icon">◆</span>
            <div>
                <h1>AgentHarness</h1>
                <div class="subtitle">Multilingual Intelligence eXchange Gateway</div>
            </div>
        </div>
        <div class="status-bar">
            <span><span class="status-dot"></span> <span id="model-name">Loading...</span></span>
            <span id="token-count">0 tokens</span>
            <span id="cost-display">$0.00</span>
        </div>
    </header>

    <div id="messages">
        <div class="message system">
            Welcome to AgentHarness Web UI. Type a message to start chatting.
        </div>
    </div>

    <div class="toolbar">
        <button onclick="clearChat()">🗑️ Clear</button>
        <button onclick="exportChat()">📥 Export</button>
        <button onclick="showFeatures()">✨ Features</button>
        <button onclick="showThemes()">🎨 Themes</button>
    </div>

    <div id="input-area">
        <textarea id="user-input" placeholder="Type your message..." onkeydown="handleKeyDown(event)"></textarea>
        <button class="btn" id="send-btn" onclick="sendMessage()">Send ▶</button>
    </div>

    <script>
        let chatHistory = [];
        let totalTokens = 0;
        let totalCost = 0;
        let isStreaming = false;

        async function sendMessage() {
            const input = document.getElementById('user-input');
            const message = input.value.trim();
            if (!message || isStreaming) return;

            input.value = '';
            addMessage('user', message);
            chatHistory.push({ role: 'user', content: message });

            isStreaming = true;
            document.getElementById('send-btn').disabled = true;
            addTypingIndicator();

            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ messages: chatHistory }),
                });

                const data = await response.json();

                // Remove typing indicator
                const indicator = document.querySelector('.typing-indicator');
                if (indicator) indicator.remove();

                if (data.error) {
                    addMessage('system', 'Error: ' + data.error);
                } else {
                    const content = data.content || data.response || data.message || '(empty response)';
                    addMessage('assistant', content);
                    chatHistory.push({ role: 'assistant', content: content });

                    // Update stats
                    totalTokens += (data.usage?.total_tokens || 0);
                    totalCost += (data.cost || 0);
                    document.getElementById('token-count').textContent =
                        totalTokens >= 1000 ? (totalTokens/1000).toFixed(1) + 'K' : totalTokens + ' tokens';
                    document.getElementById('cost-display').textContent = '$' + totalCost.toFixed(4);
                }
            } catch (err) {
                const indicator = document.querySelector('.typing-indicator');
                if (indicator) indicator.remove();
                addMessage('system', 'Connection error: ' + err.message);
            }

            isStreaming = false;
            document.getElementById('send-btn').disabled = false;
        }

        function handleKeyDown(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        }

        function addMessage(role, content) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + role;

            const time = new Date().toLocaleTimeString();
            const escaped = escapeHtml(content);
            // Simple markdown-like formatting
            const formatted = escaped
                .replace(/```([\\s\\S]*?)```/g, '<pre>$1</pre>')
                .replace(/`([^`]+)`/g, '<code>$1</code>')
                .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>')
                .replace(/\\n/g, '<br>');

            div.innerHTML = `
                <div class="meta">
                    <span>${role === 'user' ? '👤 You' : role === 'assistant' ? '🤖 AgentHarness' : '⚙️ System'}</span>
                    <span>${time}</span>
                </div>
                ${formatted}
            `;

            messages.appendChild(div);
            messages.scrollTop = messages.scrollHeight;
        }

        function addTypingIndicator() {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message assistant typing-indicator';
            div.innerHTML = `
                <div class="typing">
                    <span></span><span></span><span></span>
                </div>
            `;
            messages.appendChild(div);
            messages.scrollTop = messages.scrollHeight;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function clearChat() {
            chatHistory = [];
            totalTokens = 0;
            totalCost = 0;
            document.getElementById('messages').innerHTML = `
                <div class="message system">Chat cleared. Start a new conversation.</div>
            `;
            document.getElementById('token-count').textContent = '0 tokens';
            document.getElementById('cost-display').textContent = '$0.00';
        }

        function exportChat() {
            const blob = new Blob([JSON.stringify(chatHistory, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'minxg-chat-' + new Date().toISOString().slice(0,10) + '.json';
            a.click();
            URL.revokeObjectURL(url);
        }

        function showFeatures() {
            alert('AgentHarness Features:\\n\\n' +
                '🔌 MCP Server — 70+ tools for Claude Code\\n' +
                '🤖 32+ AI Providers — OpenAI, Anthropic, Google, DeepSeek\\n' +
                '🧮 300+ Math Operators — GA, Category, InfoGeo, Topology\\n' +
                '🔗 Polyglot Bridges — C/C++, Go, Java, Rust, R, Julia, WASM\\n' +
                '🌍 12 Languages — i18n support\\n' +
                '📱 Android + Windows — Full Termux support\\n' +
                '🚀 Driver Engine — RK4/RK45 with chaos detection\\n' +
                '🧬 Self-Evolution — Built-in learning engine\\n' +
                '🔐 Safety Guards — Depth + cost + anti-loop protection\\n' +
                '🌐 API Gateway — OpenAI-compatible /v1 API');
        }

        function showThemes() {
            alert('Available Themes:\\n\\n' +
                '🔵 Blue Premium (default)\\n' +
                '⚫ Dark Modern\\n' +
                '🟢 Matrix\\n' +
                '🟠 Warm Sunset\\n' +
                '⚪ Minimal\\n' +
                '🔶 High Contrast\\n\\n' +
                'Use /theme <name> in TUI to switch.');
        }

        // Auto-focus input
        document.getElementById('user-input').focus();
    </script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════
#  Web Server
# ═══════════════════════════════════════════════════════════════════

async def create_app() -> Any:
    """Create the web UI FastAPI application.

    NOTE: UI has moved to :mod:`minxg_ui.web`. This module keeps
    ``create_app`` as a compatibility shim for existing tests/CLI wiring.
    """
    try:
        from minxg_ui.web import app as _app
        return _app
    except Exception as e:
        print(f"ERROR: failed to load AgentHarness web UI: {e}")
        return None
    return None


def run_web_ui(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True) -> None:
    """Run the web UI server."""
    import asyncio

    async def _run():
        app = await create_app()
        if app is None:
            return

        import uvicorn
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)

        if open_browser:
            import webbrowser
            webbrowser.open(f"http://{host}:{port}")
            print(f"Opening browser at http://{host}:{port}")

        print(f"AgentHarness Web UI running at http://{host}:{port}")
        print("Press Ctrl+C to stop")

        await server.serve()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\nWeb UI stopped.")
