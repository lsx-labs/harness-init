#!/usr/bin/env bash
set -euo pipefail

echo "Uninstalling harness-init..."

rm -f ~/.local/bin/harness-init.sh
rm -f ~/.local/share/harness-hooks/harness-monitor.py
rm -rf ~/.claude/skills/harness-init
rm -rf ~/.codex/skills/harness-init

# Remove hooks from Claude Code
if [ -f ~/.claude/settings.json ]; then
    python3 -c "
import json; from pathlib import Path
p = Path.home()/'.claude'/'settings.json'; d = json.loads(p.read_text())
for event in list(d.get('hooks',{}).keys()):
    d['hooks'][event] = [i for i in d['hooks'][event] if not any('harness-monitor' in h.get('command','') for h in i.get('hooks',[]))]
    if not d['hooks'][event]: del d['hooks'][event]
p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
print('✅ Claude Code hooks cleaned')
"
fi

# Remove hooks from Codex
if [ -f ~/.codex/hooks.json ]; then
    python3 -c "
import json; from pathlib import Path
p = Path.home()/'.codex'/'hooks.json'; d = json.loads(p.read_text())
for event in list(d.get('hooks',{}).keys()):
    d['hooks'][event] = [i for i in d['hooks'][event] if not any('harness-monitor' in h.get('command','') for h in i.get('hooks',[]))]
    if not d['hooks'][event]: del d['hooks'][event]
p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
print('✅ Codex hooks cleaned')
"
fi

echo "Done!"
