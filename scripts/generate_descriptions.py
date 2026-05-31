#!/usr/bin/env python3
"""Generate CODE_MAP.md descriptions: AI + GitNexus (primary) / keywords (fallback).

Modes:
  --generate  fill empty entries only (default)
  --refresh   regenerate all (except 📌 manual overrides)
  --dry-run   show what would change
"""

from __future__ import annotations

import ast
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
from pathlib import Path

from harness_shared import (
    CODEX_EXEC_SANDBOX_ARGS,
    LOW_CONFIDENCE_MARKER,
    MANUAL_MARKER,
    SOURCE_EXTS,
    get_ai_cmd,
    gitnexus_markdown_rows,
    is_acceptable_description,
    is_low_confidence_description,
    is_low_quality_description,
    needs_description_refresh,
    parse_codemap as _parse_codemap,
    parse_gitnexus_markdown,
    read_dir_docstring,
)

HOOK_TIMEOUT = 10
DEFAULT_AI_TIMEOUT = 180
DEFAULT_BATCH_SIZE = 2
GENERIC = {"main", "init", "run", "start", "stop", "get", "set", "test", "setup", "parse",
           "build", "create", "delete", "update", "load", "save", "read", "write", "open",
           "close", "validate", "check", "add", "all", "data", "config", "path", "name", "type"}
PROJECT_OVERRIDE_PATH = Path(".harness/codemap_descriptions.json")
PROVIDER_BY_CATEGORY = {
    "manual_protected": "preserve",
    "project_override": "override",
    "code_process": "ai_gitnexus",
    "code_symbols": "local_code_summary",
    "test": "test_summary",
    "docs": "markdown_titles",
    "example": "example_summary",
    "artifact": "artifact_summary",
    "empty_or_marker": "filesystem_summary",
    "unknown": "fallback",
}


@dataclass(frozen=True)
class DirectoryEvidence:
    dir_path: str
    file_count: int
    py_count: int
    md_count: int
    json_count: int
    gitignored: bool
    gitnexus_files: int
    gitnexus_functions: int
    gitnexus_methods: int
    gitnexus_classes: int
    gitnexus_processes: int
    readme_summary: str
    module_docstring: str
    markdown_titles: tuple[str, ...]
    test_names: tuple[str, ...]
    child_dirs: tuple[str, ...]


# ══════════════════════════════════════════════════════════
# CODE_MAP.md parsing
# ══════════════════════════════════════════════════════════

def normalize_dir_key(dir_path: str, *, trailing_slash: bool = False) -> str:
    """Normalize CODE_MAP directory keys without touching filesystem state."""
    normalized = str(dir_path or "").strip().replace("\\", "/")
    normalized = re.sub(r"^\./+", "", normalized).strip("/")
    if trailing_slash and normalized:
        return f"{normalized}/"
    return normalized


