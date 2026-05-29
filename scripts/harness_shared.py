"""Shared constants and utilities for harness-init scripts."""

from __future__ import annotations

import re
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

MAIN_BRANCHES = {"main", "master"}


def should_skip(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info") or (name.startswith(".") and name != ".")


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
    if re.search(r'\b(get|set|load|build|run|parse|validate|create|update|delete)[A-Z][A-Za-z0-9]*', desc):
        return True
    if re.fullmatch(r'[A-Za-z0-9_./\s-]+', desc) and re.search(r'\b[A-Za-z]+_[A-Za-z0-9_]+\b', desc):
        return True
    if re.search(r'\b(build|resolve|run|load|parse|validate)_[A-Za-z0-9_]+', desc):
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
