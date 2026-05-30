"""Shared constants and utilities for harness-init scripts."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
from pathlib import Path

# ── Constants ──

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".gitnexus",
             ".claude", ".codex", "dist", "build", "vendor", "third_party", "sdk",
             ".worktrees", ".tox"}

STALE_THRESHOLD = 0.2
SYMBOL_THRESHOLD = 100
MANUAL_MARKER = "📌"
LOW_CONFIDENCE_MARKER = "⚠️"

SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
               ".rb", ".c", ".h", ".cpp", ".cs", ".swift", ".php"}
CODE_NAME_GENERIC = {
    "main", "init", "run", "start", "stop", "get", "set", "test", "setup",
    "parse", "build", "create", "delete", "update", "load", "save", "read",
    "write", "open", "close", "validate", "check", "add",
}

MAIN_BRANCHES = {"main", "master"}

# Sandbox flags for `codex exec`: read-only filesystem, no approval escalation.
# Mirrors the tool restriction applied on the Claude CLI path.
CODEX_EXEC_SANDBOX_ARGS = ["-s", "read-only", "-c", "approval_policy=never"]


# ── AI CLI discovery (shared by monitor / description generator) ──

def is_codex_runtime() -> bool:
    platform = os.environ.get("HARNESS_PLATFORM", "").strip().lower()
    if platform:
        return platform == "codex"
    return any(key.startswith("CODEX_") for key in os.environ)


def get_ai_cmd() -> str:
    """Find an available AI CLI for non-interactive invocation."""
    preferred = ["codex", "claude"] if is_codex_runtime() else ["claude", "codex"]
    for cmd in preferred:
        if shutil.which(cmd):
            return cmd
    codex_app = "/Applications/Codex.app/Contents/Resources/codex"
    if os.path.isfile(codex_app):
        return codex_app
    return ""


def should_skip(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info") or (name.startswith(".") and name != ".")


# ── GitNexus output parsing (shared by monitor / plan / description generator) ──

def parse_gitnexus_markdown(output: str) -> str:
    """Extract the markdown table from GitNexus cypher JSON output (dict or list form)."""
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            return data.get("markdown", "")
        if isinstance(data, list) and data:
            if isinstance(data[0], dict):
                return data[0].get("markdown", "")
            return str(data[0])
    except (json.JSONDecodeError, IndexError, TypeError):
        pass
    return ""


def gitnexus_markdown_rows(markdown: str) -> list[list[str]]:
    """Split a GitNexus markdown table into data-cell rows (header + separator dropped)."""
    lines = [line.strip() for line in markdown.split("\n") if line.strip()]
    if len(lines) < 3:
        return []
    return [[c.strip() for c in line.split("|") if c.strip()] for line in lines[2:]]


def read_dir_docstring(dir_path: str, *, limit: int = 80) -> str:
    """First line of a directory's package docstring (__init__.py), separator-stripped.

    Reads the module docstring, takes its first line, drops a leading
    "Name — / – / - " prefix, and truncates to `limit` chars.
    """
    for fname in ("__init__.py", "index.ts", "index.js", "mod.rs"):
        fpath = Path(dir_path) / fname
        if fname.endswith(".py") and fpath.exists():
            try:
                ds = ast.get_docstring(ast.parse(fpath.read_text(encoding="utf-8", errors="ignore")))
            except (SyntaxError, OSError):
                continue
            if ds:
                line = ds.strip().split("\n")[0]
                for sep in ("—", "–", "-"):
                    if sep in line:
                        line = line.split(sep, 1)[1].strip()
                        break
                return line[:limit]
    return ""


def _folder_leaf(folder: str) -> str:
    return folder.split("/")[-1].lower().lstrip("_")


def map_areas_to_dirs(areas, folders: list[str]) -> dict[str, str]:
    """Map each GitNexus community label to the folder whose leaf name uniquely matches.

    Matching is case-insensitive and ignores a leading underscore on either side. When
    two or more folders share the same leaf (e.g. src/utils and tests/utils), the match
    is ambiguous and the area is omitted rather than mis-attributed to an arbitrary one.
    """
    by_leaf: dict[str, list[str]] = {}
    for folder in folders:
        by_leaf.setdefault(_folder_leaf(folder), []).append(folder)
    mapping: dict[str, str] = {}
    for area in areas:
        candidates = by_leaf.get(area.lower().lstrip("_"), [])
        if len(candidates) == 1:
            mapping[area] = candidates[0]
    return mapping


# ── Platform ──

def platform_files(platform: str) -> tuple[str, str]:
    """Returns (own_file, other_file) for the given platform."""
    if platform == "claude":
        return "CLAUDE.md", "AGENTS.md"
    return "AGENTS.md", "CLAUDE.md"


# ── CODE_MAP.md parsing ──

def parse_codemap_entry(rest: str) -> tuple[str, int | None]:
    """Parse description and symbol count from a CODE_MAP line's trailing text."""
    desc = ""
    count = None
    cm = re.search(r'\((\d+)\s*symbols?\)', rest)
    if cm:
        count = int(cm.group(1))
        rest = rest[:cm.start()] + rest[cm.end():]
    dm = re.search(r'—\s*(.+)', rest)
    if dm:
        desc = dm.group(1).strip()
    return desc, count


