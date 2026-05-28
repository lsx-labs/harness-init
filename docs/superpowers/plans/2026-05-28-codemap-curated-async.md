# CODE_MAP Curated Async Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve CODE_MAP generation so it runs asynchronously, refreshes only directories that need attention, and rejects low-quality descriptions.

**Architecture:** Keep GitNexus-driven structure generation, but move description quality rules into shared helpers used by planning, rendering, and description generation. Background hooks should record job status and write CODE_MAP atomically so failures do not corrupt the current map.

**Tech Stack:** Python standard library, pytest, existing harness-init scripts.

---

### Task 1: Description Quality Gate

**Files:**
- Modify: `scripts/harness_shared.py`
- Modify: `scripts/generate_descriptions.py`
- Modify: `scripts/harness_monitor.py`
- Test: `tests/test_generate_descriptions.py`
- Test: `tests/test_harness_monitor.py`

- [ ] Add shared helpers that classify empty, manual, low-confidence, low-quality, and acceptable descriptions.
- [ ] Make `parse_codemap("--generate")` include empty, low-confidence, and low-quality entries.
- [ ] Reject AI descriptions that are function-name lists, truncated tokens, generic test strings, or known fallback fragments.
- [ ] Ensure fallback only writes trusted docstrings or low-confidence `⚠️` entries, never overwriting curated descriptions.

### Task 2: Incremental Structure Preservation

**Files:**
- Modify: `scripts/harness_monitor.py`
- Test: `tests/test_harness_monitor.py`

- [ ] Preserve manual and acceptable descriptions when rebuilding CODE_MAP.
- [ ] Drop low-quality descriptions during rebuild so they are eligible for regeneration.
- [ ] Keep stale detection directory-scoped.

### Task 3: Async Job Status And Atomic Writes

**Files:**
- Modify: `scripts/harness_monitor.py`
- Test: `tests/test_harness_monitor.py`

- [ ] Add `.local/share/harness-hooks/jobs` status files for background CODE_MAP runs.
- [ ] Print `job_id` when scheduling background work.
- [ ] Write `running`, `completed`, `failed`, and `skipped_locked` states.
- [ ] Use temporary files plus `os.replace` for CODE_MAP writes.

### Task 4: Verification

**Files:**
- Existing tests only.

- [ ] Run targeted tests for `generate_descriptions` and `harness_monitor`.
- [ ] Run full `pytest -q`.
