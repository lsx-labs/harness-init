#!/usr/bin/env bash
# SessionStart hook: inject dynamic project context into conversation start.
# Outputs concise git state + module mapping + harness health.
# Designed to be fast (< 3s) and compact (< 30 lines output).

set -euo pipefail

# ── Git state ──
BRANCH=$(git branch --show-current 2>/dev/null || echo "detached")
AHEAD_BEHIND=$(git rev-list --left-right --count main...HEAD 2>/dev/null | awk '{print "↑"$2" ↓"$1}' || echo "")

echo "📍 分支: $BRANCH ${AHEAD_BEHIND:+($AHEAD_BEHIND vs main)}"

# Dirty files with module mapping
DIRTY=$(git status --porcelain 2>/dev/null | head -10)
if [ -n "$DIRTY" ]; then
    DIRTY_COUNT=$(echo "$DIRTY" | wc -l | tr -d ' ')
    # Map files to top-level modules
    MODULES=$(echo "$DIRTY" | awk '{print $NF}' | cut -d'/' -f1 | sort -u | tr '\n' ' ')
    echo "📝 工作区: ${DIRTY_COUNT} 个文件变更 (${MODULES})"
else
    echo "📝 工作区: 干净"
fi

# Recent commits with module mapping
COMMITS=$(git log --oneline --no-decorate -5 2>/dev/null || true)
if [ -n "$COMMITS" ]; then
    echo "📜 最近提交:"
    echo "$COMMITS" | while read hash msg; do
        MODULE=$(git diff-tree --no-commit-id --name-only -r "$hash" 2>/dev/null | head -1 | cut -d'/' -f1-2)
        AGO=$(git log -1 --format='%cr' "$hash" 2>/dev/null | sed 's/ ago//')
        echo "  $hash ${AGO}  $msg — ${MODULE:-root}/"
    done
else
    echo "📜 最近提交: (无提交历史)"
fi

# ── Harness health ──
WARNINGS=""

# GitNexus index freshness
if [ -d ".gitnexus" ]; then
    STALE=$(npx gitnexus status 2>&1 | grep -c "stale" || true)
    if [ "$STALE" -gt 0 ]; then
        WARNINGS="${WARNINGS}\n⚠️ GitNexus 索引过期，建议运行 npx gitnexus analyze"
    fi
else
    WARNINGS="${WARNINGS}\n💡 GitNexus 未索引此项目"
fi

# CODE_MAP.md stale descriptions
if [ -f "CODE_MAP.md" ]; then
    STALE_DESCS=$(grep -c "⚠️ 描述可能过期" CODE_MAP.md 2>/dev/null || true)
    if [ "$STALE_DESCS" -gt 0 ]; then
        WARNINGS="${WARNINGS}\n⚠️ CODE_MAP.md: ${STALE_DESCS} 个目录描述待更新"
    fi
fi

if [ -n "$WARNINGS" ]; then
    echo -e "$WARNINGS"
fi
