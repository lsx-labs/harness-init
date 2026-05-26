#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Installing harness-init from $SCRIPT_DIR"
echo ""

# ── 0. Prerequisites ──

# Node.js (required by GitNexus)
if ! command -v node &>/dev/null; then
    echo "❌ Node.js not found. Install Node.js 18+ first: https://nodejs.org"
    exit 1
fi
echo "✅ Node.js $(node --version)"

# GitNexus
if ! npx gitnexus --version &>/dev/null 2>&1; then
    echo ""
    echo "⚠️  GitNexus not found. It is required for:"
    echo "   - CODE_MAP.md auto-generation (knowledge graph → module structure)"
    echo "   - PreToolUse search enrichment (grep/glob augmented with call graph)"
    echo "   - PostToolUse stale index detection (auto-reindex after commits)"
    echo ""
    read -p "Install GitNexus now? (Y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo "Installing GitNexus..."
        npm install -g gitnexus
        echo "Running GitNexus setup (registers MCP + hooks)..."
        npx gitnexus setup
        echo "✅ GitNexus installed and configured"
    else
        echo "⚠️  Continuing without GitNexus (degraded mode: docstring-only CODE_MAP, no search enrichment)"
    fi
else
    echo "✅ GitNexus $(npx gitnexus --version 2>/dev/null || echo 'installed')"
fi

# Python 3 (required by harness scripts)
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found."
    exit 1
fi
echo "✅ Python $(python3 --version | awk '{print $2}')"

echo ""

# ── 1. Shared scripts ──
mkdir -p ~/.local/bin
mkdir -p ~/.local/share/harness-hooks

cp "$SCRIPT_DIR/scripts/harness-init.sh" ~/.local/bin/harness-init.sh
chmod +x ~/.local/bin/harness-init.sh

cp "$SCRIPT_DIR/scripts/harness-monitor.py" ~/.local/share/harness-hooks/harness-monitor.py
chmod +x ~/.local/share/harness-hooks/harness-monitor.py

echo "✅ Shared scripts installed"

# ── 2. Claude Code skill ──
if [ -d ~/.claude ]; then
    mkdir -p ~/.claude/skills/harness-init
    cp "$SCRIPT_DIR/skills/claude/SKILL.md" ~/.claude/skills/harness-init/SKILL.md
    echo "✅ Claude Code skill installed"
else
    echo "⏭️  ~/.claude not found, skipping Claude Code skill"
fi

# ── 3. Codex skill ──
if [ -d ~/.codex ]; then
    mkdir -p ~/.codex/skills/harness-init
    cp "$SCRIPT_DIR/skills/codex/SKILL.md" ~/.codex/skills/harness-init/SKILL.md
    echo "✅ Codex skill installed"
else
    echo "⏭️  ~/.codex not found, skipping Codex skill"
fi

# ── 4. Hook registration ──
HOOK_CMD='python3 "'$(echo ~/.local/share/harness-hooks/harness-monitor.py)'"'
HOOK_ENTRY="{\"matcher\":\"Bash|Write\",\"hooks\":[{\"type\":\"command\",\"command\":\"$HOOK_CMD\",\"timeout\":8000,\"statusMessage\":\"Harness monitor...\"}]}"

# Claude Code
if [ -f ~/.claude/settings.json ]; then
    python3 -c "
import json
from pathlib import Path
p = Path.home() / '.claude' / 'settings.json'
d = json.loads(p.read_text())
hooks = d.setdefault('hooks', {}).setdefault('PostToolUse', [])
if not any('harness-monitor' in h.get('command','') for item in hooks for h in item.get('hooks',[])):
    hooks.append(json.loads('$HOOK_ENTRY'))
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    print('✅ Claude Code hook registered')
else:
    print('✅ Claude Code hook already exists')
"
fi

# Codex
if [ -f ~/.codex/hooks.json ]; then
    python3 -c "
import json
from pathlib import Path
p = Path.home() / '.codex' / 'hooks.json'
d = json.loads(p.read_text())
hooks = d.setdefault('hooks', {}).setdefault('PostToolUse', [])
if not any('harness-monitor' in h.get('command','') for item in hooks for h in item.get('hooks',[])):
    hooks.append(json.loads('$HOOK_ENTRY'))
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    print('✅ Codex hook registered')
else:
    print('✅ Codex hook already exists')
"
fi

echo ""
echo "Done! Run /harness-init in any project to get started."
