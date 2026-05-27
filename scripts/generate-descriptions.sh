#!/usr/bin/env bash
# Generate CODE_MAP.md descriptions deterministically from code data.
#
# Strategy:
#   1. Has __init__.py docstring → use it directly
#   2. No docstring → use "top_func1 / top_func2 / top_func3" from GitNexus
#
# Modes:
#   --generate  — produce descriptions for missing entries, write to CODE_MAP.md
#   --refresh   — regenerate ALL descriptions from current data, preserve only manual overrides
#   --dry-run   — show what would change without writing

set -euo pipefail

PROJECT_DIR="${1:-.}"
MODE="${2:---generate}"
cd "$PROJECT_DIR"

python3 - "$MODE" << 'PYEOF'
import json, re, subprocess, sys, ast
from pathlib import Path

MODE = sys.argv[1] if len(sys.argv) > 1 else "--generate"
HOOK_TIMEOUT = 10
MANUAL_MARKER = "📌"  # descriptions starting with this are manual overrides, never auto-replaced


def gitnexus_query(cypher_query):
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher", cypher_query, "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        if not output: return []
        md = json.loads(output).get("markdown", "")
        lines = [l.strip() for l in md.split("\n") if l.strip()]
        if len(lines) < 3: return []
        return [[c.strip() for c in line.split("|") if c.strip()] for line in lines[2:]]
    except Exception:
        return []


def get_top_functions(dir_path, limit=3):
    rows = gitnexus_query(
        f"MATCH (f:Function) WHERE f.filePath STARTS WITH '{dir_path}/' "
        f"AND NOT f.name STARTS WITH '_' AND f.name <> 'main' "
        f"OPTIONAL MATCH (caller)-[:CodeRelation {{type:'CALLS'}}]->(f) "
        f"WITH f, count(caller) AS refs "
        f"RETURN f.name, refs ORDER BY refs DESC LIMIT {limit}")
    return [{"name": r[0], "refs": int(r[1])} for r in rows if len(r) >= 2]


def get_docstring(dir_path):
    for fname in ("__init__.py", "index.ts", "index.js", "mod.rs", "lib.rs"):
        fpath = Path(dir_path) / fname
        if fpath.exists():
            try:
                src = fpath.read_text(encoding="utf-8", errors="ignore")
                if fname.endswith(".py"):
                    tree = ast.parse(src)
                    ds = ast.get_docstring(tree)
                    if ds:
                        first_line = ds.strip().split("\n")[0]
                        # Remove filepath prefix pattern "module/file.py — desc"
                        for sep in ("—", "–", "-"):
                            if sep in first_line:
                                first_line = first_line.split(sep, 1)[1].strip()
                                break
                        return first_line[:80]
            except Exception:
                pass
    return ""


def generate_description(dir_path):
    """Generate a description from docstring or top functions. Deterministic, no AI."""
    # Priority 1: docstring
    ds = get_docstring(dir_path)
    if ds:
        return ds

    # Priority 2: top functions from GitNexus
    funcs = get_top_functions(dir_path)
    if funcs:
        return " / ".join(f"{f['name']}({f['refs']})" for f in funcs)

    # Priority 3: nothing
    return ""


def _extract_desc_and_count(text):
    desc, count = "", None
    cm = re.search(r'\((\d+)\s*symbols?\)', text)
    if cm:
        count = int(cm.group(1))
        text = text[:cm.start()] + text[cm.end():]
    dm = re.search(r'—\s*(.+)', text)
    if dm:
        desc = dm.group(1).strip()
    return desc, count


def parse_codemap():
    codemap = Path("CODE_MAP.md")
    if not codemap.exists(): return [], ""
    content = codemap.read_text()
    entries = []
    current = ""
    for line in content.split("\n"):
        m = re.match(r'^###\s+(\S+)/?(.*)$', line)
        if m:
            current = m.group(1).rstrip("/")
            desc, count = _extract_desc_and_count(m.group(2))
            entries.append({"dir": current, "symbols": count or 0, "desc": desc, "level": "top", "line": line})
            continue
        m = re.match(r'^-\s+\*\*(\S+)/?\*\*(.*)$', line)
        if m:
            sub = f"{current}/{m.group(1).rstrip('/')}"
            desc, count = _extract_desc_and_count(m.group(2))
            entries.append({"dir": sub, "symbols": count or 0, "desc": desc, "level": "sub", "line": line})
    return entries, content


def write_codemap(entries, original_content):
    """Rewrite CODE_MAP.md with updated descriptions."""
    content = original_content
    for entry in entries:
        if "new_line" in entry and entry["new_line"] != entry["line"]:
            content = content.replace(entry["line"], entry["new_line"])
    Path("CODE_MAP.md").write_text(content)


entries, original = parse_codemap()
if not entries:
    print(json.dumps({"status": "no_codemap"}, ensure_ascii=False))
    sys.exit(0)

changes = []
for entry in entries:
    old_desc = entry["desc"]

    # Skip manual overrides (marked with 📌)
    if old_desc.startswith(MANUAL_MARKER):
        continue

    # --generate: only fill empty descriptions
    if MODE == "--generate" and old_desc and not old_desc.startswith("⚠️"):
        continue

    # Generate new description
    new_desc = generate_description(entry["dir"])
    if not new_desc:
        continue

    # Build new line
    if entry["level"] == "top":
        new_line = f"### {entry['dir']}/ ({entry['symbols']} symbols) — {new_desc}"
    else:
        new_line = f"- **{entry['dir'].split('/')[-1]}/** — {new_desc} ({entry['symbols']} symbols)"

    if new_line != entry["line"]:
        entry["new_line"] = new_line
        changes.append({
            "dir": entry["dir"],
            "old": old_desc or "(空)",
            "new": new_desc,
        })

if MODE == "--dry-run" or not changes:
    status = "no_changes" if not changes else "dry_run"
    print(json.dumps({"status": status, "changes": changes}, indent=2, ensure_ascii=False))
else:
    write_codemap(entries, original)
    print(json.dumps({
        "status": "updated",
        "count": len(changes),
        "changes": changes
    }, indent=2, ensure_ascii=False))
PYEOF
