#!/usr/bin/env bash
# SessionStart hook: inject dynamic project context.
# Output JSON for hookSpecificOutput.additionalContext

set -euo pipefail

# ── Collect context ──
BRANCH=$(git branch --show-current 2>/dev/null || echo "detached")
AHEAD_BEHIND=$(git rev-list --left-right --count main...HEAD 2>/dev/null | awk '{print "↑"$2" ↓"$1}' || echo "")

CONTEXT="📍 分支: $BRANCH ${AHEAD_BEHIND:+($AHEAD_BEHIND vs main)}\n"

DIRTY=$(git status --porcelain 2>/dev/null | head -10)
if [ -n "$DIRTY" ]; then
    DIRTY_COUNT=$(echo "$DIRTY" | wc -l | tr -d ' ')
    MODULES=$(echo "$DIRTY" | awk '{print $NF}' | cut -d'/' -f1 | sort -u | tr '\n' ' ')
    CONTEXT+="📝 工作区: ${DIRTY_COUNT} 个文件变更 (${MODULES})\n"
else
    CONTEXT+="📝 工作区: 干净\n"
fi

COMMITS=$(git log --oneline --no-decorate -5 2>/dev/null || true)
if [ -n "$COMMITS" ]; then
    CONTEXT+="📜 最近提交:\n"
    while read hash msg; do
        MODULE=$(git diff-tree --no-commit-id --name-only -r "$hash" 2>/dev/null | head -1 | cut -d'/' -f1-2)
        AGO=$(git log -1 --format='%cr' "$hash" 2>/dev/null | sed 's/ ago//')
        CONTEXT+="  $hash ${AGO}  $msg — ${MODULE:-root}/\n"
    done <<< "$COMMITS"
fi

# GitNexus
if [ -d ".gitnexus" ]; then
    STALE=$(npx gitnexus status 2>&1 | grep -c "stale" || true)
    if [ "$STALE" -gt 0 ]; then
        CONTEXT+="⚠️ GitNexus 索引过期\n"
    fi
fi

# CODE_MAP stale
if [ -f "CODE_MAP.md" ]; then
    STALE_DESCS=$(grep -c "⚠️ 描述可能过期" CODE_MAP.md 2>/dev/null || true)
    if [ "$STALE_DESCS" -gt 0 ]; then
        CONTEXT+="⚠️ CODE_MAP.md: ${STALE_DESCS} 个目录描述待更新\n"
    fi
fi

# Output plain text — Claude Code injects stdout as additionalContext
echo -e "$CONTEXT"
