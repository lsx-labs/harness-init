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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
import re
import signal
import shutil
import subprocess
import sys
from pathlib import Path

from harness_shared import (
    LOW_CONFIDENCE_MARKER,
    MANUAL_MARKER,
    is_acceptable_description,
    is_low_confidence_description,
    is_low_quality_description,
    needs_description_refresh,
    parse_codemap as _parse_codemap,
)

HOOK_TIMEOUT = 10
DEFAULT_AI_TIMEOUT = 180
DEFAULT_BATCH_SIZE = 2
DEFAULT_MAX_WORKERS = 1
GENERIC = {"main", "init", "run", "start", "stop", "get", "set", "test", "setup", "parse",
           "build", "create", "delete", "update", "load", "save", "read", "write", "open",
           "close", "validate", "check", "add", "all", "data", "config", "path", "name", "type"}
ARTIFACT_PREFIXES = (
    "data/backtest_artifacts/",
    "data/bundle/",
    "data/cache/",
    "data/factor/",
    "data/release_gate/",
    "data/results/",
)
PROJECT_OVERRIDE_PATH = Path(".harness/codemap_descriptions.json")


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
    if key.startswith(ARTIFACT_PREFIXES):
        return True
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


def _gitnexus_count(cypher: str) -> int:
    try:
        rows = gitnexus_query(cypher)
    except Exception:
        return 0
    if not rows or not rows[0]:
        return 0
    try:
        return int(str(rows[0][0]).strip())
    except (TypeError, ValueError):
        return 0


def _gitnexus_counts(dir_path: str) -> dict[str, int]:
    prefix = normalize_dir_key(dir_path, trailing_slash=True).replace("'", "\\'")
    if not prefix:
        return {"files": 0, "functions": 0, "methods": 0, "classes": 0, "processes": 0}
    return {
        "files": _gitnexus_count(f"MATCH (f:File) WHERE f.filePath STARTS WITH '{prefix}' RETURN count(f)"),
        "functions": _gitnexus_count(
            f"MATCH (n:Function) WHERE n.filePath STARTS WITH '{prefix}' RETURN count(n)"
        ),
        "methods": _gitnexus_count(
            f"MATCH (n:Method) WHERE n.filePath STARTS WITH '{prefix}' RETURN count(n)"
        ),
        "classes": _gitnexus_count(
            f"MATCH (n:Class) WHERE n.filePath STARTS WITH '{prefix}' RETURN count(n)"
        ),
        "processes": _gitnexus_count(
            "MATCH (s)-[:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process) "
            f"WHERE s.filePath STARTS WITH '{prefix}' RETURN count(DISTINCT p)"
        ),
    }


def collect_directory_evidence(dir_path: str) -> DirectoryEvidence:
    """Collect local and GitNexus evidence used by CODE_MAP description providers."""
    key = normalize_dir_key(dir_path, trailing_slash=True)
    files = _list_dir_files(key)
    suffixes = [path.suffix.lower() for path in files]
    counts = _gitnexus_counts(key)
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
    if len(desc) > 80:
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

def parse_codemap(mode: str) -> list[str]:
    """Return list of directories needing descriptions based on mode."""
    entries = _parse_codemap(Path("CODE_MAP.md"))
    dirs = []
    for e in entries:
        desc = e.get("desc") or ""
        if desc.startswith(MANUAL_MARKER):
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


def build_quality_report(codemap_path: Path = Path("CODE_MAP.md")) -> dict[str, int]:
    """Summarize CODE_MAP description quality for audit output."""
    entries = _parse_codemap(codemap_path)
    described = [e for e in entries if (e.get("desc") or "").strip()]
    return {
        "total": len(entries),
        "described": len(described),
        "acceptable": sum(1 for e in entries if is_acceptable_description(e.get("desc") or "")),
        "low_quality": sum(1 for e in entries if is_low_quality_description(e.get("desc") or "")),
        "low_confidence": sum(1 for e in entries if is_low_confidence_description(e.get("desc") or "")),
        "empty": sum(1 for e in entries if not (e.get("desc") or "").strip()),
        "needs_refresh": sum(1 for e in entries if needs_description_refresh(e.get("desc") or "")),
    }


def write_descriptions(descriptions: dict[str, str]) -> list[dict]:
    """Write descriptions to CODE_MAP.md, return list of changes."""
    codemap = Path("CODE_MAP.md")
    lines = codemap.read_text(encoding="utf-8").splitlines(keepends=True)
    changes = []
    normalized = {}
    for dir_path, raw_desc in descriptions.items():
        if not raw_desc or not isinstance(raw_desc, str):
            continue
        normalized[dir_path.strip("/")] = raw_desc.strip()[:60]
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

