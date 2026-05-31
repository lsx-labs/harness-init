"""Tests for uninstall.py"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from uninstall import cleanup_hooks


def _commands(cfg_path):
    d = json.loads(cfg_path.read_text())
    return [h.get("command", "")
            for ev in d.get("hooks", {}).values()
            for item in ev for h in item.get("hooks", [])]


class TestCleanupHooks:
    def test_removes_codex_gitnexus_wrapper_and_monitor(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        cfg.write_text(json.dumps({"hooks": {
            "PreToolUse": [{"matcher": "Grep|Glob|Bash",
                            "hooks": [{"command": 'node "/x/gitnexus-codex-hook.cjs"'}]}],
            "PostToolUse": [
                {"hooks": [{"command": 'node "/x/gitnexus-codex-hook.cjs"'}]},
                {"hooks": [{"command": "python3 /x/harness_monitor.py"}]},
            ],
        }}))
        cleanup_hooks(cfg, "Codex")
        cmds = _commands(cfg)
        assert not any("gitnexus-codex-hook" in c for c in cmds)
        assert not any("harness_monitor" in c for c in cmds)

    def test_preserves_unrelated_hooks(self, tmp_path):
        cfg = tmp_path / "hooks.json"
        cfg.write_text(json.dumps({"hooks": {
            "PreToolUse": [{"hooks": [{"command": "node /x/some-other-hook.cjs"}]}],
        }}))
        cleanup_hooks(cfg, "Codex")
        assert any("some-other-hook" in c for c in _commands(cfg))


class TestUninstallMain:
    def test_removes_jobs_and_projects_dirs(self, tmp_path, monkeypatch):
        import uninstall
        share = tmp_path / ".local" / "share" / "harness-hooks"
        (share / "jobs").mkdir(parents=True)
        (share / "projects").mkdir(parents=True)
        monkeypatch.setattr(uninstall, "HOME", tmp_path)
        uninstall.main()
        assert not (share / "jobs").exists()
        assert not (share / "projects").exists()

    def test_preserves_claude_gitnexus_hook(self, tmp_path, monkeypatch):
        # install.py only copies this hook when it's absent — if it already existed it is
        # GitNexus's OWN file, so uninstall must NOT delete it (would break GitNexus).
        import uninstall
        hook = tmp_path / ".claude" / "hooks" / "gitnexus" / "gitnexus-hook.cjs"
        hook.parent.mkdir(parents=True)
        hook.write_text("// belongs to GitNexus")
        monkeypatch.setattr(uninstall, "HOME", tmp_path)
        uninstall.main()
        assert hook.exists()
