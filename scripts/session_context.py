#!/usr/bin/env python3
"""SessionStart hook: inject dynamic project context into conversation.

Outputs git state + module mapping + harness health.
Designed to be fast (< 3s) and compact (< 15 lines output).
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from harness_shared import MAIN_BRANCHES, needs_description_refresh, parse_codemap

NOTIFY_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "notifications"


def run_git(*args, default="") -> str:
    """Run a git command, return stdout or default on failure."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else default
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return default


def get_branch() -> str:
    return run_git("branch", "--show-current", default="detached HEAD")


def _baseline_ref() -> str:
    """Branch to compare HEAD against: upstream tracking branch, else first main-like branch."""
    upstream = run_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        return upstream
    for branch in sorted(MAIN_BRANCHES):  # "main" before "master"
        if run_git("rev-parse", "--verify", "--quiet", branch):
            return branch
    return ""


def get_ahead_behind() -> str:
    base = _baseline_ref()
    if not base:
        return ""
    raw = run_git("rev-list", "--left-right", "--count", f"{base}...HEAD")
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) == 2:
        return f"(↑{parts[1]} ↓{parts[0]} vs {base})"
    return ""


def _porcelain_module(line: str) -> str:
    """Top-level dir/file from a `git status --porcelain` line.

    Drops the 2-char XY status + space, takes the rename destination (after ' -> '),
    strips git's surrounding quotes, then the first path segment.
    """
    rest = line[3:] if len(line) >= 3 else ""
    if " -> " in rest:
        rest = rest.split(" -> ", 1)[1]
    rest = rest.strip().strip('"')
    return rest.split("/")[0] if rest else ""


def get_dirty_files() -> tuple[int, str]:
    """Return (count, space-separated top-level module names)."""
    raw = run_git("status", "--porcelain")
    if not raw:
        return 0, ""
    lines = [l for l in raw.split("\n") if l.strip()]
    modules = sorted({m for l in lines[:10] if (m := _porcelain_module(l))})
    return len(lines), " ".join(modules)


def get_recent_commits(limit=5) -> list[dict]:
    raw = run_git("log", "--oneline", "--no-decorate", f"-{limit}")
    if not raw:
        return []
    commits = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        hash_id, msg = parts
        module = run_git("diff-tree", "--no-commit-id", "--name-only", "-r", hash_id)
        module = module.split("\n")[0].rsplit("/", 1)[0] if module else "root"
        ago = run_git("log", "-1", "--format=%cr", hash_id).replace(" ago", "")
        commits.append({"hash": hash_id, "msg": msg, "module": module, "ago": ago})
    return commits


def check_gitnexus_stale() -> str | None:
    if not Path(".gitnexus").is_dir():
        return "💡 GitNexus 未索引此项目"
    try:
        r = subprocess.run(["npx", "gitnexus", "status"],
                           capture_output=True, text=True, timeout=2)
        output = r.stdout + r.stderr
        if "stale" in output.lower():
            return "⚠️ GitNexus 索引过期，建议运行 npx gitnexus analyze"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def read_pending_notifications() -> list[str]:
    """Read and consume pending notifications from background processes."""
    project_name = Path(".").resolve().name
    notify_file = NOTIFY_DIR / f"{project_name}.json"
    if not notify_file.exists():
        return []
    try:
        messages = json.loads(notify_file.read_text(encoding="utf-8"))
        notify_file.unlink()
        return messages if isinstance(messages, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def check_codemap_stale() -> str | None:
    codemap = Path("CODE_MAP.md")
    if not codemap.exists():
        return None
    try:
        count = sum(1 for entry in parse_codemap(codemap)
                    if needs_description_refresh(entry.get("desc") or ""))
        if count > 0:
            return f"⚠️ CODE_MAP.md: {count} 个目录描述待更新"
    except OSError:
        pass
    return None


def main():
    branch = get_branch()
    ahead_behind = get_ahead_behind()
    print(f"📍 分支: {branch} {ahead_behind}")

    dirty_count, modules = get_dirty_files()
    if dirty_count:
        print(f"📝 工作区: {dirty_count} 个文件变更 ({modules})")
    else:
        print("📝 工作区: 干净")

    commits = get_recent_commits()
    if commits:
        print("📜 最近提交:")
        for c in commits:
            print(f"  {c['hash']} {c['ago']}  {c['msg']} — {c['module']}/")
    else:
        print("📜 最近提交: (无提交历史)")

    warnings = [w for w in [check_gitnexus_stale(), check_codemap_stale()] if w]
    for w in warnings:
        print(w)

    for msg in read_pending_notifications():
        print(msg)


if __name__ == "__main__":
    main()
