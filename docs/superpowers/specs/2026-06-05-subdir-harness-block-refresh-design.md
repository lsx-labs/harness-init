# Subdirectory Harness Block Refresh Design

## Context

The v3.4.6 CODE_MAP redesign made root platform docs stable by rendering the same number-free `CODE_MAP.md` into managed `<!-- codemap:start/end -->` blocks in both `CLAUDE.md` and `AGENTS.md`. It also moved symbol-count baselines into cache-side machine state.

Subdirectory platform docs have not received the same treatment yet. Current behavior still uses mtime-based whole-file copy between `CLAUDE.md` and `AGENTS.md` for subdirectories. That can overwrite platform-specific or hand-authored text, creates unnecessary churn, and does not make subdirectory GitNexus facts refresh when code facts change.

This design extends the same projection model to subdirectory harness content, with one important difference from root CODE_MAP: subdirectory content can become operating instructions for future agents, so v1 uses the smallest safe scope. The managed subdirectory block contains only deterministic Layer 1 GitNexus facts. It contains no AI prose, no generated constraints, and no generated dangerous-operation guidance.

- `CLAUDE.md` and `AGENTS.md` are independent platform docs.
- The only shared automatic content is a managed `<!-- harness:start/end -->` block.
- That block is deterministic GitNexus fact output, not prose interpretation.
- Human-authored prose belongs outside the managed block, for example under `## 补充约束（手动维护）`.
- Sidecar state records when each directory's harness block was last accepted, so code changes can trigger refresh without whole-file copying.

## Goals

- Remove subdirectory `CLAUDE.md` <-> `AGENTS.md` whole-file copying.
- Preserve all text outside `<!-- harness:start/end -->` exactly.
- Render the same accepted deterministic Layer 1 fact block into both platform docs when both files exist.
- Create a missing platform doc only in manual `/harness-init`, by bootstrapping a minimal shell plus the accepted harness block, not by copying the other platform doc wholesale.
- Refresh subdirectory harness blocks when code facts change enough to cross a stale threshold.
- Keep refresh state out of Git, in the existing harness cache area.
- Keep all prose interpretation and constraints out of the generated block.

## Non-Goals

- Do not auto-refresh root doc prose such as project positioning, build commands, concepts, or danger notes.
- Do not change root `CODE_MAP` block semantics.
- Do not make SessionStart run heavy GitNexus work.
- Do not rewrite manual sections outside managed markers.
- Do not treat symbol count as the only stale signal.
- Do not make platform-specific docs byte-identical.
- Do not generate or persist AI-written subdirectory prose instructions.
- Do not create new tracked subdirectory platform-doc files from unattended background jobs.

## Terminology

- Platform doc: `CLAUDE.md` or `AGENTS.md`.
- Root CODE_MAP block: `<!-- codemap:start/end -->` in root platform docs.
- Subdirectory harness block: `<!-- harness:start/end -->` in subdirectory platform docs.
- Deterministic fact block: a generated Markdown fragment rendered directly from GitNexus CLI/Cypher output with no AI interpretation.
- Structural fact block: an existing `<!-- harness:start/end -->` block that has the expected facts-only shape, with no prose headings, imperative guidance, placeholders, or old generated sections.
- Fact-current block: a structural fact block whose rendered values match freshly extracted GitNexus fact records.
- Fact-baselined block: a structural fact block that has a matching `SUBDIR_HARNESS.state.json` baseline. Its baseline may be stale.
- Legacy prose block: an existing `<!-- harness:start/end -->` block that fails the structural fact block check or contains old generated prose such as `## 约束（基于 GitNexus 事实）` / `## 危险操作（基于 GitNexus impact 分析）`.
- Manual prose: human-authored text outside `<!-- harness:start/end -->`; harness never edits it.
- Harness block source: the accepted deterministic fact block stored in cache for one directory.
- Refresh baseline: cache-side metadata describing the GitNexus/code facts that were true when the current harness block was accepted.

## Current Behavior To Replace

`scripts/sync_docs.py` currently keeps subdirectory platform docs in sync by comparing mtimes and copying the newer whole file. `scripts/harness_plan.py` also plans subdirectory docs as:

