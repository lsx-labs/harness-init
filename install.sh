#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Parse flags ──
USE_LINK=false
if [[ "${1:-}" == "--link" ]]; then
    USE_LINK=true
fi

if $USE_LINK; then
    echo "Installing harness-init from $SCRIPT_DIR (symlink mode — for developers)"
else
    echo "Installing harness-init from $SCRIPT_DIR (copy mode)"
fi
echo ""

# Helper: install a file (copy or symlink)
install_file() {
    local src="$1"
    local dst="$2"
    rm -f "$dst"
    if $USE_LINK; then
        ln -s "$src" "$dst"
    else
        cp "$src" "$dst"
        chmod +x "$dst" 2>/dev/null || true
    fi
}

# ── 0. Prerequisites ──

if ! command -v node &>/dev/null; then
    echo "❌ Node.js not found. Install Node.js 18+ first: https://nodejs.org"
    exit 1
fi
echo "✅ Node.js $(node --version)"

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
        echo "⚠️  Continuing without GitNexus (degraded mode)"
    fi
else
    echo "✅ GitNexus $(npx gitnexus --version 2>/dev/null || echo 'installed')"
fi

if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found."
    exit 1
fi
echo "✅ Python $(python3 --version | awk '{print $2}')"

echo ""

# ── 1. Shared scripts ──
mkdir -p "$HOME/.local/bin"
mkdir -p "$HOME/.local/share/harness-hooks"

install_file "$SCRIPT_DIR/scripts/harness-init.sh" "$HOME/.local/bin/harness-init.sh"
install_file "$SCRIPT_DIR/scripts/harness-monitor.py" "$HOME/.local/share/harness-hooks/harness-monitor.py"

echo "✅ Shared scripts installed"

# ── 2. Claude Code skill ──
if [ -d "$HOME/.claude" ]; then
    mkdir -p "$HOME/.claude/skills/harness-init"
    install_file "$SCRIPT_DIR/skills/claude/SKILL.md" "$HOME/.claude/skills/harness-init/SKILL.md"
    echo "✅ Claude Code skill installed"
else
    echo "⏭️  ~/.claude not found, skipping Claude Code skill"
fi

# ── 3. Codex skill ──
if [ -d "$HOME/.codex" ]; then
    mkdir -p "$HOME/.codex/skills/harness-init"
    install_file "$SCRIPT_DIR/skills/codex/SKILL.md" "$HOME/.codex/skills/harness-init/SKILL.md"
    echo "✅ Codex skill installed"
else
    echo "⏭️  ~/.codex not found, skipping Codex skill"
fi

# ── 4. Hook registration ──
HOOK_PATH="$HOME/.local/share/harness-hooks/harness-monitor.py"

register_hook() {
    local config_file="$1"
    local platform="$2"

    python3 << PYEOF
import json
from pathlib import Path

config_file = Path("$config_file")
if not config_file.exists():
    config_file.parent.mkdir(parents=True, exist_ok=True)
    if config_file.name == "hooks.json":
        config_file.write_text('{"hooks": {}}')
    else:
        config_file.write_text('{}')

d = json.loads(config_file.read_text())
hooks = d.setdefault("hooks", {}).setdefault("PostToolUse", [])

if not any("harness-monitor" in h.get("command", "") for item in hooks for h in item.get("hooks", [])):
    hooks.append({
        "matcher": "Bash|Write",
        "hooks": [{
            "type": "command",
            "command": "python3 \"$HOOK_PATH\"",
            "timeout": 8000,
            "statusMessage": "Harness monitor..."
        }]
    })
    config_file.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    print("✅ $platform hook registered")
else:
    print("✅ $platform hook already exists")
PYEOF
}

if [ -d "$HOME/.claude" ]; then
    register_hook "$HOME/.claude/settings.json" "Claude Code"
fi

if [ -d "$HOME/.codex" ]; then
    register_hook "$HOME/.codex/hooks.json" "Codex"
fi

# ── 5. GitNexus hook reachability (pure Codex environments) ──
if [ -d "$HOME/.codex" ] && [ ! -f "$HOME/.claude/hooks/gitnexus/gitnexus-hook.cjs" ]; then
    GITNEXUS_HOOK_SRC="$(npm root -g 2>/dev/null)/gitnexus/hooks/claude/gitnexus-hook.cjs"
    if [ -f "$GITNEXUS_HOOK_SRC" ]; then
        mkdir -p "$HOME/.claude/hooks/gitnexus"
        cp "$GITNEXUS_HOOK_SRC" "$HOME/.claude/hooks/gitnexus/gitnexus-hook.cjs"
        echo "✅ GitNexus hook copied for Codex compatibility"
    fi
fi

echo ""
if $USE_LINK; then
    echo "Done! (symlink mode — edits to $SCRIPT_DIR take effect immediately)"
else
    echo "Done! Run /harness-init in any project to get started."
fi
