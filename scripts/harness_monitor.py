#!/usr/bin/env python3
"""Harness monitor — automated project harness maintenance.

Triggered via PostToolUse [Bash], only on git operations:
  - Non-git commands (pytest, profile, etc.) → immediate return, zero overhead
  - Git on feature branch → growth check only (background, no file writes)
  - Git on main/master → background update:
    1. GitNexus reindex (if stale)
    2. CODE_MAP.md structure + descriptions (via generate_descriptions.py)
    3. Root CLAUDE.md / AGENTS.md CODE_MAP block rendering
  - Growth check (GitNexus/LSP recommendations) runs on every git op, both branches.
    (Sub-directory constraint files are generated only by the manual /harness-init skill.)

All heavy work runs as detached background processes. Hook returns in <500ms.
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from harness_shared import (STALE_THRESHOLD, SOURCE_EXTS, MAIN_BRANCHES,
                    should_skip, parse_codemap, is_acceptable_description,
                    needs_description_refresh, parse_gitnexus_markdown,
                    parse_codemap_entry, gitnexus_markdown_rows, map_areas_to_dirs,
                    read_dir_docstring, path_key, cache_codemap_projection,
                    ensure_codemap_gitignore, materialize_codemap_projection,
                    read_codemap_counts, write_codemap_counts,
                    update_root_codemap_docs)

# ── Config ──

CHECK_EVERY_N_FILES = 20
COUNTER_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "counters"
LOCK_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "locks"
NOTIFY_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "notifications"
JOB_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "jobs"
DIAG_SCRIPT = Path.home() / ".local" / "bin" / "harness-init.py"
DESC_SCRIPT = Path.home() / ".local" / "share" / "harness-hooks" / "generate_descriptions.py"
GITNEXUS_TIMEOUT = 15
GIT_COMMANDS = re.compile(
    r'(?:^\s*|(?:&&|\|\||[;|]|\$\(|\()\s*)'                        # start / shell separator / $( or ( subshell
    r'(?:(?:\w+=\S+|sudo|env|time|nohup|command|exec|xargs)\s+)*'  # optional env-assignments / wrappers
    r'git(?:\s+(?:-[Cc]\s+\S+|-[^\s;|&]+))*\s+'                    # git + global opts (incl. -C/-c <value>)
    r'(commit|merge|rebase|pull|checkout|switch|cherry-pick)\b'
)
# CODE_MAP description refresh budget. generate_descriptions runs AI batches and the
# single per-dir retry pass sequentially with no timeout pressure (detached bg job);
# a flat, generous subprocess cap keeps a hung refresh from holding the project lock
# indefinitely. CODEMAP_AI_TIMEOUT is the per-AI-call budget passed via --ai-timeout.
CODEMAP_AI_TIMEOUT = 150
CODEMAP_REFRESH_TIMEOUT = 1800


# ── Directory description helpers (deterministic, no AI) ──

def get_readme_first_line(dir_path) -> str:
    """Read first non-empty, non-heading content line from README.md."""
    readme = Path(dir_path) / "README.md"
    if not readme.exists():
        return ""
    # skip headings/underlines (# =), table rows (|), blockquotes (>) and HTML (<) —
    # none are prose descriptions
    skip_prefixes = ("#", "=", "|", ">", "<")
    try:
        for line in readme.read_text(encoding="utf-8", errors="ignore").split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith(skip_prefixes):
                return stripped[:80]
    except OSError:
        pass
    return ""


def get_init_docstring(dir_path) -> str:
    """First line of a directory's __init__.py package docstring (delegates to read_dir_docstring)."""
    return read_dir_docstring(dir_path)


def get_subdir_list(dir_path) -> str:
    """List subdirectory names as description."""
    try:
        subs = sorted(d.name for d in Path(dir_path).iterdir()
                      if d.is_dir() and not should_skip(d.name))
        if subs:
            # "、"-join (not " / "): the quality gate flags " / " as low-quality, which would
            # put this description in a perpetual refresh loop.
            return "、".join(subs[:8])
    except OSError:
        pass
    return ""


# ── Git state ──

def get_project_id():
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return r.stdout.strip().replace("/", "_").lstrip("_")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def is_git_operation(ctx):
    cmd = ctx.get("tool_input", {}).get("command", "")
    unquoted = re.sub(r"""(['"])(?:\\.|(?!\1).)*\1""", "", cmd)
    return bool(GIT_COMMANDS.search(unquoted))


