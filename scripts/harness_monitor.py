#!/usr/bin/env python3
"""Harness monitor — automated project harness maintenance.

Triggered via PostToolUse [Bash], only on git operations:
  - Non-git commands (pytest, profile, etc.) → immediate return, zero overhead
  - Git on feature branch → growth check only (background, no file writes)
  - Git on main/master → background update:
    1. GitNexus reindex (if stale)
    2. CODE_MAP.md structure + descriptions (via generate_descriptions.py)
    3. Sub-directory CLAUDE.md/AGENTS.md harness regions (via AI CLI + GitNexus)
    4. Growth check (GitNexus/LSP recommendations)

All heavy work runs as detached background processes. Hook returns in <500ms.
"""

from __future__ import annotations

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

from harness_shared import (SKIP_DIRS, STALE_THRESHOLD, SOURCE_EXTS, MAIN_BRANCHES,
                    should_skip, parse_codemap_entry,
                    parse_codemap, is_acceptable_description, needs_description_refresh,
                    parse_gitnexus_markdown, gitnexus_markdown_rows, map_areas_to_dirs,
                    read_dir_docstring)

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
    r'(?:^|(?:&&|\|\||[;|]|\$\()\s*)'                              # start / shell separator / $( subshell
    r'(?:(?:\w+=\S+|sudo|env|time|nohup|command|exec|xargs)\s+)*'  # optional env-assignments / wrappers
    r'git(?:\s+-[^\s;|&]+)*\s+'
    r'(commit|merge|rebase|pull|checkout|switch|cherry-pick)\b'
)
# CODE_MAP description refresh budget. generate_descriptions runs AI batches and the
# single per-dir retry pass sequentially with no timeout pressure (detached bg job);
# a flat, generous subprocess cap keeps a hung refresh from holding the project lock
# indefinitely. CODEMAP_AI_TIMEOUT is the per-AI-call budget passed via --ai-timeout.
CODEMAP_AI_TIMEOUT = 150
CODEMAP_REFRESH_TIMEOUT = 1800
# A lock held longer than this is treated as stale and reclaimed even if its recorded
# PID is alive — guards against a reused PID wedging refreshes forever.
LOCK_STALE_SECONDS = 1800


# ── Directory description helpers (deterministic, no AI) ──

def get_readme_first_line(dir_path) -> str:
    """Read first non-empty, non-heading content line from README.md."""
    readme = Path(dir_path) / "README.md"
    if not readme.exists():
        return ""
    try:
        for line in readme.read_text(encoding="utf-8", errors="ignore").split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("="):
                return stripped[:80]
    except OSError:
        pass
    return ""


def get_init_docstring(dir_path) -> str:
    """Read __init__.py / index.ts docstring first line."""
    return read_dir_docstring(dir_path)


def get_subdir_list(dir_path) -> str:
    """List subdirectory names as description."""
    try:
        subs = sorted(d.name for d in Path(dir_path).iterdir()
                      if d.is_dir() and not should_skip(d.name))
        if subs:
            return " / ".join(subs[:8])
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


