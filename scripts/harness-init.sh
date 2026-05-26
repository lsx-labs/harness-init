#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="${1:-.}"
cd "$PROJECT_DIR"

# Resolve script's own location for VERSION lookup
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

python3 - "$SCRIPT_DIR" << 'PYEOF'
import os, re, json, subprocess, sys
from pathlib import Path
from collections import Counter, defaultdict

SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__',
             '.gitnexus', '.claude', '.codex', 'dist', 'build',
             'vendor', 'third_party', 'sdk', '.tox', '.worktrees'}
TEST_DIRS = {'tests', 'test'}
GENERIC_NAMES = {'base', 'utils', 'helpers', 'common', 'core', 'main', 'config',
                 'settings', 'models', 'views', 'loader', 'registry', 'pipeline',
                 'types', 'constants', 'errors', 'exceptions', 'index', 'app', 'run',
                 'init', 'setup', 'cli', 'api', 'server', 'client', 'manager'}
STDLIB = {'os','sys','json','pathlib','re','typing','collections','dataclasses',
          'datetime','time','subprocess','threading','unittest','pytest','concurrent',
          '__future__','enum','abc','functools','contextlib','copy','io','math',
          'hashlib','shutil','glob','tempfile','textwrap','itertools','operator',
          'inspect','importlib','warnings','logging','argparse','traceback','signal'}

LANG_MAP = {
    '.py': 'Python', '.ts': 'TypeScript', '.tsx': 'TypeScript',
    '.js': 'JavaScript', '.jsx': 'JavaScript',
    '.go': 'Go', '.rs': 'Rust', '.java': 'Java', '.kt': 'Kotlin',
    '.rb': 'Ruby', '.c': 'C', '.cpp': 'C++', '.h': 'C',
    '.cs': 'C#', '.swift': 'Swift', '.php': 'PHP',
}
STRONG_TYPED = {'TypeScript', 'Go', 'Rust', 'Java', 'Kotlin', 'C#', 'Swift'}
WEAK_TYPED = {'JavaScript', 'Ruby', 'PHP'}

def should_skip(d):
    return d in SKIP_DIRS or (d.startswith('.') and d != '.')

# ── 1. Language distribution ──
lang_stats = defaultdict(lambda: {'files': 0, 'lines': 0})
import_counter = Counter()

for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if not should_skip(d)]
    in_test = any(t in root.split(os.sep) for t in TEST_DIRS)
    for f in files:
        ext = Path(f).suffix.lower()
        if ext not in LANG_MAP:
            continue
        lang = LANG_MAP[ext]
        filepath = os.path.join(root, f)
        try:
            content = open(filepath, encoding='utf-8', errors='ignore').read()
        except OSError:
            continue
        lang_stats[lang]['files'] += 1
        lang_stats[lang]['lines'] += content.count('\n') + 1

        if ext == '.py' and not in_test:
            for m in re.finditer(r'from\s+([\w.]+)\s+import', content):
                full = m.group(1)
                if '.' in full:
                    leaf = full.rsplit('.', 1)[-1]
                    if leaf not in STDLIB and leaf not in GENERIC_NAMES and len(leaf) > 3:
                        import_counter[leaf] += 1

total_lines = sum(v['lines'] for v in lang_stats.values())
languages = []
for lang, v in sorted(lang_stats.items(), key=lambda x: -x[1]['lines']):
    pct = round(v['lines'] / total_lines * 100, 1) if total_lines > 0 else 0
    if pct < 3 and v['files'] < 5:
        continue
    languages.append({'language': lang, 'files': v['files'], 'lines': v['lines'], 'percent': pct})

# ── 2. Grep noise ──
grep_noise = {"most_imported": "", "grep_noise_files": 0, "top5": []}
if import_counter:
    top5 = import_counter.most_common(5)
    most = top5[0][0]
    try:
        r = subprocess.run(
            ['grep', '-rl', most, '.', '--include=*.py',
             '--exclude-dir=__pycache__', '--exclude-dir=tests', '--exclude-dir=test',
             '--exclude-dir=.git', '--exclude-dir=.venv', '--exclude-dir=.gitnexus',
             '--exclude-dir=.worktrees', '--exclude-dir=build', '--exclude-dir=dist'],
            capture_output=True, text=True, timeout=10
        )
        count = len([l for l in r.stdout.strip().split('\n') if l])
    except (subprocess.TimeoutExpired, OSError):
        count = -1
    grep_noise = {
        "most_imported": most, "grep_noise_files": count,
        "top5": [{"module": m, "imports": c} for m, c in top5]
    }