def _current_branch():
    try:
        r = subprocess.run(["git", "branch", "--show-current"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def is_on_main_branch():
    return _current_branch() in MAIN_BRANCHES


def _branch_ok(require_main, expected_branch):
    """Branch guard for background writes. Hook path (require_main=True): must be on a main-like
    branch. Skill path (expected_branch set): the branch the user dispatched from must still be
    checked out — so a mid-run `git checkout` can't land the refresh on the wrong branch."""
    if require_main:
        return is_on_main_branch()
    if expected_branch is not None:
        return _current_branch() == expected_branch
    return True


# ── State management ──

def load_state(state_file):
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"file_count": 0, "gitnexus_recommended": False,
            "lsp_recommended": [], "retired": False}


def save_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(state_file, json.dumps(state, indent=2))


# ══════════════════════════════════════════════════════════
# CODE_MAP.md update (structure + descriptions)
# ══════════════════════════════════════════════════════════

def parse_existing_codemap(codemap_path):
    entries = parse_codemap(codemap_path)
    descs = {e["dir"]: e["desc"] for e in entries if is_acceptable_description(e["desc"])}
    counts = {e["dir"]: e["symbols"] for e in entries if e["symbols"] is not None}
    return descs, counts


def parse_codemap_text(content: str) -> list[dict]:
    """Parse generated CODE_MAP text without materializing it to disk."""
    entries = []
    current = ""
    for line in content.split("\n"):
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


def atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def get_gitnexus_communities():
    if not Path(".gitnexus").is_dir():
        return None
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher",
             "MATCH (c:Community) WITH c.label AS area, sum(c.symbolCount) AS syms, "
             "count(*) AS clusters RETURN area, syms, clusters ORDER BY syms DESC LIMIT 25",
             "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=GITNEXUS_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        if not output or r.returncode != 0:
            return None
        result = {}
        for cols in gitnexus_markdown_rows(parse_gitnexus_markdown(output)):
            if len(cols) >= 3 and cols[1].isdigit():
                area, syms, clusters = cols[0], int(cols[1]), int(cols[2])
                if area in result:
                    result[area]["symbols"] += syms
                    result[area]["clusters"] += clusters
                else:
                    result[area] = {"symbols": syms, "clusters": clusters}
        return result or None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def build_area_to_dir(communities):
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher",
             "MATCH (f:Folder) RETURN f.filePath ORDER BY f.filePath",
             "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=GITNEXUS_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        folders = [row[0] for row in gitnexus_markdown_rows(parse_gitnexus_markdown(output)) if row]
    except (subprocess.TimeoutExpired, OSError):
        folders = []
    return map_areas_to_dirs(communities, folders)


def build_codemap_structure(communities, existing_descs, old_counts):
    """Build CODE_MAP.md with structure + preserved descriptions."""
    area_to_dir = build_area_to_dir(communities)
    lines = ["# Code Map", "",
             "> Auto-generated from GitNexus. Descriptions maintained by AI + GitNexus or 📌 manual.", ""]

    stale_dirs = []
    counts = {}
    top_dirs = {}
    for area, info in sorted(communities.items(), key=lambda x: -x[1]["symbols"]):
        dir_path = area_to_dir.get(area)
        if not dir_path:
            continue
        parts = dir_path.split("/")
        top, sub = parts[0], "/".join(parts[1:]) if len(parts) > 1 else ""
        top_dirs.setdefault(top, []).append((sub, info["symbols"], area))

    for top_dir in sorted(top_dirs):
        entries = top_dirs[top_dir]
        total_syms = sum(e[1] for e in entries)
        counts[top_dir] = total_syms
        desc = existing_descs.get(top_dir, "")

        # Check staleness
        old = old_counts.get(top_dir, 0)
        if desc and old > 0 and abs(total_syms - old) / old >= STALE_THRESHOLD:
            stale_dirs.append(top_dir)

        if desc:
            lines.append(f"### {top_dir}/ — {desc}")
        else:
            lines.append(f"### {top_dir}/")

        # List sub-dirs: GitNexus communities first
        covered_subs = set()
        for sub, syms, area in sorted(entries, key=lambda x: -x[1]):
            if sub:
                covered_subs.add(sub.split("/")[0] if "/" in sub else sub)
                sub_key = f"{top_dir}/{sub}"
                counts[sub_key] = syms
                sub_desc = existing_descs.get(sub_key, "")
                sub_old = old_counts.get(sub_key, 0)
                if sub_desc and sub_old > 0 and abs(syms - sub_old) / sub_old >= STALE_THRESHOLD:
                    stale_dirs.append(sub_key)
                if sub_desc:
                    lines.append(f"- **{sub}/** — {sub_desc}")
                else:
                    lines.append(f"- **{sub}/**")

        # Append uncovered sub-dirs (e.g., a nested core/ inside a top-level package)
        try:
            top_path = Path(top_dir)
            if top_path.is_dir():
                for sub_d in sorted(top_path.iterdir()):
                    if (sub_d.is_dir() and not should_skip(sub_d.name)
                            and not sub_d.name.startswith("_")
                            and sub_d.name not in covered_subs):
                        sub_key = f"{top_dir}/{sub_d.name}"
                        sub_desc = existing_descs.get(sub_key, "") or get_readme_first_line(sub_d) or get_init_docstring(sub_d) or ""
                        if sub_desc:
                            lines.append(f"- **{sub_d.name}/** — {sub_desc}")
                        else:
                            lines.append(f"- **{sub_d.name}/**")
        except OSError:
            pass

        lines.append("")

    # Append directories not covered by GitNexus (docs, tests, etc.)
    covered = set(top_dirs.keys())
    try:
        for d in sorted(Path(".").iterdir()):
            if not d.is_dir() or should_skip(d.name) or d.name in covered:
                continue
            desc = existing_descs.get(d.name, "") or get_readme_first_line(d) or get_init_docstring(d) or get_subdir_list(d)
            if desc:
                lines.append(f"### {d.name}/ — {desc}")
            else:
                lines.append(f"### {d.name}/")
            for sub in sorted(d.iterdir()):
                if sub.is_dir() and not should_skip(sub.name) and not sub.name.startswith("_"):
                    sub_key = f"{d.name}/{sub.name}"
                    sub_desc = existing_descs.get(sub_key, "") or get_readme_first_line(sub) or get_init_docstring(sub) or ""
                    if sub_desc:
                        lines.append(f"- **{sub.name}/** — {sub_desc}")
                    else:
                        lines.append(f"- **{sub.name}/**")
            lines.append("")
    except OSError:
        pass

    return "\n".join(lines) + "\n", stale_dirs, counts


def merge_codemap_count_baseline(
    old_counts: dict[str, int],
    new_counts: dict[str, int],
    refreshed_dirs: list[str],
    *,
    seed_missing: bool = False,
) -> dict[str, int]:
    """Update count baselines only for refreshed descriptions, plus bootstrap missing sidecar."""
    merged = dict(old_counts)
    if seed_missing:
        for dir_path, count in new_counts.items():
            merged.setdefault(dir_path, count)
    for dir_path in refreshed_dirs:
        if dir_path in new_counts:
            merged[dir_path] = new_counts[dir_path]
    return merged


def changed_acceptable_description_dirs(
    before_content: str,
    after_content: str,
    candidate_dirs: list[str],
) -> list[str]:
    """Return candidate dirs whose CODE_MAP description was actually refreshed."""
    before = {e["dir"]: e.get("desc") or "" for e in parse_codemap_text(before_content)}
    after = {e["dir"]: e.get("desc") or "" for e in parse_codemap_text(after_content)}
    changed = []
    for dir_path in candidate_dirs:
        before_desc = before.get(dir_path, "")
        after_desc = after.get(dir_path, "")
        if after_desc != before_desc and not needs_description_refresh(after_desc):
            changed.append(dir_path)
    return changed


# ══════════════════════════════════════════════════════════
# CLAUDE.md ↔ AGENTS.md sync (root CODE_MAP block, subdir mtime copy)
# ══════════════════════════════════════════════════════════

def sync_platform_docs(dir_path):
    """Update root CODE_MAP blocks, or copy the newer subdirectory doc to the other."""
    root = Path(dir_path)
    if (root / "CODE_MAP.md").exists():
        result = update_root_codemap_docs(root)
        if any(value == "write_failed" for value in result.values()):
            return "codemap_block_failed"
        if any(value == "updated" for value in result.values()):
            return "codemap_block"
        return None

    claude = root / "CLAUDE.md"
    agents = root / "AGENTS.md"
    if not claude.exists() or not agents.exists():
        return None
    try:
        claude_text = claude.read_text(encoding="utf-8", errors="replace")
        agents_text = agents.read_text(encoding="utf-8", errors="replace")
        if claude_text == agents_text:
            return None
        claude_mtime = claude.stat().st_mtime_ns
        agents_mtime = agents.stat().st_mtime_ns
        if claude_mtime == agents_mtime:
            return "conflict"
        if claude_mtime > agents_mtime:
            atomic_write_text(agents, claude_text)  # atomic: never truncate a user-authored doc
            return "claude_to_agents"
        else:
            atomic_write_text(claude, agents_text)
            return "agents_to_claude"
    except OSError:
        return None


def update_root_codemap_docs_checked(dir_path, job_id=None):
    result = update_root_codemap_docs(dir_path) or {}
    failed = sorted(name for name, status in result.items() if status == "write_failed")
    if failed:
        write_job_status(job_id, {
            "root_doc_update": {"status": "write_failed", "files": failed},
        })
    return result


# ══════════════════════════════════════════════════════════
# Main update handler
# ══════════════════════════════════════════════════════════

# Locking uses fcntl.flock — an advisory lock tied to an open fd. The kernel releases it
# automatically when the holding process dies (even on SIGKILL), so there are NO stale
# locks to detect or reclaim: no PID liveness checks, no mtime staleness, no reclaim race.
_held_locks: dict[str, int] = {}  # project_id → open fd holding the flock (this process)


def _open_lock_fd(project_id):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    return os.open(str(LOCK_DIR / f"{project_id}.lock"), os.O_CREAT | os.O_RDWR, 0o644)


def _lock_held(project_id) -> bool:
    """Best-effort probe: True iff another process currently holds the lock. (The real
    guarantee is acquire_lock's own flock; this just lets the dispatcher avoid spawning a
    worker that would immediately exit.)"""
    try:
        fd = _open_lock_fd(project_id)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)  # we were only probing
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def acquire_lock(project_id):
    """Acquire an exclusive flock for project_id. Returns True if acquired. The lock is held
    by an open fd for this process's lifetime and auto-released on exit/death."""
    try:
        fd = _open_lock_fd(project_id)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())  # content is informational only
    _held_locks[project_id] = fd
    return True


