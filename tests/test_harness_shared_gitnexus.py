"""Tests for shared GitNexus parsing/mapping helpers and AI-CLI constants."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
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


class TestCodemapLocalProjection:
    def test_render_codemap_block_replaces_legacy_at_reference(self, tmp_path) -> None:
        doc = "# Project\n\n@CODE_MAP.md\n"
        codemap = "# Code Map\n\n### src/ — Core\n"

        rendered = harness_shared.render_codemap_block(doc, codemap)

        assert "@CODE_MAP.md" not in rendered
        assert "<!-- codemap:start -->" in rendered
        assert "### src/ — Core" in rendered

    def test_render_codemap_block_updates_existing_block_without_touching_other_text(self) -> None:
        doc = "# Project\n\nKeep this.\n\n<!-- codemap:start -->\nold\n<!-- codemap:end -->\n"
        codemap = "# Code Map\n\n### src/ — Core\n"

        rendered = harness_shared.render_codemap_block(doc, codemap)

        assert "Keep this." in rendered
        assert "old" not in rendered
        assert rendered.count("<!-- codemap:start -->") == 1

    def test_render_codemap_block_updates_existing_block_with_backslashes_literal(self) -> None:
        doc = "# Project\n\n<!-- codemap:start -->\nold\n<!-- codemap:end -->\n"
        codemap = "# Code Map\n\n### src/ — Regex \\1 \\g<x> \\d \\c and C:\\tmp\n"

        rendered = harness_shared.render_codemap_block(doc, codemap)

        assert "Regex \\1 \\g<x> \\d \\c and C:\\tmp" in rendered
        assert "old" not in rendered

    def test_render_codemap_block_updates_duplicate_existing_blocks(self) -> None:
        doc = (
            "# Project\n\n"
            "<!-- codemap:start -->\nold one\n<!-- codemap:end -->\n"
            "middle\n"
            "<!-- codemap:start -->\nold two\n<!-- codemap:end -->\n"
        )
        codemap = "# Code Map\n\n### src/ — Core\n"

        rendered = harness_shared.render_codemap_block(doc, codemap)

        assert "old one" not in rendered
        assert "old two" not in rendered
        assert rendered.count("### src/ — Core") == 2

    def test_render_codemap_block_does_not_duplicate_existing_heading_with_intervening_text(self) -> None:
        doc = (
            "# Project\n\n"
            "## CODE_MAP\n\n"
            "This short note is outside the generated block.\n\n"
            "<!-- codemap:start -->\nold\n<!-- codemap:end -->\n"
        )
        codemap = "# Code Map\n\n### src/ — Core\n"

        rendered = harness_shared.render_codemap_block(doc, codemap)

        assert rendered.count("## CODE_MAP") == 1
        assert "This short note is outside the generated block." in rendered
        assert "old" not in rendered
        assert "### src/ — Core" in rendered

    def test_update_root_codemap_docs_writes_both_docs_when_changed(self, tmp_path) -> None:
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### src/ — Core\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# Project\n\n@CODE_MAP.md\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Project\n\n@CODE_MAP.md\n", encoding="utf-8")

        result = harness_shared.update_root_codemap_docs(tmp_path)

        assert result == {"CLAUDE.md": "updated", "AGENTS.md": "updated"}
        assert "### src/ — Core" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "### src/ — Core" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    def test_update_root_codemap_docs_contains_single_doc_write_failure(self, tmp_path) -> None:
        (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### src/ — Core\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# Project\n\n@CODE_MAP.md\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Project\n\n@CODE_MAP.md\n", encoding="utf-8")

        original_write = harness_shared._atomic_write_text

        def flaky_write(path, content):
            if path.name == "CLAUDE.md":
                raise OSError("read-only")
            original_write(path, content)

        with patch.object(harness_shared, "_atomic_write_text", side_effect=flaky_write):
            result = harness_shared.update_root_codemap_docs(tmp_path)

        assert result == {"CLAUDE.md": "write_failed", "AGENTS.md": "updated"}
        assert "@CODE_MAP.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "### src/ — Core" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    def test_cache_key_uses_git_common_dir_for_worktree_sharing(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo" / "worktree"
        project.mkdir(parents=True)
        common = tmp_path / "repo" / ".git"
        common.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")

        def fake_run(args, **kwargs):
            assert args[:3] == ["git", "-C", str(project)]
            return type("Result", (), {"returncode": 0, "stdout": str(common) + "\n"})()

        with patch.object(harness_shared.subprocess, "run", side_effect=fake_run):
            cache_path = harness_shared.codemap_cache_path(project)

        assert cache_path == tmp_path / "cache" / harness_shared.path_key(common) / "CODE_MAP.md"

    def test_codemap_counts_cache_path_shares_codemap_cache_key(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        common = project / ".git"
        common.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": common)

        path = harness_shared.codemap_counts_cache_path(project)

        assert path == tmp_path / "cache" / harness_shared.path_key(common) / "CODE_MAP.counts.json"

    def test_read_write_codemap_counts_round_trip(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")

        assert harness_shared.write_codemap_counts(project, {"src": 10, "src/api": 3}) is True
        assert harness_shared.read_codemap_counts(project) == {"src": 10, "src/api": 3}

    def test_read_codemap_counts_accepts_legacy_counts_key(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")
        cache = harness_shared.codemap_counts_cache_path(project)
        cache.parent.mkdir(parents=True)
        cache.write_text('{"schema_version": 1, "counts": {"src": 10}}\n', encoding="utf-8")

        assert harness_shared.read_codemap_counts(project) == {"src": 10}

    def test_read_codemap_counts_wrong_top_level_json_shape_returns_empty(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")
        cache = harness_shared.codemap_counts_cache_path(project)
        cache.parent.mkdir(parents=True)

        for content in ("[]\n", "null\n", '"counts"\n'):
            cache.write_text(content, encoding="utf-8")
            assert harness_shared.read_codemap_counts(project) == {}

    def test_read_codemap_counts_nested_malformed_shape_returns_empty(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")
        cache = harness_shared.codemap_counts_cache_path(project)
        cache.parent.mkdir(parents=True)

        for payload in ({"described_counts": []}, {"described_counts": None}):
            cache.write_text(json.dumps(payload), encoding="utf-8")
            assert harness_shared.read_codemap_counts(project) == {}

    def test_read_codemap_counts_ignores_empty_normalized_keys(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")
        cache = harness_shared.codemap_counts_cache_path(project)
        cache.parent.mkdir(parents=True)
        cache.write_text(
            json.dumps({
                "schema_version": 1,
                "described_counts": {
                    "/": 7,
                    "///": 8,
                    "/src/": 2,
                },
            }),
            encoding="utf-8",
        )

        assert harness_shared.read_codemap_counts(project) == {"src": 2}

    def test_read_codemap_counts_ignores_bool_negative_and_non_integer_values(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")
        cache = harness_shared.codemap_counts_cache_path(project)
        cache.parent.mkdir(parents=True)
        cache.write_text(
            json.dumps({
                "schema_version": 1,
                "described_counts": {
                    "src": 10,
                    "flag": True,
                    "disabled": False,
                    "negative": -1,
                    "float": 2.5,
                    "string": "3",
                },
            }),
            encoding="utf-8",
        )

        assert harness_shared.read_codemap_counts(project) == {"src": 10}

    def test_write_codemap_counts_ignores_bool_values(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")

        assert harness_shared.write_codemap_counts(project, {"src": 10, "flag": True, "disabled": False}) is True

        cache = harness_shared.codemap_counts_cache_path(project)
        payload = json.loads(cache.read_text(encoding="utf-8"))
        assert payload["described_counts"] == {"src": 10}

    def test_write_codemap_counts_returns_false_when_atomic_write_fails(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")

        def fail_write(path, content) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(harness_shared, "_atomic_write_text", fail_write)

        assert harness_shared.write_codemap_counts(project, {"src": 10}) is False

    def test_materializes_missing_local_codemap_from_shared_cache(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        cache = harness_shared.codemap_cache_path(project)
        cache.parent.mkdir(parents=True)
        cache.write_text("# Code Map\n\n### src/ — Cached\n", encoding="utf-8")

        assert harness_shared.materialize_codemap_projection(project) is True

        assert (project / "CODE_MAP.md").read_text(encoding="utf-8") == cache.read_text(encoding="utf-8")

    def test_cache_codemap_projection_updates_shared_cache(self, tmp_path, monkeypatch) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
        (project / "CODE_MAP.md").write_text("# Code Map\n\n### src/ — Local\n", encoding="utf-8")

        assert harness_shared.cache_codemap_projection(project) is True

        cache = harness_shared.codemap_cache_path(project)
        assert cache.read_text(encoding="utf-8") == "# Code Map\n\n### src/ — Local\n"

    def test_ensure_codemap_gitignore_appends_once(self, tmp_path) -> None:
        (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")

        assert harness_shared.ensure_codemap_gitignore(tmp_path) is True
        assert harness_shared.ensure_codemap_gitignore(tmp_path) is False

        text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
        assert text.count("CODE_MAP.md") == 1
        assert "Harness generated local projection" in text


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
