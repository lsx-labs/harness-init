# Subdirectory Harness Facts Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace subdirectory `CLAUDE.md` / `AGENTS.md` whole-file copying with a facts-only `<!-- harness:start/end -->` block refresh pipeline backed by deterministic GitNexus facts and cache-side baselines.

**Architecture:** Add one deterministic generator script, `scripts/generate_subdir_harness.py`, that owns subdirectory fact extraction, structural checks, freshness checks, rendering, migration planning, and sidecar state. Existing orchestrators call that script instead of copying subdirectory docs wholesale. Background refresh only updates fact-baselined or structural facts-only blocks; old prose blocks are reported for manual migration and never overwritten automatically.

**Tech Stack:** Python standard library only, existing GitNexus CLI output parsing helpers, `pytest`, existing harness cache root under `~/.local/share/harness-hooks/codemaps/<project-key>/`, existing `scripts/harness_shared.py`, `scripts/harness_plan.py`, `scripts/sync_docs.py`, `scripts/harness_monitor.py`, `install.py`, README, and both skill files.

---

## Spec Source

Implement the current design in:

```text
docs/superpowers/specs/2026-06-05-subdir-harness-block-refresh-design.md
```

The important fixed choices are:

- automatic subdirectory block is facts only;
- generated heading is `## GitNexus 事实`;
- no AI prose path exists;
- no subdirectory whole-file `CLAUDE.md` <-> `AGENTS.md` copying remains in refresh/sync paths;
- background may refresh stale structural fact blocks;
- background may cache-only rebaseline current structural fact blocks that lost sidecar state;
- background must not convert legacy prose blocks;
- missing subdirectory platform docs are created only by manual bootstrap.

## File Responsibilities

- Create: `scripts/generate_subdir_harness.py`
  - Single shared generator and CLI for subdirectory harness facts.
  - Owns GitNexus fact extraction, stable sorting, fingerprints, structural checks, freshness checks, render consistency checks, sidecar state, block rendering, manual bootstrap, and legacy migration.

- Modify: `scripts/harness_shared.py`
  - Add shared constants and cache-path helper for `SUBDIR_HARNESS.state.json`.
  - Keep low-level atomic write/cache key utilities in one place.

- Modify: `scripts/harness_plan.py`
  - Replace old subdirectory `copy/generate/skip` planning with block-oriented `refresh_facts/render/rebaseline/bootstrap/manual_migration/skip`.
  - Use the new generator in plan mode.

- Modify: `scripts/sync_docs.py`
  - Remove non-root subdirectory mtime copy/sync behavior.
  - Keep root CODE_MAP block behavior unchanged.
  - For subdirectories, report block-only status and delegate fact refresh to the new generator when explicitly asked.

- Modify: `scripts/harness_monitor.py`
  - Stop using `sync_platform_docs()` for subdirectory whole-file copying.
  - After CODE_MAP/root-doc refresh, run bounded background subdirectory fact refresh on main/master only.
  - Preserve branch guards and avoid failing the whole CODE_MAP job if subdirectory refresh has a non-critical failure.

- Modify: `install.py`
  - Install `generate_subdir_harness.py` into `~/.local/share/harness-hooks`.
  - Remove stale copied versions during `--link` cleanup.

- Modify: `README.md`
  - Document the new script, sidecar, block-only refresh, and manual-only migration/bootstrap behavior.

- Modify: `skills/claude/SKILL.md`
  - Replace old generated prose constraints template with facts-only block guidance.
  - Tell `/harness-init` to use manual bootstrap/migration commands for subdirectory docs.

- Modify: `skills/codex/SKILL.md`
  - Same update as Claude, using `AGENTS.md` as the primary platform file.

- Test: `tests/test_subdir_harness.py`
  - New focused unit tests for the generator.

- Modify: `tests/test_harness_shared_gitnexus.py`
  - Cache path helper tests.

- Modify: `tests/test_harness_plan.py`
  - Block-oriented plan tests.

- Modify: `tests/test_sync_docs.py`
  - Regression tests proving subdirectory whole-file copy is gone.

- Modify: `tests/test_harness_monitor.py`
  - Background refresh orchestration and branch guard tests.

- Modify: `tests/test_install.py`
  - New script installation tests.

## Data Model

`SUBDIR_HARNESS.state.json` is stored beside the CODE_MAP cache:

```text
~/.local/share/harness-hooks/codemaps/<project-key>/SUBDIR_HARNESS.state.json
```

State shape:

```json
{
  "schema_version": 1,
  "dirs": {
    "src/core": {
      "symbol_count": 128,
      "repo_source_fingerprint": "sha256:repo",
      "source_fingerprint": "sha256:dir",
      "known_caller_source_fingerprint": "sha256:callers",
      "gitnexus_fingerprint": "sha256:graph",
      "block_hash": "sha256:block",
      "fact_block": "## GitNexus 事实\n\n- 被调用: ...\n",
      "accepted_at": "2026-06-05T00:00:00Z",
      "rendered": {
        "CLAUDE.md": {"status": "updated", "block_hash": "sha256:block"},
        "AGENTS.md": {"status": "updated", "block_hash": "sha256:block"}
      }
    }
  }
}
```

Use `ensure_ascii=False` and sorted keys only where it does not change semantic ordering of fact rows. Fact rows themselves must be sorted before hash/render.

## Task 1: Add Shared State Path Helpers

**Files:**
- Modify: `scripts/harness_shared.py`
- Test: `tests/test_harness_shared_gitnexus.py`

- [ ] **Step 1: Write failing tests for subdirectory state cache path**

Append these tests inside `TestCodemapLocalProjection` in `tests/test_harness_shared_gitnexus.py`:

```python
def test_subdir_harness_state_cache_path_shares_codemap_cache_key(self, tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    common = project / ".git"
    common.mkdir()
    monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": common)

    path = harness_shared.subdir_harness_state_cache_path(project)

    assert path == tmp_path / "cache" / harness_shared.path_key(common) / "SUBDIR_HARNESS.state.json"


def test_read_write_subdir_harness_state_round_trip(self, tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")

    payload = {
        "schema_version": 1,
        "dirs": {
            "src": {
                "block_hash": "sha256:block",
                "fact_block": "## GitNexus 事实\n\n- 被调用: 无",
            }
        },
    }

    assert harness_shared.write_subdir_harness_state(project, payload) is True
    assert harness_shared.read_subdir_harness_state(project) == payload
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_subdir_harness_state_cache_path_shares_codemap_cache_key tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_read_write_subdir_harness_state_round_trip
```

Expected: both tests fail with missing helper attributes.

