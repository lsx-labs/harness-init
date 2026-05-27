#!/usr/bin/env bash
# Generate CODE_MAP.md descriptions: AI + GitNexus (primary) / keywords (fallback).
#
# Modes:
#   --generate  fill empty entries only (default)
#   --refresh   regenerate all (except 📌 manual overrides)
#   --dry-run   show what would change

set -euo pipefail

PROJECT_DIR="${1:-.}"
MODE="${2:---generate}"
cd "$PROJECT_DIR"

# Step 1: Find directories needing descriptions
DIRS_JSON=$(python3 - "$MODE" << 'PYEOF'
import json, re, sys
from pathlib import Path

MODE = sys.argv[1]

def _extract_desc(text):
    dm = re.search(r'—\s*(.+)', text)
    return dm.group(1).strip() if dm else ""

codemap = Path("CODE_MAP.md")
if not codemap.exists():
    print(json.dumps([])); sys.exit(0)

dirs = []
current = ""
for line in codemap.read_text().split("\n"):
    m = re.match(r'^###\s+(\S+)/?(.*)$', line)
    if m:
        current = m.group(1).rstrip("/")
        desc = _extract_desc(m.group(2))
        if desc.startswith("📌"): continue
        if MODE == "--generate" and desc and not desc.startswith("⚠️"): continue
        dirs.append(current)
        continue
    m = re.match(r'^-\s+\*\*(\S+)/?\*\*(.*)$', line)
    if m:
        sub = f"{current}/{m.group(1).rstrip('/')}"
        desc = _extract_desc(m.group(2))
        if desc.startswith("📌"): continue
        if MODE == "--generate" and desc and not desc.startswith("⚠️"): continue
        dirs.append(sub)

print(json.dumps(dirs))
PYEOF
)

DIR_LIST=$(echo "$DIRS_JSON" | python3 -c "import json,sys; dirs=json.load(sys.stdin); print(' '.join(dirs))")
DIR_COUNT=$(echo "$DIRS_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")

if [ "$DIR_COUNT" = "0" ]; then
    echo '{"status": "all_described"}'
    exit 0
fi

if [ "$MODE" = "--dry-run" ]; then
    echo "{\"status\": \"dry_run\", \"dirs_needing\": $DIRS_JSON}"
    exit 0
fi

# Step 2: Try AI + GitNexus (primary path)
AI_CMD=""
if command -v claude &>/dev/null 2>&1; then
    AI_CMD="claude"
elif [ -x "/Applications/Codex.app/Contents/Resources/codex" ]; then
    AI_CMD="/Applications/Codex.app/Contents/Resources/codex"
elif command -v codex &>/dev/null 2>&1; then
    AI_CMD="codex"
fi

if [ -n "$AI_CMD" ] && [ -d ".gitnexus" ]; then
    PROMPT="你在项目 $(basename "$PWD") 中。为以下 $DIR_COUNT 个目录生成 CODE_MAP.md 导航描述。

