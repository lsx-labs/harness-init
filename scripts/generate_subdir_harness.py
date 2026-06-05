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
    candidate_codemap_dirs,
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


def current_branch(project_dir: str | Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(project_dir)), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def branch_matches(project_dir: str | Path, expected_branch: str | None) -> bool:
    return not expected_branch or current_branch(project_dir) == expected_branch


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


def migrate_legacy_doc_to_facts(
    path: Path,
    fact_block: str,
    *,
    project_dir: str | Path = ".",
    expected_branch: str | None = None,
) -> str:
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
    if not branch_matches(project_dir, expected_branch):
        return "branch_changed"
    try:
        _atomic_write_text(path, text)
    except OSError:
        return "write_failed"
    return "updated"
