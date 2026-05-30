#!/usr/bin/env python3
"""Uninstall harness-init: remove scripts, skills, and hooks."""

import json
import shutil
import sys
from pathlib import Path

HOME = Path.home()


def log(msg: str):
    print(msg)


def rm_file(path: Path):
    path.unlink(missing_ok=True)


def rm_dir(path: Path):
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def cleanup_hooks(config_file: Path, platform_name: str):
    """Remove harness-init hooks from a config file."""
    if not config_file.exists():
        return
    try:
        d = json.loads(config_file.read_text())
        hooks = d.get("hooks", {})
        for event in list(hooks.keys()):
            hooks[event] = [
                i for i in hooks[event]
                if not any("harness_monitor" in h.get("command", "") or
                           "harness-monitor" in h.get("command", "") or
                           "session_context" in h.get("command", "") or
                           "session-context" in h.get("command", "") or
                           "gitnexus-codex-hook" in h.get("command", "")
                           for h in i.get("hooks", []))
            ]
            if not hooks[event]:
                del hooks[event]
        if not hooks:
            d.pop("hooks", None)
        config_file.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        log(f"✅ {platform_name} hooks cleaned")
    except (json.JSONDecodeError, OSError) as e:
        log(f"⚠️  {platform_name}: {e}")


def main():
    log("Uninstalling harness-init...")

    # Scripts
    rm_file(HOME / ".local" / "bin" / "harness-init.py")
    rm_file(HOME / ".local" / "bin" / "harness-init.sh")  # legacy
    rm_file(HOME / ".local" / "bin" / "harness-plan.py")
    rm_file(HOME / ".local" / "bin" / "sync-docs.py")
    rm_file(HOME / ".local" / "bin" / "harness_shared.py")
    rm_file(HOME / ".local" / "bin" / "shared.py")  # legacy

    share = HOME / ".local" / "share" / "harness-hooks"
    for f in ["harness_shared.py", "shared.py", "harness_monitor.py", "harness-monitor.py",
              "generate_descriptions.py", "generate-descriptions.sh",
              "session_context.py", "session-context.sh", "VERSION"]:
        rm_file(share / f)
    rm_dir(share / "counters")
    rm_dir(share / "locks")
    rm_dir(share / "notifications")
    rm_dir(share / "jobs")
    rm_dir(share / "projects")

    # Codex GitNexus wrapper (installed by install.py section 4b)
    rm_file(HOME / ".codex" / "hooks" / "gitnexus-codex-hook.cjs")

    # Skills
    rm_dir(HOME / ".claude" / "skills" / "harness-init")
    rm_dir(HOME / ".codex" / "skills" / "harness-init")

    # Hooks
    cleanup_hooks(HOME / ".claude" / "settings.json", "Claude Code")
    cleanup_hooks(HOME / ".codex" / "hooks.json", "Codex")

    log("Done!")


if __name__ == "__main__":
    main()
