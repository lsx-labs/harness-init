"""Tests for sync_docs.py"""

import json
import os
import time
from pathlib import Path
import sys
from unittest.mock import patch

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

    def test_equal_mtime_content_conflict_does_not_overwrite(self, tmp_path):
        own = tmp_path / "CLAUDE.md"
        other = tmp_path / "AGENTS.md"
        own.write_text("claude content", encoding="utf-8")
        other.write_text("codex content", encoding="utf-8")
        timestamp = 1_700_000_000
        os.utime(own, (timestamp, timestamp))
        os.utime(other, (timestamp, timestamp))

        result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")

        assert result["action"] == "conflict"
        assert own.read_text(encoding="utf-8") == "claude content"
        assert other.read_text(encoding="utf-8") == "codex content"

    def test_neither_exists(self, tmp_path):
        assert sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md") is None

    def test_only_own_exists(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("content", encoding="utf-8")
        assert sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md") is None

    def test_root_sync_updates_codemap_block_without_copying_whole_file(self, tmp_path):
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### scripts/ - Shared map\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text(
            "# Claude Rules\n\nKeep Claude-only guidance.\n\n@CODE_MAP.md\n",
            encoding="utf-8",
        )
        (tmp_path / "AGENTS.md").write_text(
            "# Codex Rules\n\nKeep Codex-only guidance.\n\n@CODE_MAP.md\n",
            encoding="utf-8",
        )

        result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")

        assert result["action"] == "codemap_block"
        claude_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "Keep Claude-only guidance." in claude_text
        assert "Keep Codex-only guidance." in agents_text
        assert "### scripts/ - Shared map" in claude_text
        assert "### scripts/ - Shared map" in agents_text

    def test_root_codemap_copies_missing_claude_then_renders_blocks(self, tmp_path):
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### scripts/ - Shared map\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text(
            "# Codex Rules\n\nKeep Codex guidance.\n\n@CODE_MAP.md\n",
            encoding="utf-8",
        )

        result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")

        assert result["action"] == "copy"
        assert result["from"] == "AGENTS.md"
        assert result["to"] == "CLAUDE.md"
        assert (tmp_path / "CLAUDE.md").exists()
        claude_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "### scripts/ - Shared map" in claude_text
        assert "### scripts/ - Shared map" in agents_text
        assert "files" in result

    def test_root_codemap_copies_missing_agents_with_codex_order_then_renders_blocks(self, tmp_path):
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### scripts/ - Shared map\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text(
            "# Claude Rules\n\nKeep Claude guidance.\n\n@CODE_MAP.md\n",
            encoding="utf-8",
        )

        result = sd.sync_one(str(tmp_path), "AGENTS.md", "CLAUDE.md")

        assert result["action"] == "copy"
        assert result["from"] == "CLAUDE.md"
        assert result["to"] == "AGENTS.md"
        assert (tmp_path / "AGENTS.md").exists()
        agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        claude_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "### scripts/ - Shared map" in agents_text
        assert "### scripts/ - Shared map" in claude_text
        assert "files" in result

    def test_root_sync_reports_write_failed_codemap_block(self, tmp_path):
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n", encoding="utf-8")

        with patch.object(sd, "update_root_codemap_docs", return_value={"CLAUDE.md": "write_failed"}):
            result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")

        assert result == {
            "dir": str(tmp_path),
            "action": "codemap_block",
            "files": {"CLAUDE.md": "write_failed"},
        }


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


class TestMain:
    def test_main_sync(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "AGENTS.md").write_text("content", encoding="utf-8")
        monkeypatch.setattr('sys.argv', ['sd', str(tmp_path), '--platform', 'claude'])
        sd.main()
        out = json.loads(capsys.readouterr().out)
        assert out["synced"] == 1
        assert (tmp_path / "CLAUDE.md").exists()

    def test_main_nothing_to_sync(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr('sys.argv', ['sd', str(tmp_path)])
        sd.main()
        out = json.loads(capsys.readouterr().out)
        assert out["synced"] == 0

    def test_main_counts_successful_root_codemap_block(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### scripts/ - Shared map\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# Claude Rules\n\n@CODE_MAP.md\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Codex Rules\n\n@CODE_MAP.md\n", encoding="utf-8")

        monkeypatch.setattr('sys.argv', ['sd', str(tmp_path), '--platform', 'claude'])
        sd.main()

        out = json.loads(capsys.readouterr().out)
        assert out["synced"] == 1
        assert out["actions"][0]["action"] == "codemap_block"

    def test_main_does_not_count_mixed_root_codemap_write_failure(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n", encoding="utf-8")

        with patch.object(
            sd,
            "update_root_codemap_docs",
            return_value={"CLAUDE.md": "write_failed", "AGENTS.md": "updated"},
        ):
            monkeypatch.setattr('sys.argv', ['sd', str(tmp_path), '--platform', 'claude'])
            sd.main()

        out = json.loads(capsys.readouterr().out)
        assert out["synced"] == 0
        assert out["actions"] == [
            {
                "dir": ".",
                "action": "codemap_block",
                "files": {"CLAUDE.md": "write_failed", "AGENTS.md": "updated"},
            }
        ]

    def test_main_does_not_count_copy_with_root_codemap_write_failure(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Codex Rules\n\n@CODE_MAP.md\n", encoding="utf-8")

        with patch.object(
            sd,
            "update_root_codemap_docs",
            return_value={"CLAUDE.md": "write_failed", "AGENTS.md": "updated"},
        ):
            monkeypatch.setattr('sys.argv', ['sd', str(tmp_path), '--platform', 'claude'])
            sd.main()

        out = json.loads(capsys.readouterr().out)
        assert out["synced"] == 0
        assert out["actions"] == [
            {
                "dir": ".",
                "action": "copy",
                "from": "AGENTS.md",
                "to": "CLAUDE.md",
                "files": {"CLAUDE.md": "write_failed", "AGENTS.md": "updated"},
            }
        ]

    def test_main_explicit_dirs(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("sub content", encoding="utf-8")
        monkeypatch.setattr('sys.argv', ['sd', str(tmp_path), '--platform', 'claude', '--dirs', 'src'])
        sd.main()
        out = json.loads(capsys.readouterr().out)
        assert out["synced"] >= 1
        assert (sub / "CLAUDE.md").exists()

    def test_main_codex_platform(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("content", encoding="utf-8")
        monkeypatch.setattr('sys.argv', ['sd', str(tmp_path), '--platform', 'codex'])
        sd.main()
        out = json.loads(capsys.readouterr().out)
        assert out["synced"] == 1
        assert (tmp_path / "AGENTS.md").exists()
