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

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from shared import (SKIP_DIRS, STALE_THRESHOLD, SOURCE_EXTS, MAIN_BRANCHES,
                    should_skip, parse_codemap_entry)

# ── Config ──

CHECK_EVERY_N_FILES = 20
COUNTER_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "counters"
LOCK_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "locks"
NOTIFY_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "notifications"
DIAG_SCRIPT = Path.home() / ".local" / "bin" / "harness-init.py"
DESC_SCRIPT = Path.home() / ".local" / "share" / "harness-hooks" / "generate_descriptions.py"
GITNEXUS_TIMEOUT = 15
GIT_COMMANDS = re.compile(r'\bgit\s+(commit|merge|rebase|pull|checkout|switch|cherry-pick)\b')


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
    import ast as _ast
    for fname in ("__init__.py", "index.ts", "index.js", "mod.rs"):
        fpath = Path(dir_path) / fname
        if fpath.exists():
            try:
                if fname.endswith(".py"):
                    ds = _ast.get_docstring(_ast.parse(fpath.read_text(encoding="utf-8", errors="ignore")))
                    if ds:
                        line = ds.strip().split("\n")[0]
                        for sep in ("—", "–", "-"):
                            if sep in line:
                                line = line.split(sep, 1)[1].strip()
                                break
                        return line[:80]
            except (SyntaxError, OSError):
                pass
    return ""


def parse_gitnexus_markdown(output: str) -> str:
    """Extract markdown table from GitNexus output. Handles both dict and list formats."""
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            return data.get("markdown", "")
        elif isinstance(data, list) and data:
            # List format: first item may contain markdown
            if isinstance(data[0], dict):
                return data[0].get("markdown", "")
            return str(data[0])
    except (json.JSONDecodeError, IndexError, TypeError):
        pass
    return ""


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


# ── Platform detection ──

def get_ai_cmd():
    """Find available AI CLI for non-interactive invocation."""
    for cmd in ["claude", "codex"]:
        if shutil.which(cmd):
            return cmd
    # Codex app binary
    codex_app = "/Applications/Codex.app/Contents/Resources/codex"
    if os.path.isfile(codex_app):
        return codex_app
    return ""


