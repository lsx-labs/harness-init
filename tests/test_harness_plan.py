"""Tests for harness_plan.py"""

import json
import os
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import harness_plan as hp


class TestPlatformFiles:
    def test_claude(self):
        assert hp.platform_files("claude") == ("CLAUDE.md", "AGENTS.md")

    def test_codex(self):
        assert hp.platform_files("codex") == ("AGENTS.md", "CLAUDE.md")


class TestPlanRootDoc:
    def test_own_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Project")
        assert hp.plan_root_doc("CLAUDE.md", "AGENTS.md") == {"action": "skip"}

    def test_other_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# Project")
        result = hp.plan_root_doc("CLAUDE.md", "AGENTS.md")
        assert result == {"action": "copy", "from": "AGENTS.md"}

    def test_neither_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert hp.plan_root_doc("CLAUDE.md", "AGENTS.md") == {"action": "generate"}


class TestParseCodemapEntries:
    def test_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert hp.parse_codemap_entries() == []

    def test_with_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "# Code Map\n\n"
            "### src/ (200 symbols) — Core module\n"
            "- **api/** — REST endpoints (50 symbols)\n"
            "### tests/ — Test suite\n",
            encoding="utf-8"
        )
        entries = hp.parse_codemap_entries()
        assert len(entries) == 3
        assert entries[0]["dir"] == "src"
        assert entries[0]["symbols"] == 200
        assert entries[0]["desc"] == "Core module"
        assert entries[1]["dir"] == "src/api"
        assert entries[1]["symbols"] == 50


class TestPlanCodemap:
    def test_all_described(self):
        entries = [{"dir": "src", "desc": "Core", "symbols": 100}]
        result = hp.plan_codemap(entries, {"src": 100})
        assert result["action"] == "skip"

    def test_empty_desc(self):
        entries = [{"dir": "src", "desc": "", "symbols": 100}]
        result = hp.plan_codemap(entries, {})
        assert result["action"] == "refresh"
        assert "src" in result["dirs_needing"]

    def test_stale(self):
        entries = [{"dir": "src", "desc": "Old desc", "symbols": 200}]
        result = hp.plan_codemap(entries, {"src": 100})
        assert result["action"] == "refresh"

    def test_manual_marker_skip(self):
        entries = [{"dir": "src", "desc": "📌 Manual", "symbols": 200}]
        result = hp.plan_codemap(entries, {"src": 100})
        assert result["action"] == "skip"

    def test_no_entries(self):
        result = hp.plan_codemap([], {})
        assert result["action"] == "skip"


class TestPlanGitnexus:
    def test_indexed_up_to_date(self):
        diag = {"existing": {"gitnexus": {"indexed": True, "up_to_date": True}}}
        assert hp.plan_gitnexus(diag) == {"action": "skip"}

    def test_indexed_stale(self):
        diag = {"existing": {"gitnexus": {"indexed": True, "up_to_date": False}}}
        assert hp.plan_gitnexus(diag) == {"action": "analyze"}

    def test_not_indexed_high_noise(self):
        diag = {"existing": {"gitnexus": {"indexed": False}},
                "grep_noise": {"grep_noise_files": 30}}
        assert hp.plan_gitnexus(diag) == {"action": "install_and_index"}

    def test_not_indexed_low_noise(self):
        diag = {"existing": {"gitnexus": {"indexed": False}},
                "grep_noise": {"grep_noise_files": 5}}
        assert hp.plan_gitnexus(diag) == {"action": "skip"}

    def test_not_indexed_medium_noise(self):
        diag = {"existing": {"gitnexus": {"indexed": False}},
                "grep_noise": {"grep_noise_files": 15}}
        assert hp.plan_gitnexus(diag) == {"action": "suggest_install"}


class TestFindComplexDirs:
    def test_finds_complex(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### src/ (200 symbols)\n"
            "- **api/** (150 symbols)\n"
            "- **utils/** (50 symbols)\n"
            "### tests/ (30 symbols)\n",
            encoding="utf-8"
        )
        dirs = hp.find_complex_dirs()
        assert "src" in dirs
        assert "src/api" in dirs
        assert "src/utils" not in dirs
        assert "tests" not in dirs


class TestPlanSubdirs:
    def test_skip_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "CLAUDE.md").write_text("existing")
        result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")
        assert result["skip"] == ["src"]
        assert result["copy"] == []
        assert result["generate"] == []

    def test_copy_from_other(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "AGENTS.md").write_text("existing")
        result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")
        assert len(result["copy"]) == 1
        assert result["copy"][0]["from"] == "AGENTS.md"

    def test_generate_new(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        result = hp.plan_subdirs(["src"], "CLAUDE.md", "AGENTS.md")
        assert len(result["generate"]) == 1
        assert result["generate"][0]["dir"] == "src"

    def test_layers_grouping(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for d in ["src", "src/core", "src/core/engine"]:
            (tmp_path / d).mkdir(exist_ok=True)
        result = hp.plan_subdirs(
            ["src", "src/core", "src/core/engine"],
            "CLAUDE.md", "AGENTS.md"
        )
        assert len(result["layers"]) >= 2
        deepest = result["layers"][0]
        assert deepest[0] == 3  # depth of src/core/engine


class TestPlanLsp:
    def test_installed(self):
        diag = {"lsp_assessment": [
            {"language": "Python", "installed": True, "recommend": True}
        ]}
        result = hp.plan_lsp(diag)
        assert result[0]["action"] == "skip"

    def test_recommend(self):
        diag = {"lsp_assessment": [
            {"language": "Python", "installed": False, "recommend": True,
             "plugin": "code-intelligence-python", "reason": "type coverage 50%"}
        ]}
        result = hp.plan_lsp(diag)
        assert result[0]["action"] == "recommend"

    def test_empty(self):
        assert hp.plan_lsp({}) == []
