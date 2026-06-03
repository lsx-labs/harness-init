"""Tests for session_context.py"""

import json
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import harness_shared
from session_context import (
    run_git, get_branch, get_ahead_behind, get_dirty_files,
    get_recent_commits, check_gitnexus_stale, check_codemap_stale,
    read_pending_notifications
)


class TestRunGit:
    def test_success(self):
        with patch('session_context.subprocess.run') as mock:
            mock.return_value = MagicMock(returncode=0, stdout="main\n")
            assert run_git("branch", "--show-current") == "main"

    def test_failure_returns_default(self):
        with patch('session_context.subprocess.run') as mock:
            mock.return_value = MagicMock(returncode=1, stdout="")
            assert run_git("branch", "--show-current", default="detached") == "detached"

    def test_timeout_returns_default(self):
        with patch('session_context.subprocess.run', side_effect=subprocess.TimeoutExpired("git", 3)):
            assert run_git("status", default="timeout") == "timeout"

    def test_not_found_returns_default(self):
        with patch('session_context.subprocess.run', side_effect=FileNotFoundError):
            assert run_git("status", default="missing") == "missing"


class TestGetBranch:
    def test_normal_branch(self):
        with patch('session_context.run_git', return_value="feature/auth"):
            assert get_branch() == "feature/auth"

    def test_detached(self):
        # in detached HEAD, `git branch --show-current` exits 0 with EMPTY stdout
        with patch('session_context.run_git', return_value=""):
            assert get_branch() == "detached HEAD"


class TestGetAheadBehind:
    def test_ahead_behind(self):
        with patch('session_context.run_git', return_value="2\t3"):
            result = get_ahead_behind()
            assert "↑3" in result
            assert "↓2" in result

    def test_empty(self):
        with patch('session_context.run_git', return_value=""):
            assert get_ahead_behind() == ""

    def test_resolves_default_branch_when_no_main_no_upstream(self):
        # master-only repo, no upstream: must compare vs master, not a hardcoded "main".
        def fake(*args, **kwargs):
            if "@{upstream}" in args:
                return ""  # no upstream tracking branch
            if args[:2] == ("rev-parse", "--verify"):
                return "ok" if "master" in args else ""  # only master exists
            if args and args[0] == "rev-list":
                return "1\t2"
            return ""
        with patch('session_context.run_git', side_effect=fake):
            result = get_ahead_behind()
        assert "↑2" in result and "↓1" in result and "master" in result


class TestGetDirtyFiles:
    def test_clean(self):
        with patch('session_context.run_git', return_value=""):
            count, modules = get_dirty_files()
            assert count == 0
            assert modules == ""

    def test_dirty(self):
        # run_git returns .strip()'d output, so the FIRST line has no leading space.
        with patch('session_context.run_git', return_value="M src/auth.py\n M src/api/routes.py\n?? README.md"):
            count, modules = get_dirty_files()
            assert count == 3
            assert "src" in modules

    def test_first_line_module_survives_run_git_strip(self):
        # Drive the REAL run_git (.strip()) via subprocess so the leading-space loss on the
        # first porcelain line is exercised — the first file's module must not lose a char.
        raw = " M tests/a.py\n M src/b.py\n"  # what `git status --porcelain` emits
        with patch('session_context.subprocess.run',
                   return_value=MagicMock(returncode=0, stdout=raw, stderr="")):
            count, modules = get_dirty_files()
        assert count == 2
        assert sorted(modules.split()) == ["src", "tests"]  # first module not truncated to 'ests'

    def test_count_not_capped_at_10(self):
        raw = "\n".join(f" M src/file{i}.py" for i in range(15))
        with patch('session_context.run_git', return_value=raw):
            count, _ = get_dirty_files()
            assert count == 15

    def test_modules_reflect_all_changes_not_just_first_10(self):
        # 10 files in a/, then 2 in b/ → b must still appear so count and module list agree
        raw = "\n".join([f" M a/f{i}.py" for i in range(10)] + [" M b/x.py", " M b/y.py"])
        with patch('session_context.run_git', return_value=raw):
            count, modules = get_dirty_files()
        assert count == 12
        assert "a" in modules.split() and "b" in modules.split()

    def test_handles_renames_and_quoted_paths(self):
        raw = 'R  "old.py" -> "src/new.py"\n?? "lib dir/x.py"'
        with patch('session_context.run_git', return_value=raw):
            count, modules = get_dirty_files()
        assert count == 2
        assert "src" in modules        # rename destination dir
        assert "lib dir" in modules    # space-in-path dir, unquoted
        assert '"' not in modules      # no stray quote characters