def is_on_main_branch():
    try:
        r = subprocess.run(["git", "branch", "--show-current"],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and r.stdout.strip() in MAIN_BRANCHES
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


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
        desc = existing_descs.get(top_dir, "")

        # Check staleness
        old = old_counts.get(top_dir, 0)
        if desc and old > 0 and abs(total_syms - old) / old >= STALE_THRESHOLD:
            stale_dirs.append(top_dir)

        if desc:
            lines.append(f"### {top_dir}/ ({total_syms} symbols) — {desc}")
        else:
            lines.append(f"### {top_dir}/ ({total_syms} symbols)")

        # List sub-dirs: GitNexus communities first
        covered_subs = set()
        for sub, syms, area in sorted(entries, key=lambda x: -x[1]):
            if sub:
                covered_subs.add(sub.split("/")[0] if "/" in sub else sub)
                sub_key = f"{top_dir}/{sub}"
                sub_desc = existing_descs.get(sub_key, "")
                sub_old = old_counts.get(sub_key, 0)
                if sub_desc and sub_old > 0 and abs(syms - sub_old) / sub_old >= STALE_THRESHOLD:
                    stale_dirs.append(sub_key)
                if sub_desc:
                    lines.append(f"- **{sub}/** — {sub_desc} ({syms} symbols)")
                else:
                    lines.append(f"- **{sub}/** ({syms} symbols)")

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

    return "\n".join(lines) + "\n", stale_dirs


# ══════════════════════════════════════════════════════════
# Sub-directory CLAUDE.md/AGENTS.md update via AI CLI
# ══════════════════════════════════════════════════════════

def sync_platform_docs(dir_path):
    """If both CLAUDE.md and AGENTS.md exist, copy the newer one to the other."""
    claude = Path(dir_path, "CLAUDE.md")
    agents = Path(dir_path, "AGENTS.md")
    if not claude.exists() or not agents.exists():
        return None
    try:
        claude_text = claude.read_text(encoding="utf-8")
        agents_text = agents.read_text(encoding="utf-8")
        if claude_text == agents_text:
            return None
        claude_mtime = claude.stat().st_mtime_ns
        agents_mtime = agents.stat().st_mtime_ns
        if claude_mtime == agents_mtime:
            return "conflict"
        if claude_mtime > agents_mtime:
            agents.write_text(claude_text, encoding="utf-8")
            return "claude_to_agents"
        else:
            claude.write_text(agents_text, encoding="utf-8")
            return "agents_to_claude"
    except OSError:
        return None


# ══════════════════════════════════════════════════════════
# Main update handler
# ══════════════════════════════════════════════════════════

def acquire_lock(project_id):
    """Try to acquire a lock file atomically. Returns True if acquired."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_DIR / f"{project_id}.lock"
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, 0)
                if time.time() - lock_file.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock_file.unlink(missing_ok=True)  # stale despite a live (likely reused) PID
                else:
                    return False
            except OSError:
                lock_file.unlink(missing_ok=True)
        except (ValueError, OSError):
            lock_file.unlink(missing_ok=True)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock(project_id):
    lock_file = LOCK_DIR / f"{project_id}.lock"
    lock_file.unlink(missing_ok=True)


def make_job_id(project_id: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9_.-]+', "_", project_id).strip("_") or "project"
    return f"{safe}-{int(time.time() * 1000)}"


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
    current.setdefault("updated_at", time.time())
    current["updated_at"] = time.time()
    atomic_write_text(path, json.dumps(current, indent=2, ensure_ascii=False))


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


def do_main_branch_update(project_id, job_id=None):
    """Heavy work: reindex → CODE_MAP → descriptions → sub-dir docs.

    Runs as a background process (spawned by handle_main_branch_update).
    No timeout pressure — takes as long as it needs.
    """
    # Re-check the branch: the user may have switched off main between the git op
    # that spawned this worker and now. The "only write on main" guarantee depends on it.
    if not is_on_main_branch():
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
        _do_main_branch_update_inner(job_id)
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


def _do_main_branch_update_inner(job_id=None):
    # Step 0: Ensure GitNexus index is fresh before reading community data
    ensure_gitnexus_fresh(job_id)

    codemap_file = Path("CODE_MAP.md")
    old_content = codemap_file.read_text(encoding="utf-8") if codemap_file.exists() else ""
    existing_descs, old_counts = parse_existing_codemap(codemap_file)

    # Step 1: Update CODE_MAP.md structure (now with fresh index)
    communities = get_gitnexus_communities()
    if communities:
        new_content, stale_dirs = build_codemap_structure(communities, existing_descs, old_counts)
    else:
        return

    # Self-heal: even if structure is byte-identical and nothing is stale, a CODE_MAP entry
    # with an empty/low-quality description must still trigger a description run (otherwise a
    # single failed first pass strands that entry's description permanently).
    entries_need_refresh = any(needs_description_refresh(e.get("desc") or "")
                               for e in parse_codemap(codemap_file))
    if new_content == old_content and not stale_dirs and not entries_need_refresh:
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
    if desc_script:
        try:
            cmd = [sys.executable, str(desc_script), ".", "--generate", "--use-fingerprints",
                   "--ai-timeout", str(CODEMAP_AI_TIMEOUT)]
            for dir_path in stale_dirs:
                cmd.extend(["--refresh-dir", dir_path])
            subprocess.run(cmd, capture_output=True, text=True, timeout=CODEMAP_REFRESH_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Step 3: Sync root CLAUDE.md ↔ AGENTS.md if both exist
    sync_platform_docs(".")


def handle_main_branch_update(project_id):
    """Spawn background process for heavy update work. Returns immediately."""
    lock_file = LOCK_DIR / f"{project_id}.lock"
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, 0)
                print(json.dumps({
                    "status": "skipped_locked",
                    "action": "Harness 更新已在后台运行，跳过重复启动"
                }, ensure_ascii=False))
                return
            except OSError:
                pass
        except (ValueError, OSError):
            pass

    project_dir = str(Path(".").resolve())
    job_id = make_job_id(project_id)
    write_job_status(job_id, {
        "status": "queued",
        "project_id": project_id,
        "project_dir": project_dir,
        "queued_at": time.time(),
    })
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--bg", project_id, project_dir, job_id],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(json.dumps({
        "status": "background",
        "job_id": job_id,
        "action": "Harness 更新已在后台启动（GitNexus 索引 + CODE_MAP + 描述生成）"
    }, ensure_ascii=False))


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
    project_id = Path(project_dir).name
    if not acquire_lock(f"{project_id}_growth"):
        return
    try:
        _do_growth_check_inner(state_file_path, project_dir)
    finally:
        release_lock(f"{project_id}_growth")


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

    gitnexus_done = gitnexus_indexed or grep_noise <= 20
    lsp_needed = {a["language"] for a in diag.get("lsp_assessment", []) if a["recommend"]}
    if gitnexus_done and (lsp_needed.issubset(already) or not lsp_needed) and not messages:
        state["retired"] = True

    save_state(state_file, state)

    if messages:
        project_id = Path(project_dir).name
        NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
        (NOTIFY_DIR / f"{project_id}.json").write_text(
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


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--bg":
        # Background mode: harness_monitor.py --bg <project_id> <project_dir> [job_id]
        os.chdir(sys.argv[3])
        do_main_branch_update(sys.argv[2], sys.argv[4] if len(sys.argv) >= 5 else None)
    elif len(sys.argv) >= 4 and sys.argv[1] == "--bg-growth":
        # Background growth check: --bg-growth <state_file> <project_dir>
        do_growth_check(sys.argv[2], sys.argv[3])
    else:
        main()