- [ ] **Step 3: Add shared constants and helpers**

Add to `scripts/harness_shared.py` near CODE_MAP constants:

```python
SUBDIR_HARNESS_STATE_FILENAME = "SUBDIR_HARNESS.state.json"
HARNESS_BLOCK_START = "<!-- harness:start -->"
HARNESS_BLOCK_END = "<!-- harness:end -->"
HARNESS_FACT_HEADING = "## GitNexus 事实"
```

Add helper functions below `codemap_counts_cache_path()`:

```python
def subdir_harness_state_cache_path(project_dir: str | Path = ".") -> Path:
    """Shared subdirectory harness state path for a repo."""
    common = _git_common_dir(project_dir)
    cache_key = path_key(common if common is not None else project_dir)
    return CODEMAP_CACHE_ROOT / cache_key / SUBDIR_HARNESS_STATE_FILENAME


def read_subdir_harness_state(project_dir: str | Path = ".") -> dict:
    """Read cached subdirectory harness state."""
    path = subdir_harness_state_cache_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema_version": 1, "dirs": {}}
    if not isinstance(data, dict):
        return {"schema_version": 1, "dirs": {}}
    dirs = data.get("dirs", {})
    if not isinstance(dirs, dict):
        dirs = {}
    return {"schema_version": 1, "dirs": dirs}


def write_subdir_harness_state(project_dir: str | Path = ".", state: dict | None = None) -> bool:
    """Persist subdirectory harness state into the shared harness cache."""
    payload = state if isinstance(state, dict) else {"schema_version": 1, "dirs": {}}
    payload = {
        "schema_version": 1,
        "dirs": payload.get("dirs", {}) if isinstance(payload.get("dirs", {}), dict) else {},
    }
    try:
        _atomic_write_text(
            subdir_harness_state_cache_path(project_dir),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError:
        return False
    return True
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest -q tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_subdir_harness_state_cache_path_shares_codemap_cache_key tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_read_write_subdir_harness_state_round_trip
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/harness_shared.py tests/test_harness_shared_gitnexus.py
git commit -m "feat: add subdir harness state cache"
```

## Task 2: Build Generator Pure Functions

**Files:**
- Create: `scripts/generate_subdir_harness.py`
- Create: `tests/test_subdir_harness.py`

- [ ] **Step 1: Write failing tests for block rendering and replacement**

Create `tests/test_subdir_harness.py`:

```python
"""Tests for generate_subdir_harness.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_subdir_harness as gsh


def test_render_fact_block_sorts_rows_stably() -> None:
    facts = {
        "caller_counts": [
            {"target": "beta", "count": 2},
            {"target": "alpha", "count": 2},
            {"target": "gamma", "count": 9},
        ],
        "affected_modules": [
            {"module": "zeta", "count": 1},
            {"module": "alpha", "count": 1},
        ],
        "processes": [
            {"process": "Build", "count": 1},
            {"process": "Analyze", "count": 1},
        ],
        "symbol_count": 12,
    }

    rendered = gsh.render_fact_block(facts)

    assert rendered.startswith("## GitNexus 事实\n")
    assert rendered.index("gamma: 9") < rendered.index("alpha: 2") < rendered.index("beta: 2")
    assert rendered.index("alpha: 1") < rendered.index("zeta: 1")
    assert rendered.index("Analyze: 1") < rendered.index("Build: 1")


def test_render_managed_block_contains_only_harness_markers() -> None:
    block = gsh.render_managed_block("## GitNexus 事实\n\n- 被调用: 无")

    assert block == (
        "<!-- harness:start -->\n"
        "## GitNexus 事实\n\n"
        "- 被调用: 无\n"
        "<!-- harness:end -->"
    )


def test_replace_existing_harness_block_preserves_surrounding_text() -> None:
    doc = "# src\n\nmanual before\n\n<!-- harness:start -->\nold\n<!-- harness:end -->\n\nmanual after\n"
    block = gsh.render_managed_block("## GitNexus 事实\n\n- 被调用: 无")

    rendered = gsh.replace_or_insert_harness_block(doc, block)

    assert "manual before" in rendered
    assert "manual after" in rendered
    assert "old" not in rendered
    assert rendered.count("<!-- harness:start -->") == 1
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_render_fact_block_sorts_rows_stably tests/test_subdir_harness.py::test_render_managed_block_contains_only_harness_markers tests/test_subdir_harness.py::test_replace_existing_harness_block_preserves_surrounding_text
```

Expected: tests fail because `scripts/generate_subdir_harness.py` does not exist.

- [ ] **Step 3: Create generator module with render helpers**

Create `scripts/generate_subdir_harness.py`:

```python
#!/usr/bin/env python3
"""Deterministic subdirectory harness fact generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness_shared import (
    HARNESS_BLOCK_END,
    HARNESS_BLOCK_START,
    HARNESS_FACT_HEADING,
    SOURCE_EXTS,
    STALE_THRESHOLD,
    _atomic_write_text,
    gitnexus_markdown_rows,
    parse_gitnexus_markdown,
    read_subdir_harness_state,
    should_skip,
    subdir_harness_state_cache_path,
    write_subdir_harness_state,
)

MAX_FACT_ROWS = 5
DEFAULT_MAX_DIRS = 5
SUPPORTED_FACT_PREFIXES = ("- 被调用:", "- 影响面:", "- 相关模块:", "- 相关流程:", "- 截断:")
LEGACY_PROSE_HEADINGS = (
    "## 约束（基于 GitNexus 事实）",
    "## 危险操作（基于 GitNexus impact 分析）",
)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_rel_dir(dir_path: str) -> str:
    return str(dir_path).strip().strip("/").replace("\\", "/")


def _ranked(items: list[dict], name_key: str, count_key: str = "count") -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            -int(item.get(count_key, 0) or 0),
            str(item.get(name_key, "")),
            str(item.get("path", "")),
            str(item.get("id", "")),
        ),
    )


def render_fact_block(facts: dict, *, max_rows: int = MAX_FACT_ROWS) -> str:
    caller_counts = _ranked(list(facts.get("caller_counts", [])), "target")[:max_rows]
    modules = _ranked(list(facts.get("affected_modules", [])), "module")[:max_rows]
    processes = _ranked(list(facts.get("processes", [])), "process")[:max_rows]

    lines = [HARNESS_FACT_HEADING, ""]
    if caller_counts:
        lines.extend(f"- 被调用: {row['target']}: {int(row.get('count', 0))}" for row in caller_counts)
    else:
        lines.append("- 被调用: 无")
    if modules:
        lines.extend(f"- 相关模块: {row['module']}: {int(row.get('count', 0))}" for row in modules)
    else:
        lines.append("- 相关模块: 无")
    if processes:
        lines.extend(f"- 相关流程: {row['process']}: {int(row.get('count', 0))}" for row in processes)
    else:
        lines.append("- 相关流程: 无")
    return "\n".join(lines).strip()


def render_managed_block(fact_block: str) -> str:
    return f"{HARNESS_BLOCK_START}\n{fact_block.strip()}\n{HARNESS_BLOCK_END}"


def replace_or_insert_harness_block(doc_text: str, managed_block: str) -> str:
    pattern = re.compile(
        rf"{re.escape(HARNESS_BLOCK_START)}.*?{re.escape(HARNESS_BLOCK_END)}",
        re.DOTALL | re.MULTILINE,
    )
    if pattern.search(doc_text):
        return pattern.sub(lambda _: managed_block, doc_text, count=1)
    marker = "## 补充约束（手动维护）"
    if marker in doc_text:
        return doc_text.replace(marker, f"{managed_block}\n\n{marker}", 1)
    suffix = "" if doc_text.endswith("\n") else "\n"
    return f"{doc_text}{suffix}\n{managed_block}\n"
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_render_fact_block_sorts_rows_stably tests/test_subdir_harness.py::test_render_managed_block_contains_only_harness_markers tests/test_subdir_harness.py::test_replace_existing_harness_block_preserves_surrounding_text
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_subdir_harness.py tests/test_subdir_harness.py
git commit -m "feat: render subdir harness fact blocks"
```

