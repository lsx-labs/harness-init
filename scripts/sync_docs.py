#!/usr/bin/env python3
"""Cross-platform doc sync: copy CLAUDE.md ↔ AGENTS.md where needed.

Deterministic, no AI. Handles root + sub-directories.

Usage:
  python3 sync_docs.py .                          # auto-detect platform
  python3 sync_docs.py . --platform claude         # explicit
  python3 sync_docs.py . --dirs src/core src/api   # specific dirs only
"""

import json
import os
import sys
from pathlib import Path

from harness_shared import should_skip, platform_files


def sync_one(dir_path: str, own_file: str, other_file: str) -> dict | None:
    """Sync docs in one directory. Returns action taken or None."""
    own = Path(dir_path) / own_file
    other = Path(dir_path) / other_file

    if own.exists() and other.exists():
        try:
            own_text = own.read_text(encoding="utf-8")
            other_text = other.read_text(encoding="utf-8")
            if own_text == other_text:
                return None
            if own.stat().st_mtime > other.stat().st_mtime:
                other.write_text(own_text, encoding="utf-8")
                return {"dir": dir_path, "action": "sync", "from": own_file, "to": other_file}
            else:
                own.write_text(other_text, encoding="utf-8")
                return {"dir": dir_path, "action": "sync", "from": other_file, "to": own_file}
        except OSError:
            return None

    if not own.exists() and other.exists():
        try:
            own.write_text(other.read_text(encoding="utf-8"), encoding="utf-8")
            return {"dir": dir_path, "action": "copy", "from": other_file, "to": own_file}
        except OSError:
            return None

    return None


def find_doc_dirs() -> list[str]:
    """Find all directories containing CLAUDE.md or AGENTS.md."""
    dirs = set()
    for root, subdirs, files in os.walk("."):
        subdirs[:] = [d for d in subdirs if not should_skip(d)]
        if "CLAUDE.md" in files or "AGENTS.md" in files:
            dirs.add(root)
    return sorted(dirs)


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

    print(json.dumps({"synced": len(results), "actions": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
