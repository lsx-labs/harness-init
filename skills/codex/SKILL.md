---
name: harness-init
description: "Project harness lifecycle management: initialize on first run, health-check and recommend upgrades on subsequent runs. Trigger: /harness-init or 'initialize project', 'check harness', 'harness status'"
disable-model-invocation: true
---

# Harness Init — Project Harness Lifecycle Manager (Codex)

Codex entry point for the harness-init skill. Core logic is shared with Claude Code.

## How to execute

1. Run diagnostic: `bash ~/.local/bin/harness-init.sh .`
2. Parse the JSON output (fields: `languages`, `grep_noise`, `type_coverage`, `lsp_assessment`, `existing`)
3. Follow the 5-layer evaluation:

### Layer 1: AGENTS.md + CLAUDE.md + CODE_MAP.md
- Generate/update **both** AGENTS.md and CLAUDE.md (identical content, `@CODE_MAP.md` reference)
- Generate CODE_MAP.md via `echo '{"tool_name":"Bash"}' | python3 ~/.local/share/harness-hooks/harness-monitor.py`
- AI fills in missing directory descriptions by reading core source files
- **Sub-directory AGENTS.md + CLAUDE.md**: for modules with ≥ 100 symbols, bottom-up strategy with **incremental update via `<!-- harness:start/end -->` markers**. Auto-generated constraints go between markers; manual content outside markers is never overwritten. New files get full generation with markers; existing files get marker-region-only updates. GitNexus-first extraction, grep fallback. Each constraint must cite source file. Leaf auto region ≤ 20 lines, parent ≤ 15 lines. See Claude Code SKILL.md for full extraction rules.

### Layer 2: Hooks
- Check `~/.codex/hooks.json` for: PreToolUse [gitnexus] + PostToolUse [harness-monitor]
- Check GitNexus hook reachability: `~/.claude/hooks/gitnexus/gitnexus-hook.cjs` must exist

### Layer 3: Skills
- Follows Layer 4 (GitNexus analyze auto-generates skills)

### Layer 4: GitNexus MCP
- First check: GitNexus installed? → No + grep_noise > 20 → prompt install
- Then check: current project indexed (.gitnexus/ exists)? → No → `npx gitnexus analyze`
- Then check: index stale? → `npx gitnexus status` → stale → `npx gitnexus analyze`
- Check MCP registration in `~/.codex/config.toml`

### Layer 5: LSP
- Per-language assessment from `lsp_assessment` array
- Strong-typed (TS/Go/Rust/Java): file_count ≥ 30
- Python: type_coverage ≥ 30%
- Weak-typed (JS/Ruby): skip

## Output report format

Same as Claude Code version — see the report template in the shared diagnostic script.
