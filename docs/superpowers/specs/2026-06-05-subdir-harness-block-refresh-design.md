# Subdirectory Harness Block Refresh Design

## Context

The v3.4.6 CODE_MAP redesign made root platform docs stable by rendering the same number-free `CODE_MAP.md` into managed `<!-- codemap:start/end -->` blocks in both `CLAUDE.md` and `AGENTS.md`. It also moved symbol-count baselines into cache-side machine state.

Subdirectory platform docs have not received the same treatment yet. Current behavior still uses mtime-based whole-file copy between `CLAUDE.md` and `AGENTS.md` for subdirectories. That can overwrite platform-specific or hand-authored text, creates unnecessary churn, and does not make subdirectory constraints refresh when code facts change.

This design extends the same projection model to subdirectory harness content, with one important difference from root CODE_MAP: subdirectory content can become operating instructions for future agents, so background automation may only persist deterministic facts. AI-written interpretation is allowed only through a manual, reviewable path.

- `CLAUDE.md` and `AGENTS.md` are independent platform docs.
- The only shared automatic content is a managed `<!-- harness:start/end -->` block.
- The background-managed portion of that block is deterministic GitNexus fact output, not AI prose.
- AI-written interpretation is separated from the background fact layer and may be produced only by manual `/harness-init` flows that surface the result for review.
- Sidecar state records when each directory's harness block was last accepted, so code changes can trigger refresh without whole-file copying.

## Goals

- Remove subdirectory `CLAUDE.md` <-> `AGENTS.md` whole-file copying.
- Preserve all text outside `<!-- harness:start/end -->` exactly.
- Render the same accepted deterministic fact block into both platform docs when both files exist.
- Create a missing platform doc only in manual `/harness-init`, by bootstrapping a minimal shell plus the accepted harness block, not by copying the other platform doc wholesale.
- Refresh subdirectory harness blocks when code facts change enough to cross a stale threshold.
- Keep refresh state out of Git, in the existing harness cache area.
- Require AI-written interpretive constraints to stay out of the unattended background path.

## Non-Goals

- Do not auto-refresh root doc prose such as project positioning, build commands, concepts, or danger notes.
- Do not change root `CODE_MAP` block semantics.
- Do not make SessionStart run heavy GitNexus or AI work.
- Do not rewrite manual sections outside managed markers.
- Do not treat symbol count as the only stale signal.
- Do not make platform-specific docs byte-identical.
- Do not let unattended background jobs persist AI-generated prose instructions.
- Do not create new tracked subdirectory platform-doc files from unattended background jobs.

## Terminology

- Platform doc: `CLAUDE.md` or `AGENTS.md`.
- Root CODE_MAP block: `<!-- codemap:start/end -->` in root platform docs.
- Subdirectory harness block: `<!-- harness:start/end -->` in subdirectory platform docs.
- Deterministic fact block: a generated Markdown fragment rendered directly from GitNexus CLI/Cypher output with no AI interpretation.
- AI review block: optional prose interpretation created by manual `/harness-init`, never by unattended background jobs.
- Harness block source: the accepted deterministic fact block, plus any optional reviewed AI block, stored in cache for one directory.
- Refresh baseline: cache-side metadata describing the GitNexus/code facts that were true when the current harness block was accepted.

## Current Behavior To Replace

`scripts/sync_docs.py` currently keeps subdirectory platform docs in sync by comparing mtimes and copying the newer whole file. `scripts/harness_plan.py` also plans subdirectory docs as:

- skip when the current platform's doc exists;
- copy from the other platform when only the other platform's doc exists;
- generate only when neither exists.

This means existing subdirectory docs rarely regenerate, and cross-platform consistency is achieved by copying entire files rather than rendering a managed block.

## Proposed Architecture

Introduce a subdirectory harness block pipeline with four parts:

1. Plan:
   - Determine complex directories from CODE_MAP/GitNexus counts as today.
   - For each complex directory, classify platform docs as missing, present-with-block, or present-without-block.
   - Determine stale status from sidecar state plus live GitNexus/code fingerprints.

2. Extract facts:
   - Use a single shared script to query GitNexus and build deterministic fact records.
   - Render caller counts, blast radius, affected modules, and process participation directly from those records.
   - No AI is involved in this path, so numeric facts can be verified against the extracted records.

3. Optional manual interpretation:
   - Manual `/harness-init` may ask an AI to summarize the deterministic facts into prose constraints.
   - The AI result must be stored as a review candidate, not silently promoted by PostToolUse.
   - A reviewed/accepted AI block can be rendered alongside the deterministic fact block.

4. Render:
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
- validate any optional AI review block before it can be accepted.

Invocation modes:

- `--plan`: report stale directories and reasons without writing.
- `--refresh-facts`: refresh deterministic fact blocks and render to existing docs.
- `--bootstrap`: manual-only mode that may create missing platform docs.
- `--review-ai`: manual-only mode that records an AI interpretation candidate.
- `--accept-ai`: manual-only mode that promotes a reviewed AI candidate into the rendered harness block.

