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
  - Replace old subdirectory `copy/generate/skip` planning with block-oriented `refresh_facts/rebaseline/bootstrap/manual_migration/skip`.
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
                "fact_block": "## GitNexus 事实\n\n暂无已验证图谱事实。",
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
    block = gsh.render_managed_block("## GitNexus 事实\n\n暂无已验证图谱事实。")

    assert block == (
        "<!-- harness:start -->\n"
        "## GitNexus 事实\n\n"
        "暂无已验证图谱事实。\n"
        "<!-- harness:end -->"
    )


def test_replace_existing_harness_block_preserves_surrounding_text() -> None:
    doc = "# src\n\nmanual before\n\n<!-- harness:start -->\nold\n<!-- harness:end -->\n\nmanual after\n"
    block = gsh.render_managed_block("## GitNexus 事实\n\n暂无已验证图谱事实。")

    rendered = gsh.replace_or_insert_harness_block(doc, block)

    assert "manual before" in rendered
    assert "manual after" in rendered
    assert "old" not in rendered
    assert rendered.count("<!-- harness:start -->") == 1


def test_replace_existing_harness_block_replaces_duplicate_blocks() -> None:
    doc = (
        "# src\n\n"
        "<!-- harness:start -->\nold one\n<!-- harness:end -->\n\n"
        "middle\n\n"
        "<!-- harness:start -->\nold two\n<!-- harness:end -->\n"
    )
    block = gsh.render_managed_block("## GitNexus 事实\n\n暂无已验证图谱事实。")

    rendered = gsh.replace_or_insert_harness_block(doc, block)

    assert "old one" not in rendered
    assert "old two" not in rendered
    assert rendered.count("<!-- harness:start -->") == 1
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_render_fact_block_sorts_rows_stably tests/test_subdir_harness.py::test_render_managed_block_contains_only_harness_markers tests/test_subdir_harness.py::test_replace_existing_harness_block_preserves_surrounding_text tests/test_subdir_harness.py::test_replace_existing_harness_block_replaces_duplicate_blocks
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
    SYMBOL_THRESHOLD,
    _atomic_write_text,
    gitnexus_markdown_rows,
    parse_codemap,
    parse_gitnexus_markdown,
    read_codemap_counts,
    read_subdir_harness_state,
    should_skip,
    subdir_harness_state_cache_path,
    write_subdir_harness_state,
)

MAX_FACT_ROWS = 5
DEFAULT_MAX_DIRS = 5
SUPPORTED_FACT_PREFIXES = ("- 被调用:", "- 影响面:", "- 相关模块:", "- 相关流程:", "- 截断:")
EMPTY_FACT_LINE = "暂无已验证图谱事实。"
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
    if not caller_counts and not modules and not processes:
        lines.append(EMPTY_FACT_LINE)
        return "\n".join(lines).strip()
    if caller_counts:
        lines.extend(f"- 被调用: {row['target']}: {int(row.get('count', 0))}" for row in caller_counts)
    if modules:
        lines.extend(f"- 相关模块: {row['module']}: {int(row.get('count', 0))}" for row in modules)
    if processes:
        lines.extend(f"- 相关流程: {row['process']}: {int(row.get('count', 0))}" for row in processes)
    return "\n".join(lines).strip()


def render_managed_block(fact_block: str) -> str:
    return f"{HARNESS_BLOCK_START}\n{fact_block.strip()}\n{HARNESS_BLOCK_END}"