class TestGetRecentCommits:
    def test_with_commits(self):
        log_output = "abc1234 fix: auth bug\ndef5678 feat: add login"
        with patch('session_context.run_git') as mock:
            def side_effect(*args, **kwargs):
                if args[0] == "log":
                    return log_output
                if args[0] == "diff-tree":
                    return "src/auth.py"
                return "2 hours"
            mock.side_effect = side_effect
            commits = get_recent_commits(limit=2)
            assert len(commits) == 2
            assert commits[0]["hash"] == "abc1234"

    def test_no_commits(self):
        with patch('session_context.run_git', return_value=""):
            assert get_recent_commits() == []


class TestCheckGitnexusStale:
    def test_no_gitnexus_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = check_gitnexus_stale()
        assert "未索引" in result

    def test_stale(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        with patch('session_context.subprocess.run') as mock:
            mock.return_value = MagicMock(stdout="Status: stale", stderr="")
            result = check_gitnexus_stale()
            assert "过期" in result

    def test_up_to_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        with patch('session_context.subprocess.run') as mock:
            mock.return_value = MagicMock(stdout="Status: up-to-date", stderr="")
            result = check_gitnexus_stale()
            assert result is None


class TestCheckCodemapStale:
    def test_no_codemap(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert check_codemap_stale() is None

    def test_materializes_missing_codemap_from_shared_cache(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        cache = harness_shared.codemap_cache_path(tmp_path)
        cache.parent.mkdir(parents=True)
        cache.write_text("### src/ (100 symbols) — Core module\n", encoding="utf-8")

        assert check_codemap_stale() is None

        assert (tmp_path / "CODE_MAP.md").read_text(encoding="utf-8") == cache.read_text(encoding="utf-8")

    def test_has_stale(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols) — ⚠️ 描述可能过期\n")
        result = check_codemap_stale()
        assert "待更新" in result

    def test_low_confidence_description_counts_as_stale(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols) — ⚠️ run_combo / load_data\n")
        result = check_codemap_stale()
        assert "待更新" in result

    def test_clean(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols) — Core module\n")
        assert check_codemap_stale() is None

    def test_invalid_utf8_does_not_crash(self, tmp_path, monkeypatch):
        # a corrupt CODE_MAP.md must not blow up the whole SessionStart hook (must not raise)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_bytes(b"### src/ (10 symbols)\n\xff\xfe\n")
        result = check_codemap_stale()
        assert result is None or isinstance(result, str)


# ── Additional coverage tests ──

from session_context import main as sc_main


class TestGetAheadBehindMalformed:
    """Cover line 33: malformed rev-list output (not 2 parts)."""

    def test_single_part_returns_empty(self):
        with patch('session_context.run_git', return_value="5"):
            assert get_ahead_behind() == ""


class TestGetRecentCommitsEdgeCases:
    """empty lines and single-part lines in git log."""

    def test_empty_line_in_log(self):
        """Line 53: empty lines are skipped."""
        log_output = "abc1234 fix: auth bug\n\ndef5678 feat: add login"
        with patch('session_context.run_git') as mock:
            def side_effect(*args, **kwargs):
                if args[0] == "log":
                    return log_output
                if args[0] == "diff-tree":
                    return "src/auth.py"
                return "2 hours"
            mock.side_effect = side_effect
            commits = get_recent_commits(limit=2)
            assert len(commits) == 2

    def test_single_part_line_skipped(self):
        """Line 56: line with only hash (no message) is skipped."""
        log_output = "abc1234\ndef5678 feat: add login"
        with patch('session_context.run_git') as mock:
            def side_effect(*args, **kwargs):
                if args[0] == "log":
                    return log_output
                if args[0] == "diff-tree":
                    return "src/auth.py"
                return "2 hours"
            mock.side_effect = side_effect
            commits = get_recent_commits(limit=2)
            assert len(commits) == 1
            assert commits[0]["hash"] == "def5678"

    def test_module_mapping_from_diff_tree(self):
        """Cover module extraction from diff-tree (root fallback)."""
        log_output = "abc1234 fix: root level"
        with patch('session_context.run_git') as mock:
            def side_effect(*args, **kwargs):
                if args[0] == "log":
                    if "--format=%cr" in args:
                        return "5 minutes"
                    return log_output
                if args[0] == "diff-tree":
                    return ""  # empty → "root"
                return ""
            mock.side_effect = side_effect
            commits = get_recent_commits(limit=1)
            assert len(commits) == 1
            assert commits[0]["module"] == "root"

    def test_sanitizes_hostile_commit_message(self):
        # a commit message is attacker-influenced; control chars must be stripped and length capped
        evil = "feat: ok\x07\x1b[31m" + "X" * 300
        with patch('session_context.run_git') as mock:
            def side_effect(*args, **kwargs):
                if args[0] == "log":
                    if "--format=%cr" in args:
                        return "1 min"
                    return f"abc1234 {evil}"
                if args[0] == "diff-tree":
                    return "src/x.py"
                return ""
            mock.side_effect = side_effect
            commits = get_recent_commits(limit=1)
        msg = commits[0]["msg"]
        assert "\x07" not in msg and "\x1b" not in msg
        assert len(msg) <= 120

    def test_root_level_file_labeled_root(self):
        """A commit touching only a root-level file → module is "root", not the filename."""
        log_output = "abc1234 docs: tweak readme"
        with patch('session_context.run_git') as mock:
            def side_effect(*args, **kwargs):
                if args[0] == "log":
                    if "--format=%cr" in args:
                        return "5 minutes"
                    return log_output
                if args[0] == "diff-tree":
                    return "README.md"  # top-level file → "root"
                return ""
            mock.side_effect = side_effect
            commits = get_recent_commits(limit=1)
            assert commits[0]["module"] == "root"


class TestCheckGitnexusStaleTimeout:
    """timeout during gitnexus status."""

    def test_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        with patch('session_context.subprocess.run',
                   side_effect=subprocess.TimeoutExpired("npx", 5)):
            result = check_gitnexus_stale()
            assert result is None


class TestCheckCodemapStaleOSError:
    """OSError reading CODE_MAP.md."""

    def test_oserror(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("test")
        with patch.object(Path, 'read_text', side_effect=OSError("disk error")):
            result = check_codemap_stale()
            assert result is None


class TestReadPendingNotifications:
    """Cover read_pending_notifications."""

    def test_no_notification_file(self, tmp_path, monkeypatch):
        import session_context as sc
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sc, 'NOTIFY_DIR', tmp_path / "notifications")
        assert read_pending_notifications() == []

    def test_reads_and_deletes(self, tmp_path, monkeypatch):
        import session_context as sc
        monkeypatch.chdir(tmp_path)
        notify_dir = tmp_path / "notifications"
        notify_dir.mkdir()
        notify_file = notify_dir / f"{sc.path_key(str(tmp_path))}.json"
        messages = ["📊 GitNexus 建议", "📊 LSP 建议"]
        notify_file.write_text(json.dumps(messages))
        monkeypatch.setattr(sc, 'NOTIFY_DIR', notify_dir)
        result = read_pending_notifications()
        assert result == messages
        assert not notify_file.exists()

    def test_corrupted_file(self, tmp_path, monkeypatch):
        import session_context as sc
        monkeypatch.chdir(tmp_path)
        notify_dir = tmp_path / "notifications"
        notify_dir.mkdir()
        (notify_dir / f"{sc.path_key(str(tmp_path))}.json").write_text("not json{")
        monkeypatch.setattr(sc, 'NOTIFY_DIR', notify_dir)
        assert read_pending_notifications() == []


class TestMainFunction:
    """Cover main() output."""

    def test_main_clean_workspace(self, capsys):
        with patch('session_context.get_branch', return_value="main"), \
             patch('session_context.get_ahead_behind', return_value="(↑0 ↓0 vs main)"), \
             patch('session_context.get_dirty_files', return_value=(0, "")), \
             patch('session_context.get_recent_commits', return_value=[]), \
             patch('session_context.check_gitnexus_stale', return_value=None), \
             patch('session_context.check_codemap_stale', return_value=None), \
             patch('session_context.read_pending_notifications', return_value=[]):
            sc_main()
        out = capsys.readouterr().out
        assert "main" in out
        assert "干净" in out
        assert "无提交历史" in out

    def test_main_dirty_workspace_with_commits(self, capsys):
        commits = [
            {"hash": "abc1234", "ago": "2 hours", "msg": "fix: bug", "module": "src"},
            {"hash": "def5678", "ago": "3 hours", "msg": "feat: login", "module": "auth"},
        ]
        with patch('session_context.get_branch', return_value="feature/auth"), \
             patch('session_context.get_ahead_behind', return_value="(↑2 ↓0 vs main)"), \
             patch('session_context.get_dirty_files', return_value=(3, "src auth")), \
             patch('session_context.get_recent_commits', return_value=commits), \
             patch('session_context.check_gitnexus_stale', return_value="⚠️ GitNexus 索引过期"), \
             patch('session_context.check_codemap_stale', return_value="⚠️ CODE_MAP.md: 2 个目录描述待更新"), \
             patch('session_context.read_pending_notifications', return_value=[]):
            sc_main()
        out = capsys.readouterr().out
        assert "feature/auth" in out
        assert "3 个文件变更" in out
        assert "最近提交:" in out
        assert "abc1234" in out
        assert "GitNexus 索引过期" in out
        assert "CODE_MAP.md" in out

    def test_main_with_pending_notifications(self, capsys):
        with patch('session_context.get_branch', return_value="main"), \
             patch('session_context.get_ahead_behind', return_value=""), \
             patch('session_context.get_dirty_files', return_value=(0, "")), \
             patch('session_context.get_recent_commits', return_value=[]), \
             patch('session_context.check_gitnexus_stale', return_value=None), \
             patch('session_context.check_codemap_stale', return_value=None), \
             patch('session_context.read_pending_notifications',
                   return_value=["📊 建议安装 GitNexus"]):
            sc_main()
        out = capsys.readouterr().out
        assert "建议安装 GitNexus" in out

    def test_main_no_warnings(self, capsys):
        with patch('session_context.get_branch', return_value="develop"), \
             patch('session_context.get_ahead_behind', return_value=""), \
             patch('session_context.get_dirty_files', return_value=(1, "README.md")), \
             patch('session_context.get_recent_commits', return_value=[
                 {"hash": "aaa1111", "ago": "1 min", "msg": "docs: update", "module": "root"}
             ]), \
             patch('session_context.check_gitnexus_stale', return_value=None), \
             patch('session_context.check_codemap_stale', return_value=None), \
             patch('session_context.read_pending_notifications', return_value=[]):
            sc_main()
        out = capsys.readouterr().out
        assert "develop" in out
        assert "1 个文件变更" in out
