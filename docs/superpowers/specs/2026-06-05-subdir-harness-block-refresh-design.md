# Subdirectory Harness Block Refresh Design

## Context

The v3.4.6 CODE_MAP redesign made root platform docs stable by rendering the same number-free `CODE_MAP.md` into managed `<!-- codemap:start/end -->` blocks in both `CLAUDE.md` and `AGENTS.md`. It also moved symbol-count baselines into cache-side machine state.

Subdirectory platform docs have not received the same treatment yet. Current behavior still uses mtime-based whole-file copy between `CLAUDE.md` and `AGENTS.md` for subdirectories. That can overwrite platform-specific or hand-authored text, creates unnecessary churn, and does not make subdirectory constraints refresh when code facts change.

This design extends the same projection model to subdirectory harness content:

- `CLAUDE.md` and `AGENTS.md` are independent platform docs.
- The only shared automatic content is a managed `<!-- harness:start/end -->` block.
- That managed block is regenerated from GitNexus-backed facts and rendered into both platform docs.
- Sidecar state records when each directory's harness block was last accepted, so code changes can trigger refresh without whole-file copying.

## Goals

- Remove subdirectory `CLAUDE.md` <-> `AGENTS.md` whole-file copying.
- Preserve all text outside `<!-- harness:start/end -->` exactly.
- Render the same accepted harness block into both platform docs when both files exist.
- Create a missing platform doc only by bootstrapping a minimal shell plus the accepted harness block, not by copying the other platform doc wholesale.
- Refresh subdirectory harness blocks when code facts change enough to cross a stale threshold.
- Keep refresh state out of Git, in the existing harness cache area.
- Require generated constraints and dangerous-operation entries to cite GitNexus-derived facts.

## Non-Goals

- Do not auto-refresh root doc prose such as project positioning, build commands, concepts, or danger notes.
- Do not change root `CODE_MAP` block semantics.
- Do not make SessionStart run heavy GitNexus or AI work.
- Do not rewrite manual sections outside managed markers.
- Do not treat symbol count as the only stale signal.
- Do not make platform-specific docs byte-identical.

## Terminology

- Platform doc: `CLAUDE.md` or `AGENTS.md`.
- Root CODE_MAP block: `<!-- codemap:start/end -->` in root platform docs.
- Subdirectory harness block: `<!-- harness:start/end -->` in subdirectory platform docs.
- Harness block source: the accepted generated Markdown fragment stored in cache for one directory.
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

2. Generate:
   - Build one canonical harness block for each stale or missing directory.
   - Use GitNexus facts as the source for constraints and dangerous operations.
   - Validate that each generated fact-bearing entry cites an allowed source.
   - Store the accepted block and baseline in cache-side state.

