#!/usr/bin/env bash
# Generate semantic descriptions for CODE_MAP.md entries that are missing descriptions.
# Uses GitNexus data (top symbols + execution flows) to produce factual one-line summaries.
# Output: JSON array of {dir, symbols, top_functions, flows, suggested_desc}

set -euo pipefail

PROJECT_DIR="${1:-.}"
cd "$PROJECT_DIR"

python3 - << 'PYEOF'
import json, re, subprocess, sys
from pathlib import Path

HOOK_TIMEOUT = 10

def gitnexus_query(cypher_query):
    """Run a GitNexus Cypher query, return parsed markdown table rows."""
    try:
        r = subprocess.run(
            ["npx", "gitnexus", "cypher", cypher_query, "-r", Path(".").resolve().name],
            capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        if not output: return []
        md = json.loads(output).get("markdown", "")
        lines = [l.strip() for l in md.split("\n") if l.strip()]
        if len(lines) < 3: return []
        rows = []
        for line in lines[2:]:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if cols: rows.append(cols)
        return rows
    except Exception:
        return []


def get_top_functions(dir_path, limit=5):
    """Get the most-referenced functions in a directory."""
    rows = gitnexus_query(
        f"MATCH (f:Function) WHERE f.filePath STARTS WITH '{dir_path}/' "
        f"OPTIONAL MATCH (caller)-[:CodeRelation {{type:'CALLS'}}]->(f) "
        f"WITH f, count(caller) AS refs "
        f"RETURN f.name, f.filePath, refs ORDER BY refs DESC LIMIT {limit}"
    )
    return [{"name": r[0], "file": r[1], "refs": r[2]} for r in rows if len(r) >= 3]


def get_execution_flows(dir_path, limit=3):
    """Get execution flows touching this directory."""
    rows = gitnexus_query(
        f"MATCH (p:Process)-[:CodeRelation]->(f:Function) "
        f"WHERE f.filePath STARTS WITH '{dir_path}/' "
        f"WITH DISTINCT p LIMIT {limit} "
        f"RETURN p.label, p.id"
    )
    return [{"name": r[0], "id": r[1]} for r in rows if len(r) >= 2]


def parse_codemap():
    """Parse CODE_MAP.md, find entries missing descriptions."""
    codemap = Path("CODE_MAP.md")
    if not codemap.exists():
        return []
    
    entries = []
    current = ""
    for line in codemap.read_text().split("\n"):
        # ### dir/ (N symbols) — desc  OR  ### dir/ (N symbols)
        m = re.match(r'^###\s+(\S+)/?(?:\s+\((\d+)\s*symbols?\))?\s*(?:—\s*(.+))?$', line)
        if m:
            current = m.group(1).rstrip("/")
            has_desc = bool(m.group(3) and not m.group(3).startswith("⚠️"))
            count = int(m.group(2)) if m.group(2) else 0
            if not has_desc:
                entries.append({"dir": current, "symbols": count, "level": "top"})
            continue
        m = re.match(r'^-\s+\*\*(\S+)/?\*\*(?:\s*—\s*(.+?))?\s*(?:\((\d+)\s*symbols?\))?$', line)
        if m:
            sub = f"{current}/{m.group(1).rstrip('/')}"
            has_desc = bool(m.group(2) and not m.group(2).startswith("⚠️"))
            count = int(m.group(3)) if m.group(3) else 0
            if not has_desc:
                entries.append({"dir": sub, "symbols": count, "level": "sub"})
    return entries


# Main
entries = parse_codemap()

if not entries:
    print(json.dumps({"status": "all_described", "message": "所有条目已有描述"}, ensure_ascii=False))
    sys.exit(0)

results = []
for entry in entries:
    d = entry["dir"]
    
    # Get top functions from GitNexus
    top_funcs = get_top_functions(d)
    flows = get_execution_flows(d)
    
    # Also read __init__.py or main entry docstring
    docstring = ""
    for fname in ("__init__.py", "index.ts", "index.js", "mod.rs"):
        fpath = Path(d) / fname
        if fpath.exists():
            try:
                import ast
                tree = ast.parse(fpath.read_text())
                ds = ast.get_docstring(tree)
                if ds:
                    docstring = ds.strip().split("\n")[0][:100]
            except Exception:
                pass
            break
    
    results.append({
        "dir": d,
        "symbols": entry["symbols"],
        "top_functions": top_funcs,
        "flows": flows,
        "docstring": docstring,
    })

print(json.dumps({"status": "needs_descriptions", "entries": results}, indent=2, ensure_ascii=False))
PYEOF
