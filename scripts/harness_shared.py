"""Shared constants and utilities for harness-init scripts."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# ── Constants ──

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".gitnexus",
             ".claude", ".codex", "dist", "build", "vendor", "third_party", "sdk",
             ".worktrees", ".tox"}

STALE_THRESHOLD = 0.2
SYMBOL_THRESHOLD = 100
# When this many directories need AI descriptions, /harness-init delegates the CODE_MAP
# refresh to a detached background worker instead of blocking the agent's turn (each dir is
# ~half an AI batch at ~150-180s, so beyond this the in-turn wait runs into many minutes).
CODEMAP_BG_DIRS_THRESHOLD = 6
MANUAL_MARKER = "📌"
LOW_CONFIDENCE_MARKER = "⚠️"
CODEMAP_FILENAME = "CODE_MAP.md"
CODEMAP_COUNTS_FILENAME = "CODE_MAP.counts.json"
CODEMAP_CACHE_ROOT = Path.home() / ".local" / "share" / "harness-hooks" / "codemaps"
CODEMAP_BLOCK_START = "<!-- codemap:start -->"
CODEMAP_BLOCK_END = "<!-- codemap:end -->"
ROOT_PLATFORM_DOCS = ("CLAUDE.md", "AGENTS.md")

SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
               ".rb", ".c", ".h", ".cpp", ".cs", ".swift", ".php"}
CODE_NAME_GENERIC = {
    "main", "init", "run", "start", "stop", "get", "set", "test", "setup",
    "parse", "build", "create", "delete", "update", "load", "save", "read",
    "write", "open", "close", "validate", "check", "add",
}

MAIN_BRANCHES = {"main", "master"}

# Sandbox flags for `codex exec`: read-only filesystem, no approval escalation.
# Mirrors the tool restriction applied on the Claude CLI path.
CODEX_EXEC_SANDBOX_ARGS = ["-s", "read-only", "-c", "approval_policy=never"]


# ── AI CLI discovery (shared by monitor / description generator) ──

def is_codex_runtime() -> bool:
    platform = os.environ.get("HARNESS_PLATFORM", "").strip().lower()
    if platform:
        return platform == "codex"
    return any(key.startswith("CODEX_") for key in os.environ)


def get_ai_cmd() -> str:
    """Find an available AI CLI for non-interactive invocation."""
    preferred = ["codex", "claude"] if is_codex_runtime() else ["claude", "codex"]
    for cmd in preferred:
        if shutil.which(cmd):
            return cmd
    codex_app = "/Applications/Codex.app/Contents/Resources/codex"
    if os.path.isfile(codex_app):
        return codex_app
    return ""


def should_skip(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info") or (name.startswith(".") and name != ".")


def path_key(path) -> str:
    """Collision-safe per-project key: the sanitized absolute path.

    Used for notification/lock filenames so two repos with the same basename
    (e.g. ~/a/api and ~/b/api) don't read each other's state.
    """
    return str(Path(path).resolve()).replace("/", "_").lstrip("_")


def _git_common_dir(project_dir: str | Path = ".") -> Path | None:
    """Return the repository common git dir for sharing cache across linked worktrees."""
    root = Path(project_dir)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    stdout = getattr(result, "stdout", "")
    if not isinstance(stdout, str):
        return None
    raw = stdout.strip()
    if not raw:
        return None
    common = Path(raw)
    if not common.is_absolute():
        common = root / common
    return common.resolve()


def codemap_cache_path(project_dir: str | Path = ".") -> Path:
    """Shared CODE_MAP cache path for a repo, stable across linked worktrees."""
    common = _git_common_dir(project_dir)
    cache_key = path_key(common if common is not None else project_dir)
    return CODEMAP_CACHE_ROOT / cache_key / CODEMAP_FILENAME


def codemap_counts_cache_path(project_dir: str | Path = ".") -> Path:
    """Shared CODE_MAP count sidecar path for a repo."""
    common = _git_common_dir(project_dir)
    cache_key = path_key(common if common is not None else project_dir)
    return CODEMAP_CACHE_ROOT / cache_key / CODEMAP_COUNTS_FILENAME


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def cache_codemap_projection(project_dir: str | Path = ".", content: str | None = None) -> bool:
    """Persist the worktree CODE_MAP projection into the shared harness cache."""
    root = Path(project_dir)
    local = root / CODEMAP_FILENAME
    if content is None:
        if not local.exists():
            return False
        try:
            content = local.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
    try:
        _atomic_write_text(codemap_cache_path(root), content)
    except OSError:
        return False
    return True


def read_codemap_counts(project_dir: str | Path = ".") -> dict[str, int]:
    """Read description-baseline CODE_MAP symbol counts from the shared sidecar cache."""
    path = codemap_counts_cache_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    counts = data.get("described_counts", data.get("counts", {}))
    if not isinstance(counts, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in counts.items():
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            clean_key = key.strip("/")
            if clean_key:
                result[clean_key] = value
    return result


def write_codemap_counts(project_dir: str | Path = ".", counts: dict[str, int] | None = None) -> bool:
    """Persist description-baseline CODE_MAP symbol counts into the shared sidecar cache."""
    clean_counts = {
        str(key).strip("/"): int(value)
        for key, value in (counts or {}).items()
        if str(key).strip("/") and isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }
    payload = {
        "schema_version": 1,
        "described_counts": dict(sorted(clean_counts.items())),
    }
    try:
        _atomic_write_text(
            codemap_counts_cache_path(project_dir),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError:
        return False
    return True


def materialize_codemap_projection(project_dir: str | Path = ".") -> bool:
    """Copy CODE_MAP from shared cache into this worktree when the local projection is absent."""
    root = Path(project_dir)
    local = root / CODEMAP_FILENAME
    if local.exists():
        return False
    cache = codemap_cache_path(root)
    if not cache.exists():
        return False
    try:
        _atomic_write_text(local, cache.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return True


def render_codemap_block(doc_text: str, codemap_text: str) -> str:
    """Render CODE_MAP content as a managed block inside a root platform doc."""
    managed_block = f"{CODEMAP_BLOCK_START}\n{codemap_text.strip()}\n{CODEMAP_BLOCK_END}"
    section = f"## CODE_MAP\n\n{managed_block}"
    pattern = re.compile(
        rf"{re.escape(CODEMAP_BLOCK_START)}.*?{re.escape(CODEMAP_BLOCK_END)}",
        re.DOTALL | re.MULTILINE,
    )
    if pattern.search(doc_text):
        return pattern.sub(managed_block, doc_text, count=1)
    if "@CODE_MAP.md" in doc_text:
        return doc_text.replace("@CODE_MAP.md", section, 1)
    suffix = "" if doc_text.endswith("\n") else "\n"
    return f"{doc_text}{suffix}\n{section}\n"


def update_root_codemap_docs(project_dir: str | Path = ".") -> dict[str, str]:
    """Update root platform docs with the current local CODE_MAP projection."""
    root = Path(project_dir)
    codemap = root / CODEMAP_FILENAME
    if not codemap.exists():
        return {}
    try:
        codemap_text = codemap.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    results: dict[str, str] = {}
    for name in ROOT_PLATFORM_DOCS:
        path = root / name
        if not path.exists():
            continue
        try:
            old = path.read_text(encoding="utf-8", errors="replace")
            new = render_codemap_block(old, codemap_text)
        except OSError:
            continue
        if new != old:
            try:
                _atomic_write_text(path, new)
            except OSError:
                results[name] = "write_failed"
                continue
            results[name] = "updated"
        else:
            results[name] = "unchanged"
    return results


def codemap_is_ignored(project_dir: str | Path = ".") -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "check-ignore", "--no-index", "-q", CODEMAP_FILENAME],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def codemap_is_tracked(project_dir: str | Path = ".") -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "ls-files", "--error-unmatch", CODEMAP_FILENAME],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def ensure_codemap_gitignore(project_dir: str | Path = ".") -> bool:
    """Ensure CODE_MAP.md is ignored in this repo. Returns True when it edits .gitignore."""
    root = Path(project_dir)
    if codemap_is_ignored(root):
        return False
    gitignore = root / ".gitignore"
    try:
        text = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
    except OSError:
        return False
    patterns = {line.strip() for line in text.splitlines()}
    if CODEMAP_FILENAME in patterns or f"/{CODEMAP_FILENAME}" in patterns:
        return False
    prefix = "" if not text or text.endswith("\n") else "\n"
    addition = f"{prefix}\n# Harness generated local projection\n{CODEMAP_FILENAME}\n"
    try:
        gitignore.write_text(text + addition, encoding="utf-8")
    except OSError:
        return False
    return True


# ── GitNexus output parsing (shared by monitor / plan / description generator) ──

def parse_gitnexus_markdown(output: str) -> str:
    """Extract the markdown table from GitNexus cypher JSON output (dict or list form)."""
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            return data.get("markdown", "")
        if isinstance(data, list) and data:
            if isinstance(data[0], dict):
                return data[0].get("markdown", "")
            return str(data[0])
    except (json.JSONDecodeError, IndexError, TypeError):
        pass
    return ""


def gitnexus_markdown_rows(markdown: str) -> list[list[str]]:
    """Split a GitNexus markdown table into data-cell rows (header + separator dropped)."""
    lines = [line.strip() for line in markdown.split("\n") if line.strip()]
    if len(lines) < 3:
        return []
    return [[c.strip() for c in line.split("|") if c.strip()] for line in lines[2:]]


def read_dir_docstring(dir_path: str, *, limit: int = 80) -> str:
    """First line of a directory's package docstring (__init__.py), separator-stripped.

    Reads the module docstring, takes its first line, drops a leading "Name — " prefix
    (em/en-dash, or a spaced hyphen " - " so internal hyphens like "x-ray" are kept),
    and truncates to `limit` chars.
    """
    fpath = Path(dir_path) / "__init__.py"
    if not fpath.exists():
        return ""
    try:
        ds = ast.get_docstring(ast.parse(fpath.read_text(encoding="utf-8", errors="ignore")))
    except (SyntaxError, OSError):
        return ""
    if not ds:
        return ""
    line = ds.strip().split("\n")[0]
    for sep in ("—", "–", " - "):
        if sep in line:
            line = line.split(sep, 1)[1].strip()
            break
    return line[:limit]


def _folder_leaf(folder: str) -> str:
    return folder.split("/")[-1].lower().lstrip("_")


def map_areas_to_dirs(areas, folders: list[str]) -> dict[str, str]:
    """Map each GitNexus community label to the folder whose leaf name uniquely matches.

    Matching is case-insensitive and ignores a leading underscore on either side. When
    two or more folders share the same leaf (e.g. src/utils and tests/utils), the match
    is ambiguous and the area is omitted rather than mis-attributed to an arbitrary one.
    """
    by_leaf: dict[str, list[str]] = {}
    for folder in folders:
        by_leaf.setdefault(_folder_leaf(folder), []).append(folder)
    mapping: dict[str, str] = {}
    for area in areas:
        candidates = by_leaf.get(area.lower().lstrip("_"), [])
        if len(candidates) == 1:
            mapping[area] = candidates[0]
    return mapping


# ── Platform ──

def platform_files(platform: str) -> tuple[str, str]:
    """Returns (own_file, other_file) for the given platform."""
    if platform == "claude":
        return "CLAUDE.md", "AGENTS.md"
    return "AGENTS.md", "CLAUDE.md"


# ── CODE_MAP.md parsing ──

def parse_codemap_entry(rest: str) -> tuple[str, int | None]:
    """Parse description and symbol count from a CODE_MAP line's trailing text."""
    desc = ""
    count = None
    cm = re.search(r'\((\d+)\s*symbols?\)', rest)
    if cm:
        count = int(cm.group(1))
        rest = rest[:cm.start()] + rest[cm.end():]
    dm = re.search(r'—\s*(.+)', rest)
    if dm:
        desc = dm.group(1).strip()
    return desc, count