def _is_codex_runtime() -> bool:
    platform = os.environ.get("HARNESS_PLATFORM", "").strip().lower()
    if platform:
        return platform == "codex"
    return any(key.startswith("CODEX_") for key in os.environ)


def get_ai_cmd() -> str:
    preferred = ["codex", "claude"] if _is_codex_runtime() else ["claude", "codex"]
    for cmd in preferred:
        if shutil.which(cmd):
            return cmd
    codex_app = "/Applications/Codex.app/Contents/Resources/codex"
    if os.path.isfile(codex_app):
        return codex_app
    return ""


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


def ai_generate(dirs: list[str], *, timeout: int = DEFAULT_AI_TIMEOUT) -> dict[str, str] | None:
    """Invoke AI CLI to generate descriptions via GitNexus. Returns {dir: desc} or None."""
    cmd = get_ai_cmd()
    if not cmd or not Path(".gitnexus").is_dir():
        return None

    project = Path(".").resolve().name
    prompt = (
        f"你在项目 {project} 中。为以下 {len(dirs)} 个目录生成 CODE_MAP.md 导航描述。\n\n"
        f"规则：\n"
        f"1. 对每个目录，调用 gitnexus_context 查询其核心函数（被引用最多的），了解调用关系\n"
        f"2. 只基于 GitNexus 返回的数据写描述，不自行推测\n"
        f"3. 每个描述中文 ≤ 30 字，格式：核心职责 + 2-3 个关键功能词\n"
        f"4. 只输出纯 JSON，无 markdown 包裹，格式：{{\"目录名\": \"描述\"}}\n\n"
        f"目录：{' '.join(dirs)}"
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
            r = _run_ai_command([cmd, "exec", prompt], timeout)
            raw = r.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"ai_generate: timed out after {timeout}s for dirs={dirs}", file=sys.stderr)
        return None
    except (FileNotFoundError, OSError):
        return None

    return _parse_ai_json(raw)


