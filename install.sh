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

# Helper: install a file or directory
install_file() {
    local src="$1" dst="$2"
    rm -f "$dst"
    if $USE_LINK; then ln -sf "$src" "$dst"; else cp "$src" "$dst"; chmod +x "$dst" 2>/dev/null || true; fi
}
install_dir() {
    local src="$1" dst="$2"
    rm -rf "$dst"
    if $USE_LINK; then ln -sf "$src" "$dst"; else cp -r "$src" "$dst"; fi
}

# ── 0. Prerequisites ──

if ! command -v node &>/dev/null; then
    echo "❌ Node.js not found. Install Node.js 18+ first: https://nodejs.org"
    exit 1
fi
echo "✅ Node.js $(node --version)"

GITNEXUS_AVAILABLE=false
if ! npx gitnexus --version &>/dev/null 2>&1; then
    echo ""
    echo "⚠️  GitNexus not found. It is recommended for:"
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
        GITNEXUS_AVAILABLE=true
        echo "✅ GitNexus installed and configured"
    else
        echo "⚠️  Continuing without GitNexus (degraded mode: docstring-only CODE_MAP, no search enrichment)"
    fi
else
    GITNEXUS_AVAILABLE=true
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

install_file "$SCRIPT_DIR/scripts/harness-init.sh" "$HOME/.local/bin/harness-init.sh"

# harness-monitor.py: --link mode points hooks directly to repo (no intermediary)
# copy mode still needs the file in ~/.local/share/
if ! $USE_LINK; then
    mkdir -p "$HOME/.local/share/harness-hooks"
    install_file "$SCRIPT_DIR/scripts/harness-monitor.py" "$HOME/.local/share/harness-hooks/harness-monitor.py"
    cp "$SCRIPT_DIR/VERSION" "$HOME/.local/share/harness-hooks/VERSION"
fi

echo "✅ Shared scripts installed"

# ── 2. Skills ──
if [ -d "$HOME/.claude" ]; then
    install_dir "$SCRIPT_DIR/skills/claude" "$HOME/.claude/skills/harness-init"
    echo "✅ Claude Code skill installed"
else
    echo "⏭️  ~/.claude not found, skipping Claude Code skill"
fi

if [ -d "$HOME/.codex" ]; then
    install_dir "$SCRIPT_DIR/skills/codex" "$HOME/.codex/skills/harness-init"
    echo "✅ Codex skill installed"
else
    echo "⏭️  ~/.codex not found, skipping Codex skill"
fi

# ── 3. Hook registration ──
if $USE_LINK; then
    HOOK_PATH="$SCRIPT_DIR/scripts/harness-monitor.py"
else
    HOOK_PATH="$HOME/.local/share/harness-hooks/harness-monitor.py"
fi

register_hook() {
    local config_file="$1" platform="$2" hook_path="$3"
    python3 - "$config_file" "$platform" "$hook_path" << 'PYEOF'
import json, sys
from pathlib import Path

config_file, platform, hook_path = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if not config_file.exists():
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('{"hooks": {}}' if config_file.name == "hooks.json" else '{}')

d = json.loads(config_file.read_text())
hooks = d.setdefault("hooks", {}).setdefault("PostToolUse", [])

# Idempotent: remove old entries
hooks[:] = [item for item in hooks
            if not any("harness-monitor" in h.get("command", "") for h in item.get("hooks", []))]

hooks.append({
    "matcher": "Bash",
    "hooks": [{
        "type": "command",
        "command": f'python3 "{hook_path}"',
        "timeout": 20000,
        "statusMessage": "Harness monitor..."
    }]
})
config_file.write_text(json.dumps(d, indent=2, ensure_ascii=False))
print(f"✅ {platform} hook registered")
PYEOF
}

if [ -d "$HOME/.claude" ]; then
    register_hook "$HOME/.claude/settings.json" "Claude Code" "$HOOK_PATH"
fi
if [ -d "$HOME/.codex" ]; then
    register_hook "$HOME/.codex/hooks.json" "Codex" "$HOOK_PATH"
fi

# ── 4. GitNexus hook reachability (pure Codex environments) ──
if $GITNEXUS_AVAILABLE && [ -d "$HOME/.codex" ] && [ ! -f "$HOME/.claude/hooks/gitnexus/gitnexus-hook.cjs" ]; then
    GITNEXUS_HOOK_SRC="$(npm root -g 2>/dev/null)/gitnexus/hooks/claude/gitnexus-hook.cjs"
    if [ -f "$GITNEXUS_HOOK_SRC" ]; then
        mkdir -p "$HOME/.claude/hooks/gitnexus"
        cp "$GITNEXUS_HOOK_SRC" "$HOME/.claude/hooks/gitnexus/gitnexus-hook.cjs"
        echo "✅ GitNexus hook copied for Codex compatibility"
    fi
fi

# ── 5. Verify installation ──
echo ""
echo "=== Installation verification ==="
ERRORS=0

# Check scripts exist
if $USE_LINK; then
    [ -L "$HOME/.local/bin/harness-init.sh" ] && echo "  ✅ harness-init.sh (symlink)" || { echo "  ❌ harness-init.sh missing"; ERRORS=$((ERRORS+1)); }
    [ -f "$SCRIPT_DIR/scripts/harness-monitor.py" ] && echo "  ✅ harness-monitor.py (repo direct)" || { echo "  ❌ harness-monitor.py missing"; ERRORS=$((ERRORS+1)); }
else
    [ -f "$HOME/.local/bin/harness-init.sh" ] && echo "  ✅ harness-init.sh" || { echo "  ❌ harness-init.sh missing"; ERRORS=$((ERRORS+1)); }
    [ -f "$HOME/.local/share/harness-hooks/harness-monitor.py" ] && echo "  ✅ harness-monitor.py" || { echo "  ❌ harness-monitor.py missing"; ERRORS=$((ERRORS+1)); }
fi

# Check skills
[ -f "$HOME/.claude/skills/harness-init/SKILL.md" ] 2>/dev/null && echo "  ✅ Claude Code skill" || echo "  ⏭️  Claude Code skill (not installed)"
[ -f "$HOME/.codex/skills/harness-init/SKILL.md" ] 2>/dev/null && echo "  ✅ Codex skill" || echo "  ⏭️  Codex skill (not installed)"

if [ $ERRORS -gt 0 ]; then
    echo ""
    echo "⚠️  $ERRORS errors found. Please check above."
    exit 1
fi

echo ""
if $USE_LINK; then
    echo "Done! (symlink mode — edits to $SCRIPT_DIR take effect immediately)"
else
    echo "Done! Run /harness-init in any project to get started."
fi