## Task 3: Add Structural, Freshness, and Migration Checks

**Files:**
- Modify: `scripts/generate_subdir_harness.py`
- Modify: `tests/test_subdir_harness.py`

- [ ] **Step 1: Write failing tests for routing**

Append:

```python
def test_structural_check_rejects_legacy_prose_block() -> None:
    body = (
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "\n"
        "## 危险操作（基于 GitNexus impact 分析）\n"
        "- **parser.py**: 高影响修改\n"
    )

    result = gsh.structural_fact_block_check(body)

    assert result["ok"] is False
    assert result["reason"] == "legacy_prose"


def test_structural_stale_block_routes_to_refresh_not_manual_migration() -> None:
    existing = "## GitNexus 事实\n\n- 被调用: Parser: 23\n- 相关模块: core: 1\n- 相关流程: Analyze: 1"
    current_facts = {
        "caller_counts": [{"target": "Parser", "count": 40}],
        "affected_modules": [{"module": "core", "count": 1}],
        "processes": [{"process": "Analyze", "count": 1}],
        "symbol_count": 40,
    }

    action = gsh.plan_existing_block_action(existing, current_facts, baseline={"gitnexus_fingerprint": "sha256:old"})

    assert action["action"] == "refresh_facts"
    assert action["reason"] == "freshness_changed"


def test_current_structural_block_without_baseline_routes_to_rebaseline() -> None:
    current_facts = {
        "caller_counts": [{"target": "Parser", "count": 40}],
        "affected_modules": [{"module": "core", "count": 1}],
        "processes": [{"process": "Analyze", "count": 1}],
        "symbol_count": 40,
    }
    existing = gsh.render_fact_block(current_facts)

    action = gsh.plan_existing_block_action(existing, current_facts, baseline=None)

    assert action["action"] == "rebaseline"
    assert action["reason"] == "structural_fact_block_current_missing_sidecar"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_structural_check_rejects_legacy_prose_block tests/test_subdir_harness.py::test_structural_stale_block_routes_to_refresh_not_manual_migration tests/test_subdir_harness.py::test_current_structural_block_without_baseline_routes_to_rebaseline
```

Expected: tests fail because routing helpers do not exist.

- [ ] **Step 3: Add checks and routing**

Add to `scripts/generate_subdir_harness.py`:

```python
def structural_fact_block_check(block_body: str) -> dict:
    text = block_body.strip()
    if any(heading in text for heading in LEGACY_PROSE_HEADINGS):
        return {"ok": False, "reason": "legacy_prose"}
    if "{" in text or "}" in text:
        return {"ok": False, "reason": "template_braces"}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != HARNESS_FACT_HEADING:
        return {"ok": False, "reason": "missing_fact_heading"}
    for line in lines[1:]:
        if not line.startswith(SUPPORTED_FACT_PREFIXES):
            return {"ok": False, "reason": "unsupported_fact_row"}
    return {"ok": True, "reason": "structural_fact_block"}


def extract_harness_block_body(doc_text: str) -> str:
    pattern = re.compile(
        rf"{re.escape(HARNESS_BLOCK_START)}\n?(.*?){re.escape(HARNESS_BLOCK_END)}",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(doc_text)
    return match.group(1).strip() if match else ""


def render_consistency_check(rendered_block: str, facts: dict) -> dict:
    expected = render_fact_block(facts)
    if rendered_block.strip() != expected.strip():
        return {"ok": False, "reason": "render_mismatch"}
    return {"ok": True, "reason": "render_consistent"}


def gitnexus_fingerprint(facts: dict) -> str:
    stable = {
        "caller_counts": _ranked(list(facts.get("caller_counts", [])), "target"),
        "affected_modules": _ranked(list(facts.get("affected_modules", [])), "module"),
        "processes": _ranked(list(facts.get("processes", [])), "process"),
        "symbol_count": int(facts.get("symbol_count", 0) or 0),
    }
    return _sha256_text(json.dumps(stable, ensure_ascii=False, sort_keys=True))


def freshness_check(existing_block: str, facts: dict, baseline: dict | None) -> dict:
    expected = render_fact_block(facts)
    current_fp = gitnexus_fingerprint(facts)
    baseline_fp = (baseline or {}).get("gitnexus_fingerprint", "")
    if existing_block.strip() != expected.strip():
        return {"ok": False, "reason": "freshness_changed", "gitnexus_fingerprint": current_fp}
    if baseline is not None and baseline_fp != current_fp:
        return {"ok": False, "reason": "fingerprint_changed", "gitnexus_fingerprint": current_fp}
    return {"ok": True, "reason": "fresh", "gitnexus_fingerprint": current_fp}


def plan_existing_block_action(existing_block: str, facts: dict, baseline: dict | None) -> dict:
    structural = structural_fact_block_check(existing_block)
    if not structural["ok"]:
        return {"action": "manual_migration", "reason": structural["reason"]}
    fresh = freshness_check(existing_block, facts, baseline)
    if not fresh["ok"]:
        return {"action": "refresh_facts", "reason": fresh["reason"]}
    if baseline is None:
        return {"action": "rebaseline", "reason": "structural_fact_block_current_missing_sidecar"}
    return {"action": "skip", "reason": "fresh"}
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_structural_check_rejects_legacy_prose_block tests/test_subdir_harness.py::test_structural_stale_block_routes_to_refresh_not_manual_migration tests/test_subdir_harness.py::test_current_structural_block_without_baseline_routes_to_rebaseline
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_subdir_harness.py tests/test_subdir_harness.py
git commit -m "feat: classify subdir harness fact blocks"
```

