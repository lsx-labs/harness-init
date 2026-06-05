#!/usr/bin/env python3
"""Cross-platform doc sync: copy CLAUDE.md ↔ AGENTS.md where needed.

Deterministic, no AI. Handles root + sub-directories.

Usage:
  python3 sync_docs.py .                          # auto-detect platform
  python3 sync_docs.py . --platform claude         # explicit
  python3 sync_docs.py . --dirs src/core src/api   # specific dirs only
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from harness_shared import should_skip, platform_files, update_root_codemap_docs


def is_root_codemap_dir(root: Path) -> bool:
    """Treat CODE_MAP block rendering as root-only."""
    codemap = root / "CODE_MAP.md"
    if not codemap.exists():
        return False
    try:
        resolved = root.resolve()
    except OSError:
        return False
    for parent in resolved.parents:
        if (parent / "CODE_MAP.md").exists():
            return False
    return True


def sync_one(dir_path: str, own_file: str, other_file: str) -> dict | None:
    """Sync docs in one directory. Returns action taken or None."""
    root = Path(dir_path)
    own = root / own_file
    other = root / other_file

    if is_root_codemap_dir(root) and {own_file, other_file} == {"CLAUDE.md", "AGENTS.md"}:
        if not own.exists() and other.exists():
            try:
                own.write_text(other.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            except OSError:
                return None
            return {
                "dir": dir_path,
                "action": "copy",
                "from": other_file,
                "to": own_file,
                "files": update_root_codemap_docs(root),
            }

        result = update_root_codemap_docs(root)
        if any(value in {"updated", "write_failed"} for value in result.values()):
            return {"dir": dir_path, "action": "codemap_block", "files": result}
        return None

    if own.exists() or other.exists():
        return {
            "dir": dir_path,
            "action": "subdir_block_only",
            "reason": "whole_file_sync_disabled",
        }

    return None


def find_doc_dirs() -> list[str]:
    """Find all directories containing CLAUDE.md or AGENTS.md."""
    dirs = set()
    for root, subdirs, files in os.walk("."):
        subdirs[:] = [d for d in subdirs if not should_skip(d)]
        if "CLAUDE.md" in files or "AGENTS.md" in files:
            dirs.add(root)
    return sorted(dirs)


def _sync_action_succeeded(result: dict) -> bool:
    files = result.get("files", {})
    if isinstance(files, dict) and any(value == "write_failed" for value in files.values()):
        return False
    action = result.get("action")
    if action in {"copy", "sync"}:
        return True
    return (
        action == "codemap_block"
        and isinstance(files, dict)
        and any(value == "updated" for value in files.values())
    )


def main():
    project_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    platform = "claude"
    explicit_dirs = []

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--platform" and i + 1 < len(sys.argv):
            platform = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--dirs":
            explicit_dirs = sys.argv[i + 1:]
            break
        else:
            i += 1

    os.chdir(project_dir)
    own_file, other_file = platform_files(platform)

    if explicit_dirs:
        dirs = explicit_dirs
    else:
        dirs = find_doc_dirs()
        if "." not in dirs:
            dirs.insert(0, ".")

    results = []
    for d in dirs:
        r = sync_one(d, own_file, other_file)
        if r:
            results.append(r)

    synced = sum(1 for result in results if _sync_action_succeeded(result))
    print(json.dumps({"synced": synced, "actions": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
