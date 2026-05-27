"""Shared constants and utilities for harness-init scripts."""

import re
from pathlib import Path

# ── Constants ──

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".gitnexus",
             ".claude", ".codex", "dist", "build", "vendor", "third_party", "sdk",
             ".worktrees", ".tox"}

STALE_THRESHOLD = 0.2
SYMBOL_THRESHOLD = 100
MANUAL_MARKER = "📌"

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