def release_lock(project_id):
    """Release our flock (a no-op if we don't hold it)."""
    fd = _held_locks.pop(project_id, None)
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


JOB_RETENTION = 50


def make_job_id(project_id: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9_.-]+', "_", project_id).strip("_") or "project"
    return f"{safe}-{int(time.time() * 1000)}"


def _prune_old_jobs(keep: int = JOB_RETENTION) -> None:
    """Keep only the most recent `keep` job-status files; jobs/ is otherwise write-only."""
    try:
        files = sorted(JOB_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in files[keep:]:
        stale.unlink(missing_ok=True)


def write_job_status(job_id: str | None, payload: dict) -> None:
    if not job_id:
        return
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    path = JOB_DIR / f"{job_id}.json"
    current = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current = {}
    current.update(payload)
    current.setdefault("job_id", job_id)
    current["updated_at"] = time.time()
    atomic_write_text(path, json.dumps(current, indent=2, ensure_ascii=False))


def emit_post_tool_context(message: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }, ensure_ascii=False))


def ensure_gitnexus_fresh(job_id=None):
    """If GitNexus is indexed but stale, run incremental analyze first.

    A failed/timed-out analyze is recorded in the job status rather than silently
    swallowed, so a repeatedly-failing reindex is visible to whoever inspects jobs/.
    """
    if not Path(".gitnexus").is_dir():
        return
    try:
        r = subprocess.run(["npx", "gitnexus", "status"],
                           capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return
    if "stale" not in (r.stdout + r.stderr).lower():
        return
    try:
        subprocess.run(["npx", "gitnexus", "analyze"],
                       capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        write_job_status(job_id, {"gitnexus_analyze": "timeout"})
    except (FileNotFoundError, OSError):
        write_job_status(job_id, {"gitnexus_analyze": "failed"})


def do_main_branch_update(project_id, job_id=None, *, require_main=True, expected_branch=None):
    """Heavy work: reindex → CODE_MAP structure → descriptions → root CODE_MAP doc blocks.

    Runs as a background process. The PostToolUse path passes require_main=True (only write on a
    main-like branch); the /harness-init skill path passes require_main=False + expected_branch
    (refresh the branch the user dispatched from, bail if they switched away mid-run).
    """
    # Re-check the branch: the user may have switched branches between dispatch and now.
    if not _branch_ok(require_main, expected_branch):
        write_job_status(job_id, {"status": "skipped_branch_changed", "project_id": project_id})
        return
    if not acquire_lock(project_id):
        write_job_status(job_id, {
            "status": "skipped_locked",
            "project_id": project_id,
        })
        return
    write_job_status(job_id, {
        "status": "running",
        "project_id": project_id,
        "pid": os.getpid(),
        "started_at": time.time(),
    })
    try:
        _do_main_branch_update_inner(job_id, require_main=require_main, expected_branch=expected_branch)
        write_job_status(job_id, {
            "status": "completed",
            "project_id": project_id,
            "finished_at": time.time(),
        })
    except Exception as exc:
        write_job_status(job_id, {
            "status": "failed",
            "project_id": project_id,
            "finished_at": time.time(),
            "error": str(exc),
        })
        raise
    finally:
        release_lock(project_id)


def _do_main_branch_update_inner(job_id=None, *, require_main=True, expected_branch=None):
    # Step 0: Ensure GitNexus index is fresh before reading community data
    ensure_gitnexus_fresh(job_id)
    if not _branch_ok(require_main, expected_branch):
        return
    ensure_codemap_gitignore(".")
    materialize_codemap_projection(".")

    codemap_file = Path("CODE_MAP.md")
    old_content = codemap_file.read_text(encoding="utf-8", errors="replace") if codemap_file.exists() else ""
    existing_descs, legacy_counts = parse_existing_codemap(codemap_file)
    sidecar_counts = read_codemap_counts(".")
    old_counts = sidecar_counts or legacy_counts
    seed_counts = not sidecar_counts

    # Step 1: Update CODE_MAP.md structure (now with fresh index)
    communities = get_gitnexus_communities()
    if communities:
        new_content, stale_dirs, new_counts = build_codemap_structure(communities, existing_descs, old_counts)
    else:
        return

    # Self-heal: even if structure is byte-identical and nothing is stale, a CODE_MAP entry
    # with an empty/low-quality description must still trigger a description run (otherwise a
    # single failed first pass strands that entry's description permanently).
    entries_needing_refresh = [
        e["dir"]
        for e in parse_codemap_text(new_content)
        if e.get("dir") and needs_description_refresh(e.get("desc") or "")
    ]
    entries_need_refresh = bool(entries_needing_refresh)
    refresh_attempted = bool(stale_dirs) or entries_need_refresh
    if new_content == old_content and not stale_dirs and not entries_need_refresh:
        if not _branch_ok(require_main, expected_branch):
            return
        cache_codemap_projection(".")
        if seed_counts:
            write_codemap_counts(
                ".",
                merge_codemap_count_baseline(old_counts, new_counts, [], seed_missing=True),
            )
        update_root_codemap_docs_checked(".", job_id)
        return

    # Re-check before mutating: the reindex + structure prelude above can run for a while, and we
    # must never write CODE_MAP.md / CLAUDE.md / AGENTS.md onto a branch the user switched to
    # mid-run — for the hook path that means "still on main", for the skill path "still on the
    # branch you dispatched from". (The worker-start check can go stale over a multi-minute run.)
    if not _branch_ok(require_main, expected_branch):
        return

    if seed_counts and legacy_counts:
        if not write_codemap_counts(".", legacy_counts):
            return

    if new_content != old_content:
        atomic_write_text(codemap_file, new_content)

    # Step 2: Generate/refresh descriptions
    desc_script = None
    for candidate in [
        DESC_SCRIPT,
        Path(__file__).resolve().parent / "generate_descriptions.py",
    ]:
        if candidate.exists():
            desc_script = candidate
            break
    refresh_returncode_ok = False
    if desc_script:
        try:
            cmd = [sys.executable, str(desc_script), ".", "--generate", "--use-fingerprints",
                   "--ai-timeout", str(CODEMAP_AI_TIMEOUT)]
            for dir_path in stale_dirs:
                cmd.extend(["--refresh-dir", dir_path])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CODEMAP_REFRESH_TIMEOUT)
            refresh_returncode_ok = result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            refresh_returncode_ok = False
    if not _branch_ok(require_main, expected_branch):
        return
    cache_codemap_projection(".")

    refreshed_count_dirs = []
    refresh_candidate_dirs = list(dict.fromkeys([*stale_dirs, *entries_needing_refresh]))
    if refresh_attempted and refresh_returncode_ok:
        try:
            after_content = codemap_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            after_content = ""
        refreshed_count_dirs = changed_acceptable_description_dirs(
            new_content,
            after_content,
            refresh_candidate_dirs,
        )
    seed_missing_counts = seed_counts and not refresh_attempted and not legacy_counts
    should_write_counts = seed_missing_counts or bool(refreshed_count_dirs)
    if should_write_counts:
        write_codemap_counts(
            ".",
            merge_codemap_count_baseline(
                old_counts,
                new_counts,
                refreshed_count_dirs,
                seed_missing=seed_missing_counts,
            ),
        )
    update_root_codemap_docs_checked(".", job_id)


def _spawn_bg_worker(project_id, project_dir, bg_flag, extra_args=()) -> str:
    """Queue a detached background worker (returns its job_id). Shared by the PostToolUse
    dispatcher (--bg, main-only) and the /harness-init skill dispatcher (--bg-skill, branch-pinned)."""
    job_id = make_job_id(project_id)
    _prune_old_jobs()  # bound jobs/ — it is otherwise append-only
    write_job_status(job_id, {
        "status": "queued",
        "project_id": project_id,
        "project_dir": project_dir,
        "queued_at": time.time(),
    })
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), bg_flag, project_id, project_dir, job_id,
         *extra_args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return job_id