Both `/harness-init` and PostToolUse workers must use this script. They may pass different modes, but not different generation logic.

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
      "ai_block": "",
      "ai_review": {
        "status": "none",
        "candidate_hash": ""
      },
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

Use cheap-first stale checks, without allowing cheap checks to hide cross-directory graph changes:

- Missing `<!-- harness:start/end -->` block.
- Missing sidecar baseline.
- Invalid deterministic fact block.
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

The repository source fingerprint is the first global gate. If it is unchanged, the background path can skip all subdirectory graph work. If the repository source fingerprint changed, directory and known-caller source fingerprints are used only to prioritize and cap work; they do not prove freshness by themselves, because new incoming callers can originate outside the directory and outside the previous caller set.

When the repository source fingerprint changed, the worker must either compute the GitNexus fingerprint for candidate directories or record that the directory remains `stale_pending_graph_check`. It must not mark a directory fresh solely because its own source fingerprint and symbol count are unchanged.

The GitNexus fingerprint should be compact and deterministic. It does not need to store the full tool output, but it must include enough stable facts to detect semantic changes where symbol count does not move.

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

Do not advance the baseline when extraction fails, validation fails, or the final block remains invalid.

Background work should be bounded. A worker should cap the number of subdirectories refreshed in one run and record remaining stale directories for later retry rather than spending unbounded time on a single PostToolUse job.

## Managed Block Rendering

Add a renderer for subdirectory harness blocks:

```md
<!-- harness:start -->
## GitNexus 事实

- 被调用: ...
- 影响面: ...
- 相关流程: ...

## 已审核约束

暂无已审核约束。
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

These rows must be rendered from structured data produced by `npx gitnexus cypher` or another non-AI GitNexus CLI output. The validator must compare rendered values against that structured data, not just check for citation strings.

The optional AI review path has stricter limits:

- Constraints must cite `gitnexus_context`, `gitnexus_impact`, or `gitnexus_query`.
- Dangerous operations must cite `gitnexus_impact`.
- AI-written text is never accepted by PostToolUse.
- Manual `/harness-init` must present the candidate block and require explicit user acceptance before promotion.
- If GitNexus is unavailable or stale and cannot be refreshed, do not generate fact-bearing constraints. Render an empty but explicit managed block only if needed:

```md
## GitNexus 事实

暂无已验证图谱事实。

## 已审核约束