def ai_invoke(prompt, timeout=15):
    """Invoke AI CLI non-interactively. Called from background workers only."""
    cmd = get_ai_cmd()
    if not cmd:
        return ""
    try:
        if "claude" in cmd:
            # No Bash in allowedTools — prompts only need Read + GitNexus MCP
            r = subprocess.run(
                [cmd, "-p", prompt, "--allowedTools", "Read,mcp__gitnexus*"],
                capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip()
        else:
            r = subprocess.run(
                [cmd, "exec", prompt],
                capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
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
    return bool(GIT_COMMANDS.search(cmd))


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
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════
# CODE_MAP.md update (structure + descriptions)
# ══════════════════════════════════════════════════════════

def parse_existing_codemap(codemap_path):
    from shared import parse_codemap as _parse
    entries = _parse(codemap_path)
    descs = {e["dir"]: e["desc"] for e in entries if e["desc"] and not e["desc"].startswith("⚠️")}
    counts = {e["dir"]: e["symbols"] for e in entries if e["symbols"] is not None}
    return descs, counts


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
        md = parse_gitnexus_markdown(output)
        lines = [l.strip() for l in md.split("\n") if l.strip()]
        if len(lines) < 3:
            return None
        result = {}
        for line in lines[2:]:
            cols = [c.strip() for c in line.split("|") if c.strip()]
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
    mapping = {}
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher",
             "MATCH (f:Folder) RETURN f.filePath ORDER BY f.filePath",
             "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=GITNEXUS_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        md = parse_gitnexus_markdown(output)
        folders = [
            [c.strip() for c in l.split("|") if c.strip()][0]
            for l in [x.strip() for x in md.split("\n") if x.strip()][2:]
            if [c.strip() for c in l.split("|") if c.strip()]
        ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, IndexError):
        folders = []
    for area in communities:
        area_lower = area.lower().lstrip("_")
        for f in folders:
            if f.split("/")[-1].lower().lstrip("_") == area_lower:
                mapping[area] = f
                break
    return mapping


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

        # Append uncovered sub-dirs (e.g., core/ inside gmatrix_vbt_engine/)
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
        return
    try:
        if claude.stat().st_mtime >= agents.stat().st_mtime:
            agents.write_text(claude.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            claude.write_text(agents.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass


def update_subdir_docs(stale_dirs):
    """Update harness:start/end regions in sub-directory docs via AI CLI."""
    dirs_with_docs = [d for d in stale_dirs
                      if Path(d, "CLAUDE.md").exists() or Path(d, "AGENTS.md").exists()]
    if not dirs_with_docs:
        return []

    project_name = Path(".").resolve().name
    prompt = (
        f"你在项目 {project_name} 中。以下 {len(dirs_with_docs)} 个目录的代码发生了较大变动，"
        f"需要更新其 CLAUDE.md 和 AGENTS.md 中 <!-- harness:start --> 到 <!-- harness:end --> 之间的内容。\n\n"
        f"规则：\n"
        f"1. 对每个目录，调用 gitnexus_context 查询其核心函数\n"
        f"2. 基于 GitNexus 返回的事实更新约束和危险操作\n"
        f"3. 只改 harness:start/end 之间的内容，其他部分不动\n\n"
        f"目录：{', '.join(dirs_with_docs)}"
    )

    result = ai_invoke(prompt, timeout=60)
    if result:
        for d in dirs_with_docs:
            sync_platform_docs(d)
    return dirs_with_docs if result else []


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


def ensure_gitnexus_fresh():
    """If GitNexus is indexed but stale, run incremental analyze first."""
    if not Path(".gitnexus").is_dir():
        return
    try:
        r = subprocess.run(["npx", "gitnexus", "status"],
                           capture_output=True, text=True, timeout=5)
        output = (r.stdout + r.stderr).lower()
        if "stale" in output:
            subprocess.run(["npx", "gitnexus", "analyze"],
                           capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


def do_main_branch_update(project_id):
    """Heavy work: reindex → CODE_MAP → descriptions → sub-dir docs.

    Runs as a background process (spawned by handle_main_branch_update).
    No timeout pressure — takes as long as it needs.
    """
    if not acquire_lock(project_id):
        return
    try:
        _do_main_branch_update_inner()
    finally:
        release_lock(project_id)


def _do_main_branch_update_inner():
    # Step 0: Ensure GitNexus index is fresh before reading community data
    ensure_gitnexus_fresh()

    codemap_file = Path("CODE_MAP.md")
    old_content = codemap_file.read_text(encoding="utf-8") if codemap_file.exists() else ""
    existing_descs, old_counts = parse_existing_codemap(codemap_file)

    # Step 1: Update CODE_MAP.md structure (now with fresh index)
    communities = get_gitnexus_communities()
    if communities:
        new_content, stale_dirs = build_codemap_structure(communities, existing_descs, old_counts)
    else:
        return

    if new_content == old_content and not stale_dirs:
        return

    codemap_file.write_text(new_content, encoding="utf-8")

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
            subprocess.run([sys.executable, str(desc_script), ".", "--refresh"],
                           capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Step 3: Update sub-directory CLAUDE.md/AGENTS.md for stale dirs
    if stale_dirs:
        update_subdir_docs(stale_dirs)

    # Step 4: Sync root CLAUDE.md ↔ AGENTS.md if both exist
    sync_platform_docs(".")


def handle_main_branch_update(project_id):
    """Spawn background process for heavy update work. Returns immediately."""
    lock_file = LOCK_DIR / f"{project_id}.lock"
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, 0)
                return
            except OSError:
                pass
        except (ValueError, OSError):
            pass

    project_dir = str(Path(".").resolve())
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--bg", project_id, project_dir],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(json.dumps({
        "status": "background",
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
    state["file_count"] = current_count

    if current_count - prev_count < CHECK_EVERY_N_FILES:
        save_state(state_file, state)
        return

    if not DIAG_SCRIPT.exists():
        save_state(state_file, state)
        return

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
        # Background mode: harness_monitor.py --bg <project_id> <project_dir>
        os.chdir(sys.argv[3])
        do_main_branch_update(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "--bg-growth":
        # Background growth check: --bg-growth <state_file> <project_dir>
        do_growth_check(sys.argv[2], sys.argv[3])
    else:
        main()