def parse_codemap(codemap_path: Path) -> list[dict]:
    """Parse CODE_MAP.md into structured entries.

    Returns list of {dir, desc, symbols} dicts.
    All other scripts should use this instead of rolling their own parser.
    """
    if not codemap_path.exists():
        return []
    entries = []
    current = ""
    for line in codemap_path.read_text(encoding="utf-8", errors="replace").split("\n"):
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


# ── CODE_MAP.md description quality ──

_LOW_QUALITY_FRAGMENTS = (
    "Tests for ",
)


def _is_code_like_token(token: str) -> bool:
    return (
        "_" in token
        or token.endswith("_")
        or bool(re.search(r'[a-z][A-Z]', token))
    )


def is_manual_description(desc: str) -> bool:
    return desc.strip().startswith(MANUAL_MARKER)


def is_low_confidence_description(desc: str) -> bool:
    return desc.strip().startswith(LOW_CONFIDENCE_MARKER)


def is_low_quality_description(desc: str) -> bool:
    """Return True for generated descriptions that are navigation noise.

    This deliberately targets known fallback failure modes rather than trying
    to score prose quality generally.
    """
    desc = (desc or "").strip()
    if not desc or is_manual_description(desc):
        return False
    if is_low_confidence_description(desc):
        return True
    if any(fragment in desc for fragment in _LOW_QUALITY_FRAGMENTS):
        return True
    if " / " in desc:
        return True
    if re.search(r'\b[A-Za-z]+_$', desc):
        return True
    if all(ord(ch) < 128 for ch in desc):
        tokens = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', desc)
        if len(tokens) == 1 and tokens[0] == desc and _is_code_like_token(tokens[0]):
            return True
        if len(tokens) >= 2 and all(
            _is_code_like_token(token) or token.lower() in CODE_NAME_GENERIC
            for token in tokens
        ):
            return True
    return False


def is_acceptable_description(desc: str) -> bool:
    desc = (desc or "").strip()
    return bool(desc) and not is_low_quality_description(desc)


def needs_description_refresh(desc: str) -> bool:
    desc = (desc or "").strip()
    if not desc:
        return True
    if is_manual_description(desc):
        return False
    return is_low_quality_description(desc)