- skip when the current platform's doc exists;
- copy from the other platform when only the other platform's doc exists;
- generate only when neither exists.

This means existing subdirectory docs rarely regenerate, and cross-platform consistency is achieved by copying entire files rather than rendering a managed block.

## Proposed Architecture

Introduce a subdirectory harness block pipeline with three parts:

1. Plan:
   - Determine complex directories from CODE_MAP/GitNexus counts as today.
   - For each complex directory, classify platform docs as missing, present-with-block, or present-without-block.
   - Determine stale status from sidecar state plus live GitNexus/code fingerprints.

2. Extract and render facts:
   - Use a single shared script to query GitNexus and build deterministic fact records.
   - Render caller counts, blast radius, affected modules, and process participation directly from those records.
   - No AI is involved in this path, so numeric facts can be verified against the extracted records.

3. Render:
   - Insert or replace only `<!-- harness:start/end -->` in each platform doc.
   - Leave every byte outside the managed block unchanged.
   - If a platform doc is missing, create a minimal platform-specific shell only in the manual `/harness-init` path and insert the accepted block.

All entry points must call the same generator script for fact extraction, rendering, validation, and state updates. The skill prompt should not contain a separate ad hoc generation path.

## Shared Generator Script

Add a local script, tentatively `scripts/generate_subdir_harness.py`, and install it with the other harness hook scripts.

Responsibilities:

- compute cheap source fingerprints;
- compute GitNexus fact fingerprints when cheap checks indicate a possible change;
- render deterministic fact blocks;
- manage `SUBDIR_HARNESS.state.json`;
- render accepted blocks into existing platform docs;
- bootstrap missing docs only when invoked in manual mode;
- run structural, freshness, and render-consistency checks against structured GitNexus records.

Invocation modes:

- `--plan`: report stale directories and reasons without writing.
- `--refresh-facts`: refresh deterministic fact blocks and render to existing docs.
- `--bootstrap`: manual-only mode that may create missing platform docs.

Both `/harness-init` and PostToolUse workers must use this script. They may pass different modes, but not different generation logic. There is no generator mode that asks a model to produce prose.

## Cache State

Add a subdirectory sidecar under the same Git common-dir cache key used by CODE_MAP. Use a single JSON file for the first implementation, because the expected number of complex directories is small and a single file keeps atomic baseline updates straightforward.

```text
~/.local/share/harness-hooks/codemaps/<project-key>/SUBDIR_HARNESS.state.json
```

Schema:

```json
{
  "schema_version": 1,
  "dirs": {
    "src/core": {
      "symbol_count": 128,
      "repo_source_fingerprint": "sha256:...",
      "source_fingerprint": "sha256:...",
      "known_caller_source_fingerprint": "sha256:...",
      "gitnexus_fingerprint": "sha256:...",
      "block_hash": "sha256:...",
      "fact_block": "## GitNexus 事实\n...",
      "accepted_at": "2026-06-05T00:00:00Z",
      "rendered": {
        "CLAUDE.md": {"status": "updated", "block_hash": "sha256:..."},
        "AGENTS.md": {"status": "updated", "block_hash": "sha256:..."}
      }
    }
  }
}
```

State is machine/cache data. It should not be committed. Losing it should cause extra refresh or rebaseline work, not incorrect docs.

## Fingerprints And Stale Rules

Use cheap-first stale checks, without allowing cheap checks to hide cross-directory graph changes:

- Missing `<!-- harness:start/end -->` block.
- Missing sidecar baseline.
- Legacy prose block or malformed facts-only block.
- Symbol count drift at or above the existing `STALE_THRESHOLD` of `0.2`.
- Repository source fingerprint change.
- Directory source fingerprint change.
- Known caller source fingerprint change for files that previously called into the directory.
- GitNexus fingerprint change for relevant facts:
  - core symbols selected for the directory;
  - incoming caller counts;
  - upstream impact summary;
  - affected modules;
  - participating execution flows.

Missing sidecar baseline is not enough by itself to classify a block as legacy. If the existing block is structural and current, background mode may rebaseline it by writing cache state only; it must not rewrite the document during that rebaseline step.

