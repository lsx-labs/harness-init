"""Tests for generate_descriptions.py"""

import json
import os
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from generate_descriptions import (
    extract_desc, parse_codemap, write_descriptions,
    get_ai_cmd, fallback_generate, get_docstring, get_keywords,
    gitnexus_query, MANUAL_MARKER
)


class TestExtractDesc:
    def test_with_desc(self):
        assert extract_desc("(100 symbols) — Core module") == "Core module"

    def test_without_desc(self):
        assert extract_desc("(100 symbols)") == ""

    def test_with_stale(self):
        assert extract_desc("— ⚠️ 描述可能过期").startswith("⚠️")

    def test_with_pin(self):
        assert extract_desc(f"— {MANUAL_MARKER} My desc").startswith(MANUAL_MARKER)


class TestParseCodemap:
    def test_generate_mode_skips_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### src/ (100 symbols) — Existing desc\n"
            "- **api/** (50 symbols)\n"
        )
        dirs = parse_codemap("--generate")
        assert "src" not in dirs
        assert "src/api" in dirs  # no desc → needs one

    def test_refresh_mode_includes_all(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### src/ (100 symbols) — Existing desc\n"
            "- **api/** (50 symbols)\n"
        )
        dirs = parse_codemap("--refresh")
        assert "src" in dirs
        assert "src/api" in dirs

    def test_pin_protected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            f"### src/ (100 symbols) — {MANUAL_MARKER} Protected\n"
        )
        dirs = parse_codemap("--refresh")
        assert "src" not in dirs

    def test_no_codemap(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert parse_codemap("--generate") == []


class TestWriteDescriptions:
    def test_write_top_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        changes = write_descriptions({"src": "Core business logic"})
        assert len(changes) == 1
        content = (tmp_path / "CODE_MAP.md").read_text()
        assert "Core business logic" in content

    def test_write_sub_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n- **api/** (50 symbols)\n")
        changes = write_descriptions({"src/api": "REST endpoints"})
        assert len(changes) == 1
        content = (tmp_path / "CODE_MAP.md").read_text()
        assert "REST endpoints" in content

    def test_skip_empty_desc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        changes = write_descriptions({"src": ""})
        assert len(changes) == 0

    def test_truncate_long_desc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        changes = write_descriptions({"src": "x" * 100})
        assert len(changes[0]["desc"]) <= 60


class TestGetDocstring:
    def test_python_docstring(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mymodule"
        d.mkdir()
        (d / "__init__.py").write_text('"""My awesome module."""\n')
        assert get_docstring("mymodule") == "My awesome module."

    def test_python_with_separator(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mod"
        d.mkdir()
        (d / "__init__.py").write_text('"""mod — The main module."""\n')
        assert get_docstring("mod") == "The main module."

    def test_no_docstring(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mod"
        d.mkdir()
        (d / "__init__.py").write_text("x = 1\n")
        assert get_docstring("mod") == ""

    def test_no_init(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mod"
        d.mkdir()
        assert get_docstring("mod") == ""


class TestGetKeywords:
    def test_returns_keywords(self):
        with patch('generate_descriptions.gitnexus_query') as mock:
            mock.return_value = [["authenticate_user"], ["create_session"], ["validate_token"]]
            result = get_keywords("src/auth")
            assert "authenticate_user" in result

    def test_filters_generic(self):
        with patch('generate_descriptions.gitnexus_query') as mock:
            mock.return_value = [["main"], ["run"], ["authenticate_user"]]
            result = get_keywords("src")
            assert "main" not in result
            assert "run" not in result

    def test_empty_result(self):
        with patch('generate_descriptions.gitnexus_query', return_value=[]):
            assert get_keywords("empty") == ""


class TestGetAiCmd:
    def test_finds_claude(self):
        with patch('generate_descriptions.shutil.which', side_effect=lambda x: "/usr/bin/claude" if x == "claude" else None):
            assert get_ai_cmd() == "claude"

    def test_finds_codex(self):
        with patch('generate_descriptions.shutil.which', side_effect=lambda x: "/usr/bin/codex" if x == "codex" else None):
            assert get_ai_cmd() == "codex"

    def test_finds_nothing(self):
        with patch('generate_descriptions.shutil.which', return_value=None):
            with patch('generate_descriptions.os.path.isfile', return_value=False):
                assert get_ai_cmd() == ""


class TestFallbackGenerate:
    def test_fills_from_docstring(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mymod"
        d.mkdir()
        (d / "__init__.py").write_text('"""My module description."""\n')
        (tmp_path / "CODE_MAP.md").write_text("### mymod/ (50 symbols)\n")
        result = fallback_generate(["mymod"])
        assert "mymod" in result
        assert "My module description" in result["mymod"]
