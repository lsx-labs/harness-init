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
import subprocess
import sys
from pathlib import Path

from shared import (MANUAL_MARKER, STALE_THRESHOLD, SYMBOL_THRESHOLD,
                    parse_codemap, platform_files)


def plan_root_doc(own_file: str, other_file: str) -> dict:
    own = Path(own_file)
    other = Path(other_file)
    if own.exists():
        return {"action": "skip"}
    if other.exists():
        return {"action": "copy", "from": other_file}
    return {"action": "generate"}


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


def find_complex_dirs(entries: list[dict]) -> list[str]:
    return [e["dir"] for e in entries
            if e["symbols"] is not None and e["symbols"] >= SYMBOL_THRESHOLD]


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
    for i, arg in enumerate(sys.argv[2:], start=2):
        if arg == "--platform" and i + 1 < len(sys.argv):
            platform = sys.argv[i + 1]
        elif arg.startswith("--platform="):
            platform = arg.split("=", 1)[1]

    os.chdir(project_dir)
    own_file, other_file = platform_files(platform)

    diag_script = Path.home() / ".local" / "bin" / "harness-init.py"
    diagnostic = {}
    if diag_script.exists():
        try:
            r = subprocess.run([sys.executable, str(diag_script), "."],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                diagnostic = json.loads(r.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass

    entries = parse_codemap(Path("CODE_MAP.md"))
    old_counts = {e["dir"]: e["symbols"] for e in entries if e["symbols"] is not None}
    complex_dirs = find_complex_dirs(entries)

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