def ai_generate_batched(
    dirs: list[str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: int = DEFAULT_AI_TIMEOUT,
) -> tuple[dict[str, str], dict]:
    """Generate descriptions in bounded AI batches and return audit metadata."""
    cmd = get_ai_cmd()
    if not cmd:
        return {}, {"attempted": False, "reason": "no_ai_cmd"}
    if not Path(".gitnexus").is_dir():
        return {}, {"attempted": False, "reason": "no_gitnexus_index"}

    batches = batch_dirs(dirs, batch_size)
    worker_count = max(1, min(int(max_workers or 1), len(batches) or 1))
    report = {
        "attempted": True,
        "batch_size": max(1, int(batch_size or 1)),
        "max_workers": worker_count,
        "timeout_seconds": timeout,
        "success_dirs": [],
        "failed_dirs": [],
        "batches": [],
    }
    if not batches:
        return {}, report

    def run_batch(index: int, batch: list[str]) -> dict:
        try:
            result = ai_generate(batch, timeout=timeout)
        except Exception as exc:  # defensive: keep one bad worker from hiding the audit trail
            return {
                "index": index,
                "dirs": batch,
                "status": "error",
                "error": str(exc),
                "descriptions": {},
            }
        return {
            "index": index,
            "dirs": batch,
            "status": "success" if result else "failed",
            "descriptions": result or {},
        }

    results: list[dict] = []
    if worker_count == 1:
        results = [run_batch(index, batch) for index, batch in enumerate(batches)]
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(run_batch, index, batch): index
                for index, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                results.append(future.result())

    descriptions: dict[str, str] = {}
    for item in sorted(results, key=lambda row: row["index"]):
        requested = item["dirs"]
        returned = {
            key: value
            for key, value in item.get("descriptions", {}).items()
            if key in requested
        }
        descriptions.update(returned)
        success_dirs = [d for d in requested if d in returned]
        failed_dirs = [d for d in requested if d not in returned]
        report["success_dirs"].extend(success_dirs)
        report["failed_dirs"].extend(failed_dirs)
        status = item["status"]
        if returned and failed_dirs:
            status = "partial"
        report["batches"].append({
            "index": item["index"],
            "dirs": requested,
            "status": status,
            "returned_dirs": success_dirs,
            "failed_dirs": failed_dirs,
            **({"error": item["error"]} if item.get("error") else {}),
        })
    return descriptions, report


# ══════════════════════════════════════════════════════════
# Fallback: docstring + GitNexus keywords
# ══════════════════════════════════════════════════════════

def gitnexus_query(cypher: str) -> list[list[str]]:
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher", cypher, "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        if not output:
            return []
        data = json.loads(output)
        if isinstance(data, dict):
            md = data.get("markdown", "")
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            md = data[0].get("markdown", "")
        else:
            md = ""
        lines = [l.strip() for l in md.split("\n") if l.strip()]
        if len(lines) < 3:
            return []
        return [[c.strip() for c in l.split("|") if c.strip()] for l in lines[2:]]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def get_docstring(dir_path: str) -> str:
    for fname in ("__init__.py", "index.ts", "index.js", "mod.rs"):
        fpath = Path(dir_path) / fname
        if fpath.exists():
            try:
                ds = ast.get_docstring(ast.parse(fpath.read_text(encoding="utf-8")))
                if ds:
                    line = ds.strip().split("\n")[0]
                    for sep in ("—", "–", "-"):
                        if sep in line:
                            line = line.split(sep, 1)[1].strip()
                            break
                    return line[:60]
            except (SyntaxError, OSError):
                pass
    return ""


def get_keywords(dir_path: str) -> str:
    rows = gitnexus_query(
        f"MATCH (f:Function) WHERE f.filePath STARTS WITH '{dir_path}/' AND NOT f.name STARTS WITH '_' "
        f"OPTIONAL MATCH (c)-[:CodeRelation {{type:'CALLS'}}]->(f) WITH f, count(c) AS refs "
        f"WHERE refs > 0 RETURN f.name ORDER BY refs DESC LIMIT 4")
    kw = list(dict.fromkeys(r[0] for r in rows if r[0].lower() not in GENERIC and len(r[0]) > 3))
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
    parser.add_argument("--max-workers", type=int, default=_env_int("HARNESS_CODEMAP_AI_MAX_WORKERS", DEFAULT_MAX_WORKERS))
    parser.add_argument("--ai-timeout", type=int, default=_env_int("HARNESS_CODEMAP_AI_TIMEOUT", DEFAULT_AI_TIMEOUT))
    parsed = parser.parse_args(args)
    parsed.project_dir = project_dir
    parsed.mode = mode
    return parsed


def main():
    args = parse_args(sys.argv[1:])
    project_dir = args.project_dir
    mode = args.mode
    os.chdir(project_dir)

    dirs = parse_codemap(mode)
    if not dirs:
        quality = build_quality_report()
        print(json.dumps({
            "status": "all_described",
            "quality_before": quality,
            "quality_after": quality,
        }, ensure_ascii=False))
        return

    if mode == "--dry-run":
        quality = build_quality_report()
        print(json.dumps({
            "status": "dry_run",
            "dirs_needing": dirs,
            "quality_before": quality,
            "quality_after": quality,
        }, indent=2, ensure_ascii=False))
        return

    quality_before = build_quality_report()

    overrides, override_report = load_project_overrides(Path("."))
    override_descriptions = {d: overrides[d] for d in dirs if d in overrides}
    if override_descriptions:
        changes = write_descriptions(override_descriptions)
        dirs = [d for d in dirs if d not in override_descriptions]
        if not dirs:
            quality_after = build_quality_report()
            print(json.dumps({"status": "updated", "source": "project_override",
                              "count": len(changes), "changes": changes,
                              "override_report": override_report,
                              "quality_before": quality_before,
                              "quality_after": quality_after}, indent=2, ensure_ascii=False))
            return

    # Try AI + GitNexus first
    descriptions, ai_report = ai_generate_batched(
        dirs,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
        timeout=args.ai_timeout,
    )
    if descriptions:
        descriptions, rejected = filter_generated_descriptions(descriptions)
        ai_report["rejected"] = rejected
        if descriptions:
            changes = write_descriptions(descriptions)
            quality_after = build_quality_report()
            print(json.dumps({"status": "updated", "source": "ai+gitnexus",
                              "count": len(changes), "changes": changes,
                              "ai_report": ai_report,
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
        quality_after = build_quality_report()
        source = "trusted_fallback" if ai_report.get("attempted") else "fallback"
        print(json.dumps({"status": "updated", "source": source,
                          "count": len(changes), "changes": changes,
                          "ai_report": ai_report,
                          "quality_before": quality_before,
                          "quality_after": quality_after,
                          "rejected": rejected}, indent=2, ensure_ascii=False))
    else:
        status = "ai_failed" if ai_report.get("attempted") else "no_changes"
        source = "ai+gitnexus" if ai_report.get("attempted") else "fallback"
        quality_after = build_quality_report()
        print(json.dumps({
            "status": status,
            "source": source,
            "count": 0,
            "ai_report": ai_report,
            "quality_before": quality_before,
            "quality_after": quality_after,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