暂无已审核约束。
```

The generator may inspect the source for a cited high-risk symbol only after GitNexus identifies that symbol. It must not read an entire directory and infer constraints without graph evidence.

## Validation

Add two validators:

1. Deterministic fact validator:
   - Rendered caller, impact, module, and process values must match the structured GitNexus rows used to build the block.
   - No AI prose may appear in the deterministic fact section.
   - Empty-state output is valid only when the structured facts are empty or unavailable.

2. AI review validator:
   - Every non-empty constraint line must contain a source marker such as `gitnexus_context:`, `gitnexus_impact:`, or `gitnexus_query:`.
   - Every dangerous-operation line must contain `gitnexus_impact:` and a risk or caller count.
   - Placeholder text such as `TODO`, `{符号名}`, or template braces is rejected.
   - Passing validation is not enough for promotion; manual acceptance is still required.

Validation is not a replacement for human review of AI prose. The deterministic validator is the only validator allowed in unattended background refreshes.

## Plan Changes

Change `plan_subdirs()` from copy/generate/skip to block-oriented actions:

```json
{
  "refresh_facts": [{"dir": "src/core", "reason": "gitnexus_fingerprint_changed"}],
  "review_ai": [{"dir": "src/core", "reason": "manual_request"}],
  "render": [{"dir": "src/core", "files": ["CLAUDE.md", "AGENTS.md"]}],
  "bootstrap": [{"dir": "src/core", "files": ["AGENTS.md"]}],
  "skip": [{"dir": "src/api", "reason": "fresh"}]
}
```

There should be no subdirectory whole-file `copy` action. If the other platform doc exists, it may provide placement hints, but it must not be copied as the authoritative source.

`bootstrap` must be marked manual-only. Background plans may render to existing files with existing blocks, but they must not create missing tracked docs.

## Trigger Model

Manual `/harness-init` remains the only path that can bootstrap missing subdirectory platform docs or promote AI-written review content.

PostToolUse background refresh is in scope for main/master. It should extend the existing branch-guarded background worker after GitNexus freshness and CODE_MAP refresh. The worker should compute stale subdirectory harness blocks and refresh only those that cross the stale rules.

PostToolUse rules:

- only after git operations;
- branch guarded: write on main/master only for hook-triggered work;
- non-blocking;
- bounded by stale checks;
- refresh deterministic fact sections only;
- render only to existing platform docs that already contain `<!-- harness:start/end -->`;
- never bootstrap missing subdirectory docs;
- never promote AI-written review content;
- no writes outside `<!-- harness:start/end -->`.

Manual `/harness-init` remains branch-pinned and may refresh subdirectory harness blocks on the branch from which it was dispatched.

SessionStart should stay lightweight. It may warn that subdirectory harness blocks are stale, but it should not run GitNexus/AI refresh work.

## Error Handling

- Missing GitNexus index: report refresh skipped or render explicit empty state only in manual bootstrap.
- Stale GitNexus index: plan `gitnexus.analyze` first, then compute fingerprints.
- Fact extraction timeout: keep existing block and do not advance baseline.
- AI review timeout: discard candidate and do not affect deterministic facts or baselines.
- Validation failure: keep existing block and do not advance baseline.
- One platform file write fails: record file-level `write_failed`; do not mark that platform file current.
- Cache write fails: do not render a newly generated block, because the source of truth was not persisted.

## Migration

Existing subdirectory docs should be migrated in place:

- If a doc has a `<!-- harness:start/end -->` block, keep all surrounding content and replace the block on refresh.
- If only one platform doc exists, do not copy it wholesale. Manual `/harness-init` may create the missing platform doc with the minimal shell and accepted block. Background workers must leave the missing file absent.
- If old copied platform docs are byte-identical, leave them as-is outside the managed block.
- If platform docs have diverged, preserve both documents' manual text.

## Documentation Updates

Update both skill files and README to state:

- Root docs use `<!-- codemap:start/end -->` for CODE_MAP.
- Subdirectory docs use `<!-- harness:start/end -->` for generated constraints.
- Platform docs are not synchronized by copying.
- Only managed blocks are automatically refreshed.
- Subdirectory block refresh is driven by GitNexus/code fact baselines, not by mtime.
- Background refresh writes deterministic facts only; AI prose requires manual review and acceptance.

## Context Budget

Codex and Claude can load root and nested docs together. Root `CODE_MAP` plus every applicable parent/current subdirectory harness block must fit platform context budgets.

Budget rules:

- Keep the deterministic fact block compact: top caller counts, top affected modules, and top processes only.
- Add a per-block byte budget, configurable but defaulting conservatively enough for nested Codex `AGENTS.md` loading.
- Before rendering, estimate root `CODE_MAP` bytes plus the nested platform-doc stack: root doc, ancestor subdirectory docs, and the candidate subdirectory block. If the combined platform-doc payload would exceed the budget, render a truncated deterministic fact block with an explicit "truncated" line rather than overflowing.
- AI review blocks should be even smaller than fact blocks and are manual-only.

## Churn Tradeoff

This feature intentionally introduces a new tracked-doc churn source: subdirectory `CLAUDE.md` and `AGENTS.md` managed blocks may change when GitNexus/code facts change. That is the cost of keeping executable agent constraints current.

The design limits churn by:

- removing whole-file copy;
- preserving manual text outside markers;
- using cheap-first stale checks;
- refreshing only existing blocks in unattended background jobs;
- using deterministic fact rendering for background writes;
- requiring manual acceptance for AI prose.

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
- Failed fact extraction does not advance baseline.
- AI review generation never mutates docs without explicit acceptance.
- Failed validation does not advance baseline.
- File write failure records retryable state.
- `plan_subdirs()` emits block-oriented actions and no `copy` action.
- Context-budget truncation preserves a valid managed block.
- Skill docs describe block-only refresh.

Integration tests should cover:

- Running `/harness-init` on a project with one existing subdirectory platform doc creates the missing platform doc only in manual mode and updates only managed blocks.
- Manual text before and after the block remains unchanged.
- Re-running with unchanged fingerprints is a no-op.
- Changing a mocked GitNexus impact result refreshes the block.
- PostToolUse refresh on main updates deterministic facts in an existing block but does not create new files or write AI prose.

## Acceptance Criteria

- No subdirectory platform doc is rewritten wholesale by harness.
- No mtime-based subdirectory `CLAUDE.md` <-> `AGENTS.md` copy remains in the refresh/sync path.
- All automatic subdirectory content lives inside `<!-- harness:start/end -->`.
- Both platform docs can receive the same accepted harness block without sharing non-managed text.
- Stale decisions use sidecar baselines and at least one semantic fingerprint beyond symbol count.
- Baselines advance only after accepted deterministic fact extraction, validation, and persisted state.
- Background-generated fact entries are rendered from structured GitNexus output and validated against those records.
- AI-written prose never lands through unattended background refresh.
- Missing tracked subdirectory docs are created only through manual `/harness-init`.
- Existing root CODE_MAP behavior and tests continue to pass.

## Fixed Design Decisions

- Store canonical block text and baseline metadata in `SUBDIR_HARNESS.state.json`.
- Compute `gitnexus_fingerprint` from the canonical graph inputs listed in this spec.
- Add non-blocking PostToolUse refresh on main/master for deterministic facts in stale existing subdirectory harness blocks only.
- Insert a missing block after `## 测试` when present; otherwise before `## 补充约束（手动维护）` when present; otherwise append it to the file.