def handle_main_branch_update(project_id):
    """Spawn background process for heavy update work. Returns immediately."""
    if _lock_held(project_id):
        emit_post_tool_context("Harness 更新已在后台运行，跳过重复启动")
        return
    job_id = _spawn_bg_worker(project_id, str(Path(".").resolve()), "--bg")
    emit_post_tool_context(
        f"Harness 更新已在后台启动（GitNexus 索引 + CODE_MAP + 描述生成），job_id={job_id}"
    )


def handle_skill_refresh(project_dir="."):
    """Branch-agnostic dispatcher for /harness-init: queue the CODE_MAP refresh chain in a
    detached worker and print its status as JSON, so a large manual init doesn't block the turn."""
    project_dir = str(Path(project_dir).resolve())
    os.chdir(project_dir)
    project_id = get_project_id()
    if not project_id:
        print(json.dumps({"status": "error", "reason": "not_a_git_repo"}, ensure_ascii=False))
        return
    if _lock_held(project_id):
        print(json.dumps({"status": "already_running", "project_id": project_id}, ensure_ascii=False))
        return
    # pin to the branch the user dispatched from → the worker bails if they switch away mid-run
    job_id = _spawn_bg_worker(project_id, project_dir, "--bg-skill", extra_args=(_current_branch(),))
    print(json.dumps({"status": "started", "job_id": job_id,
                      "jobs_dir": str(JOB_DIR)}, ensure_ascii=False))


