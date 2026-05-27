"""Tests for harness-monitor.py"""

import json
import os
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import importlib.util

# Load module with hyphen in name
_spec = importlib.util.spec_from_file_location(
    "harness_monitor",
    os.path.join(os.path.dirname(__file__), '..', 'scripts', 'harness-monitor.py'))
hm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hm)


class TestShouldSkip:
    def test_skip_git(self):
        assert hm.should_skip(".git") is True

    def test_skip_venv(self):
        assert hm.should_skip(".venv") is True

    def test_skip_egg_info(self):
        assert hm.should_skip("package.egg-info") is True

    def test_keep_src(self):
        assert hm.should_skip("src") is False


class TestIsGitOperation:
    def test_git_commit(self):
        assert hm.is_git_operation({"tool_input": {"command": "git commit -m 'test'"}}) is True

    def test_git_merge(self):
        assert hm.is_git_operation({"tool_input": {"command": "git merge feature"}}) is True

    def test_not_git(self):
        assert hm.is_git_operation({"tool_input": {"command": "pytest tests/"}}) is False

    def test_empty(self):
        assert hm.is_git_operation({"tool_input": {"command": ""}}) is False


class TestIsOnMainBranch:
    def test_on_main(self):
        mock_result = MagicMock(returncode=0, stdout="main\n")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            assert hm.is_on_main_branch() is True

    def test_on_feature(self):
        mock_result = MagicMock(returncode=0, stdout="feature/auth\n")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            assert hm.is_on_main_branch() is False

    def test_timeout(self):
        with patch.object(hm.subprocess, 'run', side_effect=subprocess.TimeoutExpired("git", 3)):
            assert hm.is_on_main_branch() is False


class TestGetAiCmd:
    def test_finds_claude(self):
        with patch.object(hm.shutil, 'which', side_effect=lambda x: "/usr/bin/claude" if x == "claude" else None):
            assert hm.get_ai_cmd() == "claude"

    def test_finds_codex(self):
        with patch.object(hm.shutil, 'which', side_effect=lambda x: "/usr/bin/codex" if x == "codex" else None):
            assert hm.get_ai_cmd() == "codex"


class TestExtractDescAndCount:
    def test_desc_before_count(self):
        desc, count = hm._extract_desc_and_count("(100 symbols) — My description")
        assert desc == "My description"
        assert count == 100

    def test_only_count(self):
        desc, count = hm._extract_desc_and_count("(50 symbols)")
        assert desc == ""
        assert count == 50

    def test_empty(self):
        desc, count = hm._extract_desc_and_count("")
        assert desc == ""
        assert count is None


class TestParseExistingCodemap:
    def test_full_parse(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### autoresearch/ (4412 symbols) — Research platform\n"
            "- **distributed/** — Worker coordination (1608 symbols)\n"
            "- **_lib/** (327 symbols)\n"
        )
        descs, counts = hm.parse_existing_codemap(Path("CODE_MAP.md"))
        assert descs["autoresearch"] == "Research platform"
        assert counts["autoresearch"] == 4412

    def test_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        descs, counts = hm.parse_existing_codemap(Path("CODE_MAP.md"))
        assert descs == {}


class TestLoadSaveState:
    def test_default(self, tmp_path):
        state = hm.load_state(tmp_path / "none.json")
        assert state["file_count"] == 0

    def test_roundtrip(self, tmp_path):
        f = tmp_path / "state.json"
        hm.save_state(f, {"file_count": 42})
        state = hm.load_state(f)
        assert state["file_count"] == 42

    def test_corrupted(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text("bad json")
        state = hm.load_state(f)
        assert state["file_count"] == 0