def replace_or_insert_harness_block(doc_text: str, managed_block: str) -> str:
    pattern = re.compile(
        rf"{re.escape(HARNESS_BLOCK_START)}.*?{re.escape(HARNESS_BLOCK_END)}",
        re.DOTALL | re.MULTILINE,
    )
    if pattern.search(doc_text):
        rendered = pattern.sub(lambda _: managed_block, doc_text)
        first = rendered.find(managed_block)
        if first == -1:
            return rendered
        before = rendered[: first + len(managed_block)]
        after = rendered[first + len(managed_block) :].replace(managed_block, "")
        return before + after
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
        if line == EMPTY_FACT_LINE:
            continue
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
    written: dict = {}
    monkeypatch.setattr(gsh, "read_subdir_harness_state", lambda project_dir=".": {"schema_version": 1, "dirs": {}})
    monkeypatch.setattr(gsh, "write_subdir_harness_state", lambda project_dir, payload: written.update(payload) or True)
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
    assert written["dirs"]["src"]["gitnexus_fingerprint"].startswith("sha256:")


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


def _state_fingerprint_field(facts: dict, field: str) -> str:
    value = str(facts.get(field, ""))
    if value.startswith("sha256:"):
        return value
    fallback = json.dumps({"field": field, "facts": facts}, ensure_ascii=False, sort_keys=True)
    return _sha256_text(fallback)