3. Render:
   - Insert or replace only `<!-- harness:start/end -->` in each platform doc.
   - Leave every byte outside the managed block unchanged.
   - If a platform doc is missing, create a minimal platform-specific shell and insert the accepted block.

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
      "source_fingerprint": "sha256:...",
      "gitnexus_fingerprint": "sha256:...",
      "block_hash": "sha256:...",
      "block": "## 约束（基于 GitNexus 事实）\n...",
      "accepted_at": "2026-06-05T00:00:00Z",
      "rendered": {
        "CLAUDE.md": {"status": "updated", "block_hash": "sha256:..."},
        "AGENTS.md": {"status": "updated", "block_hash": "sha256:..."}
      }
    }
  }
}
```

State is machine/cache data. It should not be committed. Losing it should cause extra refresh work, not incorrect docs.

## Fingerprints And Stale Rules

Use multiple stale signals:

- Missing `<!-- harness:start/end -->` block.
- Missing sidecar baseline.
- Low-quality or invalid harness block.
- Symbol count drift at or above the existing `STALE_THRESHOLD` of `0.2`.
- Source fingerprint change for files under the directory.
- GitNexus fingerprint change for relevant facts:
  - core symbols selected for the directory;
  - incoming caller counts;
  - upstream impact summary;
  - affected modules;
  - participating execution flows.

The GitNexus fingerprint should be compact and deterministic. It does not need to store the full tool output, but it must include enough stable facts to detect semantic changes where symbol count does not move.

Use these canonical fingerprint inputs:

- tracked source-file content hash for files under the directory, excluding `CLAUDE.md`, `AGENTS.md`, generated caches, and ignored artifacts;
- GitNexus symbols defined under the directory: symbol id or name, kind, file path, and start line;
- incoming graph edges targeting directory symbols, grouped by edge type and target symbol;
- outgoing graph edges from directory symbols, grouped by edge type and source symbol;
- process ids or labels containing directory symbols;
- community/folder symbol count used by CODE_MAP planning.

The implementation may obtain these facts through `npx gitnexus cypher` rather than MCP tools, because the refresh workers run as local scripts.

Only advance the baseline after:

- generation completed successfully;
- source validation passed;
- the accepted block was written to cache;
- rendering either succeeded for all target files or recorded a retryable write failure without claiming the file is current.

Do not advance the baseline when generation fails, validation fails, or the final block remains low quality.

## Managed Block Rendering

Add a renderer for subdirectory harness blocks:

```md
<!-- harness:start -->
## 约束（基于 GitNexus 事实）

- ...

## 危险操作（基于 GitNexus impact 分析）

- ...
<!-- harness:end -->
```

Rendering rules:

- If a block exists, replace only the marker range.
- If no block exists, insert a block after the `## 测试` section when present; otherwise append near the end before `## 补充约束（手动维护）` if present; otherwise append to the file.
- If the file is missing, create a platform-specific shell:

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

The generated harness block should contain only fact-backed items:

- Constraints must cite `gitnexus_context`, `gitnexus_impact`, or `gitnexus_query`.
- Dangerous operations must cite `gitnexus_impact`.
- If GitNexus is unavailable or stale and cannot be refreshed, do not generate fact-bearing constraints. Render an empty but explicit managed block only if needed:

```md
## 约束（基于 GitNexus 事实）

暂无已验证约束。

## 危险操作（基于 GitNexus impact 分析）

暂无已验证危险操作。
```

The generator may inspect the source for a cited high-risk symbol only after GitNexus identifies that symbol. It must not read an entire directory and infer constraints without graph evidence.

## Validation

Add a lightweight validator before accepting a generated block:

- Every non-empty constraint line must contain a source marker such as `gitnexus_context:`, `gitnexus_impact:`, or `gitnexus_query:`.
- Every dangerous-operation line must contain `gitnexus_impact:` and a risk or caller count.
- Placeholder text such as `TODO`, `{符号名}`, or template braces is rejected.
- A block with no fact-bearing items is valid only when it uses the explicit empty-state wording.

Validation is not a replacement for model quality, but it prevents uncited prose from becoming persistent project instructions.

## Plan Changes

Change `plan_subdirs()` from copy/generate/skip to block-oriented actions:

```json
{
  "refresh": [{"dir": "src/core", "reason": "gitnexus_fingerprint_changed"}],
  "render": [{"dir": "src/core", "files": ["CLAUDE.md", "AGENTS.md"]}],
  "bootstrap": [{"dir": "src/core", "files": ["AGENTS.md"]}],
  "skip": [{"dir": "src/api", "reason": "fresh"}]
}
```

There should be no subdirectory whole-file `copy` action. If the other platform doc exists, it may provide placement hints or manual shell inspiration, but it must not be copied as the authoritative source.

## Trigger Model

Manual `/harness-init` remains the main path for generating or refreshing subdirectory harness blocks.

PostToolUse background refresh is in scope for main/master. It should extend the existing branch-guarded background worker after GitNexus freshness and CODE_MAP refresh. The worker should compute stale subdirectory harness blocks and refresh only those that cross the stale rules.

