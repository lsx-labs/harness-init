#!/usr/bin/env python3
"""Generate CODE_MAP.md descriptions: AI + GitNexus (primary) / keywords (fallback).

Modes:
  --generate  fill empty entries only (default)
  --refresh   regenerate all (except 📌 manual overrides)
  --dry-run   show what would change
"""

from __future__ import annotations

import ast
import json
import os
import re
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
GENERIC = {"main", "init", "run", "start", "stop", "get", "set", "test", "setup", "parse",
           "build", "create", "delete", "update", "load", "save", "read", "write", "open",
           "close", "validate", "check", "add", "all", "data", "config", "path", "name", "type"}


# ══════════════════════════════════════════════════════════
# CODE_MAP.md parsing
# ══════════════════════════════════════════════════════════

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


def write_descriptions(descriptions: dict[str, str]) -> list[dict]:
    """Write descriptions to CODE_MAP.md, return list of changes."""
    codemap = Path("CODE_MAP.md")
    content = codemap.read_text(encoding="utf-8")
    changes = []
    for dir_path, desc in descriptions.items():
        if not desc or not isinstance(desc, str):
            continue
        desc = desc.strip()[:60]
        # Top-level
        p = re.compile(rf'^(###\s+{re.escape(dir_path)}/\s+\(\d+\s+symbols\))(.*)$', re.MULTILINE)
        m = p.search(content)
        if m:
            content = content[:m.start()] + f"{m.group(1)} — {desc}" + content[m.end():]
            changes.append({"dir": dir_path, "desc": desc})
            continue
        # Sub-level: search within the parent section to avoid ambiguous leaf names
        parts = dir_path.split("/")
        if len(parts) >= 2:
            parent, sub_name = parts[0], parts[-1]
            parent_pat = re.compile(rf'^###\s+{re.escape(parent)}/', re.MULTILINE)
            parent_m = parent_pat.search(content)
            if parent_m:
                section_start = parent_m.start()
                next_section = re.search(r'^### ', content[parent_m.end():], re.MULTILINE)
                section_end = parent_m.end() + next_section.start() if next_section else len(content)
                section = content[section_start:section_end]
                p = re.compile(rf'^(-\s+\*\*{re.escape(sub_name)}/?\*\*)\s*(.*?)(\(\d+\s+symbols\))(.*)$', re.MULTILINE)
                m = p.search(section)
                if m:
                    abs_start = section_start + m.start()
                    abs_end = section_start + m.end()
                    content = content[:abs_start] + f"{m.group(1)} — {desc} {m.group(3)}" + content[abs_end:]
                    changes.append({"dir": dir_path, "desc": desc})
    if changes:
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


def ai_generate(dirs: list[str]) -> dict[str, str] | None:
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
            r = subprocess.run(
                [cmd, "-p", prompt, "--allowedTools", "Read,mcp__gitnexus*",
                 "--output-format", "json"],
                capture_output=True, text=True, timeout=40)
            try:
                raw = json.loads(r.stdout)["result"]
            except (json.JSONDecodeError, KeyError):
                print(f"ai_generate: claude failed, stderr={r.stderr[:200]}", file=sys.stderr)
                return None
        else:
            r = subprocess.run([cmd, "exec", prompt],
                               capture_output=True, text=True, timeout=40)
            raw = r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if not raw:
        return None

    # Extract JSON from response
    json_match = re.search(r'\{[^{}]*("[\w/]+":\s*"[^"]*"[,\s]*)+\}', raw, re.DOTALL)
    if not json_match:
        print(f"ai_generate: no JSON found in response ({len(raw)} chars)", file=sys.stderr)
        return None
    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"ai_generate: JSON parse failed: {e}", file=sys.stderr)
        return None


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


def fallback_generate(dirs: list[str]) -> dict[str, str]:
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
        keywords = get_keywords(d)
        if keywords:
            result[d] = f"{LOW_CONFIDENCE_MARKER} {keywords}"
    return result


# ══════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════

def main():
    project_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    mode = sys.argv[2] if len(sys.argv) > 2 else "--generate"
    os.chdir(project_dir)

    dirs = parse_codemap(mode)
    if not dirs:
        print(json.dumps({"status": "all_described"}, ensure_ascii=False))
        return

    if mode == "--dry-run":
        print(json.dumps({"status": "dry_run", "dirs_needing": dirs}, indent=2, ensure_ascii=False))
        return

    # Try AI + GitNexus first
    descriptions = ai_generate(dirs)
    if descriptions:
        descriptions, rejected = filter_generated_descriptions(descriptions)
        if descriptions:
            changes = write_descriptions(descriptions)
            print(json.dumps({"status": "updated", "source": "ai+gitnexus",
                              "count": len(changes), "changes": changes,
                              "rejected": rejected}, indent=2, ensure_ascii=False))
            return

    # Fallback
    descriptions = fallback_generate(dirs)
    if descriptions:
        descriptions, rejected = filter_generated_descriptions(descriptions, allow_low_confidence=True)
        changes = write_descriptions(descriptions)
        print(json.dumps({"status": "updated", "source": "fallback",
                          "count": len(changes), "changes": changes,
                          "rejected": rejected}, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({"status": "no_changes"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