def _state_entry_for(dir_path: str, facts: dict, fact_block: str, rendered_files: dict | None = None) -> dict:
    current_fp = gitnexus_fingerprint(facts)
    return {
        "symbol_count": int(facts.get("symbol_count", 0) or 0),
        "repo_source_fingerprint": _state_fingerprint_field(facts, "repo_source_fingerprint"),
        "source_fingerprint": _state_fingerprint_field(facts, "source_fingerprint"),
        "known_caller_source_fingerprint": _state_fingerprint_field(facts, "known_caller_source_fingerprint"),
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

- [ ] **Step 1: Write failing tests for CLI plan, bootstrap, migration, and per-file safety**

Append:

```python
def _patch_state(monkeypatch):
    written: dict = {}
    monkeypatch.setattr(gsh, "read_subdir_harness_state", lambda project_dir=".": {"schema_version": 1, "dirs": {}})
    monkeypatch.setattr(gsh, "write_subdir_harness_state", lambda project_dir, payload: written.update(payload) or True)
    return written


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
    assert result["file_actions"] == [{"file": "CLAUDE.md", "action": "refresh_facts", "reason": "freshness_changed"}]


def test_plan_directory_does_not_overwrite_mixed_legacy_doc(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    (doc_dir / "CLAUDE.md").write_text(gsh.render_managed_block(gsh.render_fact_block(facts)), encoding="utf-8")
    (doc_dir / "AGENTS.md").write_text(
        "<!-- harness:start -->\n"
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "<!-- harness:end -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path: facts)

    result = gsh.plan_directory(project, "src", ["CLAUDE.md", "AGENTS.md"], mode="background")

    assert result["action"] == "manual_migration"
    assert result["files"] == ["AGENTS.md"]
    assert result["file_actions"] == [
        {"file": "CLAUDE.md", "action": "rebaseline", "reason": "structural_fact_block_current_missing_sidecar"},
        {"file": "AGENTS.md", "action": "manual_migration", "reason": "legacy_prose"},
    ]


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
    written = _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path: new_facts)

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="background")

    text = (doc_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["action"] == "refresh_facts"
    assert "manual before" in text
    assert "manual after" in text
    assert "Parser: 40" in text
    assert "Parser: 23" not in text
    assert written["dirs"]["src"]["rendered"]["CLAUDE.md"]["status"] == "updated"


def test_manual_bootstrap_creates_missing_platform_doc(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    (project / "src").mkdir(parents=True)
    (project / ".git").mkdir()
    facts = {"caller_counts": [], "affected_modules": [], "processes": [], "symbol_count": 0}
    written = _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path: facts)

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="manual", bootstrap=True)

    text = (project / "src" / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["action"] == "bootstrap"
    assert "# src/ — GitNexus 事实" in text
    assert "## 测试" in text
    assert "未识别专用测试命令" in text
    assert "<!-- harness:start -->" in text
    assert "暂无已验证图谱事实。" in text
    assert "## 补充约束（手动维护）" in text
    assert written["dirs"]["src"]["rendered"]["CLAUDE.md"]["status"] == "created"


def test_manual_migrate_routes_legacy_doc_through_migration(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    (doc_dir / "CLAUDE.md").write_text(
        "# src\n\n"
        "<!-- harness:start -->\n"
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "<!-- harness:end -->\n",
        encoding="utf-8",
    )
    facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path: facts)

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="manual", migrate=True)

    text = (doc_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["action"] == "manual_migration"
    assert result["status"] == "updated"
    assert "Parser: 40" in text
    assert "### 从旧 harness 块迁移" in text
    assert "Parser 被多个调用方使用" in text


def test_extract_dir_facts_uses_schema_tolerant_gitnexus_rows(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    outputs = iter([
        [["Parser", "40", "src/parser.py"]],
        [["Core", "3"]],
        [["AnalyzeProject", "2"]],
        [["128"]],
    ])
    monkeypatch.setattr(gsh, "_run_gitnexus_cypher", lambda project_dir, cypher: next(outputs))
    monkeypatch.setattr(gsh, "source_fingerprint", lambda project_dir, paths=None: "sha256:source")

    facts = gsh.extract_dir_facts(project, "src")

    assert facts["caller_counts"] == [{"target": "Parser", "count": 40, "path": "src/parser.py"}]
    assert facts["affected_modules"] == [{"module": "Core", "count": 3}]
    assert facts["processes"] == [{"process": "AnalyzeProject", "count": 2}]
    assert facts["symbol_count"] == 128
    assert facts["repo_source_fingerprint"] == "sha256:source"
    assert facts["source_fingerprint"] == "sha256:source"
    assert facts["known_caller_source_fingerprint"] == "sha256:source"


def test_source_fingerprint_changes_when_source_file_changes(tmp_path) -> None:
    project = tmp_path / "repo"
    (project / "src").mkdir(parents=True)
    source = project / "src" / "parser.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    first = gsh.source_fingerprint(project, ["src/parser.py"])
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = gsh.source_fingerprint(project, ["src/parser.py"])

    assert first.startswith("sha256:")
    assert second.startswith("sha256:")
    assert first != second


def test_plan_directory_skips_graph_when_repo_source_fingerprint_unchanged(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    fact_block = gsh.render_fact_block(facts)
    (doc_dir / "CLAUDE.md").write_text(gsh.render_managed_block(fact_block), encoding="utf-8")
    monkeypatch.setattr(gsh, "source_fingerprint", lambda project_dir, paths=None: "sha256:same")
    monkeypatch.setattr(
        gsh,
        "read_subdir_harness_state",
        lambda project_dir=".": {
            "schema_version": 1,
            "dirs": {
                "src": {
                    "repo_source_fingerprint": "sha256:same",
                    "gitnexus_fingerprint": gsh.gitnexus_fingerprint(facts),
                    "block_hash": gsh._sha256_text(fact_block),
                    "fact_block": fact_block,
                }
            },
        },
    )
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path: (_ for _ in ()).throw(AssertionError("graph should be skipped")))

    result = gsh.plan_directory(project, "src", ["CLAUDE.md"], mode="background")

    assert result["action"] == "skip"
    assert result["reason"] == "repo_source_fingerprint_unchanged"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_subdir_harness.py::test_plan_directory_reports_refresh_for_stale_structural_block tests/test_subdir_harness.py::test_plan_directory_does_not_overwrite_mixed_legacy_doc tests/test_subdir_harness.py::test_refresh_directory_updates_only_managed_block tests/test_subdir_harness.py::test_manual_bootstrap_creates_missing_platform_doc tests/test_subdir_harness.py::test_manual_migrate_routes_legacy_doc_through_migration tests/test_subdir_harness.py::test_extract_dir_facts_uses_schema_tolerant_gitnexus_rows tests/test_subdir_harness.py::test_source_fingerprint_changes_when_source_file_changes tests/test_subdir_harness.py::test_plan_directory_skips_graph_when_repo_source_fingerprint_unchanged
```

Expected: tests fail because directory orchestration helpers do not exist.

- [ ] **Step 3: Add extraction and directory orchestration**

Add GitNexus helpers:

```python
def tracked_source_files(project_dir: str | Path) -> list[str]:
    root = Path(project_dir)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        result = None
    if result is not None and result.returncode == 0:
        candidates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    else:
        candidates = [
            str(path.relative_to(root)).replace("\\", "/")
            for path in root.rglob("*")
            if path.is_file() and not any(should_skip(part) for part in path.relative_to(root).parts)
        ]
    return sorted(
        rel for rel in candidates
        if Path(rel).suffix in SOURCE_EXTS and not any(should_skip(part) for part in Path(rel).parts)
    )


def source_fingerprint(project_dir: str | Path, paths: list[str] | None = None) -> str:
    root = Path(project_dir)
    rels = sorted(paths if paths is not None else tracked_source_files(root))
    digest = hashlib.sha256()
    for rel in rels:
        clean = str(rel).replace("\\", "/").strip("/")
        path = root / clean
        if not path.is_file() or path.suffix not in SOURCE_EXTS:
            continue
        digest.update(clean.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _dir_source_paths(project_dir: str | Path, dir_path: str) -> list[str]:
    rel = _clean_rel_dir(dir_path)
    prefix = rel + "/"
    return [path for path in tracked_source_files(project_dir) if path == rel or path.startswith(prefix)]


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


def _row_int(value: str) -> int | None:
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def extract_dir_facts(project_dir: str | Path, dir_path: str) -> dict:
    rel = _clean_rel_dir(dir_path)
    prefix = _quote_cypher(rel + "/")
    exact = _quote_cypher(rel)
    dir_paths = _dir_source_paths(project_dir, rel)
    repo_fp = source_fingerprint(project_dir)
    dir_fp = source_fingerprint(project_dir, dir_paths)
    caller_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (caller)-[:CodeRelation {type: 'CALLS'}]->(target) "
        f"WHERE target.filePath = '{exact}' OR target.filePath STARTS WITH '{prefix}' "
        "RETURN target.name AS target, count(DISTINCT caller) AS callers, target.filePath AS path "
        "ORDER BY callers DESC, target ASC LIMIT 5",
    )
    module_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (caller)-[:CodeRelation {type: 'CALLS'}]->(target)-[:CodeRelation {type: 'MEMBER_OF'}]->(c:Community) "
        f"WHERE target.filePath = '{exact}' OR target.filePath STARTS WITH '{prefix}' "
        "RETURN coalesce(c.heuristicLabel, c.label, c.name, 'unknown') AS module, count(DISTINCT caller) AS count "
        "ORDER BY count DESC, module ASC LIMIT 5",
    )
    process_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (s)-[:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process) "
        f"WHERE s.filePath = '{exact}' OR s.filePath STARTS WITH '{prefix}' "
        "RETURN coalesce(p.heuristicLabel, p.label, p.name, 'unknown') AS process, count(DISTINCT s) AS count "
        "ORDER BY count DESC, process ASC LIMIT 5",
    )
    symbol_rows = _run_gitnexus_cypher(
        project_dir,
        "MATCH (s) "
        f"WHERE s.filePath = '{exact}' OR s.filePath STARTS WITH '{prefix}' "
        "RETURN count(DISTINCT s) AS symbols",
    )
    caller_paths = sorted({row[2] for row in caller_rows if len(row) >= 3 and row[2]})
    return {
        "caller_counts": [
            {"target": row[0], "count": count, "path": row[2] if len(row) >= 3 else ""}
            for row in caller_rows
            if len(row) >= 2 and (count := _row_int(row[1])) is not None
        ],
        "affected_modules": [
            {"module": row[0], "count": count}
            for row in module_rows
            if len(row) >= 2 and (count := _row_int(row[1])) is not None
        ],
        "processes": [
            {"process": row[0], "count": count}
            for row in process_rows
            if len(row) >= 2 and (count := _row_int(row[1])) is not None
        ],
        "symbol_count": int(symbol_rows[0][0]) if symbol_rows and symbol_rows[0] and str(symbol_rows[0][0]).isdigit() else 0,
        "repo_source_fingerprint": repo_fp,
        "source_fingerprint": dir_fp,
        "known_caller_source_fingerprint": source_fingerprint(project_dir, caller_paths),
    }
```

Before implementing the final Cypher strings, run this calibration command in the real repo and adjust only property names that the output proves are unavailable:

```bash
npx gitnexus cypher "MATCH (c:Community) RETURN keys(c) LIMIT 1" -r harness-init
npx gitnexus cypher "MATCH (p:Process) RETURN keys(p) LIMIT 1" -r harness-init
```

Expected: the queries return markdown tables. Keep `coalesce(c.heuristicLabel, c.label, c.name, 'unknown')` and `coalesce(p.heuristicLabel, p.label, p.name, 'unknown')` unless calibration proves different property names are required.

Add document orchestration:

```python
def _platform_doc_paths(project_dir: str | Path, dir_path: str, files: list[str]) -> list[Path]:
    root = Path(project_dir)
    rel = _clean_rel_dir(dir_path)
    return [root / rel / name for name in files]


def bootstrap_doc_shell(dir_path: str, managed_block: str) -> str:
    rel = _clean_rel_dir(dir_path)
    return (
        f"# {rel}/ — GitNexus 事实\n\n"
        "## 测试\n\n"
        "- 未识别专用测试命令\n\n"
        f"{managed_block.strip()}\n\n"
        "## 补充约束（手动维护）\n"
    )


def _highest_priority_action(file_actions: list[dict]) -> dict:
    for action in ("manual_migration", "refresh_facts", "rebaseline", "bootstrap"):
        selected = [item for item in file_actions if item["action"] == action]
        if selected:
            return {
                "action": action,
                "files": [item["file"] for item in selected],
                "reason": selected[0].get("reason", action),
            }
    return {"action": "skip", "files": [], "reason": "fresh"}


def plan_directory(project_dir: str | Path, dir_path: str, files: list[str], *, mode: str = "background") -> dict:
    state = _read_state(project_dir)
    baseline = state.get("dirs", {}).get(_clean_rel_dir(dir_path))
    paths = _platform_doc_paths(project_dir, dir_path, files)
    existing_paths = [path for path in paths if path.exists()]
    current_repo_fp = source_fingerprint(project_dir)
    if mode == "background" and baseline and baseline.get("repo_source_fingerprint") == current_repo_fp and existing_paths:
        file_actions = []
        for path in existing_paths:
            body = extract_harness_block_body(path.read_text(encoding="utf-8", errors="replace"))
            structural = structural_fact_block_check(body)
            if not structural["ok"]:
                file_actions.append({"file": path.name, "action": "manual_migration", "reason": structural["reason"]})
            elif _sha256_text(body) == baseline.get("block_hash"):
                file_actions.append({"file": path.name, "action": "skip", "reason": "repo_source_fingerprint_unchanged"})
            else:
                file_actions.append({"file": path.name, "action": "refresh_facts", "reason": "block_hash_changed"})
        summary = _highest_priority_action(file_actions)
        if summary["action"] == "skip":
            return {
                "dir": _clean_rel_dir(dir_path),
                "action": "skip",
                "reason": "repo_source_fingerprint_unchanged",
                "files": [],
                "file_actions": file_actions,
            }
    facts = extract_dir_facts(project_dir, dir_path)
    fact_block = render_fact_block(facts)
    file_actions: list[dict] = []
    for path in paths:
        if not path.exists():
            file_actions.append({"file": path.name, "action": "bootstrap", "reason": "missing_file", "manual_only": True})
            continue
        body = extract_harness_block_body(path.read_text(encoding="utf-8", errors="replace"))
        if not body:
            file_actions.append({"file": path.name, "action": "bootstrap", "reason": "missing_harness_block", "manual_only": True})
            continue
        action = plan_existing_block_action(body, facts, baseline)
        file_actions.append({"file": path.name, "action": action["action"], "reason": action["reason"]})
    summary = _highest_priority_action(file_actions)
    return {
        "dir": _clean_rel_dir(dir_path),
        "action": summary["action"],
        "reason": summary["reason"],
        "files": summary["files"],
        "facts": facts,
        "fact_block": fact_block,
        "file_actions": file_actions,
        "manual_only": summary["action"] in {"manual_migration", "bootstrap"},
    }


def refresh_directory(
    project_dir: str | Path,
    dir_path: str,
    files: list[str],
    *,
    mode: str = "background",
    bootstrap: bool = False,
    migrate: bool = False,
) -> dict:
    plan = plan_directory(project_dir, dir_path, files, mode=mode)
    if plan["action"] == "manual_migration" and not (mode == "manual" and migrate):
        return plan
    if plan["action"] == "bootstrap" and not (mode == "manual" and bootstrap):
        return plan
    facts = plan.get("facts") or extract_dir_facts(project_dir, dir_path)
    fact_block = render_fact_block(facts)
    if render_consistency_check(fact_block, facts)["ok"] is False:
        return {"dir": _clean_rel_dir(dir_path), "action": "refresh_facts", "status": "render_consistency_failed"}
    if plan["action"] == "rebaseline":
        return write_rebaseline_state(project_dir, dir_path, facts, plan["files"])
    if plan["action"] == "manual_migration":
        statuses = {}
        for path in _platform_doc_paths(project_dir, dir_path, plan["files"]):
            statuses[path.name] = migrate_legacy_doc_to_facts(path, fact_block)
        rendered_status = {
            name: {"status": status, "block_hash": _sha256_text(fact_block)}
            for name, status in statuses.items()
        }
        state = _read_state(project_dir)
        state.setdefault("dirs", {})[_clean_rel_dir(dir_path)] = _state_entry_for(dir_path, facts, fact_block, rendered_status)
        cache_status = "updated" if _write_state(project_dir, state) else "cache_write_failed"
        return {
            "dir": _clean_rel_dir(dir_path),
            "action": "manual_migration",
            "status": "updated" if any(status == "updated" for status in statuses.values()) else cache_status,
            "files": plan["files"],
            "rendered": statuses,
        }
    if plan["action"] not in {"refresh_facts", "bootstrap"}:
        return plan
    rendered_status: dict[str, dict] = {}
    managed = render_managed_block(fact_block)
    for path in _platform_doc_paths(project_dir, dir_path, plan["files"]):
        try:
            if plan["action"] == "bootstrap" and not path.exists():
                _atomic_write_text(path, bootstrap_doc_shell(dir_path, managed))
                status = "created"
            else:
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
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--platform", choices=("claude", "codex"), default="claude")
    parser.add_argument("--dirs", nargs="+", default=[])
    parser.add_argument("--max-dirs", type=int, default=DEFAULT_MAX_DIRS)
    return parser.parse_args(argv)


def _files_for_platform(platform: str) -> list[str]:
    return ["CLAUDE.md", "AGENTS.md"] if platform == "claude" else ["AGENTS.md", "CLAUDE.md"]


def discover_candidate_dirs(project_dir: str | Path, max_dirs: int) -> list[str]:
    root = Path(project_dir)
    entries = parse_codemap(root / "CODE_MAP.md")
    recorded_counts = read_codemap_counts(root)
    selected: list[str] = []
    for entry in entries:
        dir_path = _clean_rel_dir(entry.get("dir", ""))
        if not dir_path:
            continue
        symbols = recorded_counts.get(dir_path, entry.get("symbols"))
        if symbols is not None and int(symbols) >= SYMBOL_THRESHOLD:
            selected.append(dir_path)
    return selected[:max_dirs]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    mode = "manual" if args.manual or args.bootstrap or args.migrate else "background"
    dirs = [_clean_rel_dir(dir_path) for dir_path in args.dirs[: args.max_dirs] if _clean_rel_dir(dir_path)]
    if not dirs and (args.plan or args.refresh_facts):
        dirs = discover_candidate_dirs(args.project_dir, args.max_dirs)
    files = _files_for_platform(args.platform)
    actions = []
    for dir_path in dirs:
        if args.refresh_facts or args.bootstrap or args.migrate:
            actions.append(
                refresh_directory(
                    args.project_dir,
                    dir_path,
                    files,
                    mode=mode,
                    bootstrap=args.bootstrap,
                    migrate=args.migrate,
                )
            )
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
    monkeypatch.setattr(hp, "_plan_subdir_with_generator", lambda d, files: {"action": "bootstrap", "files": ["CLAUDE.md"], "manual_only": True})

    result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")

    assert result["copy"] == []
    assert result["bootstrap"] == [{"dir": "src", "files": ["CLAUDE.md"], "manual_only": True}]
```

Add:

```python
def test_existing_facts_doc_refresh_is_reported(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "CLAUDE.md").write_text("facts", encoding="utf-8")
    monkeypatch.setattr(hp, "_plan_subdir_with_generator", lambda d, files: {"action": "refresh_facts", "files": ["CLAUDE.md"], "reason": "freshness_changed"})

    result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")

    assert result["refresh_facts"] == [{"dir": "src", "files": ["CLAUDE.md"], "reason": "freshness_changed"}]
    assert result["copy"] == []


def test_existing_facts_doc_rebaseline_is_reported(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "CLAUDE.md").write_text("facts", encoding="utf-8")
    monkeypatch.setattr(hp, "_plan_subdir_with_generator", lambda d, files: {"action": "rebaseline", "files": ["CLAUDE.md"], "reason": "structural_fact_block_current_missing_sidecar"})

    result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")

    assert result["rebaseline"] == [{"dir": "src", "files": ["CLAUDE.md"], "reason": "structural_fact_block_current_missing_sidecar"}]


def test_legacy_doc_manual_migration_is_reported_not_rendered(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(hp, "_plan_subdir_with_generator", lambda d, files: {"action": "manual_migration", "files": ["AGENTS.md"], "reason": "legacy_prose"})

    result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")

    assert result["manual_migration"] == [{"dir": "src", "files": ["AGENTS.md"], "reason": "legacy_prose"}]
    assert result["refresh_facts"] == []
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_harness_plan.py::TestPlanSubdirs::test_existing_other_doc_needs_manual_bootstrap_not_copy tests/test_harness_plan.py::TestPlanSubdirs::test_existing_facts_doc_refresh_is_reported tests/test_harness_plan.py::TestPlanSubdirs::test_existing_facts_doc_rebaseline_is_reported tests/test_harness_plan.py::TestPlanSubdirs::test_legacy_doc_manual_migration_is_reported_not_rendered
```

Expected: tests fail because current plan still emits `copy`.

- [ ] **Step 3: Update `plan_subdirs()`**

Add near imports:

```python
try:
    import generate_subdir_harness as subdir_harness
except ImportError:
    subdir_harness = None
```

Replace the old function body with:

```python
def _plan_subdir_with_generator(dir_path: str, files: list[str]) -> dict:
    if subdir_harness is None:
        return {"action": "bootstrap", "files": files[:1], "manual_only": True, "reason": "generator_unavailable"}
    return subdir_harness.plan_directory(".", dir_path, files, mode="background")


def _append_action(result: dict, action: str, item: dict) -> None:
    result.setdefault(action, []).append(item)


def plan_subdirs(complex_dirs: list[str], own_file: str, other_file: str) -> dict:
    result = {
        "refresh_facts": [],
        "rebaseline": [],
        "bootstrap": [],
        "manual_migration": [],
        "skip": [],
        "copy": [],
        "generate": [],
        "layers": [],
    }

    for d in complex_dirs:
        plan = _plan_subdir_with_generator(d, [own_file, other_file])
        action = plan.get("action", "skip")
        item = {"dir": d, "files": plan.get("files", [])}
        if plan.get("reason"):
            item["reason"] = plan["reason"]
        if action == "bootstrap":
            item["manual_only"] = True
            item["depth"] = len(d.split("/"))
        if action in {"refresh_facts", "rebaseline", "bootstrap", "manual_migration", "skip"}:
            _append_action(result, action, item)
        else:
            result["skip"].append({"dir": d, "files": [], "reason": f"unknown_action:{action}"})

    layers = {}
    for item in result["bootstrap"]:
        if "depth" in item:
            layers.setdefault(item["depth"], []).append(item["dir"])
    result["layers"] = [[depth, dirs] for depth, dirs in sorted(layers.items(), reverse=True)]
    return result
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
def test_main_update_runs_bounded_subdir_harness_refresh(tmp_path, monkeypatch):
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
    assert "--dirs" in subdir_calls[0]
    dirs_index = subdir_calls[0].index("--dirs")
    assert subdir_calls[0][dirs_index + 1] == "src"
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
pytest -q tests/test_harness_monitor.py::test_main_update_runs_bounded_subdir_harness_refresh
```

Expected: test fails because monitor does not call the new script.

- [ ] **Step 3: Add monitor constants and runner**

In the `harness_shared` import list in `scripts/harness_monitor.py`, add `SYMBOL_THRESHOLD`:

```python
from harness_shared import (STALE_THRESHOLD, SYMBOL_THRESHOLD, SOURCE_EXTS, MAIN_BRANCHES,
                    should_skip, parse_codemap, is_acceptable_description,
                    needs_description_refresh, parse_gitnexus_markdown,
                    parse_codemap_entry, gitnexus_markdown_rows, map_areas_to_dirs,
                    read_dir_docstring, path_key, cache_codemap_projection,
                    ensure_codemap_gitignore, materialize_codemap_projection,
                    read_codemap_counts, write_codemap_counts,
                    update_root_codemap_docs)
```

In `scripts/harness_monitor.py`, add near `DESC_SCRIPT`:

```python
SUBDIR_HARNESS_SCRIPT = Path.home() / ".local" / "share" / "harness-hooks" / "generate_subdir_harness.py"
SUBDIR_HARNESS_TIMEOUT = 120
SUBDIR_HARNESS_MAX_DIRS = 5
```

Add helpers:

```python
def _subdir_harness_candidate_dirs(codemap_text: str, new_counts: dict[str, int] | None = None) -> list[str]:
    entries = parse_codemap_text(codemap_text)
    recorded_counts = read_codemap_counts(".")
    counts = recorded_counts or (new_counts or {})
    candidates = []
    for entry in entries:
        dir_path = entry.get("dir", "").strip("/")
        if not dir_path:
            continue
        symbols = counts.get(dir_path, entry.get("symbols"))
        if symbols is not None and int(symbols) >= SYMBOL_THRESHOLD:
            candidates.append(dir_path)
    return list(dict.fromkeys(candidates))[:SUBDIR_HARNESS_MAX_DIRS]


def refresh_subdir_harness_blocks(codemap_text: str, new_counts: dict[str, int] | None = None, job_id=None):
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
    dirs = _subdir_harness_candidate_dirs(codemap_text, new_counts)
    if not dirs:
        return "no_dirs"
    cmd = [
        sys.executable,
        str(script),
        ".",
        "--refresh-facts",
        "--platform",
        "claude",
        "--dirs",
        *dirs,
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
        refresh_subdir_harness_blocks(new_content, new_counts, job_id)
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