# ══════════════════════════════════════════════════════════
# Growth detection
# ══════════════════════════════════════════════════════════

def count_source_files():
    count = 0
    try:
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if not should_skip(d)]
            for f in files:
                if Path(f).suffix.lower() in SOURCE_EXTS:
                    count += 1
    except OSError:
        pass
    return count


def handle_growth_check(state, state_file):
    """Fast path: count files + threshold check (sync). Heavy path: spawn background."""
    if state.get("retired"):
        return

    current_count = count_source_files()
    prev_count = state.get("file_count", 0)

    if current_count < prev_count:
        state["file_count"] = current_count
        save_state(state_file, state)
        return

    if current_count - prev_count < CHECK_EVERY_N_FILES:
        save_state(state_file, state)
        return

    if not DIAG_SCRIPT.exists():
        save_state(state_file, state)
        return

    state["file_count"] = current_count
    save_state(state_file, state)
    project_dir = str(Path(".").resolve())
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()),
         "--bg-growth", str(state_file), project_dir],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def do_growth_check(state_file_path, project_dir):
    """Background worker: run diagnostic + save notifications."""
    lock_id = f"{path_key(project_dir)}_growth"  # full path, not basename → collision-safe
    if not acquire_lock(lock_id):
        return
    try:
        _do_growth_check_inner(state_file_path, project_dir)
    finally:
        release_lock(lock_id)