def _list_dir_files(dir_path: str) -> list[Path]:
    path = Path(normalize_dir_key(dir_path))
    if not path.exists():
        return []
    if path.is_file():
        return [path]

    rg = shutil.which("rg")
    if rg:
        try:
            result = subprocess.run(
                [rg, "--files", str(path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return [Path(line) for line in result.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return sorted(p for p in path.rglob("*") if p.is_file())


def _is_gitignored_dir(dir_path: str) -> bool:
    key = normalize_dir_key(dir_path, trailing_slash=True)
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", key],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _read_readme_summary(dir_path: str) -> str:
    path = Path(normalize_dir_key(dir_path))
    for name in ("README.md", "readme.md"):
        readme = path / name
        if not readme.is_file():
            continue
        try:
            for line in readme.read_text(encoding="utf-8").splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    return stripped[:80]
        except OSError:
            return ""
    return ""


def _markdown_titles(files: list[Path], *, limit: int = 12) -> tuple[str, ...]:
    titles: list[str] = []
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            match = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
            if not match:
                continue
            title = match.group(1).strip().strip("#").strip()
            if title and title not in titles:
                titles.append(title[:80])
            if len(titles) >= limit:
                return tuple(titles)
    return tuple(titles)


def _test_names(files: list[Path], *, limit: int = 40) -> tuple[str, ...]:
    names: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                if node.name not in names:
                    names.append(node.name)
            if len(names) >= limit:
                return tuple(names)
    return tuple(names)


def _child_dirs(dir_path: str) -> tuple[str, ...]:
    path = Path(normalize_dir_key(dir_path))
    if not path.is_dir():
        return ()
    children = []
    for child in sorted(path.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            children.append(child.name)
    return tuple(children)


def _gitnexus_counts(dir_path: str) -> dict[str, int]:
    prefix = normalize_dir_key(dir_path, trailing_slash=True).replace("'", "\\'")
    if not prefix:
        return {"files": 0, "functions": 0, "methods": 0, "classes": 0, "processes": 0}
    if not Path(".gitnexus").is_dir():
        return {"files": 0, "functions": 0, "methods": 0, "classes": 0, "processes": 0}
    rows = gitnexus_query(
        f"OPTIONAL MATCH (f:File) WHERE f.filePath STARTS WITH '{prefix}' "
        "WITH count(DISTINCT f) AS files "
        f"OPTIONAL MATCH (fn:Function) WHERE fn.filePath STARTS WITH '{prefix}' "
        "WITH files, count(DISTINCT fn) AS functions "
        f"OPTIONAL MATCH (m:Method) WHERE m.filePath STARTS WITH '{prefix}' "
        "WITH files, functions, count(DISTINCT m) AS methods "
        f"OPTIONAL MATCH (c:Class) WHERE c.filePath STARTS WITH '{prefix}' "
        "WITH files, functions, methods, count(DISTINCT c) AS classes "
        "OPTIONAL MATCH (s)-[:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process) "
        f"WHERE s.filePath STARTS WITH '{prefix}' "
        "RETURN files, functions, methods, classes, count(DISTINCT p) AS processes"
    )
    if not rows or len(rows[0]) < 5:
        return {"files": 0, "functions": 0, "methods": 0, "classes": 0, "processes": 0}
    keys = ("files", "functions", "methods", "classes", "processes")
    counts: dict[str, int] = {}
    for key, value in zip(keys, rows[0]):
        try:
            counts[key] = int(str(value).strip())
        except (TypeError, ValueError):
            counts[key] = 0
    return counts


def _gitnexus_counts_for_dirs(dirs: list[str]) -> dict[str, dict[str, int]]:
    prefixes = [normalize_dir_key(d, trailing_slash=True) for d in dirs if normalize_dir_key(d)]
    if not prefixes or not Path(".gitnexus").is_dir():
        return {}
    rows = gitnexus_query(
        f"UNWIND {json.dumps(prefixes)} AS prefix "
        "OPTIONAL MATCH (f:File) WHERE f.filePath STARTS WITH prefix "
        "WITH prefix, count(DISTINCT f) AS files "
        "OPTIONAL MATCH (fn:Function) WHERE fn.filePath STARTS WITH prefix "
        "WITH prefix, files, count(DISTINCT fn) AS functions "
        "OPTIONAL MATCH (m:Method) WHERE m.filePath STARTS WITH prefix "
        "WITH prefix, files, functions, count(DISTINCT m) AS methods "
        "OPTIONAL MATCH (c:Class) WHERE c.filePath STARTS WITH prefix "
        "WITH prefix, files, functions, methods, count(DISTINCT c) AS classes "
        "OPTIONAL MATCH (s)-[:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process) "
        "WHERE s.filePath STARTS WITH prefix "
        "RETURN prefix, files, functions, methods, classes, count(DISTINCT p) AS processes"
    )
    result: dict[str, dict[str, int]] = {}
    keys = ("files", "functions", "methods", "classes", "processes")
    for row in rows:
        if len(row) < 6:
            continue
        dir_key = normalize_dir_key(row[0])
        counts: dict[str, int] = {}
        for key, value in zip(keys, row[1:6]):
            try:
                counts[key] = int(str(value).strip())
            except (TypeError, ValueError):
                counts[key] = 0
        result[dir_key] = counts
    return result


def collect_directory_evidence(
    dir_path: str,
    *,
    gitnexus_counts: dict[str, int] | None = None,
) -> DirectoryEvidence:
    """Collect local and GitNexus evidence used by CODE_MAP description providers."""
    key = normalize_dir_key(dir_path, trailing_slash=True)
    files = _list_dir_files(key)
    suffixes = [path.suffix.lower() for path in files]
    counts = gitnexus_counts or _gitnexus_counts(key)
    return DirectoryEvidence(
        dir_path=key,
        file_count=len(files),
        py_count=sum(1 for suffix in suffixes if suffix == ".py"),
        md_count=sum(1 for suffix in suffixes if suffix == ".md"),
        json_count=sum(1 for suffix in suffixes if suffix == ".json"),
        gitignored=_is_gitignored_dir(key),
        gitnexus_files=counts["files"],
        gitnexus_functions=counts["functions"],
        gitnexus_methods=counts["methods"],
        gitnexus_classes=counts["classes"],
        gitnexus_processes=counts["processes"],
        readme_summary=_read_readme_summary(key),
        module_docstring=get_docstring(key),
        markdown_titles=_markdown_titles(files),
        test_names=_test_names(files),
        child_dirs=_child_dirs(key),
    )


def _override_description(raw_value) -> tuple[str, str]:
    if isinstance(raw_value, str):
        desc = raw_value.strip()
    elif isinstance(raw_value, dict):
        desc = str(raw_value.get("description") or "").strip()
    else:
        return "", "invalid_value"
    if not desc:
        return "", "empty"
    if len(desc) > 60:  # match write_descriptions truncation so validated text == written text
        return "", "too_long"
    if is_low_quality_description(desc) or not is_acceptable_description(desc):
        return "", "low_quality"
    return desc, ""


def load_project_overrides(root: Path = Path(".")) -> tuple[dict[str, str], dict]:
    """Load optional project-maintained CODE_MAP descriptions."""
    path = Path(root) / PROJECT_OVERRIDE_PATH
    report = {
        "path": str(PROJECT_OVERRIDE_PATH),
        "loaded": 0,
        "rejected": {},
        "error": None,
    }
    if not path.is_file():
        return {}, report
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        report["error"] = str(exc)
        return {}, report
    raw_descriptions = data.get("descriptions") if isinstance(data, dict) else None
    if not isinstance(raw_descriptions, dict):
        report["error"] = "missing_descriptions_object"
        return {}, report

    overrides: dict[str, str] = {}
    for raw_key, raw_value in raw_descriptions.items():
        key = normalize_dir_key(str(raw_key))
        if not key:
            report["rejected"][str(raw_key)] = "empty_key"
            continue
        desc, reason = _override_description(raw_value)
        if reason:
            report["rejected"][key] = reason
            continue
        overrides[key] = desc
    report["loaded"] = len(overrides)
    return overrides, report


def _has_source_files(dir_path: str) -> bool:
    return any(path.suffix.lower() in SOURCE_EXTS for path in _list_dir_files(dir_path))


def classify_directory(evidence: DirectoryEvidence, *, has_override: bool, existing_desc: str) -> str:
    """Classify a CODE_MAP directory so generation can choose the right provider."""
    desc = (existing_desc or "").strip()
    key = normalize_dir_key(evidence.dir_path, trailing_slash=True)
    if desc.startswith(MANUAL_MARKER):
        return "manual_protected"
    if has_override:
        return "project_override"
    if key == "tests/" or key.startswith("tests/"):
        return "test"
    if key == "docs/" or key.startswith("docs/") or key == "doc/" or key.startswith("doc/"):
        return "docs"
    if key == "examples/" or key.startswith("examples/"):
        return "example"
    if evidence.gitnexus_processes > 0:
        return "code_process"
    if evidence.py_count > 0 or evidence.gitnexus_functions > 0 or evidence.gitnexus_methods > 0 or evidence.gitnexus_classes > 0:
        return "code_symbols"
    if evidence.gitignored:
        return "artifact"
    if evidence.file_count == 0 or (
        evidence.file_count <= 2
        and evidence.py_count == 0
        and evidence.md_count == 0
        and evidence.json_count == 0
    ):
        return "empty_or_marker"
    return "unknown"


def select_provider(category: str) -> str:
    return PROVIDER_BY_CATEGORY.get(category, "fallback")


def build_classification_report(
    dirs: list[str],
    *,
    overrides: dict[str, str] | None = None,
    include_evidence: bool = False,
) -> dict[str, dict]:
    overrides = overrides or {}
    existing = {entry["dir"]: entry.get("desc") or "" for entry in _parse_codemap(Path("CODE_MAP.md"))}
    gitnexus_counts_by_dir = _gitnexus_counts_for_dirs(dirs)
    report: dict[str, dict] = {}
    for dir_path in dirs:
        key = normalize_dir_key(dir_path)
        evidence = collect_directory_evidence(
            key,
            gitnexus_counts=gitnexus_counts_by_dir.get(key),
        )
        category = classify_directory(
            evidence,
            has_override=key in overrides,
            existing_desc=existing.get(key, ""),
        )
        row = {
            "category": category,
            "provider": select_provider(category),
            "file_count": evidence.file_count,
            "gitnexus_files": evidence.gitnexus_files,
            "gitnexus_processes": evidence.gitnexus_processes,
        }
        if include_evidence:
            row["evidence"] = evidence
        report[key] = row
    return report


def _topic_from_tokens(tokens: list[str]) -> list[str]:
    topics: list[str] = []
    mapping = (
        ("session", "会话"),
        ("release", "发布"),
        ("gate", "门禁"),
        ("cache", "缓存"),
        ("worker", "worker"),
        ("config", "配置"),
        ("auth", "认证"),
        ("api", "API"),
        ("database", "数据库"),
        ("cli", "CLI"),
        ("distributed", "分布式"),
        ("doctor", "诊断"),
    )
    text = " ".join(tokens).lower()
    for needle, label in mapping:
        if needle in text and label not in topics:
            topics.append(label)
    return topics[:4]


def summarize_test_dir(evidence: DirectoryEvidence) -> str:
    key = normalize_dir_key(evidence.dir_path)
    leaf = key.rsplit("/", 1)[-1] if key and key != "tests" else ""
    label = f"{leaf.replace('_', ' ')} 测试" if leaf else "测试套件"
    topics = _topic_from_tokens(list(evidence.test_names) + list(evidence.child_dirs) + [key])
    if topics:
        return f"{label}：{'、'.join(topics)}"
    return f"{label}：行为校验、边界条件与回归覆盖"


def summarize_docs_dir(evidence: DirectoryEvidence) -> str:
    key = normalize_dir_key(evidence.dir_path)
    topics = _topic_from_tokens(list(evidence.markdown_titles) + [key])
    if topics:
        return f"项目文档：{'、'.join(topics)}"
    leaf = key.rsplit("/", 1)[-1] if key and key not in {"doc", "docs"} else "项目"
    return f"{leaf.replace('_', ' ')} 文档：说明、设计记录与操作参考"


def summarize_artifact_dir(evidence: DirectoryEvidence) -> str:
    key = normalize_dir_key(evidence.dir_path)
    parts = set(key.split("/"))
    if "cache" in parts:
        return "本地缓存产物：计算中间结果与可复用运行状态"
    if {"result", "results", "report", "reports"} & parts:
        return "结果产物目录：运行输出、汇总与审计记录"
    if {"release", "gate", "release_gate"} & parts:
        return "发布检查产物：检查结果、判定与审计记录"
    if "data" in parts:
        return "本地数据目录：数据文件、缓存与生成产物"
    return "生成产物目录：缓存、结果与运行审计文件"


def summarize_examples_dir(evidence: DirectoryEvidence) -> str:
    if evidence.py_count:
        return "示例入口：最小运行脚本与用法演示"
    return "示例入口：最小配置与使用方式"


def deterministic_generate(
    dirs: list[str],
    *,
    classification: dict[str, dict] | None = None,
    evidence_by_dir: dict[str, DirectoryEvidence] | None = None,
) -> tuple[dict[str, str], dict]:
    """Generate descriptions for directory classes that do not need AI."""
    descriptions: dict[str, str] = {}
    provider_counts: dict[str, int] = {}
    evidence_by_dir = evidence_by_dir or {}
    for dir_path in dirs:
        key = normalize_dir_key(dir_path)
        evidence = evidence_by_dir.get(key) or collect_directory_evidence(key)
        row = (classification or {}).get(key)
        category = row["category"] if row else classify_directory(evidence, has_override=False, existing_desc="")
        provider = select_provider(category)
        desc = ""
        if category == "test":
            desc = summarize_test_dir(evidence)
        elif category == "docs":
            desc = summarize_docs_dir(evidence)
        elif category == "artifact":
            desc = summarize_artifact_dir(evidence)
        elif category == "example":
            desc = summarize_examples_dir(evidence)
        if desc:
            descriptions[key] = desc
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
    return descriptions, {"provider_counts": provider_counts}


def build_dir_fingerprint(dir_path: str) -> str:
    """Build a stable fingerprint for deciding whether a CODE_MAP dir changed."""
    key = normalize_dir_key(dir_path)
    if _is_gitignored_dir(key) and not _has_source_files(key):
        return f"artifact:{key}"
    hasher = hashlib.sha256()
    try:
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", key],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result and result.returncode == 0 and result.stdout.strip():
        hasher.update(result.stdout.encode("utf-8"))
        return hasher.hexdigest()

    for path in _list_dir_files(key):
        try:
            stat = path.stat()
        except OSError:
            continue
        hasher.update(str(path).encode("utf-8"))
        hasher.update(str(stat.st_size).encode("utf-8"))
        hasher.update(str(int(stat.st_mtime_ns)).encode("utf-8"))
    return hasher.hexdigest()


def fingerprint_state_path(root: Path = Path(".")) -> Path:
    project_id = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path.home() / ".local" / "share" / "harness-hooks" / "projects" / project_id / "codemap_fingerprints.json"


def _read_fingerprint_state(state_path: Path) -> dict[str, str]:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def filter_dirs_by_fingerprints(
    dirs: list[str],
    *,
    state_path: Path | None = None,
) -> tuple[list[str], dict]:
    state_path = state_path or fingerprint_state_path()
    state = _read_fingerprint_state(state_path)
    existing = {entry["dir"]: entry.get("desc") or "" for entry in _parse_codemap(Path("CODE_MAP.md"))}
    selected: list[str] = []
    skipped: list[str] = []
    changed: list[str] = []
    missing: list[str] = []
    fingerprints: dict[str, str] = {}
    for dir_path in dirs:
        key = normalize_dir_key(dir_path)
        current = build_dir_fingerprint(key)
        fingerprints[key] = current
        previous = state.get(key)
        if previous is None:
            missing.append(key)
            selected.append(key)
            continue
        if previous != current:
            changed.append(key)
            selected.append(key)
            continue
        if needs_description_refresh(existing.get(key, "")):
            selected.append(key)
            continue
        skipped.append(key)
    return selected, {
        "state_path": str(state_path),
        "skipped": skipped,
        "changed": changed,
        "missing": missing,
        "fingerprints": fingerprints,
    }


def save_dir_fingerprints(dirs: list[str], *, state_path: Path | None = None) -> None:
    if not dirs:
        return
    state_path = state_path or fingerprint_state_path()
    state = _read_fingerprint_state(state_path)
    for dir_path in dirs:
        key = normalize_dir_key(dir_path)
        state[key] = build_dir_fingerprint(key)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, state_path)

def _matches_refresh_dir(dir_path: str, refresh_dirs: list[str] | None) -> bool:
    if not refresh_dirs:
        return True
    key = normalize_dir_key(dir_path)
    for raw in refresh_dirs:
        target = normalize_dir_key(raw)
        if key == target or key.startswith(f"{target}/"):
            return True
    return False


def parse_codemap(mode: str, *, refresh_dirs: list[str] | None = None) -> list[str]:
    """Return list of directories needing descriptions based on mode."""
    entries = _parse_codemap(Path("CODE_MAP.md"))
    dirs = []
    for e in entries:
        desc = e.get("desc") or ""
        if desc.startswith(MANUAL_MARKER):
            continue
        if not _matches_refresh_dir(e["dir"], refresh_dirs):
            continue
        if refresh_dirs:
            dirs.append(e["dir"])
            continue
        if mode in {"--generate", "--dry-run"} and not needs_description_refresh(desc):
            continue
        dirs.append(e["dir"])
    return dirs


def filter_generated_descriptions(
    descriptions: dict[str, str],
    *,
    allow_low_confidence: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Reject low-signal generated descriptions before writing CODE_MAP.md."""
    accepted: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for dir_path, desc in descriptions.items():
        if not isinstance(desc, str) or not desc.strip():
            rejected[dir_path] = "empty"
            continue
        desc = desc.strip()
        if is_low_confidence_description(desc):
            if allow_low_confidence:
                accepted[dir_path] = desc
            else:
                rejected[dir_path] = "low_confidence"
            continue
        if not is_acceptable_description(desc):
            rejected[dir_path] = "low_quality"
            continue
        accepted[dir_path] = desc
    return accepted, rejected


def build_quality_report(
    codemap_path: Path = Path("CODE_MAP.md"),
    *,
    classification: dict[str, dict] | None = None,
    include_breakdown: bool = False,
) -> dict:
    """Summarize CODE_MAP description quality for audit output."""
    entries = _parse_codemap(codemap_path)
    described = [e for e in entries if (e.get("desc") or "").strip()]
    report = {
        "total": len(entries),
        "described": len(described),
        "acceptable": sum(1 for e in entries if is_acceptable_description(e.get("desc") or "")),
        "low_quality": sum(1 for e in entries if is_low_quality_description(e.get("desc") or "")),
        "low_confidence": sum(1 for e in entries if is_low_confidence_description(e.get("desc") or "")),
        "empty": sum(1 for e in entries if not (e.get("desc") or "").strip()),
        "needs_refresh": sum(1 for e in entries if needs_description_refresh(e.get("desc") or "")),
    }
    if not include_breakdown:
        return report

    classification = classification or {}
    by_category: dict[str, dict[str, int]] = {}
    by_provider: dict[str, int] = {}
    not_indexed_dirs: list[str] = []
    indexed_but_no_process_dirs: list[str] = []
    for entry in entries:
        key = normalize_dir_key(entry["dir"])
        row = classification.get(key, {})
        category = row.get("category", "unknown")
        provider = row.get("provider", "unknown")
        bucket = by_category.setdefault(category, {"total": 0, "acceptable": 0, "needs_refresh": 0})
        bucket["total"] += 1
        if is_acceptable_description(entry.get("desc") or ""):
            bucket["acceptable"] += 1
        if needs_description_refresh(entry.get("desc") or ""):
            bucket["needs_refresh"] += 1
        by_provider[provider] = by_provider.get(provider, 0) + 1
        gitnexus_files = int(row.get("gitnexus_files") or 0)
        gitnexus_processes = int(row.get("gitnexus_processes") or 0)
        if gitnexus_files == 0:
            not_indexed_dirs.append(key)
        elif gitnexus_processes == 0:
            indexed_but_no_process_dirs.append(key)
    report.update({
        "by_category": by_category,
        "by_provider": by_provider,
        "not_indexed_dirs": not_indexed_dirs,
        "indexed_but_no_process_dirs": indexed_but_no_process_dirs,
    })
    return report


_TRUNC_BOUNDARY = set(" \t，、；。：/|·")


def _truncate_desc(text: str, limit: int = 60) -> str:
    """Cap a description at `limit` chars without ending mid-token.

    If the cut lands inside a token, back off to the nearest boundary char within a
    short window (works for both ASCII spaces and CJK punctuation); otherwise accept
    the hard cut rather than chop off too much.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    if text[limit] not in _TRUNC_BOUNDARY and head[-1] not in _TRUNC_BOUNDARY:
        for i in range(len(head) - 1, limit - 12, -1):
            if head[i] in _TRUNC_BOUNDARY:
                return head[:i].rstrip()
    return head


def write_descriptions(descriptions: dict[str, str]) -> list[dict]:
    """Write descriptions to CODE_MAP.md, return list of changes."""
    codemap = Path("CODE_MAP.md")
    lines = codemap.read_text(encoding="utf-8").splitlines(keepends=True)
    changes = []
    normalized = {}
    for dir_path, raw_desc in descriptions.items():
        if not raw_desc or not isinstance(raw_desc, str):
            continue
        normalized[dir_path.strip("/")] = _truncate_desc(raw_desc)
    if not normalized:
        return []

    def split_newline(line: str) -> tuple[str, str]:
        body = line.rstrip("\r\n")
        return body, line[len(body):]

    def rewrite_line(line: str, desc: str) -> str:
        body, newline = split_newline(line)
        count = ""
        count_match = re.search(r'\s+(\(\d+\s+symbols?\))\s*$', body)
        if count_match:
            count = count_match.group(1)
            body = body[:count_match.start()].rstrip()
        base = body.split("—", 1)[0].rstrip()
        suffix = f" {count}" if count else ""
        return f"{base} — {desc}{suffix}{newline}"

    current_top = ""
    updated_lines = []
    for line in lines:
        top_match = re.match(r'^(###\s+)(\S+)/?(.*)$', line)
        if top_match:
            current_top = top_match.group(2).rstrip("/")
            desc = normalized.get(current_top)
            if desc is not None:
                updated_lines.append(rewrite_line(line, desc))
                changes.append({"dir": current_top, "desc": desc})
                continue
        sub_match = re.match(r'^-\s+\*\*(\S+?)/?\*\*', line)
        if current_top and sub_match:
            sub_path = sub_match.group(1).rstrip("/")
            key = f"{current_top}/{sub_path}"
            desc = normalized.get(key)
            if desc is not None:
                updated_lines.append(rewrite_line(line, desc))
                changes.append({"dir": key, "desc": desc})
                continue
        updated_lines.append(line)

    if changes:
        content = "".join(updated_lines)
        tmp = codemap.with_suffix(codemap.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, codemap)
    return changes


# ══════════════════════════════════════════════════════════
# AI + GitNexus (primary path)
# ══════════════════════════════════════════════════════════

def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _run_ai_command(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run an AI CLI in its own process group so timeout cleans children too."""
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def batch_dirs(dirs: list[str], batch_size: int) -> list[list[str]]:
    """Split directories into stable, bounded AI prompt batches."""
    if not dirs:
        return []
    batch_size = max(1, int(batch_size or 1))
    return [dirs[i:i + batch_size] for i in range(0, len(dirs), batch_size)]


def _parse_ai_json(raw: str) -> dict[str, str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        print(f"ai_generate: no JSON found in response ({len(raw)} chars)", file=sys.stderr)
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"ai_generate: JSON parse failed: {e}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def _normalize_ai_descriptions(data: dict[str, str] | None, requested_dirs: list[str]) -> dict[str, str] | None:
    if not data:
        return None
    requested = {normalize_dir_key(d) for d in requested_dirs}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in data.items():
        key = normalize_dir_key(str(raw_key))
        if key not in requested or not isinstance(raw_value, str):
            continue
        normalized[key] = raw_value
    return normalized or None


def _ai_evidence_payload(
    dirs: list[str],
    *,
    evidence_by_dir: dict[str, DirectoryEvidence] | None = None,
    classification: dict[str, dict] | None = None,
) -> list[dict]:
    payload = []
    evidence_by_dir = evidence_by_dir or {}
    classification = classification or {}
    collect_evidence = bool(evidence_by_dir or classification)
    for dir_path in dirs:
        key = normalize_dir_key(dir_path)
        evidence = evidence_by_dir.get(key)
        if evidence is None and collect_evidence:
            evidence = collect_directory_evidence(key)
        row = classification.get(key)
        if not row:
            category = classify_directory(evidence, has_override=False, existing_desc="") if evidence else "unknown"
            row = {"category": category, "provider": select_provider(category)}
        evidence_data = asdict(evidence) if evidence else {"dir_path": normalize_dir_key(key, trailing_slash=True)}
        payload.append({
            "dir": key,
            "category": row["category"],
            "provider": row["provider"],
            "evidence": evidence_data,
        })
    return payload


def ai_generate(
    dirs: list[str],
    *,
    timeout: int = DEFAULT_AI_TIMEOUT,
    evidence_by_dir: dict[str, DirectoryEvidence] | None = None,
    classification: dict[str, dict] | None = None,
) -> dict[str, str] | None:
    """Invoke AI CLI to generate descriptions via GitNexus. Returns {dir: desc} or None."""
    cmd = get_ai_cmd()
    if not cmd or not Path(".gitnexus").is_dir():
        return None

    project = Path(".").resolve().name
    evidence_payload = _ai_evidence_payload(
        dirs,
        evidence_by_dir=evidence_by_dir,
        classification=classification,
    )
    prompt = (
        f"你在项目 {project} 中。为以下 {len(dirs)} 个目录生成 CODE_MAP.md 导航描述。\n\n"
        f"规则：\n"
        f"1. evidence 中有 category/provider/file/symbol/process 信息，必须优先使用这些事实\n"
        f"2. 如果 category 是 test/docs/artifact/example，优先使用 evidence，不要强制调用 GitNexus\n"
        f"3. 如果 category 是 code_process，必须使用 GitNexus 查询或 evidence 中的 process/symbol 信息\n"
        f"4. 禁止输出函数名列表、截断 token、泛化测试描述；不要自行编造 evidence 以外的事实\n"
        f"5. 每个描述中文 ≤ 50 字，格式：核心职责：2-3 个关键功能词\n"
        f"6. 只输出纯 JSON，无 markdown 包裹，key 必须完全等于输入目录，格式：{{\"目录名\": \"描述\"}}\n\n"
        f"目录：{' '.join(dirs)}\n\n"
        f"evidence：{json.dumps(evidence_payload, ensure_ascii=False)}"
    )

    try:
        if "claude" in cmd:
            r = _run_ai_command(
                [cmd, "-p", prompt, "--allowedTools", "Read,mcp__gitnexus*",
                 "--output-format", "json"],
                timeout)
            try:
                raw = json.loads(r.stdout)["result"]
            except (json.JSONDecodeError, KeyError):
                print(f"ai_generate: claude failed, stderr={r.stderr[:200]}", file=sys.stderr)
                return None
        else:
            r = _run_ai_command([cmd, "exec", *CODEX_EXEC_SANDBOX_ARGS, prompt], timeout)
            raw = r.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"ai_generate: timed out after {timeout}s for dirs={dirs}", file=sys.stderr)
        return None
    except (FileNotFoundError, OSError):
        return None

    return _normalize_ai_descriptions(_parse_ai_json(raw), dirs)


def ai_generate_batched(
    dirs: list[str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: int = DEFAULT_AI_TIMEOUT,
    evidence_by_dir: dict[str, DirectoryEvidence] | None = None,
    classification: dict[str, dict] | None = None,
) -> tuple[dict[str, str], dict]:
    """Generate descriptions in bounded AI batches (sequential) and return audit metadata.

    Runs as a detached background job with no timeout pressure, so batches and the
    single per-dir retry pass run sequentially — no worker pool.
    """
    cmd = get_ai_cmd()
    if not cmd:
        return {}, {"attempted": False, "reason": "no_ai_cmd"}
    if not Path(".gitnexus").is_dir():
        return {}, {"attempted": False, "reason": "no_gitnexus_index"}

    batches = batch_dirs(dirs, batch_size)
    report = {
        "attempted": True,
        "batch_size": max(1, int(batch_size or 1)),
        "timeout_seconds": timeout,
        "success_dirs": [],
        "failed_dirs": [],
        "initial_success_dirs": [],
        "initial_failed_dirs": [],
        "retry_attempted": False,
        "retry_timeout_seconds": None,
        "retry_success_dirs": [],
        "retry_failed_dirs": [],
        "provider_counts": {},
        "batches": [],
        "retries": [],
    }
    if classification:
        for dir_path in dirs:
            provider = classification.get(normalize_dir_key(dir_path), {}).get("provider", "ai_gitnexus")
            report["provider_counts"][provider] = report["provider_counts"].get(provider, 0) + 1
    if not batches:
        return {}, report

    def _ai_kwargs(batch: list[str], timeout_seconds: int) -> dict:
        kwargs = {"timeout": timeout_seconds}
        if evidence_by_dir is not None:
            kwargs["evidence_by_dir"] = {
                normalize_dir_key(d): evidence_by_dir[normalize_dir_key(d)]
                for d in batch
                if normalize_dir_key(d) in evidence_by_dir
            }
        if classification is not None:
            kwargs["classification"] = {
                normalize_dir_key(d): classification[normalize_dir_key(d)]
                for d in batch
                if normalize_dir_key(d) in classification
            }
        return kwargs

    def run_one(index: int, batch: list[str], timeout_seconds: int) -> dict:
        try:
            result = ai_generate(batch, **_ai_kwargs(batch, timeout_seconds))
        except Exception as exc:  # defensive: keep a bad call from hiding the audit trail
            return {"index": index, "dirs": batch, "status": "error",
                    "error": str(exc), "descriptions": {}}
        return {"index": index, "dirs": batch, "status": "success" if result else "failed",
                "descriptions": result or {}}

    def record(item: dict, success_key: str, failed_key: str, log_key: str) -> list[str]:
        requested = item["dirs"]
        returned = {k: v for k, v in item.get("descriptions", {}).items() if k in requested}
        descriptions.update(returned)
        success_dirs = [d for d in requested if d in returned]
        failed_dirs = [d for d in requested if d not in returned]
        report[success_key].extend(success_dirs)
        report[failed_key].extend(failed_dirs)
        status = "partial" if (returned and failed_dirs) else item["status"]
        report[log_key].append({
            "index": item["index"], "dirs": requested, "status": status,
            "returned_dirs": success_dirs, "failed_dirs": failed_dirs,
            **({"error": item["error"]} if item.get("error") else {}),
        })
        return failed_dirs

    descriptions: dict[str, str] = {}
    retry_dirs: list[str] = []
    for index, batch in enumerate(batches):
        for d in record(run_one(index, batch, timeout),
                        "initial_success_dirs", "initial_failed_dirs", "batches"):
            if d not in retry_dirs:
                retry_dirs.append(d)

    if retry_dirs:
        retry_timeout = max(timeout, 240)
        report["retry_attempted"] = True
        report["retry_timeout_seconds"] = retry_timeout
        for index, dir_path in enumerate(retry_dirs):
            record(run_one(index, [dir_path], retry_timeout),
                   "retry_success_dirs", "retry_failed_dirs", "retries")

    report["success_dirs"] = [d for d in dirs if d in descriptions]
    report["failed_dirs"] = [d for d in dirs if d not in descriptions]
    return descriptions, report


# ══════════════════════════════════════════════════════════
# Fallback: docstring + GitNexus keywords
# ══════════════════════════════════════════════════════════

def gitnexus_query(cypher: str) -> list[list[str]]:
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher", cypher, "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=HOOK_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return []
    output = r.stdout.strip() or r.stderr.strip()
    if not output:
        return []
    return gitnexus_markdown_rows(parse_gitnexus_markdown(output))


def get_docstring(dir_path: str) -> str:
    return read_dir_docstring(dir_path, limit=60)


def get_keywords(dir_path: str) -> str:
    prefix = normalize_dir_key(dir_path, trailing_slash=True).replace("'", "\\'")
    rows = gitnexus_query(
        f"MATCH (f:Function) WHERE f.filePath STARTS WITH '{prefix}' AND NOT f.name STARTS WITH '_' "
        f"OPTIONAL MATCH (c)-[:CodeRelation {{type:'CALLS'}}]->(f) WITH f, count(c) AS refs "
        f"WHERE refs > 0 RETURN f.name ORDER BY refs DESC LIMIT 4")
    names = []
    for row in rows:
        if not row or not isinstance(row[0], str):
            continue
        name = row[0].strip()
        if name and name.lower() not in GENERIC and len(name) > 3:
            names.append(name)
    kw = list(dict.fromkeys(names))
    return " / ".join(kw[:3]) if kw else ""


def fallback_generate(dirs: list[str], *, allow_keyword: bool = True) -> dict[str, str]:
    """Degraded path: fill ONLY genuinely-empty entries, never overwrite good ones.

    docstring (from __init__.py) is trusted; keyword joins are marked ⚠️
    low-confidence so the AI path retries them on the next run.
    """
    entries = {e["dir"]: e.get("desc") or "" for e in _parse_codemap(Path("CODE_MAP.md"))}
    refreshable = set(parse_codemap("--generate"))  # empty + low-confidence + low-quality entries
    result = {}
    for d in dirs:
        if d not in refreshable:
            continue
        docstring = get_docstring(d)
        if docstring and not is_low_quality_description(docstring):
            result[d] = docstring
            continue
        if (
            entries.get(d)
            and not is_low_confidence_description(entries[d])
            and not is_low_quality_description(entries[d])
        ):
            continue
        if not allow_keyword:
            continue
        keywords = get_keywords(d)
        if keywords:
            result[d] = f"{LOW_CONFIDENCE_MARKER} {keywords}"
    return result


# ══════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def parse_args(argv: list[str]) -> argparse.Namespace:
    args = list(argv)
    project_dir = "."
    mode = "--generate"
    if args and not args[0].startswith("--"):
        project_dir = args.pop(0)
    if args and args[0] in {"--generate", "--refresh", "--dry-run"}:
        mode = args.pop(0)
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=_env_int("HARNESS_CODEMAP_AI_BATCH_SIZE", DEFAULT_BATCH_SIZE))
    parser.add_argument("--ai-timeout", type=int, default=_env_int("HARNESS_CODEMAP_AI_TIMEOUT", DEFAULT_AI_TIMEOUT))
    parser.add_argument("--refresh-dir", action="append", default=[])
    parser.add_argument("--use-fingerprints", action="store_true")
    parsed = parser.parse_args(args)
    parsed.project_dir = project_dir
    parsed.mode = mode
    return parsed


def main():
    args = parse_args(sys.argv[1:])
    project_dir = args.project_dir
    mode = args.mode
    os.chdir(project_dir)

    dirs = parse_codemap(mode, refresh_dirs=args.refresh_dir)
    fingerprint_report = {}
    if args.use_fingerprints and args.refresh_dir:
        fingerprint_report = {
            "forced_refresh_dirs": [normalize_dir_key(d) for d in args.refresh_dir],
        }
    elif args.use_fingerprints and dirs:
        dirs, fingerprint_report = filter_dirs_by_fingerprints(dirs)
    if not dirs:
        quality = build_quality_report()
        print(json.dumps({
            "status": "all_described",
            "fingerprint_report": fingerprint_report,
            "quality_before": quality,
            "quality_after": quality,
        }, ensure_ascii=False))
        return

    if mode == "--dry-run":
        overrides, override_report = load_project_overrides(Path("."))
        all_dirs = [entry["dir"] for entry in _parse_codemap(Path("CODE_MAP.md"))]
        all_classification = build_classification_report(all_dirs, overrides=overrides)
        classification = {
            normalize_dir_key(d): all_classification[normalize_dir_key(d)]
            for d in dirs
            if normalize_dir_key(d) in all_classification
        }
        quality = build_quality_report(classification=all_classification, include_breakdown=True)
        print(json.dumps({
            "status": "dry_run",
            "dirs_needing": dirs,
            "classification": classification,
            "override_report": override_report,
            "fingerprint_report": fingerprint_report,
            "quality_before": quality,
            "quality_after": quality,
        }, indent=2, ensure_ascii=False))
        return

    quality_before = build_quality_report()
    all_changes: list[dict] = []
    sources: list[str] = []

    overrides, override_report = load_project_overrides(Path("."))
    override_descriptions = {d: overrides[d] for d in dirs if d in overrides}
    if override_descriptions:
        changes = write_descriptions(override_descriptions)
        all_changes.extend(changes)
        if changes:
            sources.append("project_override")
        dirs = [d for d in dirs if d not in override_descriptions]
        if not dirs:
            quality_after = build_quality_report()
            if args.use_fingerprints:
                save_dir_fingerprints([change["dir"] for change in all_changes])
            print(json.dumps({"status": "updated", "source": "+".join(sources),
                              "count": len(all_changes), "changes": all_changes,
                              "override_report": override_report,
                              "fingerprint_report": fingerprint_report,
                              "quality_before": quality_before,
                              "quality_after": quality_after}, indent=2, ensure_ascii=False))
            return

    classification = build_classification_report(dirs, overrides=overrides, include_evidence=True)
    evidence_by_dir = {
        key: row["evidence"]
        for key, row in classification.items()
        if isinstance(row.get("evidence"), DirectoryEvidence)
    }
    public_classification = {
        key: {field: value for field, value in row.items() if field != "evidence"}
        for key, row in classification.items()
    }
    deterministic_descriptions, deterministic_report = deterministic_generate(
        dirs,
        classification=public_classification,
        evidence_by_dir=evidence_by_dir,
    )
    if deterministic_descriptions:
        deterministic_descriptions, rejected = filter_generated_descriptions(deterministic_descriptions)
        if deterministic_descriptions:
            changes = write_descriptions(deterministic_descriptions)
            all_changes.extend(changes)
            if changes:
                sources.append("deterministic")
            written_dirs = {change["dir"] for change in changes}
            dirs = [d for d in dirs if normalize_dir_key(d) not in written_dirs]
            if not dirs:
                quality_after = build_quality_report()
                if args.use_fingerprints:
                    save_dir_fingerprints([change["dir"] for change in all_changes])
                print(json.dumps({"status": "updated", "source": "+".join(sources),
                                  "count": len(all_changes), "changes": all_changes,
                                  "classification": public_classification,
                                  "deterministic_report": deterministic_report,
                                  "override_report": override_report,
                                  "fingerprint_report": fingerprint_report,
                                  "quality_before": quality_before,
                                  "quality_after": quality_after,
                                  "rejected": rejected}, indent=2, ensure_ascii=False))
                return

    # Try AI + GitNexus first
    descriptions, ai_report = ai_generate_batched(
        dirs,
        batch_size=args.batch_size,
        timeout=args.ai_timeout,
        classification=public_classification,
    )
    if descriptions:
        descriptions, rejected = filter_generated_descriptions(descriptions)
        ai_report["rejected"] = rejected
        if descriptions:
            changes = write_descriptions(descriptions)
            all_changes.extend(changes)
            if changes:
                sources.append("ai+gitnexus")
            quality_after = build_quality_report()
            if args.use_fingerprints:
                save_dir_fingerprints([change["dir"] for change in all_changes])
            print(json.dumps({"status": "updated", "source": "+".join(sources),
                              "count": len(all_changes), "changes": all_changes,
                              "ai_report": ai_report,
                              "fingerprint_report": fingerprint_report,
                              "quality_before": quality_before,
                              "quality_after": quality_after,
                              "rejected": rejected}, indent=2, ensure_ascii=False))
            return

    # Fallback
    if ai_report.get("attempted"):
        descriptions = fallback_generate(dirs, allow_keyword=False)
    else:
        descriptions = fallback_generate(dirs)
    if descriptions:
        descriptions, rejected = filter_generated_descriptions(descriptions, allow_low_confidence=True)
        changes = write_descriptions(descriptions)
        all_changes.extend(changes)
        quality_after = build_quality_report()
        if args.use_fingerprints:
            save_dir_fingerprints([change["dir"] for change in all_changes])
        fallback_source = "trusted_fallback" if ai_report.get("attempted") else "fallback"
        if changes:
            sources.append(fallback_source)
        print(json.dumps({"status": "updated", "source": "+".join(sources),
                          "count": len(all_changes), "changes": all_changes,
                          "ai_report": ai_report,
                          "fingerprint_report": fingerprint_report,
                          "quality_before": quality_before,
                          "quality_after": quality_after,
                          "rejected": rejected}, indent=2, ensure_ascii=False))
    else:
        status = "ai_failed" if ai_report.get("attempted") else "no_changes"
        source = "ai+gitnexus" if ai_report.get("attempted") else "fallback"
        if all_changes:
            status = "partial"
            failed_source = "ai_failed" if ai_report.get("attempted") else "fallback_failed"
            source = f"{'+'.join(sources)}+{failed_source}"
            if args.use_fingerprints:
                save_dir_fingerprints([change["dir"] for change in all_changes])
        quality_after = build_quality_report()
        print(json.dumps({
            "status": status,
            "source": source,
            "count": len(all_changes),
            "changes": all_changes,
            "pending_dirs": dirs,
            "ai_report": ai_report,
            "fingerprint_report": fingerprint_report,
            "quality_before": quality_before,
            "quality_after": quality_after,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