PostToolUse rules:

- only after git operations;
- branch guarded: write on main/master only for hook-triggered work;
- non-blocking;
- bounded by stale checks;
- no writes outside `<!-- harness:start/end -->`.

Manual `/harness-init` remains branch-pinned and may refresh subdirectory harness blocks on the branch from which it was dispatched.

SessionStart should stay lightweight. It may warn that subdirectory harness blocks are stale, but it should not run GitNexus/AI refresh work.

## Error Handling

- Missing GitNexus index: report refresh skipped or render explicit empty state only when bootstrapping is required.
- Stale GitNexus index: plan `gitnexus.analyze` first, then compute fingerprints.
- Generation timeout: keep existing block and do not advance baseline.
- Validation failure: keep existing block and do not advance baseline.
- One platform file write fails: record file-level `write_failed`; do not mark that platform file current.
- Cache write fails: do not render a newly generated block, because the source of truth was not persisted.

## Migration

Existing subdirectory docs should be migrated in place:

- If a doc has a `<!-- harness:start/end -->` block, keep all surrounding content and replace the block on refresh.
- If only one platform doc exists, do not copy it wholesale. Create the missing platform doc with the minimal shell and accepted block.
- If old copied platform docs are byte-identical, leave them as-is outside the managed block.
- If platform docs have diverged, preserve both documents' manual text.

## Documentation Updates

Update both skill files and README to state:

- Root docs use `<!-- codemap:start/end -->` for CODE_MAP.
- Subdirectory docs use `<!-- harness:start/end -->` for generated constraints.
- Platform docs are not synchronized by copying.
- Only managed blocks are automatically refreshed.
- Subdirectory block refresh is driven by GitNexus/code fact baselines, not by mtime.

## Test Strategy

Unit tests should cover:

- Subdirectory `sync-docs.py` no longer whole-file copies when both docs exist.
- Existing text outside `<!-- harness:start/end -->` is preserved exactly.
- Missing platform doc is bootstrapped with a minimal shell plus block.
- Same accepted block renders into both `CLAUDE.md` and `AGENTS.md`.
- Sidecar state round-trips and is keyed by Git common dir.
- Symbol count drift triggers stale.
- Source fingerprint change triggers stale.
- GitNexus fingerprint change triggers stale even when symbol count is unchanged.
- Failed generation does not advance baseline.
- Failed validation does not advance baseline.
- File write failure records retryable state.
- `plan_subdirs()` emits block-oriented actions and no `copy` action.
- Skill docs describe block-only refresh.

Integration tests should cover:

- Running `/harness-init` on a project with one existing subdirectory platform doc creates or updates only managed blocks.
- Manual text before and after the block remains unchanged.
- Re-running with unchanged fingerprints is a no-op.
- Changing a mocked GitNexus impact result refreshes the block.

## Acceptance Criteria

- No subdirectory platform doc is rewritten wholesale by harness.
- No mtime-based subdirectory `CLAUDE.md` <-> `AGENTS.md` copy remains in the refresh/sync path.
- All automatic subdirectory content lives inside `<!-- harness:start/end -->`.
- Both platform docs can receive the same accepted harness block without sharing non-managed text.
- Stale decisions use sidecar baselines and at least one semantic fingerprint beyond symbol count.
- Baselines advance only after accepted generation and persisted state.
- Generated fact-bearing entries include GitNexus source markers.
- Existing root CODE_MAP behavior and tests continue to pass.

## Fixed Design Decisions

- Store canonical block text and baseline metadata in `SUBDIR_HARNESS.state.json`.
- Compute `gitnexus_fingerprint` from the canonical graph inputs listed in this spec.
- Add non-blocking PostToolUse refresh on main/master for stale subdirectory harness blocks.
- Insert a missing block after `## 测试` when present; otherwise before `## 补充约束（手动维护）` when present; otherwise append it to the file.
