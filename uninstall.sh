#!/usr/bin/env bash
set -euo pipefail

echo "Uninstalling harness-init..."

rm -f ~/.local/bin/harness-init.sh
rm -f ~/.local/share/harness-hooks/harness-monitor.py
rm -f ~/.local/share/harness-hooks/session-context.sh
rm -f ~/.local/share/harness-hooks/VERSION
rm -rf ~/.local/share/harness-hooks/counters
rm -rf ~/.claude/skills/harness-init
rm -rf ~/.codex/skills/harness-init

cleanup_hooks() {
    local config_file="$1" platform="$2"
    [ -f "$config_file" ] || return 0
    python3 - "$config_file" "$platform" << 'PYEOF'
import json, sys
from pathlib import Path

p, platform = Path(sys.argv[1]), sys.argv[2]
d = json.loads(p.read_text())
hooks = d.get("hooks", {})
for event in list(hooks.keys()):
    hooks[event] = [i for i in hooks[event]
                    if not any("harness-monitor" in h.get("command", "") or "session-context" in h.get("command", "")
                              for h in i.get("hooks", []))]
    if not hooks[event]:
        del hooks[event]
if not hooks:
    d.pop("hooks", None)
p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
print(f"✅ {platform} hooks cleaned")
PYEOF
}

cleanup_hooks "$HOME/.claude/settings.json" "Claude Code"
cleanup_hooks "$HOME/.codex/hooks.json" "Codex"

echo "Done!"