## Task 4: Add State Updates, Bootstrap, and Legacy Migration

**Files:**
- Modify: `scripts/generate_subdir_harness.py`
- Modify: `tests/test_subdir_harness.py`

- [ ] **Step 1: Write failing tests for state and manual migration**

Append:

```python
def test_write_rebaseline_state_does_not_change_doc(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").mkdir()
    monkeypatch.setattr(gsh, "subdir_harness_state_cache_path", lambda project_dir=".": tmp_path / "state.json")
    doc = project / "src" / "CLAUDE.md"
    doc.parent.mkdir()
    facts = {
        "caller_counts": [{"target": "Parser", "count": 40}],
        "affected_modules": [],
        "processes": [],
        "symbol_count": 40,
    }
    original = gsh.render_managed_block(gsh.render_fact_block(facts))
    doc.write_text(original, encoding="utf-8")

    result = gsh.write_rebaseline_state(project, "src", facts, ["CLAUDE.md"])

    assert result["action"] == "rebaseline"
    assert doc.read_text(encoding="utf-8") == original


def test_manual_migration_preserves_legacy_body_outside_block(tmp_path) -> None:
    doc_path = tmp_path / "src" / "CLAUDE.md"
    doc_path.parent.mkdir()
    doc_path.write_text(
        "# src\n\n"
        "<!-- harness:start -->\n"
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "<!-- harness:end -->\n",
        encoding="utf-8",
    )
    fact_block = gsh.render_fact_block({"caller_counts": [], "affected_modules": [], "processes": [], "symbol_count": 0})

    result = gsh.migrate_legacy_doc_to_facts(doc_path, fact_block)

    text = doc_path.read_text(encoding="utf-8")
    assert result == "updated"
    assert "## GitNexus 事实" in text
    assert "### 从旧 harness 块迁移" in text
    assert "Parser 被多个调用方使用" in text
    assert text.index("## GitNexus 事实") < text.index("### 从旧 harness 块迁移")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_write_rebaseline_state_does_not_change_doc tests/test_subdir_harness.py::test_manual_migration_preserves_legacy_body_outside_block
```

Expected: tests fail because state/migration helpers do not exist.

- [ ] **Step 3: Add state and manual migration helpers**

Add:

```python
def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_entry_for(dir_path: str, facts: dict, fact_block: str, rendered_files: dict | None = None) -> dict:
    current_fp = gitnexus_fingerprint(facts)
    return {
        "symbol_count": int(facts.get("symbol_count", 0) or 0),
        "repo_source_fingerprint": str(facts.get("repo_source_fingerprint", "")),
        "source_fingerprint": str(facts.get("source_fingerprint", "")),
        "known_caller_source_fingerprint": str(facts.get("known_caller_source_fingerprint", "")),
        "gitnexus_fingerprint": current_fp,
        "block_hash": _sha256_text(fact_block),
        "fact_block": fact_block,
        "accepted_at": _utc_now(),
        "rendered": rendered_files or {},
    }


def _read_state(project_dir: str | Path) -> dict:
    return read_subdir_harness_state(project_dir)


def _write_state(project_dir: str | Path, state: dict) -> bool:
    return write_subdir_harness_state(project_dir, state)


def write_rebaseline_state(project_dir: str | Path, dir_path: str, facts: dict, platform_files: list[str]) -> dict:
    fact_block = render_fact_block(facts)
    state = _read_state(project_dir)
    dirs = state.setdefault("dirs", {})
    rendered = {
        name: {"status": "rebaselined", "block_hash": _sha256_text(fact_block)}
        for name in platform_files
    }
    dirs[_clean_rel_dir(dir_path)] = _state_entry_for(dir_path, facts, fact_block, rendered)
    if not _write_state(project_dir, state):
        return {"action": "rebaseline", "status": "cache_write_failed", "dir": _clean_rel_dir(dir_path)}
    return {"action": "rebaseline", "status": "updated", "dir": _clean_rel_dir(dir_path)}


def _ensure_manual_section(doc_text: str) -> str:
    marker = "## 补充约束（手动维护）"
    if marker in doc_text:
        return doc_text
    suffix = "" if doc_text.endswith("\n") else "\n"
    return f"{doc_text}{suffix}\n{marker}\n"


def migrate_legacy_doc_to_facts(path: Path, fact_block: str) -> str:
    try:
        old = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "read_failed"
    legacy_body = extract_harness_block_body(old)
    managed = render_managed_block(fact_block)
    without_old_block = replace_or_insert_harness_block(old, managed)
    text = _ensure_manual_section(without_old_block)
    migrated = "### 从旧 harness 块迁移\n\n" + legacy_body.strip() + "\n"
    if legacy_body.strip() and legacy_body.strip() not in text:
        text = text.rstrip() + "\n\n" + migrated
    if text == old:
        return "unchanged"
    try:
        _atomic_write_text(path, text)
    except OSError:
        return "write_failed"
    return "updated"
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_write_rebaseline_state_does_not_change_doc tests/test_subdir_harness.py::test_manual_migration_preserves_legacy_body_outside_block
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_subdir_harness.py tests/test_subdir_harness.py
git commit -m "feat: manage subdir harness state"
```

## Task 5: Add Deterministic GitNexus Extraction and CLI

**Files:**
- Modify: `scripts/generate_subdir_harness.py`
- Modify: `tests/test_subdir_harness.py`

- [ ] **Step 1: Write failing tests for CLI plan and refresh with mocked facts**

Append:

