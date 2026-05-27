#!/usr/bin/env python3
"""SessionStart hook: inject dynamic project context into conversation.

Outputs git state + module mapping + harness health.
Designed to be fast (< 3s) and compact (< 15 lines output).
"""

import subprocess
import sys
from pathlib import Path


def run_git(*args, default="") -> str:
    """Run a git command, return stdout or default on failure."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else default
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return default


def get_branch() -> str:
    return run_git("branch", "--show-current", default="detached HEAD")


def get_ahead_behind() -> str:
    raw = run_git("rev-list", "--left-right", "--count", "main...HEAD")
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) == 2:
        return f"(↑{parts[1]} ↓{parts[0]} vs main)"
    return ""


def get_dirty_files() -> tuple[int, str]:
    """Return (count, space-separated top-level module names)."""
    raw = run_git("status", "--porcelain")
    if not raw:
        return 0, ""
    lines = [l for l in raw.split("\n") if l.strip()][:10]
    modules = sorted(set(l.split()[-1].split("/")[0] for l in lines if l.split()))
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
                           capture_output=True, text=True, timeout=5)
        output = r.stdout + r.stderr
        if "stale" in output.lower():
            return "⚠️ GitNexus 索引过期，建议运行 npx gitnexus analyze"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def check_codemap_stale() -> str | None:
    codemap = Path("CODE_MAP.md")
    if not codemap.exists():
        return None
    try:
        count = codemap.read_text().count("⚠️ 描述可能过期")
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


if __name__ == "__main__":
    main()
