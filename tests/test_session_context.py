"""Tests for session_context.py"""

import json
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
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
        with patch('session_context.run_git', return_value="detached HEAD"):
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


class TestGetDirtyFiles:
    def test_clean(self):
        with patch('session_context.run_git', return_value=""):
            count, modules = get_dirty_files()
            assert count == 0
            assert modules == ""

    def test_dirty(self):
        with patch('session_context.run_git', return_value=" M src/auth.py\n M src/api/routes.py\n?? README.md"):
            count, modules = get_dirty_files()
            assert count == 3
            assert "src" in modules


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

    def test_has_stale(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols) — ⚠️ 描述可能过期\n")
        result = check_codemap_stale()
        assert "待更新" in result

    def test_clean(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols) — Core module\n")
        assert check_codemap_stale() is None


# ── Additional coverage tests ──

from session_context import main as sc_main


class TestGetAheadBehindMalformed:
    """Cover line 33: malformed rev-list output (not 2 parts)."""

    def test_single_part_returns_empty(self):
        with patch('session_context.run_git', return_value="5"):
            assert get_ahead_behind() == ""


class TestGetRecentCommitsEdgeCases:
    """Cover lines 53, 56: empty lines and single-part lines in git log."""

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


class TestCheckGitnexusStaleTimeout:
    """Cover lines 74-75: timeout during gitnexus status."""

    def test_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        with patch('session_context.subprocess.run',
                   side_effect=subprocess.TimeoutExpired("npx", 5)):
            result = check_gitnexus_stale()
            assert result is None


class TestCheckCodemapStaleOSError:
    """Cover lines 87-88: OSError reading CODE_MAP.md."""

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
        notify_file = notify_dir / f"{tmp_path.name}.json"
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
        (notify_dir / f"{tmp_path.name}.json").write_text("not json{")
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
