#!/usr/bin/env python3
"""Install harness-init: deploy scripts, skills, and hooks to Claude Code / Codex."""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
USE_LINK = "--link" in sys.argv
HOME = Path.home()


def log(msg: str):
    print(msg)


def install_file(src: Path, dst: Path):
    """Copy or symlink a file. Copy mode skips existing symlinks."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not USE_LINK and dst.is_symlink():
        log(f"  ⏭️  {dst.name} is a symlink (--link mode), skipping copy")
        return
    dst.unlink(missing_ok=True)
    if USE_LINK:
        dst.symlink_to(src)
    else:
        shutil.copy2(src, dst)
        try:
            dst.chmod(0o755)
        except OSError:
            pass


def install_dir(src: Path, dst: Path):
    """Copy or symlink a directory. Copy mode skips existing symlinks."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not USE_LINK and dst.is_symlink():
        log(f"  ⏭️  {dst.name} is a symlink (--link mode), skipping copy")
        return
    if dst.is_symlink():
        dst.unlink()
    elif dst.is_dir():
        shutil.rmtree(dst)
    if USE_LINK:
        dst.symlink_to(src)
    else:
        shutil.copytree(src, dst)


def check_command(cmd: str) -> str | None:
    """Return version string or None."""
    try:
        r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip().split("\n")[0] if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def register_hooks(config_file: Path, platform_name: str, monitor_path: str, context_path: str):
    """Register PostToolUse + SessionStart hooks in a JSON config file."""
    if not config_file.exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text('{"hooks": {}}' if config_file.name == "hooks.json" else '{}')

    d = json.loads(config_file.read_text())
    hooks = d.setdefault("hooks", {})

    # PostToolUse: harness_monitor (idempotent)
    post = hooks.setdefault("PostToolUse", [])
    post[:] = [item for item in post
               if not any("harness_monitor" in h.get("command", "") or "harness-monitor" in h.get("command", "")
                          or "Harness monitor" in h.get("statusMessage", "")
                          for h in item.get("hooks", []))]
    post.append({
        "matcher": "Bash",
        "hooks": [{
            "type": "command",
            "command": f'python3 "{monitor_path}"',
            "timeout": 20000,
            "statusMessage": "Harness monitor..."
        }]
    })

    # SessionStart: session-context (idempotent)
    session = hooks.setdefault("SessionStart", [])
    session[:] = [item for item in session
                  if not any("session-context" in h.get("command", "") or "session_context" in h.get("command", "")
                             for h in item.get("hooks", []))]
    session.append({
        "matcher": "startup|clear",
        "hooks": [{
            "type": "command",
            "command": f'python3 "{context_path}"',
            "timeout": 10000,
            "statusMessage": "Loading project context..."
        }]
    })

    config_file.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    log(f"✅ {platform_name} hooks registered (PostToolUse + SessionStart)")