```python
def test_plan_directory_reports_refresh_for_stale_structural_block(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    old_facts = {"caller_counts": [{"target": "Parser", "count": 23}], "affected_modules": [], "processes": [], "symbol_count": 23}
    new_facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    (doc_dir / "CLAUDE.md").write_text(gsh.render_managed_block(gsh.render_fact_block(old_facts)), encoding="utf-8")
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path: new_facts)

    result = gsh.plan_directory(project, "src", ["CLAUDE.md"], mode="background")

    assert result["action"] == "refresh_facts"
    assert result["files"] == ["CLAUDE.md"]


def test_refresh_directory_updates_only_managed_block(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    old_facts = {"caller_counts": [{"target": "Parser", "count": 23}], "affected_modules": [], "processes": [], "symbol_count": 23}
    new_facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    (doc_dir / "CLAUDE.md").write_text(
        "# src\n\nmanual before\n\n"
        + gsh.render_managed_block(gsh.render_fact_block(old_facts))
        + "\n\nmanual after\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path: new_facts)

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="background")

    text = (doc_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["action"] == "refresh_facts"
    assert "manual before" in text
    assert "manual after" in text
    assert "Parser: 40" in text
    assert "Parser: 23" not in text
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_plan_directory_reports_refresh_for_stale_structural_block tests/test_subdir_harness.py::test_refresh_directory_updates_only_managed_block
```

Expected: tests fail because directory orchestration helpers do not exist.

- [ ] **Step 3: Add extraction and directory orchestration**

Add GitNexus helpers:

```python
def _run_gitnexus_cypher(project_dir: str | Path, cypher: str, *, timeout: int = 20) -> list[list[str]]:
    try:
        result = subprocess.run(
            ["npx", "gitnexus", "cypher", cypher, "-r", Path(project_dir).resolve().name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    output = result.stdout.strip() or result.stderr.strip()
    return gitnexus_markdown_rows(parse_gitnexus_markdown(output))


def _quote_cypher(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def extract_dir_facts(project_dir: str | Path, dir_path: str) -> dict:
    rel = _clean_rel_dir(dir_path)
    prefix = _quote_cypher(rel + "/")
    exact = _quote_cypher(rel)
    caller_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (caller)-[:CodeRelation {type: 'CALLS'}]->(target) "
        f"WHERE target.filePath = '{exact}' OR target.filePath STARTS WITH '{prefix}' "
        "RETURN target.name AS target, count(DISTINCT caller) AS callers "
        "ORDER BY callers DESC, target ASC LIMIT 5",
    )
    module_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (caller)-[:CodeRelation {type: 'CALLS'}]->(target)-[:CodeRelation {type: 'MEMBER_OF'}]->(c:Community) "
        f"WHERE target.filePath = '{exact}' OR target.filePath STARTS WITH '{prefix}' "
        "RETURN c.heuristicLabel AS module, count(DISTINCT caller) AS count "
        "ORDER BY count DESC, module ASC LIMIT 5",
    )
    process_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (s)-[:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process) "
        f"WHERE s.filePath = '{exact}' OR s.filePath STARTS WITH '{prefix}' "
        "RETURN p.heuristicLabel AS process, count(DISTINCT s) AS count "
        "ORDER BY count DESC, process ASC LIMIT 5",
    )
    symbol_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (s) "
        f"WHERE s.filePath = '{exact}' OR s.filePath STARTS WITH '{prefix}' "
        "RETURN count(DISTINCT s) AS symbols",
    )
    return {
        "caller_counts": [
            {"target": row[0], "count": int(row[1])}
            for row in caller_rows if len(row) >= 2 and row[1].isdigit()
        ],
        "affected_modules": [
            {"module": row[0], "count": int(row[1])}
            for row in module_rows if len(row) >= 2 and row[1].isdigit()
        ],
        "processes": [
            {"process": row[0], "count": int(row[1])}
            for row in process_rows if len(row) >= 2 and row[1].isdigit()
        ],
        "symbol_count": int(symbol_rows[0][0]) if symbol_rows and symbol_rows[0] and symbol_rows[0][0].isdigit() else 0,
    }
```

Add document orchestration:

```python
def _platform_doc_paths(project_dir: str | Path, dir_path: str, files: list[str]) -> list[Path]:
    root = Path(project_dir)
    rel = _clean_rel_dir(dir_path)
    return [root / rel / name for name in files]


def plan_directory(project_dir: str | Path, dir_path: str, files: list[str], *, mode: str = "background") -> dict:
    facts = extract_dir_facts(project_dir, dir_path)
    fact_block = render_fact_block(facts)
    state = _read_state(project_dir)
    baseline = state.get("dirs", {}).get(_clean_rel_dir(dir_path))
    existing_paths = [path for path in _platform_doc_paths(project_dir, dir_path, files) if path.exists()]
    if not existing_paths:
        return {"dir": _clean_rel_dir(dir_path), "action": "bootstrap", "files": files, "manual_only": True}
    bodies = []
    for path in existing_paths:
        body = extract_harness_block_body(path.read_text(encoding="utf-8", errors="replace"))
        if not body:
            return {"dir": _clean_rel_dir(dir_path), "action": "bootstrap", "files": [path.name], "manual_only": True}
        bodies.append(body)
    action = plan_existing_block_action(bodies[0], facts, baseline)
    return {"dir": _clean_rel_dir(dir_path), "action": action["action"], "reason": action["reason"], "files": [p.name for p in existing_paths], "facts": facts, "fact_block": fact_block}


def refresh_directory(project_dir: str | Path, dir_path: str, files: list[str], *, mode: str = "background") -> dict:
    plan = plan_directory(project_dir, dir_path, files, mode=mode)
    if plan["action"] == "manual_migration" and mode != "manual":
        return plan
    if plan["action"] == "bootstrap" and mode != "manual":
        return plan
    facts = plan.get("facts") or extract_dir_facts(project_dir, dir_path)
    fact_block = render_fact_block(facts)
    if render_consistency_check(fact_block, facts)["ok"] is False:
        return {"dir": _clean_rel_dir(dir_path), "action": "refresh_facts", "status": "render_consistency_failed"}
    if plan["action"] == "rebaseline":
        return write_rebaseline_state(project_dir, dir_path, facts, plan["files"])
    if plan["action"] not in {"refresh_facts", "bootstrap"}:
        return plan
    rendered_status: dict[str, dict] = {}
    managed = render_managed_block(fact_block)
    for path in _platform_doc_paths(project_dir, dir_path, plan["files"]):
        try:
            old = path.read_text(encoding="utf-8", errors="replace")
            new = replace_or_insert_harness_block(old, managed)
            if new != old:
                _atomic_write_text(path, new)
                status = "updated"
            else:
                status = "unchanged"
        except OSError:
            status = "write_failed"
        rendered_status[path.name] = {"status": status, "block_hash": _sha256_text(fact_block)}
    state = _read_state(project_dir)
    state.setdefault("dirs", {})[_clean_rel_dir(dir_path)] = _state_entry_for(dir_path, facts, fact_block, rendered_status)
    if not _write_state(project_dir, state):
        return {"dir": _clean_rel_dir(dir_path), "action": plan["action"], "status": "cache_write_failed"}
    return {"dir": _clean_rel_dir(dir_path), "action": plan["action"], "status": "updated", "files": plan["files"]}
```