规则：
1. 对每个目录，调用 gitnexus_context 查询其核心函数（被引用最多的），了解调用关系
2. 只基于 GitNexus 返回的数据写描述，不自行推测
3. 每个描述中文 ≤ 30 字，格式：核心职责 + 2-3 个关键功能词
4. 只输出纯 JSON，无 markdown 包裹，格式：{\"目录名\": \"描述\"}

目录：$DIR_LIST"

    RESULT=""
    if [ "$AI_CMD" = "claude" ]; then
        RESULT=$(timeout 15 claude -p "$PROMPT" --output-format stream-json 2>/dev/null | \
                 python3 -c "
import json, sys
text = ''
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        if d.get('type') == 'text':
            text += d.get('content', '')
    except: pass
print(text)
" 2>/dev/null || echo "")
    else
        RESULT=$($AI_CMD exec "$PROMPT" 2>/dev/null || echo "")
    fi

    if [ -n "$RESULT" ]; then
        # Parse AI output and write to CODE_MAP.md
        python3 - "$RESULT" << 'PYEOF'
import json, re, sys
from pathlib import Path

raw = sys.argv[1]
json_match = re.search(r'\{[^{}]*("[\w/]+":\s*"[^"]*"[,\s]*)+\}', raw, re.DOTALL)
if not json_match:
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
if not json_match:
    print(json.dumps({"status": "ai_parse_error"}, ensure_ascii=False))
    sys.exit(0)

try:
    descriptions = json.loads(json_match.group())
except json.JSONDecodeError:
    print(json.dumps({"status": "ai_parse_error"}, ensure_ascii=False))
    sys.exit(0)

codemap = Path("CODE_MAP.md")
content = codemap.read_text()
changes = []

for dir_path, desc in descriptions.items():
    if not desc or not isinstance(desc, str): continue
    desc = desc.strip()[:60]

    # Top-level
    p = re.compile(rf'^(###\s+{re.escape(dir_path)}/\s+\(\d+\s+symbols\))(.*)$', re.MULTILINE)
    m = p.search(content)
    if m:
        content = content[:m.start()] + f"{m.group(1)} — {desc}" + content[m.end():]
        changes.append({"dir": dir_path, "desc": desc})
        continue

    # Sub-level
    sub_name = dir_path.split("/")[-1]
    p = re.compile(rf'^(-\s+\*\*{re.escape(sub_name)}/?\*\*)\s*(.*?)(\(\d+\s+symbols\))(.*)$', re.MULTILINE)
    m = p.search(content)
    if m:
        content = content[:m.start()] + f"{m.group(1)} — {desc} {m.group(3)}" + content[m.end():]
        changes.append({"dir": dir_path, "desc": desc})

if changes:
    codemap.write_text(content)

print(json.dumps({"status": "updated", "source": "ai+gitnexus", "count": len(changes), "changes": changes}, indent=2, ensure_ascii=False))
PYEOF
        exit 0
    fi
fi

# Step 3: Fallback — keyword extraction (no AI or no GitNexus)
# IMPORTANT: fallback only fills EMPTY descriptions, never overwrites existing ones.
# Only the AI path (Step 2) has permission to refresh existing descriptions.
# Re-parse with --generate mode to get only empty entries.
FALLBACK_DIRS=$(python3 - "--generate" << 'FBEOF'
import json, re, sys
from pathlib import Path
MODE = sys.argv[1]
def _extract_desc(text):
    dm = re.search(r'—\s*(.+)', text)
    return dm.group(1).strip() if dm else ""
codemap = Path("CODE_MAP.md")
if not codemap.exists(): print(json.dumps([])); sys.exit(0)
dirs, current = [], ""
for line in codemap.read_text().split("\n"):
    m = re.match(r'^###\s+(\S+)/?(.*)$', line)
    if m:
        current = m.group(1).rstrip("/")
        desc = _extract_desc(m.group(2))
        if not desc or desc.startswith("⚠️"): dirs.append(current)
        continue
    m = re.match(r'^-\s+\*\*(\S+)/?\*\*(.*)$', line)
    if m:
        sub = f"{current}/{m.group(1).rstrip('/')}"
        desc = _extract_desc(m.group(2))
        if not desc or desc.startswith("⚠️"): dirs.append(sub)
print(json.dumps(dirs))
FBEOF
)
python3 - "$FALLBACK_DIRS" << 'PYEOF'
import json, re, subprocess, sys, ast
from pathlib import Path

dirs = json.loads(sys.argv[1])
HOOK_TIMEOUT = 10
GENERIC = {"main","init","run","start","stop","get","set","test","setup","parse",
           "build","create","delete","update","load","save","read","write","open",
           "close","validate","check","add","all","data","config","path","name","type"}

def gn_query(q):
    try:
        r = subprocess.run(["npx","gitnexus","cypher",q,"-r",Path(".").resolve().name],
                           capture_output=True,text=True,timeout=HOOK_TIMEOUT)
        output = r.stdout.strip() or r.stderr.strip()
        if not output: return []
        md = json.loads(output).get("markdown","")
        lines = [l.strip() for l in md.split("\n") if l.strip()]
        return [[c.strip() for c in l.split("|") if c.strip()] for l in lines[2:]] if len(lines)>=3 else []
    except: return []

def get_desc(d):
    # Try docstring
    for f in ("__init__.py","index.ts","mod.rs"):
        p = Path(d)/f
        if p.exists():
            try:
                ds = ast.get_docstring(ast.parse(p.read_text()))
                if ds:
                    line = ds.strip().split("\n")[0]
                    for sep in ("—","–","-"):
                        if sep in line: line=line.split(sep,1)[1].strip(); break
                    return line[:60]
            except: pass
    # Try GitNexus keywords
    rows = gn_query(
        f"MATCH (f:Function) WHERE f.filePath STARTS WITH '{d}/' AND NOT f.name STARTS WITH '_' "
        f"OPTIONAL MATCH (c)-[:CodeRelation {{type:'CALLS'}}]->(f) WITH f, count(c) AS refs "
        f"WHERE refs > 0 RETURN f.name ORDER BY refs DESC LIMIT 4")
    kw = [r[0] for r in rows if r[0].lower() not in GENERIC and len(r[0])>3]
    return " / ".join(kw[:3]) if kw else ""

codemap = Path("CODE_MAP.md")
content = codemap.read_text()
changes = []

for d in dirs:
    desc = get_desc(d)
    if not desc: continue
    sub = d.split("/")[-1]
    p = re.compile(rf'^(###\s+{re.escape(d)}/\s+\(\d+\s+symbols\))(.*)$', re.MULTILINE)
    m = p.search(content)
    if m:
        content = content[:m.start()] + f"{m.group(1)} — {desc}" + content[m.end():]
        changes.append({"dir": d, "desc": desc})
        continue
    p = re.compile(rf'^(-\s+\*\*{re.escape(sub)}/?\*\*)\s*(.*?)(\(\d+\s+symbols\))(.*)$', re.MULTILINE)
    m = p.search(content)
    if m:
        content = content[:m.start()] + f"{m.group(1)} — {desc} {m.group(3)}" + content[m.end():]
        changes.append({"dir": d, "desc": desc})

if changes:
    codemap.write_text(content)

print(json.dumps({"status": "updated", "source": "fallback", "count": len(changes), "changes": changes}, indent=2, ensure_ascii=False))
PYEOF