Freshness mismatch is not a structural failure. If an existing structural fact block contains old values, such as `被调用: 23` while current GitNexus facts say `40`, route it to `refresh_facts`, not `manual_migration`.

Legacy prose blocks are not automatically writable states for existing subdirectory docs. In background mode, a legacy prose block must be classified as `manual_migration_required`, not `refresh_facts`. This prevents a main-branch hook from silently replacing an old prose block with a new facts-only block.

The repository source fingerprint is the first global gate. If it is unchanged, the background path can skip all subdirectory graph work. If the repository source fingerprint changed, directory and known-caller source fingerprints are used only to prioritize and cap work; they do not prove freshness by themselves, because new incoming callers can originate outside the directory and outside the previous caller set.

When the repository source fingerprint changed, the worker must either compute the GitNexus fingerprint for candidate directories or record that the directory remains `stale_pending_graph_check`. It must not mark a directory fresh solely because its own source fingerprint and symbol count are unchanged.

The GitNexus fingerprint should be compact and deterministic. It does not need to store the full tool output, but it must include enough stable facts to detect semantic changes where symbol count does not move.

Every fact list used for fingerprints, validation, and rendering must be normalized with a stable total ordering before hashing or Markdown generation. Use count or risk descending for ranked facts, then symbol/file/process name ascending, then stable path or id ascending as a final tie-breaker. Never rely on Cypher row order.

Use these canonical fingerprint inputs:

- tracked source-file content hash for the whole repository, excluding generated caches and ignored artifacts;
- tracked source-file content hash for files under the directory, excluding `CLAUDE.md`, `AGENTS.md`, generated caches, and ignored artifacts;
- tracked source-file content hash for files that the previous baseline recorded as incoming callers;
- GitNexus symbols defined under the directory: symbol id or name, kind, file path, and start line;
- incoming graph edges targeting directory symbols, grouped by edge type and target symbol;
- outgoing graph edges from directory symbols, grouped by edge type and source symbol;
- process ids or labels containing directory symbols;
- community/folder symbol count used by CODE_MAP planning.

The implementation may obtain these facts through `npx gitnexus cypher` rather than MCP tools, because the refresh workers run as local scripts.

Only advance the baseline after:

- deterministic fact extraction completed successfully;
- the rendered fact block was verified against extracted records;
- the accepted fact block was written to cache;
- rendering either succeeded for all target files or recorded a retryable write failure without claiming the file is current.

Do not advance the baseline when extraction fails, render consistency fails, or the final block is not a structural fact block.

Background work should be bounded. A worker should cap the number of subdirectories refreshed in one run and record remaining stale directories for later retry rather than spending unbounded time on a single PostToolUse job.

## Managed Block Rendering

Add a renderer for subdirectory harness blocks:

```md
<!-- harness:start -->
## GitNexus 事实

- 被调用: ...
- 影响面: ...
- 相关流程: ...
<!-- harness:end -->
```

Rendering rules:

- If a block exists, replace only the marker range.
- If no block exists, insert a block after the `## 测试` section when present; otherwise append near the end before `## 补充约束（手动维护）` if present; otherwise append to the file.
- If the file is missing and the invocation is manual `/harness-init --bootstrap`, create a platform-specific shell:

```md
# <dir>/ — <short role>

## 测试

<best known focused test command or "未识别专用测试命令">

<!-- harness:start -->
...
<!-- harness:end -->

## 补充约束（手动维护）
```

The shell may differ between `CLAUDE.md` and `AGENTS.md` if future platform-specific wording is needed. The managed block content should be identical for the same directory and block hash.

## Generation Requirements

The unattended background path should generate only deterministic fact-backed items:

- caller counts rendered from GitNexus graph rows;
- blast-radius or impact counts rendered from GitNexus graph rows;
- affected modules rendered from GitNexus graph rows;
- participating processes rendered from GitNexus graph rows.

These rows must be rendered from structured data produced by `npx gitnexus cypher` or another non-AI GitNexus CLI output. `render_consistency_check` must compare rendered values against that structured data, not just check for citation strings.

The generated block must not contain prose interpretation, normative constraints, or dangerous-operation recommendations. Those belong only in hand-authored text outside `<!-- harness:start/end -->`.

