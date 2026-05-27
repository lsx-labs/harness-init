#!/usr/bin/env python3
"""Harness monitor — CODE_MAP.md updates + project growth detection.

Triggered via PostToolUse [Bash]:
  1. Update CODE_MAP.md (GitNexus or docstring fallback) + detect stale descriptions
  2. Check project growth (file count delta) + periodic diagnostic (GitNexus/LSP recommendations)

State persisted per-project in ~/.local/share/harness-hooks/counters/{project}.json
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ── Config ──

CHECK_EVERY_N_FILES = 20
STALE_THRESHOLD = 0.2
COUNTER_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "counters"
STALE_DIR = Path.home() / ".local" / "share" / "harness-hooks" / "stale-pending"
DIAG_SCRIPT = Path.home() / ".local" / "bin" / "harness-init.sh"
HOOK_TIMEOUT = 15  # seconds — subprocess timeouts within the hook must be less than hook's own timeout

SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".c", ".h", ".cpp", ".cs", ".swift", ".php"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".gitnexus", ".claude", ".codex",
             "dist", "build", "vendor", "third_party", "sdk", ".worktrees", ".tox"}


def should_skip(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info") or (name.startswith(".") and name != ".")


def has_source(d: Path) -> bool:
    try:
        return any(d.rglob(f"*{e}") for e in SOURCE_EXTS)
    except Exception:
        return False


# ── Project identity ──

def get_project_id() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return r.stdout.strip().replace("/", "_").lstrip("_")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def get_stale_track_file(project_id: str) -> Path:
    return STALE_DIR / f"{project_id}.json"


# ── State management ──

def load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "file_count": 0, "last_check_count": 0,
        "gitnexus_recommended": False, "lsp_recommended": [],
        "retired": False, "last_symbol_counts": {},
    }


def save_state(state_file: Path, state: dict):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))


# ══════════════════════════════════════════════════════════
# CODE_MAP.md update
# ══════════════════════════════════════════════════════════

def _extract_desc_and_count(text: str) -> tuple[str, int | None]:
    """Parse description and symbol count, tolerating both orders."""
    desc, count = "", None
    cm = re.search(r'\((\d+)\s*symbols?\)', text)
    if cm:
        count = int(cm.group(1))
        text = text[:cm.start()] + text[cm.end():]
    dm = re.search(r'—\s*(.+)', text)
    if dm:
        desc = dm.group(1).strip()
    return desc, count


def parse_existing_codemap(codemap_path: Path) -> tuple[dict[str, str], dict[str, int]]:
    descs, counts = {}, {}
    if not codemap_path.exists():
        return descs, counts
    current_section = ""
    for line in codemap_path.read_text().split("\n"):
        m = re.match(r'^###\s+(\S+)/?(.*)$', line)
        if m:
            current_section = m.group(1).rstrip("/")
            desc, count = _extract_desc_and_count(m.group(2))
            if desc and not desc.startswith("⚠️"):
                descs[current_section] = desc
            if count is not None:
                counts[current_section] = count
            continue
        m = re.match(r'^-\s+\*\*(\S+)/?\*\*(.*)$', line)
        if m:
            sub = f"{current_section}/{m.group(1)}"
            desc, count = _extract_desc_and_count(m.group(2))
            if desc and not desc.startswith("⚠️"):
                descs[sub] = desc
            if count is not None:
                counts[sub] = count
    return descs, counts


def check_staleness(key, new_syms, old_counts, descs):
    if key not in descs or key not in old_counts:
        return None
    old = old_counts[key]
    if old == 0:
        return None
    change = abs(new_syms - old) / old
    if change >= STALE_THRESHOLD:
        return f"⚠️ 描述可能过期（符号数 {old}→{new_syms}，变化 {change:.0%}）请运行 /harness-init 更新"
    return None


def get_gitnexus_communities():
    if not Path(".gitnexus").is_dir():
        return None
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher",
             "MATCH (c:Community) WITH c.label AS area, sum(c.symbolCount) AS syms, "
             "count(*) AS clusters RETURN area, syms, clusters ORDER BY syms DESC LIMIT 25",
             "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        if r.returncode != 0:
            return None
        output = r.stdout.strip() or r.stderr.strip()
        if not output:
            return None
        md = json.loads(output).get("markdown", "")
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
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError):
        return None


def build_area_to_dir(communities):
    mapping = {}
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher",
             "MATCH (f:Folder) RETURN f.filePath ORDER BY f.filePath",
             "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        md = json.loads(output).get("markdown", "")
        folders = []
        for line in [l.strip() for l in md.split("\n") if l.strip()][2:]:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if cols:
                folders.append(cols[0])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        folders = []
    for area in communities:
        area_lower = area.lower().lstrip("_")
        for f in folders:
            if f.split("/")[-1].lower().lstrip("_") == area_lower:
                mapping[area] = f
                break
    return mapping


def build_gitnexus_codemap(communities, existing_descs, old_counts):
    area_to_dir = build_area_to_dir(communities)
    lines = ["# Code Map", "",
             "> Auto-generated structure from GitNexus knowledge graph. Descriptions are human-maintained.", ""]

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
        stale = check_staleness(top_dir, total_syms, old_counts, existing_descs)
        if stale:
            lines.append(f"### {top_dir}/ ({total_syms} symbols) — {stale}")
        elif desc:
            lines.append(f"### {top_dir}/ ({total_syms} symbols) — {desc}")
        else:
            lines.append(f"### {top_dir}/ ({total_syms} symbols)")
        for sub, syms, area in sorted(entries, key=lambda x: -x[1]):
            if sub:
                sub_key = f"{top_dir}/{sub}"
                sub_desc = existing_descs.get(sub_key, "")
                sub_stale = check_staleness(sub_key, syms, old_counts, existing_descs)
                if sub_stale:
                    lines.append(f"- **{sub}/** — {sub_stale} ({syms} symbols)")
                elif sub_desc:
                    lines.append(f"- **{sub}/** — {sub_desc} ({syms} symbols)")
                else:
                    lines.append(f"- **{sub}/** ({syms} symbols)")
        lines.append("")
    return "\n".join(lines) + "\n"


def extract_doc(fp):
    try:
        src = fp.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    ext = fp.suffix.lower()
    if ext == ".py":
        try:
            ds = ast.get_docstring(ast.parse(src))
            if ds:
                line = ds.strip().split("\n")[0]
                for sep in ("—", "–", "-"):
                    if sep in line:
                        line = line.split(sep, 1)[1].strip()
                        break
                return line[:100]
        except SyntaxError:
            pass
    elif ext in (".js", ".ts", ".jsx", ".tsx", ".java", ".kt"):
        m = re.search(r'/\*\*\s*\n?\s*\*?\s*(.+?)[\n*]', src[:2000])
        if m:
            return m.group(1).strip().rstrip("*/").strip()[:100]
    elif ext == ".go":
        ls = src.split("\n")
        for i, l in enumerate(ls):
            if l.strip().startswith("package "):
                for j in range(max(0, i - 5), i):
                    cl = ls[j].strip()
                    if cl.startswith("//") and not cl.startswith("//go:"):
                        return cl.lstrip("/").strip()[:100]
                break
    elif ext == ".rs":
        for l in src.split("\n")[:10]:
            if l.strip().startswith("//!"):
                return l.strip().lstrip("/!").strip()[:100]
    return ""


def build_docstring_codemap(existing_descs):
    root = Path(".")
    lines = ["# Code Map", ""]
    for d in sorted(root.iterdir()):
        if not d.is_dir() or should_skip(d.name) or not has_source(d):
            continue
        desc = existing_descs.get(d.name, "")
        if not desc:
            for entry in ("__init__.py", "index.ts", "index.js", "mod.rs", "lib.rs"):
                f = d / entry
                if f.exists():
                    desc = extract_doc(f)
                    if desc:
                        break
        lines.append(f"### {d.name}/" + (f" — {desc}" if desc else ""))
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and not should_skip(sub.name) and not sub.name.startswith("_"):
                sub_key = f"{d.name}/{sub.name}"
                sub_desc = existing_descs.get(sub_key, "")
                if not sub_desc:
                    for entry in ("__init__.py", "index.ts", "index.js", "mod.rs", "lib.rs"):
                        f = sub / entry
                        if f.exists():
                            sub_desc = extract_doc(f)
                            if sub_desc:
                                break
                lines.append(f"  - **{sub.name}/**" + (f" — {sub_desc}" if sub_desc else ""))
        lines.append("")
    return "\n".join(lines) + "\n"


def handle_codemap_update(project_id: str):
    """Update CODE_MAP.md — called on Bash triggers."""
    codemap_file = Path("CODE_MAP.md")
    stale_track = get_stale_track_file(project_id)

    # Fallback check: previous stale markers not resolved → escalate
    if stale_track.exists() and codemap_file.exists():
        try:
            pending = json.loads(stale_track.read_text())
            codemap_text = codemap_file.read_text()
            still_stale = [d for d in pending.get("dirs", [])
                           if any("⚠️ 描述可能过期" in l for l in codemap_text.split("\n") if d in l)]
            if still_stale:
                print(json.dumps({
                    "decision": "warn",
                    "reason": (
                        f"⚠️ 以下目录的描述在上次标记后仍未更新：{', '.join(still_stale)}。"
                        f"请手动运行 /harness-init 更新这些目录的 CODE_MAP.md 描述"
                        + (f"和子目录 CLAUDE.md/AGENTS.md" if any(
                            Path(d, "CLAUDE.md").exists() for d in still_stale) else "")
                        + "。"
                    )
                }, ensure_ascii=False))
                stale_track.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            stale_track.unlink(missing_ok=True)

    existing_descs, old_counts = parse_existing_codemap(codemap_file)

    communities = get_gitnexus_communities()
    if communities:
        new_content = build_gitnexus_codemap(communities, existing_descs, old_counts)
        source = "gitnexus"
    else:
        new_content = build_docstring_codemap(existing_descs)
        source = "docstring"

    if not new_content.strip() or new_content.strip() == "# Code Map":
        return

    old_content = codemap_file.read_text() if codemap_file.exists() else ""

    stale_dirs = []
    for line in new_content.split("\n"):
        if "⚠️ 描述可能过期" in line:
            m = re.match(r'^(?:###\s+|.*\*\*)(\S+?)/?', line)
            if m:
                stale_dirs.append(m.group(1))

    if new_content != old_content:
        codemap_file.write_text(new_content)
        result = {"status": "codemap_updated", "source": source}
        if stale_dirs:
            result["stale_descriptions"] = stale_dirs
            stale_module_docs = [d for d in stale_dirs if
                                 Path(d, "CLAUDE.md").exists() or
                                 Path(d, "AGENTS.md").exists()]
            affected_files = ["CODE_MAP.md"]
            for d in stale_module_docs:
                affected_files.extend([f"{d}/CLAUDE.md", f"{d}/AGENTS.md"])
            result["affected_files"] = affected_files
            result["action"] = (
                f"由于当前功能变动较大，以下文件需要同步更新：{', '.join(affected_files)}。"
                f"请提交这些变更的文件：git add {' '.join(affected_files)} && "
                f'git commit -m "docs: update harness files"'
            )
            stale_track.parent.mkdir(parents=True, exist_ok=True)
            stale_track.write_text(json.dumps({"dirs": stale_dirs}))
        else:
            stale_track.unlink(missing_ok=True)
        print(json.dumps(result, ensure_ascii=False))


# ══════════════════════════════════════════════════════════
# Project growth detection (piggybacks on Bash triggers)
# ══════════════════════════════════════════════════════════

def run_diagnostic():
    if not DIAG_SCRIPT.exists():
        return None
    try:
        r = subprocess.run(["bash", str(DIAG_SCRIPT), "."],
                           capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    return None


def count_source_files() -> int:
    """Quick count of source files for growth detection."""
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
    """Check project growth on Bash triggers."""
    if state.get("retired"):
        return

    current_count = count_source_files()
    prev_count = state.get("file_count", 0)
    state["file_count"] = current_count

    if current_count - prev_count < CHECK_EVERY_N_FILES:
        save_state(state_file, state)
        return

    diag = run_diagnostic()
    if not diag:
        save_state(state_file, state)
        return

    state["last_check_count"] = state["file_count"]
    messages = []

    grep_noise = diag.get("grep_noise", {}).get("grep_noise_files", 0)
    most_imported = diag.get("grep_noise", {}).get("most_imported", "")
    gitnexus_indexed = diag.get("existing", {}).get("gitnexus", {}).get("indexed", False)

    if grep_noise > 20 and not gitnexus_indexed and not state.get("gitnexus_recommended"):
        state["gitnexus_recommended"] = True
        messages.append(
            f"📊 项目复杂度增长提醒：最热模块 `{most_imported}` 的 grep 噪声已达 {grep_noise} 个文件，"
            f"建议安装 GitNexus 知识图谱索引。运行 /harness-init 查看详情。")

    already_recommended = set(state.get("lsp_recommended", []))
    for assessment in diag.get("lsp_assessment", []):
        lang = assessment["language"]
        if assessment["recommend"] and lang not in already_recommended:
            already_recommended.add(lang)
            messages.append(f"📊 {lang} LSP 建议：{assessment['reason']}。运行 /harness-init 查看详情。")
    state["lsp_recommended"] = list(already_recommended)

    gitnexus_done = gitnexus_indexed or grep_noise <= 20
    lsp_needed = {a["language"] for a in diag.get("lsp_assessment", []) if a["recommend"]}
    if gitnexus_done and (lsp_needed.issubset(already_recommended) or not lsp_needed) and not messages:
        state["retired"] = True

    save_state(state_file, state)

    if messages:
        print(json.dumps({"decision": "warn", "reason": "\n".join(messages)}))


# ══════════════════════════════════════════════════════════
# Git state detection
# ══════════════════════════════════════════════════════════

GIT_COMMANDS = re.compile(r'\bgit\s+(commit|merge|rebase|pull|checkout|switch|cherry-pick)\b')
MAIN_BRANCHES = {"main", "master"}


def is_git_operation(ctx: dict) -> bool:
    """Check if the Bash command was a git operation."""
    cmd = ctx.get("tool_input", {}).get("command", "")
    return bool(GIT_COMMANDS.search(cmd))


def is_on_main_branch() -> bool:
    """Check if current HEAD is on main/master branch."""
    try:
        r = subprocess.run(["git", "branch", "--show-current"],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and r.stdout.strip() in MAIN_BRANCHES
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def is_merge_to_main(ctx: dict) -> bool:
    """Check if this is a merge/checkout to main branch."""
    if not is_git_operation(ctx):
        return False
    return is_on_main_branch()


# ══════════════════════════════════════════════════════════
# Main entry
# ══════════════════════════════════════════════════════════

def main():
    try:
        ctx = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    if not isinstance(ctx, dict) or "tool_name" not in ctx:
        return

    tool = ctx.get("tool_name", "")
    if tool != "Bash":
        return

    # Fast exit: not a git operation → skip entirely (zero overhead for pytest/profile/etc.)
    if not is_git_operation(ctx):
        return

    project_id = get_project_id()
    if not project_id:
        return

    state_file = COUNTER_DIR / f"{project_id}.json"
    state = load_state(state_file)

    if is_on_main_branch():
        # On main: full processing — update CODE_MAP.md + growth check
        handle_codemap_update(project_id)
        handle_growth_check(state, state_file)
    else:
        # Feature branch: growth check only (notify, never write files)
        handle_growth_check(state, state_file)


main()
