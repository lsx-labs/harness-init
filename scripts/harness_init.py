#!/usr/bin/env python3
"""Core diagnostic script — outputs JSON project analysis.

schema_version: 1
Fields: languages, grep_noise, type_coverage, lsp_assessment, existing
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from harness_shared import should_skip

# ── Config ──
TEST_DIRS = {"tests", "test"}
GENERIC_NAMES = {"base", "utils", "helpers", "common", "core", "main", "config",
                 "settings", "models", "views", "loader", "registry", "pipeline",
                 "types", "constants", "errors", "exceptions", "index", "app", "run",
                 "init", "setup", "cli", "api", "server", "client", "manager"}
STDLIB = {"os", "sys", "json", "pathlib", "re", "typing", "collections", "dataclasses",
          "datetime", "time", "subprocess", "threading", "unittest", "pytest", "concurrent",
          "__future__", "enum", "abc", "functools", "contextlib", "copy", "io", "math",
          "hashlib", "shutil", "glob", "tempfile", "textwrap", "itertools", "operator",
          "inspect", "importlib", "warnings", "logging", "argparse", "traceback", "signal"}
LANG_MAP = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".c": "C", ".cpp": "C++", ".h": "C",
    ".cs": "C#", ".swift": "Swift", ".php": "PHP",
}
STRONG_TYPED = {"TypeScript", "Go", "Rust", "Java", "Kotlin", "C#", "Swift"}
WEAK_TYPED = {"JavaScript", "Ruby", "PHP"}
LSP_PLUGIN_MAP = {
    "Python": ["code-intelligence-python", "pyright-lsp"],
    "TypeScript": ["code-intelligence-typescript", "typescript-lsp"],
    "Go": ["code-intelligence-go", "gopls-lsp"],
    "Rust": ["code-intelligence-rust", "rust-analyzer-lsp"],
    "Java": ["code-intelligence-java"], "Kotlin": ["code-intelligence-java"],
    "C#": ["code-intelligence-csharp"],
    "Swift": ["code-intelligence-swift"],
    "C": ["code-intelligence-cpp"], "C++": ["code-intelligence-cpp"],
}


# ── 1. Language distribution ──

def scan_languages() -> tuple[list[dict], Counter]:
    lang_stats = defaultdict(lambda: {"files": 0, "lines": 0})
    import_counter = Counter()

    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if not should_skip(d)]
        in_test = any(t in root.split(os.sep) for t in TEST_DIRS)
        for f in files:
            ext = Path(f).suffix.lower()
            if ext not in LANG_MAP:
                continue
            lang = LANG_MAP[ext]
            filepath = os.path.join(root, f)
            try:
                content = open(filepath, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            lang_stats[lang]["files"] += 1
            lang_stats[lang]["lines"] += content.count("\n") + 1

            if ext == ".py" and not in_test:
                for m in re.finditer(r"from\s+([\w.]+)\s+import", content):
                    full = m.group(1)
                    if "." in full:
                        leaf = full.rsplit(".", 1)[-1]
                        if leaf not in STDLIB and leaf not in GENERIC_NAMES and len(leaf) > 3:
                            import_counter[leaf] += 1

    total_lines = sum(v["lines"] for v in lang_stats.values())
    languages = []
    for lang, v in sorted(lang_stats.items(), key=lambda x: -x[1]["lines"]):
        pct = round(v["lines"] / total_lines * 100, 1) if total_lines > 0 else 0
        if pct < 3 and v["files"] < 5:
            continue
        languages.append({"language": lang, "files": v["files"], "lines": v["lines"], "percent": pct})
    return languages, import_counter


# ── 2. Grep noise ──

def measure_grep_noise(import_counter: Counter) -> dict:
    if not import_counter:
        return {"most_imported": "", "grep_noise_files": 0, "top5": []}
    top5 = import_counter.most_common(5)
    most = top5[0][0]
    try:
        r = subprocess.run(
            ["grep", "-rl", most, ".", "--include=*.py",
             "--exclude-dir=__pycache__", "--exclude-dir=tests", "--exclude-dir=test",
             "--exclude-dir=.git", "--exclude-dir=.venv", "--exclude-dir=.gitnexus",
             "--exclude-dir=.worktrees", "--exclude-dir=build", "--exclude-dir=dist"],
            capture_output=True, text=True, timeout=10)
        count = len([l for l in r.stdout.strip().split("\n") if l])
    except (subprocess.TimeoutExpired, OSError):
        count = -1
    return {
        "most_imported": most, "grep_noise_files": count,
        "top5": [{"module": m, "imports": c} for m, c in top5],
    }


# ── 3. Type coverage ──

def measure_type_coverage(languages: list[dict]) -> dict:
    if not any(l["language"] == "Python" for l in languages):
        return {"typed_funcs": 0, "total_funcs": 0, "coverage": 0}
    typed = total = 0
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if not should_skip(d)]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                c = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                total += len(re.findall(r"\bdef\s+", c))
                typed += len(re.findall(r"\bdef\s+\w+\s*\([^)]*\)\s*->", c))
            except OSError:
                pass
    return {"typed_funcs": typed, "total_funcs": total,
            "coverage": round(typed / total * 100, 1) if total else 0}


# ── 4. Existing harness state ──

def check_existing() -> dict:
    existing = {}
    for name, fpath in [("claude_md", "CLAUDE.md"), ("agents_md", "AGENTS.md")]:
        p = Path(fpath)
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
        existing[name] = {
            "exists": p.exists(),
            "has_codemap": "@CODE_MAP.md" in txt or "<!-- codemap:start -->" in txt,
            "has_gitnexus": "<!-- gitnexus:start -->" in txt,
        }
    gitnexus_indexed = Path(".gitnexus").is_dir()
    gitnexus_up_to_date = False
    if gitnexus_indexed:
        try:
            r = subprocess.run(["npx", "gitnexus", "status"],
                               capture_output=True, text=True, timeout=5)
            gitnexus_up_to_date = "up-to-date" in (r.stdout + r.stderr).lower()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    existing["gitnexus"] = {
        "indexed": gitnexus_indexed,
        "up_to_date": gitnexus_up_to_date,
        "in_gitignore": ".gitnexus" in (Path(".gitignore").read_text(encoding="utf-8") if Path(".gitignore").exists() else ""),
    }
    existing["gitnexus_hook_reachable"] = (Path.home() / ".claude" / "hooks" / "gitnexus" / "gitnexus-hook.cjs").exists()

    def check_hooks_multi(path, keys):
        """Like check_hooks but supports multiple match strings per key."""
        try:
            hooks = json.loads(Path(path).read_text(encoding="utf-8")).get("hooks", {})
            result = {}
            for k, patterns in keys.items():
                if isinstance(patterns, str):
                    patterns = [patterns]
                result[k] = any(
                    any(p in h.get("command", "") for p in patterns)
                    for items in hooks.values()
                    for item in items for h in item.get("hooks", []))
            return result
        except (json.JSONDecodeError, OSError, KeyError):
            return {k: False for k in keys}

    existing["hooks_claude"] = check_hooks_multi(
        Path.home() / ".claude" / "settings.json",
        {"gitnexus": "gitnexus", "harness_monitor": ["harness_monitor", "harness-monitor"]})
    existing["hooks_codex"] = check_hooks_multi(
        Path.home() / ".codex" / "hooks.json",
        {"gitnexus": "gitnexus", "harness_monitor": ["harness_monitor", "harness-monitor"]})
    existing["codex_gitnexus_wrapper"] = check_codex_gitnexus_wrapper()
    existing["mcp_claude"] = "gitnexus" in (
        (Path.home() / ".claude.json").read_text(encoding="utf-8") if (Path.home() / ".claude.json").exists() else "")
    existing["mcp_codex"] = "gitnexus" in (
        (Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8") if (Path.home() / ".codex" / "config.toml").exists() else "")
    return existing


def _hook_commands(hooks: dict, event_name: str) -> list[str]:
    commands = []
    for item in hooks.get(event_name, []):
        for hook in item.get("hooks", []):
            command = hook.get("command", "")
            if isinstance(command, str) and command:
                commands.append(command)
    return commands


def check_codex_gitnexus_wrapper() -> dict:
    """Check Codex's GitNexus hook wrapper and its upgrade-safety self-test."""
    home = Path.home()
    hooks_path = home / ".codex" / "hooks.json"
    wrapper_path = home / ".codex" / "hooks" / "gitnexus-codex-hook.cjs"
    result = {
        "status": "missing_hooks",
        "hooks_json_exists": hooks_path.exists(),
        "wrapper_exists": wrapper_path.exists(),
        "configured": False,
        "pretooluse_points_to_wrapper": False,
        "posttooluse_points_to_wrapper": False,
        "self_test_passed": False,
        "self_test_output": "",
    }

    if not hooks_path.exists():
        return result

    try:
        hooks = json.loads(hooks_path.read_text(encoding="utf-8")).get("hooks", {})
    except (json.JSONDecodeError, OSError):
        result["status"] = "invalid_hooks_json"
        return result

    wrapper_ref = str(wrapper_path)
    pre_commands = _hook_commands(hooks, "PreToolUse")
    post_commands = _hook_commands(hooks, "PostToolUse")
    result["pretooluse_points_to_wrapper"] = any(wrapper_ref in command for command in pre_commands)
    result["posttooluse_points_to_wrapper"] = any(wrapper_ref in command for command in post_commands)
    result["configured"] = (
        result["pretooluse_points_to_wrapper"] and result["posttooluse_points_to_wrapper"]
    )

    if not wrapper_path.exists():
        result["status"] = "missing_wrapper"
        return result
    if not result["configured"]:
        result["status"] = "not_configured"
        return result

    try:
        run = subprocess.run(
            ["node", str(wrapper_path), "--self-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        result["status"] = "self_test_failed"
        result["self_test_output"] = str(exc)
        return result

    result["self_test_passed"] = run.returncode == 0
    result["self_test_output"] = ((run.stdout or "") + (run.stderr or "")).strip()[:500]
    result["status"] = "pass" if result["self_test_passed"] else "self_test_failed"
    return result


# ── 5. LSP assessment ──

def check_lsp_installed(lang: str) -> bool:
    aliases = LSP_PLUGIN_MAP.get(lang, [])
    if not aliases:
        return False
    claude_plugins = Path.home() / ".claude" / "plugins"
    if claude_plugins.is_dir():
        for alias in aliases:
            if any(claude_plugins.glob(f"*{alias}*")):
                return True
    for cfg in [Path.home() / ".claude" / "settings.json", Path.home() / ".claude.json"]:
        if cfg.exists():
            text = cfg.read_text(encoding="utf-8")
            if any(alias in text for alias in aliases):
                return True
    return False


def assess_lsp(languages: list[dict], type_coverage: dict) -> list[dict]:
    result = []
    for li in languages:
        lang, files = li["language"], li["files"]
        installed = check_lsp_installed(lang)
        a = {"language": lang, "files": files, "recommend": False,
             "installed": installed, "plugin": LSP_PLUGIN_MAP.get(lang, [""])[0], "reason": ""}
        if installed:
            a["reason"] = "✅ 已安装"
        elif lang in STRONG_TYPED:
            a["recommend"] = files >= 30
            a["reason"] = f"{files} 个文件{'，强类型，LSP 价值高' if a['recommend'] else ' < 30，暂不需要'}"
        elif lang == "Python":
            cov = type_coverage["coverage"]
            a["recommend"] = cov >= 30
            a["reason"] = f"类型覆盖 {cov}%{'，LSP 可有效检测类型错误' if cov >= 30 else '，LSP 价值低' if cov < 15 else '，可选安装'}"
        elif lang in WEAK_TYPED:
            a["reason"] = "弱类型语言，LSP 价值低"
        elif lang in ("C", "C++"):
            a["recommend"] = files >= 30
            a["reason"] = f"{files} 个文件{'，需配置 compile_commands.json' if a['recommend'] else ' < 30，暂不需要'}"
        result.append(a)
    return result


# ── Version ──

def get_version() -> str:
    script_dir = Path(__file__).resolve().parent
    for candidate in [
        script_dir.parent / "VERSION",
        Path.home() / ".local" / "share" / "harness-hooks" / "VERSION",
    ]:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return "unknown"


# ── Main ──

def diagnose(project_dir: str = ".") -> dict:
    os.chdir(project_dir)
    languages, import_counter = scan_languages()
    grep_noise = measure_grep_noise(import_counter)
    type_coverage = measure_type_coverage(languages)
    existing = check_existing()
    lsp = assess_lsp(languages, type_coverage)
    return {
        "schema_version": 1,
        "harness_version": get_version(),
        "project": os.path.basename(os.path.abspath(".")),
        "project_dir": os.path.abspath("."),
        "languages": languages,
        "grep_noise": grep_noise,
        "type_coverage": type_coverage,
        "lsp_assessment": lsp,
        "existing": existing,
    }


def main():
    project_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    result = diagnose(project_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