If GitNexus is unavailable or stale and cannot be refreshed, do not invent facts. Render an empty but explicit managed block only if needed:

```md
## GitNexus 事实

暂无已验证图谱事实。
```

The generator may inspect source file paths only to compute fingerprints and to anchor facts already identified by GitNexus. It must not read an entire directory and infer constraints.

## Validation

Use three separate checks so structure, renderer correctness, and freshness do not get conflated:

- `structural_fact_block_check`: checks whether an existing block is a facts-only block. It accepts the expected `## GitNexus 事实` heading, supported fact rows, stable empty-state text, and truncation marker. It rejects old prose headings such as `## 约束（基于 GitNexus 事实）` / `## 危险操作（基于 GitNexus impact 分析）`, generated constraint prose, imperative guidance, placeholders such as `TODO` or `{符号名}`, and template braces. It does not compare values to current GitNexus facts.
- `render_consistency_check`: checks whether a newly rendered block matches the structured GitNexus rows used to build it. Rendered caller, impact, module, and process values must match those rows. Empty-state output is valid only when the structured facts are empty or unavailable.
- `freshness_check`: checks whether an existing structural fact block matches freshly extracted GitNexus fact records and the current fingerprint. A mismatch means the block is stale and should be refreshed; it does not make the block legacy.

Action routing:

| Existing block state | Freshness and baseline | Action |
|----------------------|------------------------|--------|
| Legacy prose block or structural check fails | Any | `manual_migration`; background reports only |
| Structural fact block | Current facts and fingerprint match the baseline | `skip` |
| Structural fact block | Current facts match and baseline is missing | `rebaseline`; background writes cache only |
| Structural fact block | Current facts differ from the block, fingerprint differs from the baseline, or count drift crossed threshold | `refresh_facts`; background may re-render the managed block |

Validation is data verification, not prose review. If fact extraction fails or a newly rendered block fails `render_consistency_check`, keep the existing block and baseline.

## Plan Changes

Change `plan_subdirs()` from copy/generate/skip to block-oriented actions:

```json
{
  "refresh_facts": [{"dir": "src/core", "reason": "gitnexus_fingerprint_changed"}],
  "render": [{"dir": "src/core", "files": ["CLAUDE.md", "AGENTS.md"]}],
  "rebaseline": [{"dir": "src/cache-lost", "reason": "structural_fact_block_current_missing_sidecar"}],
  "bootstrap": [{"dir": "src/core", "files": ["AGENTS.md"]}],
  "manual_migration": [{"dir": "src/legacy", "reason": "legacy_prose_block_without_baseline"}],
  "skip": [{"dir": "src/api", "reason": "fresh"}]
}
```

There should be no subdirectory whole-file `copy` action. If the other platform doc exists, it may provide placement hints, but it must not be copied as the authoritative source.

`bootstrap` must be marked manual-only. Background plans may render to existing files with existing blocks, but they must not create missing tracked docs.

`rebaseline` may run in background mode only when the existing block is structural and current. It writes `SUBDIR_HARNESS.state.json` and per-file rendered state, but leaves the platform doc bytes unchanged.

`manual_migration` must be marked manual-only. Background plans may report it, but must not rewrite the block or advance the baseline.

## Trigger Model

Manual `/harness-init` remains the only path that can bootstrap missing subdirectory platform docs.

PostToolUse background refresh is in scope for main/master. It should extend the existing branch-guarded background worker after GitNexus freshness and CODE_MAP refresh. The worker should compute stale subdirectory harness blocks and refresh only those that cross the stale rules.

PostToolUse rules:

- only after git operations;
- branch guarded: write on main/master only for hook-triggered work;
- non-blocking;
- bounded by stale checks;
- refresh deterministic fact sections only;
- render only to existing platform docs that contain a fact-baselined `<!-- harness:start/end -->` block;
- rebaseline structural fact blocks that are current but lost sidecar state, without changing doc bytes;
- never bootstrap missing subdirectory docs;
- never convert legacy prose blocks;
- no writes outside `<!-- harness:start/end -->`.

Manual `/harness-init` remains branch-pinned and may refresh subdirectory harness blocks on the branch from which it was dispatched.