def parse_codemap(codemap_path: Path) -> list[dict]:
    """Parse CODE_MAP.md into structured entries.

    Returns list of {dir, desc, symbols} dicts.
    All other scripts should use this instead of rolling their own parser.
    """
    if not codemap_path.exists():
        return []
    entries = []
    current = ""
    for line in codemap_path.read_text(encoding="utf-8").split("\n"):
        m = re.match(r'^###\s+(\S+)/?(.*)$', line)
        if m:
            current = m.group(1).rstrip("/")
            desc, count = parse_codemap_entry(m.group(2))
            entries.append({"dir": current, "desc": desc, "symbols": count})
            continue
        m = re.match(r'^-\s+\*\*(\S+)/?\*\*(.*)$', line)
        if m:
            sub = f"{current}/{m.group(1).rstrip('/')}"
            desc, count = parse_codemap_entry(m.group(2))
            entries.append({"dir": sub, "desc": desc, "symbols": count})
    return entries


# ── CODE_MAP.md description quality ──

_LOW_QUALITY_FRAGMENTS = (
    "Tests for ",
)


def _is_code_like_token(token: str) -> bool:
    return (
        "_" in token
        or token.endswith("_")
        or bool(re.search(r'[a-z][A-Z]', token))
    )


def is_manual_description(desc: str) -> bool:
    return desc.strip().startswith(MANUAL_MARKER)


def is_low_confidence_description(desc: str) -> bool:
    return desc.strip().startswith(LOW_CONFIDENCE_MARKER)


def is_low_quality_description(desc: str) -> bool:
    """Return True for generated descriptions that are navigation noise.

    This deliberately targets known fallback failure modes rather than trying
    to score prose quality generally.
    """
    desc = (desc or "").strip()
    if not desc or is_manual_description(desc):
        return False
    if is_low_confidence_description(desc):
        return True
    if any(fragment in desc for fragment in _LOW_QUALITY_FRAGMENTS):
        return True
    if " / " in desc:
        return True
    if re.search(r'\b[A-Za-z]+_$', desc):
        return True
    if all(ord(ch) < 128 for ch in desc):
        tokens = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', desc)
        if len(tokens) == 1 and tokens[0] == desc and _is_code_like_token(tokens[0]):
            return True
        if len(tokens) >= 2 and all(
            _is_code_like_token(token) or token.lower() in CODE_NAME_GENERIC
            for token in tokens
        ):
            return True
    return False


def is_acceptable_description(desc: str) -> bool:
    desc = (desc or "").strip()
    return bool(desc) and not is_low_quality_description(desc)


def needs_description_refresh(desc: str) -> bool:
    desc = (desc or "").strip()
    if not desc:
        return True
    if is_manual_description(desc):
        return False
    return is_low_quality_description(desc)
