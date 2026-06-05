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


def build_source_snapshot(project_dir: str | Path) -> dict:
    source_files = tracked_source_files(project_dir)
    return {
        "source_files": source_files,
        "repo_source_fingerprint": source_fingerprint(project_dir, source_files),
    }


def _dir_source_paths(source_snapshot: dict, dir_path: str) -> list[str]:
    rel = _clean_rel_dir(dir_path)
    prefix = rel + "/"
    return [path for path in source_snapshot.get("source_files", []) if path == rel or path.startswith(prefix)]


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


def extract_dir_facts(project_dir: str | Path, dir_path: str, source_snapshot: dict | None = None) -> dict:
    rel = _clean_rel_dir(dir_path)
    prefix = _quote_cypher(rel + "/")
    exact = _quote_cypher(rel)
    snapshot = source_snapshot or build_source_snapshot(project_dir)
    dir_paths = _dir_source_paths(snapshot, rel)
    repo_fp = snapshot["repo_source_fingerprint"]
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


def plan_directory(
    project_dir: str | Path,
    dir_path: str,
    files: list[str],
    *,
    mode: str = "background",
    source_snapshot: dict | None = None,
    expected_branch: str | None = None,
) -> dict:
    state = _read_state(project_dir)
    baseline = state.get("dirs", {}).get(_clean_rel_dir(dir_path))
    paths = _platform_doc_paths(project_dir, dir_path, files)
    existing_paths = [path for path in paths if path.exists()]
    snapshot = source_snapshot or build_source_snapshot(project_dir)
    current_repo_fp = snapshot["repo_source_fingerprint"]
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
    facts = extract_dir_facts(project_dir, dir_path, source_snapshot=snapshot)
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
    source_snapshot: dict | None = None,
    expected_branch: str | None = None,
) -> dict:
    plan = plan_directory(project_dir, dir_path, files, mode=mode, source_snapshot=source_snapshot, expected_branch=expected_branch)
    if plan["action"] == "manual_migration" and not (mode == "manual" and migrate):
        return plan
    if plan["action"] == "bootstrap" and not (mode == "manual" and bootstrap):
        return plan
    if plan["action"] == "skip":
        return plan
    facts = plan.get("facts") or extract_dir_facts(project_dir, dir_path, source_snapshot=source_snapshot)
    fact_block = render_fact_block(facts)
    if render_consistency_check(fact_block, facts)["ok"] is False:
        return {"dir": _clean_rel_dir(dir_path), "action": "refresh_facts", "status": "render_consistency_failed"}
    if plan["action"] == "rebaseline":
        return write_rebaseline_state(project_dir, dir_path, facts, plan["files"])
    if plan["action"] == "manual_migration":
        statuses = {}
        for path in _platform_doc_paths(project_dir, dir_path, plan["files"]):
            statuses[path.name] = migrate_legacy_doc_to_facts(path, fact_block, project_dir=project_dir, expected_branch=expected_branch)
        rendered_status = {
            name: {"status": status, "block_hash": _sha256_text(fact_block)}
            for name, status in statuses.items()
        }
        if any(status == "branch_changed" for status in statuses.values()):
            return {
                "dir": _clean_rel_dir(dir_path),
                "action": "manual_migration",
                "status": "branch_changed",
                "files": plan["files"],
                "rendered": statuses,
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
            if not branch_matches(project_dir, expected_branch):
                status = "branch_changed"
                rendered_status[path.name] = {"status": status, "block_hash": _sha256_text(fact_block)}
                continue
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
    if any(item.get("status") == "branch_changed" for item in rendered_status.values()):
        return {"dir": _clean_rel_dir(dir_path), "action": plan["action"], "status": "branch_changed", "files": plan["files"]}
    state = _read_state(project_dir)
    state.setdefault("dirs", {})[_clean_rel_dir(dir_path)] = _state_entry_for(dir_path, facts, fact_block, rendered_status)
    if not _write_state(project_dir, state):
        return {"dir": _clean_rel_dir(dir_path), "action": plan["action"], "status": "cache_write_failed"}
    if any(item.get("status") in {"updated", "created"} for item in rendered_status.values()):
        status = "updated"
    else:
        status = "unchanged"
    return {"dir": _clean_rel_dir(dir_path), "action": plan["action"], "status": status, "files": plan["files"]}


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
    parser.add_argument("--expected-branch", default="")
    return parser.parse_args(argv)


def _files_for_platform(platform: str) -> list[str]:
    return ["CLAUDE.md", "AGENTS.md"] if platform == "claude" else ["AGENTS.md", "CLAUDE.md"]


def discover_candidate_dirs(project_dir: str | Path, max_dirs: int) -> list[str]:
    root = Path(project_dir)
    entries = parse_codemap(root / "CODE_MAP.md")
    recorded_counts = read_codemap_counts(root)
    return candidate_codemap_dirs(entries, recorded_counts, max_dirs=max_dirs)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    mode = "manual" if args.manual or args.bootstrap or args.migrate else "background"
    dirs = [_clean_rel_dir(dir_path) for dir_path in args.dirs[: args.max_dirs] if _clean_rel_dir(dir_path)]
    if not dirs and (args.plan or args.refresh_facts):
        dirs = discover_candidate_dirs(args.project_dir, args.max_dirs)
    files = _files_for_platform(args.platform)
    source_snapshot = build_source_snapshot(args.project_dir)
    expected_branch = args.expected_branch or None
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
                    source_snapshot=source_snapshot,
                    expected_branch=expected_branch,
                )
            )
        else:
            actions.append(
                plan_directory(
                    args.project_dir,
                    dir_path,
                    files,
                    mode=mode,
                    source_snapshot=source_snapshot,
                    expected_branch=expected_branch,
                )
            )
    print(json.dumps({"schema_version": 1, "actions": actions}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