- [ ] **Step 4: Add CLI parsing**

Add:

```python
def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic subdirectory harness facts.")
    parser.add_argument("project_dir", nargs="?", default=".")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--refresh-facts", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--platform", choices=("claude", "codex"), default="claude")
    parser.add_argument("--dirs", nargs="+", default=[])
    parser.add_argument("--max-dirs", type=int, default=DEFAULT_MAX_DIRS)
    return parser.parse_args(argv)


def _files_for_platform(platform: str) -> list[str]:
    return ["CLAUDE.md", "AGENTS.md"] if platform == "claude" else ["AGENTS.md", "CLAUDE.md"]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    mode = "manual" if args.manual or args.bootstrap else "background"
    dirs = [_clean_rel_dir(dir_path) for dir_path in args.dirs[: args.max_dirs] if _clean_rel_dir(dir_path)]
    files = _files_for_platform(args.platform)
    actions = []
    for dir_path in dirs:
        if args.refresh_facts or args.bootstrap:
            actions.append(refresh_directory(args.project_dir, dir_path, files, mode=mode))
        else:
            actions.append(plan_directory(args.project_dir, dir_path, files, mode=mode))
    print(json.dumps({"schema_version": 1, "actions": actions}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```bash
pytest -q tests/test_subdir_harness.py
```

Expected: all generator tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_subdir_harness.py tests/test_subdir_harness.py
git commit -m "feat: add subdir harness generator"
```

## Task 6: Replace Subdirectory Copy Planning

**Files:**
- Modify: `scripts/harness_plan.py`
- Modify: `tests/test_harness_plan.py`

- [ ] **Step 1: Write failing tests for block-oriented `plan_subdirs()`**

Replace old `TestPlanSubdirs.test_copy_from_other` with:

```python
def test_existing_other_doc_needs_manual_bootstrap_not_copy(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("existing", encoding="utf-8")
    result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")
    assert result["copy"] == []
    assert result["bootstrap"] == [{"dir": "src", "files": ["CLAUDE.md"], "manual_only": True}]
```

Add:

```python
def test_existing_doc_with_harness_block_is_render_candidate(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "CLAUDE.md").write_text(
        "<!-- harness:start -->\n## GitNexus 事实\n\n- 被调用: 无\n<!-- harness:end -->\n",
        encoding="utf-8",
    )

    result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")

    assert result["render"] == [{"dir": "src", "files": ["CLAUDE.md"]}]
    assert result["copy"] == []
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_harness_plan.py::TestPlanSubdirs::test_existing_other_doc_needs_manual_bootstrap_not_copy tests/test_harness_plan.py::TestPlanSubdirs::test_existing_doc_with_harness_block_is_render_candidate
```

Expected: tests fail because current plan still emits `copy`.

- [ ] **Step 3: Update `plan_subdirs()`**

Replace the old function body with:

```python
def _has_harness_block(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "<!-- harness:start -->" in text and "<!-- harness:end -->" in text


def plan_subdirs(complex_dirs: list[str], own_file: str, other_file: str) -> dict:
    refresh_facts = []
    render = []
    rebaseline = []
    bootstrap = []
    manual_migration = []
    skip = []

    for d in complex_dirs:
        own = Path(d) / own_file
        other = Path(d) / other_file
        files = [name for path, name in ((own, own_file), (other, other_file)) if path.exists()]
        block_files = [name for path, name in ((own, own_file), (other, other_file)) if _has_harness_block(path)]
        if block_files:
            render.append({"dir": d, "files": block_files})
        elif files:
            bootstrap.append({"dir": d, "files": [own_file] if not own.exists() else [], "manual_only": True})
        else:
            depth = len(d.split("/"))
            bootstrap.append({"dir": d, "files": [own_file], "depth": depth, "manual_only": True})

    layers = {}
    for item in bootstrap:
        if "depth" in item:
            layers.setdefault(item["depth"], []).append(item["dir"])
    sorted_layers = [[depth, dirs] for depth, dirs in sorted(layers.items(), reverse=True)]

    return {
        "refresh_facts": refresh_facts,
        "render": render,
        "rebaseline": rebaseline,
        "bootstrap": bootstrap,
        "manual_migration": manual_migration,
        "skip": skip,
        "copy": [],
        "generate": [],
        "layers": sorted_layers,
    }
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest -q tests/test_harness_plan.py::TestPlanSubdirs
```

Expected: `TestPlanSubdirs` passes after updating assertions for the old `generate_new` and `layers_grouping` tests to inspect `bootstrap` instead of `generate`.

- [ ] **Step 5: Commit**

```bash
git add scripts/harness_plan.py tests/test_harness_plan.py
git commit -m "feat: plan subdir docs as managed blocks"
```

## Task 7: Stop Subdirectory Whole-File Sync

**Files:**
- Modify: `scripts/sync_docs.py`
- Modify: `scripts/harness_monitor.py`
- Modify: `tests/test_sync_docs.py`
- Modify: `tests/test_harness_monitor.py`

- [ ] **Step 1: Write failing sync_docs tests**

In `tests/test_sync_docs.py`, change `test_subdir_with_own_codemap_under_project_root_still_uses_mtime_sync` to:

```python
def test_subdir_docs_are_not_whole_file_synced(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CODE_MAP.md").write_text("# Root Code Map\n", encoding="utf-8")
    subdir = tmp_path / "pkg"
    subdir.mkdir()
    claude = subdir / "CLAUDE.md"
    agents = subdir / "AGENTS.md"
    claude.write_text("claude manual text", encoding="utf-8")
    agents.write_text("agents manual text", encoding="utf-8")
    os.utime(claude, (1_700_000_000, 1_700_000_000))
    os.utime(agents, (1_700_000_500, 1_700_000_500))

    result = sd.sync_one(str(subdir), "CLAUDE.md", "AGENTS.md")

    assert result == {"dir": str(subdir), "action": "subdir_block_only", "reason": "whole_file_sync_disabled"}
    assert claude.read_text(encoding="utf-8") == "claude manual text"
    assert agents.read_text(encoding="utf-8") == "agents manual text"
```

- [ ] **Step 2: Write failing monitor test**

Add to `tests/test_harness_monitor.py`:

