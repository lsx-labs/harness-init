"""Tests for shared GitNexus parsing/mapping helpers and AI-CLI constants."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import harness_shared


class TestParseGitnexusMarkdown:
    def test_dict_form(self) -> None:
        assert harness_shared.parse_gitnexus_markdown(json.dumps({"markdown": "x"})) == "x"

    def test_list_of_dicts_form(self) -> None:
        assert harness_shared.parse_gitnexus_markdown(json.dumps([{"markdown": "y"}])) == "y"

    def test_list_of_scalars_form(self) -> None:
        assert harness_shared.parse_gitnexus_markdown(json.dumps(["z"])) == "z"

    def test_invalid_json_returns_empty(self) -> None:
        assert harness_shared.parse_gitnexus_markdown("not json") == ""


class TestGitnexusMarkdownRows:
    def test_drops_header_and_separator(self) -> None:
        md = "| area | syms |\n|---|---|\n| src | 10 |\n| lib | 20 |"
        assert harness_shared.gitnexus_markdown_rows(md) == [["src", "10"], ["lib", "20"]]

    def test_too_short_returns_empty(self) -> None:
        assert harness_shared.gitnexus_markdown_rows("| a |\n|--|") == []


class TestMapAreasToDirs:
    def test_matches_on_leaf_segment(self) -> None:
        assert harness_shared.map_areas_to_dirs(["api"], ["src/api", "src/core"]) == {"api": "src/api"}

    def test_underscore_prefix_normalized(self) -> None:
        assert harness_shared.map_areas_to_dirs(["_lib"], ["src/lib"]) == {"_lib": "src/lib"}

    def test_ambiguous_leaf_is_omitted(self) -> None:
        # two folders share the leaf "utils" → ambiguous → skip (don't mis-attribute symbols)
        assert harness_shared.map_areas_to_dirs(["utils"], ["a/utils", "b/utils"]) == {}

    def test_unmatched_area_omitted(self) -> None:
        assert harness_shared.map_areas_to_dirs(["ghost"], ["src/api"]) == {}


class TestReadDirDocstring:
    def test_em_dash_prefix_stripped(self, tmp_path) -> None:
        (tmp_path / "__init__.py").write_text('"""mypkg — does the thing."""\n')
        assert harness_shared.read_dir_docstring(str(tmp_path)) == "does the thing."

    def test_spaced_hyphen_prefix_stripped(self, tmp_path) -> None:
        (tmp_path / "__init__.py").write_text('"""mypkg - core utilities."""\n')
        assert harness_shared.read_dir_docstring(str(tmp_path)) == "core utilities."

    def test_internal_hyphen_preserved(self, tmp_path) -> None:
        (tmp_path / "__init__.py").write_text('"""Utilities for x-ray image pre-processing."""\n')
        assert harness_shared.read_dir_docstring(str(tmp_path)) == "Utilities for x-ray image pre-processing."


class TestParseCodemapEncoding:
    def test_tolerates_invalid_utf8(self, tmp_path) -> None:
        # a botched manual edit / merge / wrong-encoding save must not raise UnicodeDecodeError
        p = tmp_path / "CODE_MAP.md"
        p.write_bytes(b"### src/ (10 symbols)\n\xff\xfe garbage\n- **api/** (2 symbols)\n")
        entries = harness_shared.parse_codemap(p)
        assert any(e["dir"] == "src" for e in entries)


class TestPathKey:
    def test_sanitizes_absolute_path(self) -> None:
        assert harness_shared.path_key("/a/b/c") == "a_b_c"

    def test_distinguishes_same_basename(self) -> None:
        # the whole point: two repos named "myproject" under different parents must not collide
        assert harness_shared.path_key("/home/x/myproject") != harness_shared.path_key("/home/y/myproject")


def test_codex_exec_sandbox_args_are_read_only_and_non_escalating() -> None:
    args = harness_shared.CODEX_EXEC_SANDBOX_ARGS
    assert "read-only" in args
    assert "approval_policy=never" in args


class TestGetAiCmd:
    def test_falls_back_to_codex_app(self):
        with patch.object(harness_shared.shutil, "which", return_value=None), \
             patch.object(harness_shared.os.path, "isfile", return_value=True):
            assert harness_shared.get_ai_cmd() == "/Applications/Codex.app/Contents/Resources/codex"

    def test_returns_empty_when_nothing_available(self):
        with patch.object(harness_shared.shutil, "which", return_value=None), \
             patch.object(harness_shared.os.path, "isfile", return_value=False):
            assert harness_shared.get_ai_cmd() == ""
