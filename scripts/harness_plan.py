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
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

def _load_subdir_harness():
    try:
        import generate_subdir_harness as module
        return module
    except ImportError:
        pass

    installed_path = Path.home() / ".local" / "share" / "harness-hooks" / "generate_subdir_harness.py"
    if not installed_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("generate_subdir_harness", installed_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (ImportError, OSError, AttributeError):
        return None


subdir_harness = _load_subdir_harness()

from harness_shared import (MANUAL_MARKER, STALE_THRESHOLD,
                    CODEMAP_BG_DIRS_THRESHOLD,
                    candidate_codemap_dirs,
                    codemap_cache_path, codemap_is_ignored, codemap_is_tracked,
                    gitnexus_markdown_rows, map_areas_to_dirs, needs_description_refresh,
                    parse_codemap, parse_gitnexus_markdown, platform_files,
                    read_codemap_counts)


def plan_root_doc(own_file: str, other_file: str) -> dict:
    own = Path(own_file)
    other = Path(other_file)
    if own.exists():
        return {"action": "skip"}
    if other.exists():
        return {"action": "copy", "from": other_file}
    return {"action": "generate"}


def plan_codemap(entries: list[dict], live_counts: dict) -> dict:
    """Decide which CODE_MAP entries need description refresh.

    entries: parsed from CODE_MAP.md (recorded state)
    live_counts: fresh symbol counts from GitNexus/filesystem (current state)
    """
    if not entries:
        return {"action": "skip", "dirs_needing": [], "background": False}
    needing = []
    for e in entries:
        desc = e.get("desc") or ""
        if desc.startswith(MANUAL_MARKER):
            continue
        if needs_description_refresh(desc):
            needing.append(e["dir"])
            continue
        recorded = e["symbols"]
        live = live_counts.get(e["dir"])
        if recorded is None or live is None:
            continue
        if recorded == 0 and live == 0:
            continue
        denom = recorded if recorded != 0 else live
        if abs(live - recorded) / denom >= STALE_THRESHOLD:
            needing.append(e["dir"])
    if not needing:
        return {"action": "skip", "dirs_needing": [], "background": False}
    # large refresh → /harness-init hands it to a detached worker instead of blocking the turn
    return {"action": "refresh", "dirs_needing": needing,
            "background": len(needing) >= CODEMAP_BG_DIRS_THRESHOLD}


def plan_codemap_local_projection(project_dir: str | Path = ".") -> dict:
    """Report CODE_MAP.md local-projection state and any migration still needed."""
    tracked = codemap_is_tracked(project_dir)
    ignored = codemap_is_ignored(project_dir)
    migration = "none"
    if tracked:
        migration = "git_rm_cached"
    elif not ignored:
        migration = "add_gitignore"
    return {
        "mode": "local_projection",
        "tracked": tracked,
        "ignored": ignored,
        "migration": migration,
        "cache_path": str(codemap_cache_path(project_dir)),
    }


_WRAPPER_FIX_REASONS = {
    "missing_hooks": "Codex hooks.json 不存在，运行 install.py 配置 GitNexus 包装器",
    "invalid_hooks_json": "Codex hooks.json 解析失败，需修复 JSON 后重装",
    "missing_wrapper": "缺少 gitnexus-codex-hook.cjs，运行 install.py 安装",
    "not_configured": "Pre/PostToolUse 未指向 GitNexus 包装器，运行 install.py 注册",
    "self_test_failed": "GitNexus 包装器 --self-test 失败，建议升级 GitNexus 后重装",
}


def plan_codex_gitnexus_wrapper(diagnostic: dict, platform: str) -> dict:
    """Recommend fixing the Codex GitNexus wrapper when it is configured but unhealthy.

    Only relevant on Codex; missing_hooks on a non-Codex setup is silently skipped.
    """
    if platform != "codex":
        return {"action": "skip"}
    status = diagnostic.get("existing", {}).get("codex_gitnexus_wrapper", {}).get("status", "")
    if status in ("", "pass"):
        return {"action": "skip"}
    return {"action": "fix", "status": status,
            "reason": _WRAPPER_FIX_REASONS.get(status, "Codex GitNexus 包装器需要修复")}


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
    return candidate_codemap_dirs(entries)


def _plan_subdir_with_generator(dir_path: str, files: list[str], source_snapshot: dict | None = None) -> dict:
    if subdir_harness is None:
        return {"action": "bootstrap", "files": files, "manual_only": True, "reason": "generator_unavailable"}
    return subdir_harness.plan_directory(
        ".",
        dir_path,
        files,
        mode="background",
        source_snapshot=source_snapshot,
        allow_graph=False,
    )


def _append_action(result: dict, action: str, item: dict) -> None:
    result.setdefault(action, []).append(item)


def plan_subdirs(complex_dirs: list[str], own_file: str, other_file: str) -> dict:
    source_snapshot = subdir_harness.build_source_snapshot(".") if subdir_harness is not None else None
    result = {
        "refresh_facts": [],
        "rebaseline": [],
        "bootstrap": [],
        "manual_migration": [],
        "skip": [],
        "copy": [],
        "generate": [],
        "layers": [],
    }

    for d in complex_dirs:
        plan = _plan_subdir_with_generator(d, [own_file, other_file], source_snapshot=source_snapshot)
        action = plan.get("action", "skip")
        item = {"dir": d, "files": plan.get("files", [])}
        if plan.get("reason"):
            item["reason"] = plan["reason"]
        if action == "bootstrap":
            item["manual_only"] = True
        if action in {"refresh_facts", "rebaseline", "bootstrap", "manual_migration", "skip"}:
            _append_action(result, action, item)
        else:
            result["skip"].append({"dir": d, "files": [], "reason": f"unknown_action:{action}"})

    layers = {}
    for item in result["bootstrap"]:
        layers.setdefault(len(item["dir"].split("/")), []).append(item["dir"])
    result["layers"] = [[depth, dirs] for depth, dirs in sorted(layers.items(), reverse=True)]
    return result


def _gitnexus_markdown_query(cypher: str) -> str:
    try:
        result = subprocess.run(
            ["npx", "gitnexus", "cypher", cypher, "-r", Path(".").resolve().name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return parse_gitnexus_markdown(result.stdout.strip() or result.stderr.strip())


def _get_gitnexus_communities() -> dict[str, int]:
    markdown = _gitnexus_markdown_query(
        "MATCH (c:Community) WITH c.label AS area, sum(c.symbolCount) AS syms "
        "RETURN area, syms ORDER BY syms DESC LIMIT 25"
    )
    communities: dict[str, int] = {}
    for row in gitnexus_markdown_rows(markdown):
        if len(row) >= 2 and row[1].isdigit():
            communities[row[0]] = communities.get(row[0], 0) + int(row[1])
    return communities


def _get_gitnexus_folders() -> list[str]:
    markdown = _gitnexus_markdown_query(
        "MATCH (f:Folder) RETURN f.filePath ORDER BY f.filePath"
    )
    return [row[0] for row in gitnexus_markdown_rows(markdown) if row]


def _get_live_symbol_counts() -> dict[str, int]:
    """Get current symbol counts from GitNexus, preserving CODE_MAP units."""
    if not Path(".gitnexus").is_dir():
        return {}
    communities = _get_gitnexus_communities()
    if not communities:
        return {}
    folders = _get_gitnexus_folders()
    if not folders:
        return {}

    area_to_dir = map_areas_to_dirs(communities, folders)

    exact_counts: dict[str, int] = {}
    top_counts: dict[str, int] = {}
    for area, symbols in communities.items():
        dir_path = area_to_dir.get(area)
        if not dir_path:
            continue
        parts = [part for part in re.split(r"/+", dir_path.strip("/")) if part]
        if not parts:
            continue
        exact = "/".join(parts)
        top = parts[0]
        exact_counts[exact] = exact_counts.get(exact, 0) + symbols
        top_counts[top] = top_counts.get(top, 0) + symbols
    counts = exact_counts.copy()
    counts.update(top_counts)
    return counts


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
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--platform" and i + 1 < len(sys.argv):
            platform = sys.argv[i + 1]
            i += 2
        elif sys.argv[i].startswith("--platform="):
            platform = sys.argv[i].split("=", 1)[1]
            i += 1
        else:
            i += 1

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
    recorded_counts = read_codemap_counts(".")
    live_counts = _get_live_symbol_counts()
    if recorded_counts:
        for entry in entries:
            entry["symbols"] = recorded_counts.get(entry["dir"])
    else:
        for entry in entries:
            if entry.get("symbols") is None:
                entry["symbols"] = live_counts.get(entry["dir"])
    complex_dirs = find_complex_dirs(entries)

    plan = {
        "platform": platform,
        "doc_file": own_file,
        "other_doc_file": other_file,
        "root_doc": plan_root_doc(own_file, other_file),
        "codemap": plan_codemap(entries, live_counts),
        "codemap_local_projection": plan_codemap_local_projection("."),
        "gitnexus": plan_gitnexus(diagnostic),
        "subdirs": plan_subdirs(complex_dirs, own_file, other_file),
        "lsp": plan_lsp(diagnostic),
        "codex_gitnexus_wrapper": plan_codex_gitnexus_wrapper(diagnostic, platform),
    }

    print(json.dumps(plan, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