SessionStart should stay lightweight. It may warn that subdirectory harness blocks are stale, but it should not run GitNexus refresh work.

## Error Handling

- Missing GitNexus index: report refresh skipped or render explicit empty state only in manual bootstrap.
- Stale GitNexus index: plan `gitnexus.analyze` first, then compute fingerprints.
- Fact extraction timeout: keep existing block and do not advance baseline.
- Render consistency failure: keep existing block and do not advance baseline.
- One platform file write fails: record file-level `write_failed`; do not mark that platform file current.
- Cache write fails: do not render a newly generated block, because the source of truth was not persisted.

## Migration

Existing subdirectory docs should be migrated in place:

- If a doc has a fact-baselined `<!-- harness:start/end -->` block, keep all surrounding content and replace the block on refresh.
- If a doc has a structural fact block but no sidecar state, background refresh may rebuild the sidecar baseline only when `freshness_check` passes, leaving the file unchanged.
- If a doc has a structural fact block with stale values, background refresh may re-render the managed block through `refresh_facts`.
- If a doc has a legacy prose block, background refresh must leave the file unchanged and report `manual_migration_required`.
- Manual migration from legacy prose to facts-only must preserve the old prose verbatim outside the managed block. Move the old block body under `## 补充约束（手动维护）`, preferably under a subheading such as `### 从旧 harness 块迁移`, then render the new `## GitNexus 事实` block. If the same migrated text is already present outside the block, do not duplicate it.
- If a structural fact block passes `freshness_check` but only lacks sidecar state, manual migration may also rebaseline the existing fact block without moving any text.
- If only one platform doc exists, do not copy it wholesale. Manual `/harness-init` may create the missing platform doc with the minimal shell and accepted block. Background workers must leave the missing file absent.
- If old copied platform docs are byte-identical, leave them as-is outside the managed block.
- If platform docs have diverged, preserve both documents' manual text.

## Documentation Updates

Update both skill files and README to state:

- Root docs use `<!-- codemap:start/end -->` for CODE_MAP.
- Subdirectory docs use `<!-- harness:start/end -->` for generated GitNexus facts.
- Platform docs are not synchronized by copying.
- Only managed blocks are automatically refreshed.
- Subdirectory block refresh is driven by GitNexus/code fact baselines, not by mtime.
- Prose interpretation and constraints are hand-authored outside the managed block.

## Context Budget

Codex and Claude can load root and nested docs together. Root `CODE_MAP` plus every applicable parent/current subdirectory harness block must fit platform context budgets.

Budget rules:

- Keep the deterministic fact block compact: top caller counts, top affected modules, and top processes only.
- Add a per-block byte budget, configurable but defaulting conservatively enough for nested Codex `AGENTS.md` loading.
- Before rendering, estimate root `CODE_MAP` bytes plus the nested platform-doc stack: root doc, ancestor subdirectory docs, and the candidate subdirectory block. If the combined platform-doc payload would exceed the budget, render a truncated deterministic fact block with an explicit "truncated" line rather than overflowing.

## Churn Tradeoff

This feature intentionally introduces a new tracked-doc churn source: subdirectory `CLAUDE.md` and `AGENTS.md` managed blocks may change when GitNexus/code facts change. That is the cost of keeping agent-facing facts current.

The product value is passive visibility: agents can see important call graph and process facts without issuing a fresh query first. These facts are still snapshots. They do not replace the root requirement to run GitNexus impact/context queries before risky edits, and they may be briefly stale until the next bounded refresh.

The design limits churn by:

- removing whole-file copy;
- preserving manual text outside markers;
- using cheap-first stale checks;
- refreshing only existing blocks in unattended background jobs;
- using deterministic fact rendering for all managed-block writes.

## Test Strategy

Unit tests should cover:

- Subdirectory `sync-docs.py` no longer whole-file copies when both docs exist.
- Existing text outside `<!-- harness:start/end -->` is preserved exactly.
- Missing platform doc is bootstrapped with a minimal shell plus block only in manual mode.
- Background mode does not create missing subdirectory platform docs.
- Same accepted block renders into both `CLAUDE.md` and `AGENTS.md`.
- Sidecar state round-trips and is keyed by Git common dir.
- Symbol count drift triggers stale.
- Source fingerprint change triggers stale.
- GitNexus fingerprint change triggers stale even when symbol count is unchanged.
- Unchanged repository source fingerprint skips expensive GitNexus fingerprinting.
- Changed repository source fingerprint plus unchanged directory source fingerprint does not mark a directory fresh without graph checking or `stale_pending_graph_check`.
- Cypher rows in different orders render the same block and hash after stable sorting.
- A structural fact block whose values differ from current GitNexus facts plans `refresh_facts`, not `manual_migration`.
- A structural fact block that is current but lacks sidecar baseline plans `rebaseline`, and background mode writes state while leaving the file unchanged.
- A legacy prose block plans `manual_migration`, and background mode leaves the file unchanged.
- Manual migration moves the old block body under `## 补充约束（手动维护）` without duplicating existing migrated text.
- Failed fact extraction does not advance baseline.
- Failed render consistency check does not advance baseline.
- File write failure records retryable state.
- `plan_subdirs()` emits block-oriented actions and no `copy` action.
- Context-budget truncation preserves a structural facts-only managed block.
- Skill docs describe block-only refresh.

Integration tests should cover:

- Running `/harness-init` on a project with one existing subdirectory platform doc creates the missing platform doc only in manual mode and updates only managed blocks.
- Manual text before and after the block remains unchanged.
- Running background refresh against a facts-only block whose caller count changed from `23` to `40` refreshes the managed block instead of reporting manual migration.
- Running background refresh against a facts-only `<!-- harness:start/end -->` block with missing cache state rebaselines sidecar state without changing doc bytes.
- Running background refresh against an old prose-style `<!-- harness:start/end -->` block reports manual migration and does not rewrite the doc.
- Running manual migration against an old prose-style block preserves the old prose outside the marker and writes a facts-only managed block.
- Re-running with unchanged fingerprints is a no-op.
- Changing a mocked GitNexus impact result refreshes the block.
- PostToolUse refresh on main updates deterministic facts in an existing block but does not create new files or write prose.

## Acceptance Criteria

- No subdirectory platform doc is rewritten wholesale by harness.
- No mtime-based subdirectory `CLAUDE.md` <-> `AGENTS.md` copy remains in the refresh/sync path.
- All automatic subdirectory content lives inside `<!-- harness:start/end -->`.
- Both platform docs can receive the same accepted harness block without sharing non-managed text.
- Stale decisions use sidecar baselines and at least one semantic fingerprint beyond symbol count.
- Baselines advance only after accepted deterministic fact extraction, render consistency, and persisted state.
- Background-generated fact entries are rendered from structured GitNexus output and checked against those records.
- Background refresh treats structural-but-stale fact blocks as `refresh_facts`, not `manual_migration`.
- Background refresh may rebuild missing sidecar state for a current structural fact block without changing doc bytes.
- Background refresh never replaces a legacy prose block.
- Manual legacy migration preserves old prose outside the managed block before writing facts.
- Rendered fact entries have a stable total ordering, so equivalent GitNexus rows do not cause block-hash churn.
- AI-written prose is never generated or persisted by the subdirectory harness pipeline.
- Missing tracked subdirectory docs are created only through manual `/harness-init`.
- Existing root CODE_MAP behavior and tests continue to pass.

## Fixed Design Decisions

- Store canonical block text and baseline metadata in `SUBDIR_HARNESS.state.json`.
- Render the user-visible managed-block title as `## GitNexus 事实`; keep `Layer 1` as a design-scope term, not an emitted heading.
- Allow background cache-only rebaseline for current structural fact blocks with missing sidecar state.
- Compute `gitnexus_fingerprint` from the canonical graph inputs listed in this spec.
- Add non-blocking PostToolUse refresh on main/master for deterministic facts in stale existing subdirectory harness blocks only.
- Insert a missing block after `## 测试` when present; otherwise before `## 补充约束（手动维护）` when present; otherwise append it to the file.
- Scope v1 is A: only Layer 1 deterministic facts, no AI prose path, implemented as one GitNexus/Cypher-to-Markdown renderer.