# ── 3. Type coverage (Python) ──
type_coverage = {"typed_funcs": 0, "total_funcs": 0, "coverage": 0}
if any(l['language'] == 'Python' for l in languages):
    typed = total = 0
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if not should_skip(d)]
        for f in files:
            if not f.endswith('.py'): continue
            try:
                c = open(os.path.join(root, f), encoding='utf-8', errors='ignore').read()
                total += len(re.findall(r'\bdef\s+', c))
                typed += len(re.findall(r'\bdef\s+\w+\s*\([^)]*\)\s*->', c))
            except OSError: pass
    type_coverage = {"typed_funcs": typed, "total_funcs": total,
                     "coverage": round(typed/total*100, 1) if total else 0}

# ── 4. Existing harness state ──
existing = {}
for name, path in [('claude_md', 'CLAUDE.md'), ('agents_md', 'AGENTS.md')]:
    p = Path(path)
    txt = p.read_text() if p.exists() else ''
    existing[name] = {
        'exists': p.exists(),
        'has_codemap': '@CODE_MAP.md' in txt or '<!-- codemap:start -->' in txt,
        'has_gitnexus': '<!-- gitnexus:start -->' in txt,
    }

existing['gitnexus'] = {
    'indexed': Path('.gitnexus').is_dir(),
    'in_gitignore': '.gitnexus' in (Path('.gitignore').read_text() if Path('.gitignore').exists() else ''),
}

# GitNexus hook script reachability
gitnexus_hook_path = Path.home() / '.claude' / 'hooks' / 'gitnexus' / 'gitnexus-hook.cjs'
existing['gitnexus_hook_reachable'] = gitnexus_hook_path.exists()

def check_hooks(path, keys):
    try:
        hooks = json.loads(Path(path).read_text()).get('hooks', {})
        return {k: any(v in h.get('command','') for items in hooks.values()
                       for item in items for h in item.get('hooks',[]))
                for k, v in keys.items()}
    except (json.JSONDecodeError, OSError, KeyError):
        return {k: False for k in keys}

existing['hooks_claude'] = check_hooks(
    Path.home()/'.claude'/'settings.json',
    {'gitnexus': 'gitnexus', 'harness_monitor': 'harness-monitor'})
existing['hooks_codex'] = check_hooks(
    Path.home()/'.codex'/'hooks.json',
    {'gitnexus': 'gitnexus', 'harness_monitor': 'harness-monitor'})
existing['mcp_claude'] = 'gitnexus' in ((Path.home()/'.claude.json').read_text()
                                         if (Path.home()/'.claude.json').exists() else '')
existing['mcp_codex'] = 'gitnexus' in ((Path.home()/'.codex'/'config.toml').read_text()
                                        if (Path.home()/'.codex'/'config.toml').exists() else '')

# ── 5. LSP assessment ──
lsp = []
for li in languages:
    lang, files = li['language'], li['files']
    a = {'language': lang, 'files': files, 'recommend': False, 'reason': ''}
    if lang in STRONG_TYPED:
        a['recommend'] = files >= 30
        a['reason'] = f"{files} 个文件{'，强类型，LSP 价值高' if a['recommend'] else ' < 30，暂不需要'}"
    elif lang == 'Python':
        cov = type_coverage['coverage']
        a['recommend'] = cov >= 30
        a['reason'] = f"类型覆盖 {cov}%{'，LSP 可有效检测类型错误' if cov >= 30 else '，LSP 价值低' if cov < 15 else '，可选安装'}"
    elif lang in WEAK_TYPED:
        a['reason'] = "弱类型语言，LSP 价值低"
    elif lang in ('C', 'C++'):
        a['recommend'] = files >= 30
        a['reason'] = f"{files} 个文件{'，需配置 compile_commands.json' if a['recommend'] else ' < 30，暂不需要'}"
    lsp.append(a)

print(json.dumps({
    'schema_version': 1,
    'harness_version': next(
        (p.read_text().strip() for p in [
            Path(sys.argv[1]).parent / 'VERSION',  # script_dir/../VERSION (works in both symlink and copy modes)
            Path.home() / '.local' / 'share' / 'harness-hooks' / 'VERSION',  # copy mode fallback
        ] if p.exists()), 'unknown'),
    'project': os.path.basename(os.path.abspath('.')),
    'project_dir': os.path.abspath('.'),
    'languages': languages, 'grep_noise': grep_noise,
    'type_coverage': type_coverage, 'lsp_assessment': lsp, 'existing': existing,
}, indent=2, ensure_ascii=False))
PYEOF
