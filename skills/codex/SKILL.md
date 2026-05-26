---
name: harness-init
description: "Project harness lifecycle management: initialize on first run, health-check and recommend upgrades on subsequent runs. Trigger: /harness-init or 'initialize project', 'check harness', 'harness status'"
disable-model-invocation: true
---

# Harness Init — Project Harness Lifecycle Manager (Codex)

This is the Codex entry point. Core diagnostic logic lives in `~/.local/bin/harness-init.sh` (shared with Claude Code). This skill reads its JSON output and executes platform-specific actions for Codex.

## How to execute

1. Run diagnostic: `bash ~/.local/bin/harness-init.sh .`
2. Parse the JSON output
3. Follow the same 5-layer evaluation as the Claude Code version
4. Key differences for Codex:
   - Generate/update **AGENTS.md** (not just CLAUDE.md — both files must stay in sync)
   - Check Codex hooks in `~/.codex/hooks.json`
   - Check Codex MCP in `~/.codex/config.toml`
   - LSP plugin installation uses Codex plugin system (not `claude plugin add`)

## Cross-platform parity

Both CLAUDE.md and AGENTS.md are generated with identical content. The diagnostic script and hook scripts are shared. Only the registration configs differ per platform.

Refer to `~/.claude/skills/harness-init/SKILL.md` for the complete 5-layer evaluation logic, report format, and threshold definitions. The logic is identical — only the file paths and commands differ.