```python
def test_sync_platform_docs_does_not_copy_subdir_docs(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subdir = tmp_path / "src"
    subdir.mkdir()
    claude = subdir / "CLAUDE.md"
    agents = subdir / "AGENTS.md"
    claude.write_text("claude manual text", encoding="utf-8")
    agents.write_text("agents manual text", encoding="utf-8")
    os.utime(claude, (1_700_000_000, 1_700_000_000))
    os.utime(agents, (1_700_000_500, 1_700_000_500))

    result = hm.sync_platform_docs(str(subdir))

    assert result == "subdir_block_only"
    assert claude.read_text(encoding="utf-8") == "claude manual text"
    assert agents.read_text(encoding="utf-8") == "agents manual text"
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_sync_docs.py::TestSyncOne::test_subdir_docs_are_not_whole_file_synced tests/test_harness_monitor.py::test_sync_platform_docs_does_not_copy_subdir_docs
```

Expected: tests fail because subdirectory files are still copied by mtime.

- [ ] **Step 4: Change `sync_docs.sync_one()` for non-root subdirectories**

In `scripts/sync_docs.py`, replace the non-root branch after root CODE_MAP handling with:

```python
    if own.exists() or other.exists():
        return {
            "dir": dir_path,
            "action": "subdir_block_only",
            "reason": "whole_file_sync_disabled",
        }

    return None
```

Keep root CODE_MAP logic unchanged.

- [ ] **Step 5: Change `harness_monitor.sync_platform_docs()` for non-root subdirectories**

In `scripts/harness_monitor.py`, replace the non-root mtime sync portion with:

```python
    claude = root / "CLAUDE.md"
    agents = root / "AGENTS.md"
    if claude.exists() or agents.exists():
        return "subdir_block_only"
    return None
```

- [ ] **Step 6: Run tests to verify GREEN**

Run:

```bash
pytest -q tests/test_sync_docs.py::TestSyncOne::test_subdir_docs_are_not_whole_file_synced tests/test_harness_monitor.py::test_sync_platform_docs_does_not_copy_subdir_docs
```

Expected: both pass. Update any older tests that asserted subdirectory copying.

- [ ] **Step 7: Commit**

```bash
git add scripts/sync_docs.py scripts/harness_monitor.py tests/test_sync_docs.py tests/test_harness_monitor.py
git commit -m "feat: disable subdir whole-file doc sync"
```

## Task 8: Wire Background Refresh Into Monitor

**Files:**
- Modify: `scripts/harness_monitor.py`
- Modify: `tests/test_harness_monitor.py`

- [ ] **Step 1: Write failing monitor orchestration test**

Add:

```python
def test_main_update_runs_bounded_subdir_harness_refresh(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitnexus").mkdir()
    (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### src/ — Stable\n", encoding="utf-8")
    subdir_script = tmp_path / "generate_subdir_harness.py"
    subdir_script.write_text("pass", encoding="utf-8")

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout='{"schema_version": 1, "actions": []}', stderr="")

    with patch.object(hm, "ensure_gitnexus_fresh"), \
         patch.object(hm, "materialize_codemap_projection"), \
         patch.object(hm, "parse_existing_codemap", return_value=({"src": "Stable"}, {})), \
         patch.object(hm, "read_codemap_counts", return_value={"src": 120}), \
         patch.object(hm, "get_gitnexus_communities", return_value={"src": {"symbols": 120, "clusters": 1}}), \
         patch.object(hm, "build_codemap_structure", return_value=("# Code Map\n\n### src/ — Stable\n", [], {"src": 120})), \
         patch.object(hm, "cache_codemap_projection"), \
         patch.object(hm, "update_root_codemap_docs"), \
         patch.object(hm, "SUBDIR_HARNESS_SCRIPT", subdir_script), \
         patch.object(hm.subprocess, "run", side_effect=fake_run):
        hm._do_main_branch_update_inner(require_main=False)

    subdir_calls = [cmd for cmd in calls if any("generate_subdir_harness.py" in str(part) for part in cmd)]
    assert subdir_calls
    assert "--refresh-facts" in subdir_calls[0]
    assert "--max-dirs" in subdir_calls[0]
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
pytest -q tests/test_harness_monitor.py::test_main_update_runs_bounded_subdir_harness_refresh
```

Expected: test fails because monitor does not call the new script.

- [ ] **Step 3: Add monitor constants and runner**

In `scripts/harness_monitor.py`, add near `DESC_SCRIPT`:

```python
SUBDIR_HARNESS_SCRIPT = Path.home() / ".local" / "share" / "harness-hooks" / "generate_subdir_harness.py"
SUBDIR_HARNESS_TIMEOUT = 120
SUBDIR_HARNESS_MAX_DIRS = 5
```

Add helper:

```python
def refresh_subdir_harness_blocks(job_id=None):
    script = None
    for candidate in [
        SUBDIR_HARNESS_SCRIPT,
        Path(__file__).resolve().parent / "generate_subdir_harness.py",
    ]:
        if candidate.exists():
            script = candidate
            break
    if script is None:
        return "missing_script"
    cmd = [
        sys.executable,
        str(script),
        ".",
        "--refresh-facts",
        "--platform",
        "claude",
        "--max-dirs",
        str(SUBDIR_HARNESS_MAX_DIRS),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBDIR_HARNESS_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return "failed"
    return "updated" if result.returncode == 0 else "failed"
```

Call it at the end of `_do_main_branch_update_inner()` after `update_root_codemap_docs_checked(".", job_id)`:

```python
    if _branch_ok(require_main, expected_branch):
        refresh_subdir_harness_blocks(job_id)
```

Add the same call in the early-return path after root docs update.

- [ ] **Step 4: Run test to verify GREEN**

Run:

```bash
pytest -q tests/test_harness_monitor.py::test_main_update_runs_bounded_subdir_harness_refresh
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add scripts/harness_monitor.py tests/test_harness_monitor.py
git commit -m "feat: refresh subdir harness facts in monitor"
```

## Task 9: Install the New Script

**Files:**
- Modify: `install.py`
- Modify: `tests/test_install.py`

- [ ] **Step 1: Write failing install test**

Add to `tests/test_install.py`:

```python
def test_install_mentions_generate_subdir_harness_script():
    source = Path(os.path.join(os.path.dirname(__file__), "..", "install.py")).read_text(encoding="utf-8")
    assert "generate_subdir_harness.py" in source
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
pytest -q tests/test_install.py::test_install_mentions_generate_subdir_harness_script
```

Expected: test fails because install does not mention the new script.

- [ ] **Step 3: Update install copy and link cleanup**

In `install.py`, add `generate_subdir_harness.py` to link cleanup:

```python
for stale in ["harness-monitor.py", "harness_monitor.py", "shared.py",
               "harness_shared.py", "generate_descriptions.py", "generate_subdir_harness.py",
               "session_context.py"]:
    (local_share / stale).unlink(missing_ok=True)
```