def _do_growth_check_inner(state_file_path, project_dir):
    os.chdir(project_dir)
    state_file = Path(state_file_path)
    state = load_state(state_file)

    try:
        r = subprocess.run([sys.executable, str(DIAG_SCRIPT), "."],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not r.stdout.strip():
            return
        diag = json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return

    messages = []
    grep_noise = diag.get("grep_noise", {}).get("grep_noise_files", 0)
    most_imported = diag.get("grep_noise", {}).get("most_imported", "")
    gitnexus_indexed = diag.get("existing", {}).get("gitnexus", {}).get("indexed", False)

    if grep_noise > 20 and not gitnexus_indexed and not state.get("gitnexus_recommended"):
        state["gitnexus_recommended"] = True
        messages.append(
            f"📊 项目复杂度增长：`{most_imported}` grep 噪声 {grep_noise} 文件，建议安装 GitNexus。")

    already = set(state.get("lsp_recommended", []))
    for a in diag.get("lsp_assessment", []):
        if a["recommend"] and a["language"] not in already:
            already.add(a["language"])
            messages.append(f"📊 {a['language']} LSP 建议：{a['reason']}")
    state["lsp_recommended"] = list(already)

    # grep_noise == -1 is a measurement failure sentinel, not "low noise" — treat it as
    # inconclusive so a transient grep failure can't permanently retire growth detection.
    gitnexus_done = gitnexus_indexed or 0 <= grep_noise <= 20
    lsp_needed = {a["language"] for a in diag.get("lsp_assessment", []) if a["recommend"]}
    if gitnexus_done and (lsp_needed.issubset(already) or not lsp_needed) and not messages:
        state["retired"] = True

    save_state(state_file, state)

    if messages:
        NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
        (NOTIFY_DIR / f"{path_key(project_dir)}.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8")


# ══════════════════════════════════════════════════════════
# Main entry
# ══════════════════════════════════════════════════════════

def main():
    try:
        ctx = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    if not isinstance(ctx, dict) or ctx.get("tool_name") != "Bash":
        return

    if not is_git_operation(ctx):
        return

    project_id = get_project_id()
    if not project_id:
        return

    state_file = COUNTER_DIR / f"{project_id}.json"
    state = load_state(state_file)

    if is_on_main_branch():
        handle_main_branch_update(project_id)
        handle_growth_check(state, state_file)
    else:
        handle_growth_check(state, state_file)


def _cli_main(argv):
    """Dispatch the CLI by argv (testable without a subprocess)."""
    if len(argv) >= 4 and argv[1] == "--bg":
        # Background mode: harness_monitor.py --bg <project_id> <project_dir> [job_id]
        os.chdir(argv[3])
        do_main_branch_update(argv[2], argv[4] if len(argv) >= 5 else None)
    elif len(argv) >= 4 and argv[1] == "--bg-skill":
        # Skill-initiated worker (branch-pinned): --bg-skill <project_id> <project_dir> [job_id] [branch]
        os.chdir(argv[3])
        do_main_branch_update(argv[2], argv[4] if len(argv) >= 5 else None,
                              require_main=False,
                              expected_branch=argv[5] if len(argv) >= 6 else None)
    elif len(argv) >= 2 and argv[1] == "--refresh-bg":
        # Skill dispatcher: --refresh-bg [project_dir] → detach worker, print job JSON
        handle_skill_refresh(argv[2] if len(argv) >= 3 else ".")
    elif len(argv) >= 4 and argv[1] == "--bg-growth":
        # Background growth check: --bg-growth <state_file> <project_dir>
        do_growth_check(argv[2], argv[3])
    else:
        main()


if __name__ == "__main__":
    _cli_main(sys.argv)
