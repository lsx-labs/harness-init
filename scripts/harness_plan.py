#!/usr/bin/env python3
"""Generate a deterministic action plan for /harness-init.

Replaces AI decision-making for all deterministic steps:
  - Root doc: skip / copy / generate
  - CODE_MAP: skip / refresh (which dirs need descriptions)
  - GitNexus: skip / analyze
  - Sub-dirs: skip / copy / generate, grouped by depth layer
  - LSP: recommendations from diagnostic

Usage:
  python3 harness_plan.py . --platform claude
  python3 harness_plan.py /path/to/project --platform codex
"""

import json
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".gitnexus",
             ".claude", ".codex", "dist", "build", "vendor", "third_party", "sdk",
             ".worktrees", ".tox"}
SYMBOL_THRESHOLD = 100
STALE_THRESHOLD = 0.2
MANUAL_MARKER = "📌"


def detect_platform() -> str:
    if os.environ.get("CODEX_ENV") or "codex" in os.environ.get("TERM_PROGRAM", "").lower():
        return "codex"
    return "claude"


def platform_files(platform: str) -> tuple[str, str]:
    if platform == "claude":
        return "CLAUDE.md", "AGENTS.md"
    return "AGENTS.md", "CLAUDE.md"


def plan_root_doc(own_file: str, other_file: str) -> dict:
    own = Path(own_file)
    other = Path(other_file)
    if own.exists():
        return {"action": "skip"}
    if other.exists():
        return {"action": "copy", "from": other_file}
    return {"action": "generate"}


def parse_codemap_entries() -> list[dict]:
    codemap = Path("CODE_MAP.md")
    if not codemap.exists():
        return []
    entries = []
    current = ""
    for line in codemap.read_text(encoding="utf-8").split("\n"):
        m = re.match(r'^###\s+(\S+)/?(.*)$', line)
        if m:
            current = m.group(1).rstrip("/")
            rest = m.group(2)
            entries.append(_parse_entry(current, rest))
            continue
        m = re.match(r'^-\s+\*\*(\S+)/?\*\*(.*)$', line)
        if m:
            sub = f"{current}/{m.group(1).rstrip('/')}"
            entries.append(_parse_entry(sub, m.group(2)))
    return entries


def _parse_entry(dir_path: str, rest: str) -> dict:
    desc = ""
    count = None
    cm = re.search(r'\((\d+)\s*symbols?\)', rest)
    if cm:
        count = int(cm.group(1))
        rest = rest[:cm.start()] + rest[cm.end():]
    dm = re.search(r'—\s*(.+)', rest)
    if dm:
        desc = dm.group(1).strip()
    return {"dir": dir_path, "desc": desc, "symbols": count}


def plan_codemap(entries: list[dict], old_counts: dict) -> dict:
    if not entries:
        return {"action": "skip", "dirs_needing": []}
    needing = []
    for e in entries:
        if e["desc"].startswith(MANUAL_MARKER):
            continue
        if not e["desc"]:
            needing.append(e["dir"])
            continue
        old = old_counts.get(e["dir"], 0)
        if e["symbols"] and old > 0 and abs(e["symbols"] - old) / old >= STALE_THRESHOLD:
            needing.append(e["dir"])
    if not needing:
        return {"action": "skip", "dirs_needing": []}
    return {"action": "refresh", "dirs_needing": needing}


def plan_gitnexus(diagnostic: dict) -> dict:
    existing = diagnostic.get("existing", {})
    gn = existing.get("gitnexus", {})
    if not gn.get("indexed"):
        grep_noise = diagnostic.get("grep_noise", {}).get("grep_noise_files", 0)
        if grep_noise > 20:
            return {"action": "install_and_index"}
        elif grep_noise > 10:
            return {"action": "suggest_install"}
        return {"action": "skip"}
    if not gn.get("up_to_date"):
        return {"action": "analyze"}
    return {"action": "skip"}


def find_complex_dirs() -> list[dict]:
    codemap = Path("CODE_MAP.md")
    if not codemap.exists():
        return []
    dirs = []
    current_top = ""
    for line in codemap.read_text(encoding="utf-8").split("\n"):
        m = re.match(r'^###\s+(\S+)/?.*\((\d+)\s*symbols?\)', line)
        if m:
            current_top = m.group(1).rstrip("/")
            if int(m.group(2)) >= SYMBOL_THRESHOLD:
                dirs.append(current_top)
            continue
        m = re.match(r'^-\s+\*\*(\S+)/?\*\*.*\((\d+)\s*symbols?\)', line)
        if m and current_top:
            sub = f"{current_top}/{m.group(1).rstrip('/')}"
            if int(m.group(2)) >= SYMBOL_THRESHOLD:
                dirs.append(sub)
    return dirs


def plan_subdirs(complex_dirs: list[str], own_file: str, other_file: str) -> dict:
    copy_list = []
    generate_list = []
    skip_list = []

    for d in complex_dirs:
        own = Path(d) / own_file
        other = Path(d) / other_file
        if own.exists():
            skip_list.append(d)
        elif other.exists():
            copy_list.append({"dir": d, "from": other_file})
        else:
            depth = len(d.split("/"))
            generate_list.append({"dir": d, "depth": depth})

    layers = {}
    for item in generate_list:
        layers.setdefault(item["depth"], []).append(item["dir"])
    sorted_layers = [[depth, dirs] for depth, dirs in sorted(layers.items(), reverse=True)]

    return {
        "copy": copy_list,
        "generate": generate_list,
        "skip": skip_list,
        "layers": sorted_layers,
    }


def plan_lsp(diagnostic: dict) -> list[dict]:
    result = []
    for a in diagnostic.get("lsp_assessment", []):
        if a.get("installed"):
            result.append({"language": a["language"], "action": "skip", "reason": "已安装"})
        elif a.get("recommend"):
            result.append({"language": a["language"], "action": "recommend",
                           "plugin": a.get("plugin", ""), "reason": a.get("reason", "")})
    return result


def main():
    project_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    platform = "claude"
    for arg in sys.argv[2:]:
        if arg.startswith("--platform"):
            if "=" in arg:
                platform = arg.split("=", 1)[1]
            else:
                idx = sys.argv.index(arg)
                if idx + 1 < len(sys.argv):
                    platform = sys.argv[idx + 1]

    os.chdir(project_dir)
    own_file, other_file = platform_files(platform)

    diag_script = Path.home() / ".local" / "bin" / "harness-init.py"
    diagnostic = {}
    if diag_script.exists():
        import subprocess
        try:
            r = subprocess.run([sys.executable, str(diag_script), "."],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                diagnostic = json.loads(r.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass

    entries = parse_codemap_entries()
    old_counts = {e["dir"]: e["symbols"] for e in entries if e["symbols"] is not None}
    complex_dirs = find_complex_dirs()

    plan = {
        "platform": platform,
        "doc_file": own_file,
        "other_doc_file": other_file,
        "root_doc": plan_root_doc(own_file, other_file),
        "codemap": plan_codemap(entries, old_counts),
        "gitnexus": plan_gitnexus(diagnostic),
        "subdirs": plan_subdirs(complex_dirs, own_file, other_file),
        "lsp": plan_lsp(diagnostic),
    }

    print(json.dumps(plan, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
