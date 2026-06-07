<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **harness-init** (2341 symbols, 5124 relationships, 153 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/harness-init/context` | Codebase overview, check index freshness |
| `gitnexus://repo/harness-init/clusters` | All functional areas |
| `gitnexus://repo/harness-init/processes` | All execution flows |
| `gitnexus://repo/harness-init/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## CODE_MAP

<!-- codemap:start -->
# Code Map

> Auto-generated from GitNexus. Descriptions maintained by AI + GitNexus or 📌 manual.

### hooks/ — 核心职责：GitNexus钩子、Codex兼容、输出规范化

### scripts/ — 核心职责：初始化、CODE_MAP生成、钩子维护

### tests/ — 测试套件：行为校验、边界条件与回归覆盖

### docs/ — 项目文档：门禁
- **superpowers/** — 项目文档：门禁

### skills/ — 核心职责：生命周期管理、执行流程、文档模板
- **claude/** — 项目驾具生命周期管理：Claude 计划执行、模板生成、GitNexus 工具选择
- **codex/** — 项目驾具生命周期管理：Codex 计划执行、AGENTS 模板、GitNexus 工具选择
<!-- codemap:end -->
