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
    get_recent_commits, check_gitnexus_stale, check_codemap_stale
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
