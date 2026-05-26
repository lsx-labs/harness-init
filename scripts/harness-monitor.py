#!/usr/bin/env python3
"""Unified harness monitor — CODE_MAP.md updates + project growth detection.

Two trigger modes via PostToolUse:
  Bash  → update CODE_MAP.md (GitNexus or docstring) + detect stale descriptions
  Write → increment file counter + periodic diagnostic (GitNexus/LSP recommendations)

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
DIAG_SCRIPT = Path.home() / ".local" / "bin" / "harness-init.sh"

SOURCE_EXTS = {".py",".ts",".tsx",".js",".jsx",".go",".rs",".java",".kt",".rb",".c",".cpp",".cs",".swift"}
SKIP_DIRS = {".git",".venv","venv","node_modules","__pycache__",".gitnexus",".claude",".codex",
             "dist","build","vendor","third_party","sdk",".worktrees",".tox","data","doc","docs"}

def should_skip(n): return n in SKIP_DIRS or n.endswith(".egg-info") or (n.startswith(".") and n != ".")
def has_source(d):
    try: return any(d.rglob(f"*{e}") for e in SOURCE_EXTS)
    except: return False


# ── Project identity ──

def get_project_id() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return r.stdout.strip().replace("/", "_").lstrip("_")
    except: pass
    return ""


# ── State management ──

def load_state(state_file: Path) -> dict:
    if state_file.exists():
        try: return json.loads(state_file.read_text())
        except: pass
    return {
        "file_count": 0, "last_check_count": 0,
        "gitnexus_recommended": False, "lsp_recommended": [],
        "retired": False, "last_symbol_counts": {},
    }

def save_state(state_file: Path, state: dict):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))


# ══════════════════════════════════════════════════════════
# CODE_MAP.md update (triggered by Bash)
# ══════════════════════════════════════════════════════════

def parse_existing_codemap(codemap_path: Path) -> tuple[dict[str, str], dict[str, int]]:
    descs, counts = {}, {}
    if not codemap_path.exists():
        return descs, counts
    current_section = ""
    for line in codemap_path.read_text().split("\n"):
        m = re.match(r'^###\s+(\S+?)/?(?:\s+\((\d+) symbols\))?\s*(?:—\s*(.+))?$', line)
        if m:
            current_section = m.group(1)
            if m.group(3):
                desc = m.group(3).strip()
                if not desc.startswith("⚠️"): descs[current_section] = desc
            if m.group(2): counts[current_section] = int(m.group(2))
            continue
        m = re.match(r'^-\s+\*\*(\S+?)/?(?:\*\*)\s*(?:—\s*(.+?))?\s*(?:\((\d+) symbols\))?$', line)
        if m:
            sub = f"{current_section}/{m.group(1)}"
            if m.group(2):
                desc = m.group(2).strip()
                if not desc.startswith("⚠️"): descs[sub] = desc
            if m.group(3): counts[sub] = int(m.group(3))
    return descs, counts


def check_staleness(key, new_syms, old_counts, descs):
    if key not in descs or key not in old_counts: return None
    old = old_counts[key]
    if old == 0: return None
    change = abs(new_syms - old) / old
    if change >= STALE_THRESHOLD:
        return f"⚠️ 描述可能过期（符号数 {old}→{new_syms}，变化 {change:.0%}）请运行 /harness-init 更新"
    return None


def get_gitnexus_communities():
    if not Path(".gitnexus").is_dir(): return None
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher",
             "MATCH (c:Community) WITH c.label AS area, sum(c.symbolCount) AS syms, "
             "count(*) AS clusters RETURN area, syms, clusters ORDER BY syms DESC LIMIT 25",
             "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=10)
        if r.returncode != 0: return None
        output = r.stdout.strip() or r.stderr.strip()
        if not output: return None
        md = json.loads(output).get("markdown", "")
        lines = [l.strip() for l in md.split("\n") if l.strip()]
        if len(lines) < 3: return None
        result = {}
        for line in lines[2:]:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 3 and cols[1].isdigit():
                area, syms, clusters = cols[0], int(cols[1]), int(cols[2])
                if area in result:
                    result[area]["symbols"] += syms; result[area]["clusters"] += clusters
                else:
                    result[area] = {"symbols": syms, "clusters": clusters}
        return result or None
    except: return None


def build_area_to_dir(communities):
    mapping = {}
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher",
             "MATCH (f:Folder) RETURN f.filePath ORDER BY f.filePath",
             "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=10)
        output = r.stdout.strip() or r.stderr.strip()
        md = json.loads(output).get("markdown", "")
        folders = []
        for line in [l.strip() for l in md.split("\n") if l.strip()][2:]:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if cols: folders.append(cols[0])
    except: folders = []
    for area in communities:
        area_lower = area.lower().lstrip("_")
        for f in folders:
            if f.split("/")[-1].lower().lstrip("_") == area_lower:
                mapping[area] = f; break
    return mapping


def build_gitnexus_codemap(communities, existing_descs, old_counts):
    area_to_dir = build_area_to_dir(communities)
    lines = ["# Code Map", "",
             "> Auto-generated structure from GitNexus knowledge graph. Descriptions are human-maintained.", ""]

    top_dirs = {}
    for area, info in sorted(communities.items(), key=lambda x: -x[1]["symbols"]):
        dir_path = area_to_dir.get(area)
        if not dir_path: continue
        parts = dir_path.split("/")
        top, sub = parts[0], "/".join(parts[1:]) if len(parts) > 1 else ""
        top_dirs.setdefault(top, []).append((sub, info["symbols"], area))

    for top_dir in sorted(top_dirs):
        entries = top_dirs[top_dir]
        total_syms = sum(e[1] for e in entries)
        desc = existing_descs.get(top_dir, "")
        stale = check_staleness(top_dir, total_syms, old_counts, existing_descs)
        if stale:       lines.append(f"### {top_dir}/ ({total_syms} symbols) — {stale}")
        elif desc:      lines.append(f"### {top_dir}/ ({total_syms} symbols) — {desc}")
        else:           lines.append(f"### {top_dir}/ ({total_syms} symbols)")
        for sub, syms, area in sorted(entries, key=lambda x: -x[1]):
            if sub:
                sub_key = f"{top_dir}/{sub}"
                sub_desc = existing_descs.get(sub_key, "")
                sub_stale = check_staleness(sub_key, syms, old_counts, existing_descs)
                if sub_stale:    lines.append(f"- **{sub}/** — {sub_stale} ({syms} symbols)")
                elif sub_desc:   lines.append(f"- **{sub}/** — {sub_desc} ({syms} symbols)")
                else:            lines.append(f"- **{sub}/** ({syms} symbols)")
        lines.append("")
    return "\n".join(lines) + "\n"


def extract_doc(fp):
    try: src = fp.read_text(encoding="utf-8", errors="ignore")
    except: return ""
    ext = fp.suffix.lower()
    if ext == ".py":
        try:
            ds = ast.get_docstring(ast.parse(src))
            if ds:
                line = ds.strip().split("\n")[0]
                for sep in ("—","–","-"):
                    if sep in line: line = line.split(sep,1)[1].strip(); break
                return line[:100]
        except: pass
    elif ext in (".js",".ts",".jsx",".tsx",".java",".kt"):
        m = re.search(r'/\*\*\s*\n?\s*\*?\s*(.+?)[\n*]', src[:2000])
        if m: return m.group(1).strip().rstrip("*/").strip()[:100]
    elif ext == ".go":
        ls = src.split("\n")
        for i,l in enumerate(ls):
            if l.strip().startswith("package "):
                for j in range(max(0,i-5),i):
                    cl = ls[j].strip()
                    if cl.startswith("//") and not cl.startswith("//go:"): return cl.lstrip("/").strip()[:100]
                break
    elif ext == ".rs":
        for l in src.split("\n")[:10]:
            if l.strip().startswith("//!"): return l.strip().lstrip("/!").strip()[:100]
    return ""


def build_docstring_codemap(existing_descs):
    root = Path(".")
    lines = ["# Code Map", ""]
    for d in sorted(root.iterdir()):
        if not d.is_dir() or should_skip(d.name) or not has_source(d): continue
        desc = existing_descs.get(d.name, "")
        if not desc:
            for entry in ("__init__.py","index.ts","index.js","mod.rs","lib.rs"):
                f = d / entry
                if f.exists():
                    desc = extract_doc(f)
                    if desc: break
        lines.append(f"### {d.name}/" + (f" — {desc}" if desc else ""))
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and not should_skip(sub.name) and not sub.name.startswith("_"):
                sub_key = f"{d.name}/{sub.name}"
                sub_desc = existing_descs.get(sub_key, "")
                if not sub_desc:
                    for entry in ("__init__.py","index.ts","index.js","mod.rs","lib.rs"):
                        f = sub / entry
                        if f.exists():
                            sub_desc = extract_doc(f)
                            if sub_desc: break
                lines.append(f"  - **{sub.name}/**" + (f" — {sub_desc}" if sub_desc else ""))
        lines.append("")
    return "\n".join(lines) + "\n"


def handle_codemap_update():
    """Update CODE_MAP.md — called on Bash triggers."""
    codemap_file = Path("CODE_MAP.md")
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
            if m: stale_dirs.append(m.group(1))

    if new_content != old_content:
        codemap_file.write_text(new_content)
        result = {"status": "codemap_updated", "source": source}
        if stale_dirs:
            result["stale_descriptions"] = stale_dirs
            # Check which stale dirs also have sub-directory CLAUDE.md/AGENTS.md
            stale_module_docs = [d for d in stale_dirs if
                                 Path(d.replace("/", os.sep), "CLAUDE.md").exists() or
                                 Path(d.replace("/", os.sep), "AGENTS.md").exists()]
            actions = []
            actions.append(
                f"CODE_MAP.md 中 {len(stale_dirs)} 个目录的描述可能过期：{', '.join(stale_dirs)}。"
                f"请用 subagent 读取这些目录的核心源文件，更新 CODE_MAP.md 中对应的一句话描述。"
            )
            if stale_module_docs:
                actions.append(
                    f"以下模块的 CLAUDE.md/AGENTS.md 也可能需要更新（同步过期）：{', '.join(stale_module_docs)}。"
                    f"请一并读取核心源文件，更新模块约束（测试命令/编码约束/危险操作）。两个文件内容保持一致。"
                )
            result["action"] = " ".join(actions)
        print(json.dumps(result, ensure_ascii=False))


# ══════════════════════════════════════════════════════════
# Project growth detection (triggered by Write)
# ══════════════════════════════════════════════════════════

def run_diagnostic():
    if not DIAG_SCRIPT.exists(): return None
    try:
        r = subprocess.run(["bash", str(DIAG_SCRIPT), "."],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except: pass
    return None


def handle_file_growth(ctx, state, state_file):
    """Track new file creation, periodic diagnostic — called on Write triggers."""
    file_path = ctx.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    if state.get("retired"):
        return

    state["file_count"] += 1
    files_since_check = state["file_count"] - state["last_check_count"]

    if files_since_check < CHECK_EVERY_N_FILES:
        save_state(state_file, state)
        return

    diag = run_diagnostic()
    if not diag:
        save_state(state_file, state)
        return

    state["last_check_count"] = state["file_count"]
    messages = []

    # Check GitNexus
    grep_noise = diag.get("grep_noise", {}).get("grep_noise_files", 0)
    most_imported = diag.get("grep_noise", {}).get("most_imported", "")
    gitnexus_indexed = diag.get("existing", {}).get("gitnexus", {}).get("indexed", False)

    if grep_noise > 20 and not gitnexus_indexed and not state.get("gitnexus_recommended"):
        state["gitnexus_recommended"] = True
        messages.append(
            f"📊 项目复杂度增长提醒：最热模块 `{most_imported}` 的 grep 噪声已达 {grep_noise} 个文件，"
            f"建议安装 GitNexus 知识图谱索引。运行 /harness-init 查看详情。")

    # Check LSP
    already_recommended = set(state.get("lsp_recommended", []))
    for assessment in diag.get("lsp_assessment", []):
        lang = assessment["language"]
        if assessment["recommend"] and lang not in already_recommended:
            already_recommended.add(lang)
            messages.append(f"📊 {lang} LSP 建议：{assessment['reason']}。运行 /harness-init 查看详情。")
    state["lsp_recommended"] = list(already_recommended)

    # Retire check
    gitnexus_done = gitnexus_indexed or grep_noise <= 20
    lsp_needed = {a["language"] for a in diag.get("lsp_assessment", []) if a["recommend"]}
    if gitnexus_done and (lsp_needed.issubset(already_recommended) or not lsp_needed) and not messages:
        state["retired"] = True

    save_state(state_file, state)

    if messages:
        print(json.dumps({"decision": "warn", "reason": "\n".join(messages)}))


# ══════════════════════════════════════════════════════════
# Main entry
# ══════════════════════════════════════════════════════════

def main():
    try:
        ctx = json.load(sys.stdin)
    except:
        return

    tool = ctx.get("tool_name", "")

    project_id = get_project_id()
    if not project_id:
        return

    state_file = COUNTER_DIR / f"{project_id}.json"
    state = load_state(state_file)

    if tool in ("Bash",):
        handle_codemap_update()

    elif tool in ("Write",):
        handle_file_growth(ctx, state, state_file)


main()
