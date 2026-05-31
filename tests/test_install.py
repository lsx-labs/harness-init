"""Tests for install.py"""

import json
import os
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from install import (install_file, install_dir, check_command, register_hooks,
                     register_codex_gitnexus_wrapper)


def test_codex_wrapper_inner_timeout_below_registration(tmp_path):
    # the .cjs bounds the child (DEFAULT_TIMEOUT_MS) BELOW the Codex registration timeout so it
    # can normalize output before Codex kills the wrapper — keep the margin if either changes.
    import re
    cjs = Path(os.path.join(os.path.dirname(__file__), '..', 'hooks', 'gitnexus-codex-hook.cjs')).read_text()
    inner = int(re.search(r"DEFAULT_TIMEOUT_MS = (\d+)", cjs).group(1))
    hooks_file = tmp_path / "hooks.json"
    register_codex_gitnexus_wrapper(hooks_file, "/x/wrapper.cjs")
    reg = json.loads(hooks_file.read_text())
    outer = reg["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"]
    assert inner < outer, f"wrapper inner timeout {inner}ms must be < registration {outer}ms"


def test_install_uses_postponed_annotations_for_python39():
    # install.py uses PEP 604 `X | None` annotations; without this it hard-crashes at
    # import on Python 3.9 (the runtime scripts already have the import).
    source = Path(os.path.join(os.path.dirname(__file__), '..', 'install.py')).read_text()
    assert "from __future__ import annotations" in source
    assert source.index("from __future__ import annotations") < source.index("def check_command")


class TestInstallFile:
    def test_copy_mode(self, tmp_path):
        src = tmp_path / "source.txt"
        dst = tmp_path / "dest.txt"
        src.write_text("hello")
        with patch('install.USE_LINK', False):
            install_file(src, dst)
        assert dst.read_text() == "hello"

    def test_link_mode(self, tmp_path):
        src = tmp_path / "source.txt"
        dst = tmp_path / "dest.txt"
        src.write_text("hello")
        with patch('install.USE_LINK', True):
            install_file(src, dst)
        assert dst.is_symlink()

    def test_copy_skips_symlink(self, tmp_path):
        src = tmp_path / "source.txt"
        target = tmp_path / "target.txt"
        dst = tmp_path / "dest.txt"
        src.write_text("new")
        target.write_text("old")
        dst.symlink_to(target)
        with patch('install.USE_LINK', False):
            install_file(src, dst)
        assert dst.is_symlink()
        assert dst.read_text() == "old"

    def test_copy_replaces_broken_symlink(self, tmp_path):
        src = tmp_path / "source.txt"
        dst = tmp_path / "dest.txt"
        src.write_text("new")
        dst.symlink_to(tmp_path / "missing.txt")
        with patch('install.USE_LINK', False):
            install_file(src, dst)
        assert not dst.is_symlink()
        assert dst.read_text() == "new"


class TestInstallDir:
    def test_copy_dir(self, tmp_path):
        src = tmp_path / "srcdir"
        src.mkdir()
        (src / "file.txt").write_text("content")
        dst = tmp_path / "dstdir"
        with patch('install.USE_LINK', False):
            install_dir(src, dst)
        assert (dst / "file.txt").exists()

    def test_link_dir(self, tmp_path):
        src = tmp_path / "srcdir"
        src.mkdir()
        dst = tmp_path / "dstdir"
        with patch('install.USE_LINK', True):
            install_dir(src, dst)
        assert dst.is_symlink()

    def test_copy_replaces_broken_dir_symlink(self, tmp_path):
        src = tmp_path / "srcdir"
        src.mkdir()
        (src / "file.txt").write_text("content")
        dst = tmp_path / "dstdir"
        dst.symlink_to(tmp_path / "missingdir")
        with patch('install.USE_LINK', False):
            install_dir(src, dst)
        assert not dst.is_symlink()
        assert (dst / "file.txt").read_text() == "content"


class TestCheckCommand:
    def test_existing(self):
        assert check_command("python3") is not None

    def test_missing(self):
        assert check_command("nonexistent_xyz") is None


class TestRegisterHooks:
    def test_creates_config(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        register_hooks(cfg, "Test", "/monitor.py", "/context.py")
        d = json.loads(cfg.read_text())
        assert "PostToolUse" in d["hooks"]
        assert "SessionStart" in d["hooks"]

    def test_idempotent(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        register_hooks(cfg, "Test", "/monitor.py", "/context.py")
        register_hooks(cfg, "Test", "/monitor.py", "/context.py")
        d = json.loads(cfg.read_text())
        post = [i for i in d["hooks"]["PostToolUse"]
                if any("monitor" in h.get("command","") for h in i.get("hooks",[]))]
        assert len(post) == 1

    def test_preserves_other(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        cfg.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Grep", "hooks": []}]}}))
        register_hooks(cfg, "Test", "/monitor.py", "/context.py")
        d = json.loads(cfg.read_text())
        assert "PreToolUse" in d["hooks"]


class TestRegisterCodexGitnexusWrapper:
    def _wrapper_cmds(self, d, event):
        return [h.get("command", "")
                for i in d["hooks"].get(event, []) for h in i.get("hooks", [])
                if "gitnexus-codex-hook" in h.get("command", "")]

    def test_registers_pre_and_post(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        register_codex_gitnexus_wrapper(cfg, "/w/gitnexus-codex-hook.cjs")
        d = json.loads(cfg.read_text())
        assert self._wrapper_cmds(d, "PreToolUse")
        assert self._wrapper_cmds(d, "PostToolUse")

    def test_idempotent(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        register_codex_gitnexus_wrapper(cfg, "/w/gitnexus-codex-hook.cjs")
        register_codex_gitnexus_wrapper(cfg, "/w/gitnexus-codex-hook.cjs")
        d = json.loads(cfg.read_text())
        assert len(self._wrapper_cmds(d, "PreToolUse")) == 1
        assert len(self._wrapper_cmds(d, "PostToolUse")) == 1

    def test_preserves_harness_monitor_post(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        register_hooks(cfg, "Codex", "/monitor.py", "/context.py")
        register_codex_gitnexus_wrapper(cfg, "/w/gitnexus-codex-hook.cjs")
        d = json.loads(cfg.read_text())
        post_cmds = [h.get("command", "") for i in d["hooks"]["PostToolUse"] for h in i.get("hooks", [])]
        assert any("monitor" in c for c in post_cmds)
        assert any("gitnexus-codex-hook" in c for c in post_cmds)
        assert "SessionStart" in d["hooks"]


class TestNodeMajor:
    def test_parses_major(self):
        from install import _node_major
        assert _node_major("v18.17.0") == 18
        assert _node_major("v20.1.0") == 20

    def test_old_version(self):
        from install import _node_major
        assert _node_major("v16.20.0") == 16

    def test_unparseable(self):
        from install import _node_major
        assert _node_major("garbage") is None