In copy mode after `generate_descriptions.py`, add:

```python
install_file(SCRIPT_DIR / "scripts" / "generate_subdir_harness.py", local_share / "generate_subdir_harness.py")
```

- [ ] **Step 4: Run test to verify GREEN**

Run:

```bash
pytest -q tests/test_install.py::test_install_mentions_generate_subdir_harness_script
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add install.py tests/test_install.py
git commit -m "feat: install subdir harness generator"
```

## Task 10: Update Skill Docs and README

**Files:**
- Modify: `README.md`
- Modify: `skills/claude/SKILL.md`
- Modify: `skills/codex/SKILL.md`

- [ ] **Step 1: Write doc expectation tests**

Add to `tests/test_harness_init.py`:

```python
def test_docs_describe_subdir_harness_facts_only():
    root = Path(os.path.join(os.path.dirname(__file__), ".."))
    for rel in ["README.md", "skills/claude/SKILL.md", "skills/codex/SKILL.md"]:
        text = (root / rel).read_text(encoding="utf-8")
        assert "<!-- harness:start -->" in text
        assert "## GitNexus 事实" in text
        assert "## 约束（基于 GitNexus 事实）" not in text
        assert "## 危险操作（基于 GitNexus impact 分析）" not in text
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
pytest -q tests/test_harness_init.py::test_docs_describe_subdir_harness_facts_only
```

Expected: test fails because the skill docs still describe old prose constraints.

- [ ] **Step 3: Update README**

Change the file structure section:

```md
│   ├── sync_docs.py             ← 根文档 CODE_MAP 块渲染；子目录不做整文件同步
│   ├── generate_subdir_harness.py ← 子目录 GitNexus 事实块生成与刷新
```

Add this paragraph after CODE_MAP storage model:

```md
子目录 `CLAUDE.md` / `AGENTS.md` 使用托管 `<!-- harness:start/end -->` 块保存确定性 GitNexus 事实。该块标题固定为 `## GitNexus 事实`，内容只来自 GitNexus/Cypher 结构化输出，不包含 AI prose、约束或危险操作建议。人工约束必须写在块外，例如 `## 补充约束（手动维护）`。
```

- [ ] **Step 4: Update both skill files**

Replace old subdirectory harness template in each skill file with:

```md
<!-- harness:start -->
## GitNexus 事实

- 被调用: Parser: 40
- 相关模块: core: 3
- 相关流程: AnalyzeProject: 2
<!-- harness:end -->

## 补充约束（手动维护）
```

Add command guidance:

```md
子目录 harness 块由 `generate_subdir_harness.py` 生成。后台只刷新已有 facts-only 块或 cache-only rebaseline；遇到旧 prose 块时只报告 `manual_migration_required`。手动迁移时必须先把旧块内容移到 `## 补充约束（手动维护）`，再写入新的 `## GitNexus 事实` 块。
```

- [ ] **Step 5: Run docs test to verify GREEN**

Run:

```bash
pytest -q tests/test_harness_init.py::test_docs_describe_subdir_harness_facts_only
```

Expected: test passes.

- [ ] **Step 6: Commit**

```bash
git add README.md skills/claude/SKILL.md skills/codex/SKILL.md tests/test_harness_init.py
git commit -m "docs: document subdir harness facts"
```

## Task 11: End-to-End Verification

**Files:**
- Verify only unless tests reveal a defect.

- [ ] **Step 1: Run focused subdir tests**

Run:

```bash
pytest -q tests/test_subdir_harness.py tests/test_harness_plan.py::TestPlanSubdirs tests/test_sync_docs.py::TestSyncOne tests/test_harness_monitor.py::test_main_update_runs_bounded_subdir_harness_refresh
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full pytest suite**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run Codex hook self-test**

Run:

```bash
node hooks/gitnexus-codex-hook.cjs --self-test
```

Expected: exit code 0 and self-test output reports pass.

- [ ] **Step 4: Run harness diagnostic script**

Run:

```bash
python3 scripts/harness_init.py .
```

Expected: JSON output with `"schema_version": 1`, `"existing"`, and no Python traceback.

- [ ] **Step 5: Run generator help and dry plan**

Run:

```bash
python3 scripts/generate_subdir_harness.py . --plan --dirs scripts --max-dirs 1
```

Expected: JSON output with `"schema_version": 1` and an `"actions"` array. GitNexus-unavailable projects may report empty facts or manual/bootstrap actions, but the command must not crash.

- [ ] **Step 6: Run GitNexus detect changes**

Run through MCP or CLI before merging:

```text
gitnexus_detect_changes(scope="all")
```

Expected: changed symbols and affected processes match the touched implementation/doc surfaces. Investigate any HIGH or CRITICAL risk before merging.

- [ ] **Step 7: Commit final verification marker if files changed during verification**

If verification required only code/test fixes, commit those fixes:

```bash
git status --short
git add scripts/generate_subdir_harness.py scripts/harness_shared.py scripts/harness_plan.py scripts/sync_docs.py scripts/harness_monitor.py install.py README.md skills/claude/SKILL.md skills/codex/SKILL.md tests/test_subdir_harness.py tests/test_harness_shared_gitnexus.py tests/test_harness_plan.py tests/test_sync_docs.py tests/test_harness_monitor.py tests/test_install.py tests/test_harness_init.py
git commit -m "test: verify subdir harness facts"
```

If no files changed, do not create an empty commit.

## Self-Review

Spec coverage:

- Facts-only automatic block: Tasks 2, 3, 5, and 10.
- No AI prose path: Tasks 2, 3, 5, and 10.
- Shared sidecar state: Tasks 1, 4, and 5.
- Structural/freshness/render checks: Task 3.
- Stable sorting: Task 2.
- No subdirectory whole-file copy: Tasks 6 and 7.
- Background refresh and cache-only rebaseline: Tasks 5 and 8.
- Legacy prose manual migration: Tasks 3, 4, 5, and 10.
- Manual-only bootstrap: Tasks 4, 5, 6, and 10.
- Install/update docs: Tasks 9 and 10.
- Final verification: Task 11.

Placeholder scan:

- This plan intentionally contains no future placeholders or deferred implementation references.
- The string normally used as a generic pending-work marker is intentionally absent from code snippets.

Type consistency:

- `structural_fact_block_check`, `render_consistency_check`, and `freshness_check` are defined before use.
- `plan_existing_block_action()` returns `action` and `reason`, which are used by `plan_directory()` and tests.
- `refresh_directory()` returns `action`, `status`, `dir`, and `files` consistently for downstream monitor and CLI JSON consumers.
