"""Tests for sync_docs.py"""

import json
import os
import time
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import sync_docs as sd


class TestPlatformFiles:
    def test_claude(self):
        assert sd.platform_files("claude") == ("CLAUDE.md", "AGENTS.md")

    def test_codex(self):
        assert sd.platform_files("codex") == ("AGENTS.md", "CLAUDE.md")


class TestSyncOne:
    def test_own_missing_other_exists(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("content from codex", encoding="utf-8")
        result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")
        assert result["action"] == "copy"
        assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "content from codex"

    def test_both_exist_own_newer(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("old", encoding="utf-8")
        time.sleep(0.05)
        (tmp_path / "CLAUDE.md").write_text("new", encoding="utf-8")
        result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")
        assert result["action"] == "sync"
        assert result["from"] == "CLAUDE.md"
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "new"

    def test_both_exist_other_newer(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("old", encoding="utf-8")
        time.sleep(0.05)
        (tmp_path / "AGENTS.md").write_text("new", encoding="utf-8")
        result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")
        assert result["action"] == "sync"
        assert result["from"] == "AGENTS.md"
        assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "new"

    def test_neither_exists(self, tmp_path):
        assert sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md") is None

    def test_only_own_exists(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("content", encoding="utf-8")
        assert sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md") is None


class TestFindDocDirs:
    def test_finds_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("root")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "CLAUDE.md").write_text("sub")
        deep = sub / "core"
        deep.mkdir()
        (deep / "AGENTS.md").write_text("deep")
        dirs = sd.find_doc_dirs()
        assert "." in dirs
        assert "src" in dirs or "./src" in dirs

    def test_skips_hidden(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        git = tmp_path / ".git"
        git.mkdir()
        (git / "CLAUDE.md").write_text("nope")
        dirs = sd.find_doc_dirs()
        assert not any(".git" in d for d in dirs)