def main():
    mode = "symlink" if USE_LINK else "copy"
    log(f"Installing harness-init from {SCRIPT_DIR} ({mode} mode)")
    log("")

    # ── 0. Prerequisites ──
    node_ver = check_command("node")
    if not node_ver:
        log("❌ Node.js not found. Install Node.js 18+ first: https://nodejs.org")
        sys.exit(1)
    log(f"✅ Node.js {node_ver}")

    gitnexus_available = False
    gitnexus_ver = check_command("npx gitnexus".split()[0])
    # Better check
    try:
        r = subprocess.run(["npx", "gitnexus", "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            gitnexus_available = True
            log(f"✅ GitNexus {r.stdout.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    if not gitnexus_available:
        log("")
        log("⚠️  GitNexus not found. It is recommended for:")
        log("   - CODE_MAP.md auto-generation (knowledge graph → module structure)")
        log("   - PreToolUse search enrichment")
        log("   - PostToolUse stale index detection")
        log("")
        answer = input("Install GitNexus now? (Y/n) ").strip().lower()
        if answer != "n":
            log("Installing GitNexus...")
            subprocess.run(["npm", "install", "-g", "gitnexus"], check=False)
            log("Running GitNexus setup...")
            subprocess.run(["npx", "gitnexus", "setup"], check=False)
            gitnexus_available = True
            log("✅ GitNexus installed")
        else:
            log("⚠️  Continuing without GitNexus (degraded mode)")

    python_ver = check_command("python3")
    if not python_ver:
        log("❌ Python 3 not found.")
        sys.exit(1)
    log(f"✅ Python {python_ver}")
    log("")

    # ── 1. Shared scripts ──
    local_bin = HOME / ".local" / "bin"
    local_share = HOME / ".local" / "share" / "harness-hooks"
    local_bin.mkdir(parents=True, exist_ok=True)

    install_file(SCRIPT_DIR / "scripts" / "harness_shared.py", local_bin / "harness_shared.py")
    install_file(SCRIPT_DIR / "scripts" / "harness_init.py", local_bin / "harness-init.py")
    install_file(SCRIPT_DIR / "scripts" / "harness_plan.py", local_bin / "harness-plan.py")
    install_file(SCRIPT_DIR / "scripts" / "sync_docs.py", local_bin / "sync-docs.py")

    if USE_LINK:
        monitor_path = str(SCRIPT_DIR / "scripts" / "harness_monitor.py")
        context_path = str(SCRIPT_DIR / "scripts" / "session_context.py")
        # Clean up stale copy-mode files (current + legacy names)
        for stale in ["harness-monitor.py", "harness_monitor.py", "shared.py",
                       "harness_shared.py", "generate_descriptions.py", "session_context.py"]:
            (local_share / stale).unlink(missing_ok=True)
    else:
        local_share.mkdir(parents=True, exist_ok=True)
        install_file(SCRIPT_DIR / "scripts" / "harness_shared.py", local_share / "harness_shared.py")
        install_file(SCRIPT_DIR / "scripts" / "harness_monitor.py", local_share / "harness_monitor.py")
        install_file(SCRIPT_DIR / "scripts" / "generate_descriptions.py", local_share / "generate_descriptions.py")
        install_file(SCRIPT_DIR / "scripts" / "session_context.py", local_share / "session_context.py")
        shutil.copy2(SCRIPT_DIR / "VERSION", local_share / "VERSION")
        monitor_path = str(local_share / "harness_monitor.py")
        context_path = str(local_share / "session_context.py")

    log("✅ Shared scripts installed")

    # ── 2. Skills ──
    claude_dir = HOME / ".claude"
    codex_dir = HOME / ".codex"

    if claude_dir.is_dir():
        install_dir(SCRIPT_DIR / "skills" / "claude", claude_dir / "skills" / "harness-init")
        log("✅ Claude Code skill installed")
    else:
        log("⏭️  ~/.claude not found, skipping Claude Code skill")

    if codex_dir.is_dir():
        install_dir(SCRIPT_DIR / "skills" / "codex", codex_dir / "skills" / "harness-init")
        log("✅ Codex skill installed")
    else:
        log("⏭️  ~/.codex not found, skipping Codex skill")

    # ── 3. Hook registration ──
    if claude_dir.is_dir():
        register_hooks(claude_dir / "settings.json", "Claude Code", monitor_path, context_path)
    if codex_dir.is_dir():
        register_hooks(codex_dir / "hooks.json", "Codex", monitor_path, context_path)

    # ── 4. GitNexus hook reachability (pure Codex) ──
    gitnexus_hook = HOME / ".claude" / "hooks" / "gitnexus" / "gitnexus-hook.cjs"
    if gitnexus_available and codex_dir.is_dir() and not gitnexus_hook.exists():
        try:
            r = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=5)
            src = Path(r.stdout.strip()) / "gitnexus" / "hooks" / "claude" / "gitnexus-hook.cjs"
            if src.exists():
                gitnexus_hook.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, gitnexus_hook)
                log("✅ GitNexus hook copied for Codex compatibility")
        except (subprocess.TimeoutExpired, OSError):
            pass

    # ── 5. Verify ──
    log("")
    log("=== Installation verification ===")
    errors = 0

    for script_name in ["harness_shared.py", "harness-init.py", "harness-plan.py", "sync-docs.py"]:
        p = local_bin / script_name
        if p.exists() or p.is_symlink():
            log(f"  ✅ {script_name} ({'symlink' if p.is_symlink() else 'copy'})")
        else:
            log(f"  ❌ {script_name} missing")
            errors += 1

    if USE_LINK:
        if Path(monitor_path).exists():
            log("  ✅ harness_monitor.py (repo direct)")
        else:
            log("  ❌ harness_monitor.py missing")
            errors += 1
    else:
        mp = local_share / "harness_monitor.py"
        if mp.exists():
            log("  ✅ harness_monitor.py")
        else:
            log("  ❌ harness_monitor.py missing")
            errors += 1

    for name, path in [("Claude Code", claude_dir / "skills" / "harness-init" / "SKILL.md"),
                       ("Codex", codex_dir / "skills" / "harness-init" / "SKILL.md")]:
        if path.exists():
            log(f"  ✅ {name} skill")
        else:
            log(f"  ⏭️  {name} skill (not installed)")

    if errors:
        log(f"\n⚠️  {errors} errors found.")
        sys.exit(1)

    log("")
    if USE_LINK:
        log(f"Done! (symlink mode — edits to {SCRIPT_DIR} take effect immediately)")
    else:
        log("Done! Run /harness-init in any project to get started.")


if __name__ == "__main__":
    main()
