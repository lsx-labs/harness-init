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


# ── Additional coverage tests ──


class TestGetAiCmdCodexApp:
    """Cover lines 54-57: Codex.app fallback path."""

    def test_finds_codex_app(self):
        with patch.object(hm.shutil, 'which', return_value=None):
            with patch.object(hm.os.path, 'isfile', return_value=True):
                assert hm.get_ai_cmd() == "/Applications/Codex.app/Contents/Resources/codex"

    def test_finds_nothing(self):
        with patch.object(hm.shutil, 'which', return_value=None):
            with patch.object(hm.os.path, 'isfile', return_value=False):
                assert hm.get_ai_cmd() == ""


class TestAiInvoke:
    """Cover lines 62-78: ai_invoke for claude and codex paths."""

    def test_no_cmd(self):
        """Line 64: no AI command → empty string."""
        with patch.object(hm, 'get_ai_cmd', return_value=""):
            assert hm.ai_invoke("test prompt") == ""

    def test_claude_path(self):
        """Lines 66-71: claude -p with allowedTools."""
        mock_result = MagicMock(returncode=0, stdout="AI response\n")
        with patch.object(hm, 'get_ai_cmd', return_value="claude"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result) as mock_run:
            result = hm.ai_invoke("test prompt", timeout=10)
            assert result == "AI response"
            args = mock_run.call_args[0][0]
            assert "claude" in args
            assert "-p" in args
            assert "--allowedTools" in args

    def test_codex_path(self):
        """Lines 73-76: codex exec path."""
        mock_result = MagicMock(returncode=0, stdout="Codex response\n")
        with patch.object(hm, 'get_ai_cmd', return_value="codex"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result) as mock_run:
            result = hm.ai_invoke("test prompt")
            assert result == "Codex response"
            args = mock_run.call_args[0][0]
            assert "codex" in args
            assert "exec" in args

    def test_timeout(self):
        """Lines 77-78: subprocess timeout → empty string."""
        with patch.object(hm, 'get_ai_cmd', return_value="claude"), \
             patch.object(hm.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired("claude", 15)):
            assert hm.ai_invoke("test") == ""

    def test_file_not_found(self):
        """Lines 77-78: FileNotFoundError → empty string."""
        with patch.object(hm, 'get_ai_cmd', return_value="claude"), \
             patch.object(hm.subprocess, 'run', side_effect=FileNotFoundError):
            assert hm.ai_invoke("test") == ""


class TestGetProjectId:
    """Cover lines 84-91: get_project_id."""

    def test_success(self):
        mock_result = MagicMock(returncode=0, stdout="/Users/dev/myproject\n")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            result = hm.get_project_id()
            assert result == "Users_dev_myproject"

    def test_failure(self):
        mock_result = MagicMock(returncode=1, stdout="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            assert hm.get_project_id() == ""

    def test_timeout(self):
        with patch.object(hm.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired("git", 3)):
            assert hm.get_project_id() == ""


class TestGetGitnexusCommunities:
    """Cover lines 168-196: get_gitnexus_communities."""

    def test_no_gitnexus_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert hm.get_gitnexus_communities() is None

    def test_successful_query(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        md = "| area | syms | clusters |\n| --- | --- | --- |\n| auth | 100 | 3 |\n| core | 200 | 5 |"
        mock_result = MagicMock(returncode=0,
                                stdout=json.dumps({"markdown": md}),
                                stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            result = hm.get_gitnexus_communities()
            assert result is not None
            assert result["auth"]["symbols"] == 100
            assert result["core"]["symbols"] == 200

    def test_duplicate_area_merges(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        md = "| area | syms | clusters |\n| --- | --- | --- |\n| auth | 100 | 3 |\n| auth | 50 | 2 |"
        mock_result = MagicMock(returncode=0,
                                stdout=json.dumps({"markdown": md}),
                                stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            result = hm.get_gitnexus_communities()
            assert result["auth"]["symbols"] == 150
            assert result["auth"]["clusters"] == 5

    def test_non_zero_return(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            assert hm.get_gitnexus_communities() is None

    def test_empty_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            assert hm.get_gitnexus_communities() is None

    def test_too_few_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        md = "| area | syms |\n| --- | --- |"
        mock_result = MagicMock(returncode=0,
                                stdout=json.dumps({"markdown": md}),
                                stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            assert hm.get_gitnexus_communities() is None

    def test_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(hm.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired("npx", 15)):
            assert hm.get_gitnexus_communities() is None

    def test_non_digit_syms_skipped(self, tmp_path, monkeypatch):
        """Line 187: cols[1].isdigit() check — non-digit rows skipped."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        md = "| area | syms | clusters |\n| --- | --- | --- |\n| auth | abc | 3 |"
        mock_result = MagicMock(returncode=0,
                                stdout=json.dumps({"markdown": md}),
                                stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            assert hm.get_gitnexus_communities() is None


class TestBuildAreaToDir:
    """Cover lines 200-222: build_area_to_dir."""

    def test_maps_areas_to_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {"auth": {"symbols": 100}, "core": {"symbols": 200}}
        md = "| f.filePath |\n| --- |\n| src/auth |\n| src/core |\n| lib/utils |"
        mock_result = MagicMock(returncode=0,
                                stdout=json.dumps({"markdown": md}),
                                stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            mapping = hm.build_area_to_dir(communities)
            assert mapping["auth"] == "src/auth"
            assert mapping["core"] == "src/core"

    def test_timeout_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {"auth": {"symbols": 100}}
        with patch.object(hm.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired("npx", 15)):
            mapping = hm.build_area_to_dir(communities)
            assert mapping == {}

    def test_case_insensitive_matching(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {"Auth": {"symbols": 100}}
        md = "| f.filePath |\n| --- |\n| src/auth |"
        mock_result = MagicMock(returncode=0,
                                stdout=json.dumps({"markdown": md}),
                                stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result):
            mapping = hm.build_area_to_dir(communities)
            assert mapping["Auth"] == "src/auth"


class TestBuildCodemapStructure:
    """Cover lines 227-269: build_codemap_structure."""

    def test_basic_structure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {
            "auth": {"symbols": 100, "clusters": 3},
            "core": {"symbols": 200, "clusters": 5},
        }
        area_to_dir = {"auth": "src/auth", "core": "src/core"}
        with patch.object(hm, 'build_area_to_dir', return_value=area_to_dir):
            content, stale = hm.build_codemap_structure(communities, {}, {})
            assert "### src/" in content
            assert "**auth/**" in content or "**core/**" in content
            assert stale == []

    def test_preserves_existing_desc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {"auth": {"symbols": 100, "clusters": 3}}
        area_to_dir = {"auth": "src/auth"}
        existing_descs = {"src": "Source code"}
        with patch.object(hm, 'build_area_to_dir', return_value=area_to_dir):
            content, _ = hm.build_codemap_structure(communities, existing_descs, {})
            assert "Source code" in content

    def test_detects_stale_top_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {"auth": {"symbols": 200, "clusters": 3}}
        area_to_dir = {"auth": "src/auth"}
        existing_descs = {"src": "Old desc"}
        old_counts = {"src": 100}  # 200 vs 100 → 100% change > 20%
        with patch.object(hm, 'build_area_to_dir', return_value=area_to_dir):
            _, stale = hm.build_codemap_structure(communities, existing_descs, old_counts)
            assert "src" in stale

    def test_detects_stale_sub_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {"auth": {"symbols": 200, "clusters": 3}}
        area_to_dir = {"auth": "src/auth"}
        existing_descs = {"src/auth": "Auth module"}
        old_counts = {"src/auth": 100}  # 200 vs 100 → stale
        with patch.object(hm, 'build_area_to_dir', return_value=area_to_dir):
            _, stale = hm.build_codemap_structure(communities, existing_descs, old_counts)
            assert "src/auth" in stale

    def test_no_dir_mapping_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        communities = {"unmapped": {"symbols": 50, "clusters": 1}}
        with patch.object(hm, 'build_area_to_dir', return_value={}):
            content, stale = hm.build_codemap_structure(communities, {}, {})
            assert "unmapped" not in content

    def test_top_level_only(self, tmp_path, monkeypatch):
        """Area maps to top-level dir with no sub."""
        monkeypatch.chdir(tmp_path)
        communities = {"scripts": {"symbols": 150, "clusters": 2}}
        area_to_dir = {"scripts": "scripts"}
        with patch.object(hm, 'build_area_to_dir', return_value=area_to_dir):
            content, stale = hm.build_codemap_structure(communities, {}, {})
            assert "### scripts/" in content


class TestUpdateSubdirDocs:
    """Cover lines 278-296: update_subdir_docs."""

    def test_no_docs_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = hm.update_subdir_docs(["nonexistent"])
        assert result == []

    def test_with_claude_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "src"
        d.mkdir()
        (d / "CLAUDE.md").write_text("<!-- harness:start -->\nold\n<!-- harness:end -->\n")
        with patch.object(hm, 'ai_invoke', return_value="updated content"):
            result = hm.update_subdir_docs(["src"])
            assert result == ["src"]

    def test_ai_invoke_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "src"
        d.mkdir()
        (d / "CLAUDE.md").write_text("content")
        with patch.object(hm, 'ai_invoke', return_value=""):
            result = hm.update_subdir_docs(["src"])
            assert result == []


class TestHandleMainBranchUpdate:
    """Cover lines 305-351: handle_main_branch_update."""

    def test_no_communities(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        with patch.object(hm, 'get_gitnexus_communities', return_value=None):
            hm.handle_main_branch_update("test_project")
        # Should return without output
        assert capsys.readouterr().out == ""

    def test_no_changes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # Create existing CODE_MAP.md with same content as would be generated
        communities = {"scripts": {"symbols": 100, "clusters": 2}}
        area_to_dir = {"scripts": "scripts"}
        with patch.object(hm, 'build_area_to_dir', return_value=area_to_dir):
            expected_content, _ = hm.build_codemap_structure(communities, {}, {})
        (tmp_path / "CODE_MAP.md").write_text(expected_content)

        with patch.object(hm, 'get_gitnexus_communities', return_value=communities), \
             patch.object(hm, 'build_codemap_structure', return_value=(expected_content, [])):
            hm.handle_main_branch_update("test_project")
        assert capsys.readouterr().out == ""

    def test_full_update_with_descriptions(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        old_content = "# Old Code Map\n"
        (tmp_path / "CODE_MAP.md").write_text(old_content)
        communities = {"auth": {"symbols": 100, "clusters": 2}}
        new_content = "# Code Map\n\n### src/ (100 symbols)\n"

        # Create a fake desc_script that exists
        desc_script = tmp_path / "generate_descriptions.py"
        desc_script.write_text("pass")

        with patch.object(hm, 'get_gitnexus_communities', return_value=communities), \
             patch.object(hm, 'build_codemap_structure', return_value=(new_content, ["src"])), \
             patch.object(hm, 'update_subdir_docs', return_value=["src"]), \
             patch.object(hm, 'DESC_SCRIPT', desc_script), \
             patch.object(hm.subprocess, 'run', return_value=MagicMock(returncode=0)):
            hm.handle_main_branch_update("test_project")

        out = capsys.readouterr().out
        assert out  # Should have output
        data = json.loads(out)
        assert data["status"] == "updated"
        assert "CODE_MAP.md" in data["affected_files"]

    def test_update_no_desc_script(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        communities = {"auth": {"symbols": 100, "clusters": 2}}
        new_content = "# Code Map\n\n### src/ (100 symbols)\n"
        old_content = "# Old Code Map"
        (tmp_path / "CODE_MAP.md").write_text(old_content)

        with patch.object(hm, 'get_gitnexus_communities', return_value=communities), \
             patch.object(hm, 'build_codemap_structure', return_value=(new_content, [])), \
             patch.object(hm, 'update_subdir_docs', return_value=[]):
            # Make all desc script candidates not exist
            hm.handle_main_branch_update("test_project")

        out = capsys.readouterr().out
        if out:
            data = json.loads(out)
            assert data["status"] == "updated"


class TestCountSourceFiles:
    """Cover lines 367-376: count_source_files."""

    def test_counts_source_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "main.ts").write_text("const x = 1")
        (tmp_path / "readme.md").write_text("not source")
        assert hm.count_source_files() == 2

    def test_skips_hidden_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks.py").write_text("x = 1")
        assert hm.count_source_files() == 1

    def test_empty_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert hm.count_source_files() == 0


class TestHandleGrowthCheck:
    """Cover lines 380-430: handle_growth_check."""

    def test_retired_state(self, tmp_path):
        state_file = tmp_path / "state.json"
        state = {"retired": True, "file_count": 100}
        hm.handle_growth_check(state, state_file)
        # Should return immediately — no state file written
        assert not state_file.exists()

    def test_below_threshold(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1")
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        hm.handle_growth_check(state, state_file)
        # 1 file - 0 prev < 20 threshold → just save state
        saved = json.loads(state_file.read_text())
        assert saved["file_count"] == 1

    def test_no_diag_script(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create enough files to exceed threshold
        for i in range(25):
            (tmp_path / f"mod{i}.py").write_text("x = 1")
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        with patch.object(hm, 'DIAG_SCRIPT', Path("/nonexistent/harness-init.py")):
            hm.handle_growth_check(state, state_file)
        saved = json.loads(state_file.read_text())
        assert saved["file_count"] == 25

    def test_diag_script_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        for i in range(25):
            (src / f"mod{i}.py").write_text("x = 1")
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        diag_script = tmp_path / "diag.sh"  # non-.py to avoid counting
        diag_script.write_text("pass")
        mock_result = MagicMock(returncode=1, stdout="")
        with patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.handle_growth_check(state, state_file)
        saved = json.loads(state_file.read_text())
        assert saved["file_count"] == 25

    def test_diag_script_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        diag_script = tmp_path / "diag.sh"
        diag_script.write_text("pass")
        with patch.object(hm, 'count_source_files', return_value=25), \
             patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired("python", 15)):
            hm.handle_growth_check(state, state_file)
        saved = json.loads(state_file.read_text())
        assert saved["file_count"] == 25

    def test_gitnexus_recommendation(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False, "gitnexus_recommended": False, "lsp_recommended": []}
        diag = {
            "grep_noise": {"grep_noise_files": 30, "most_imported": "auth_module"},
            "existing": {"gitnexus": {"indexed": False}},
            "lsp_assessment": [],
        }
        diag_script = tmp_path / "diag.sh"
        diag_script.write_text("pass")
        mock_result = MagicMock(returncode=0, stdout=json.dumps(diag))
        with patch.object(hm, 'count_source_files', return_value=25), \
             patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.handle_growth_check(state, state_file)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["decision"] == "warn"
        assert "GitNexus" in data["reason"]
        saved = json.loads(state_file.read_text())
        assert saved["gitnexus_recommended"] is True

    def test_lsp_recommendation(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False, "gitnexus_recommended": True, "lsp_recommended": []}
        diag = {
            "grep_noise": {"grep_noise_files": 5, "most_imported": ""},
            "existing": {"gitnexus": {"indexed": True}},
            "lsp_assessment": [
                {"language": "Python", "recommend": True, "reason": "类型覆盖 50%，LSP 有效"}
            ],
        }
        diag_script = tmp_path / "diag.sh"
        diag_script.write_text("pass")
        mock_result = MagicMock(returncode=0, stdout=json.dumps(diag))
        with patch.object(hm, 'count_source_files', return_value=25), \
             patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.handle_growth_check(state, state_file)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "Python" in data["reason"]

    def test_retirement(self, tmp_path, monkeypatch, capsys):
        """State retires when all recommendations are done and no new messages."""
        monkeypatch.chdir(tmp_path)
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False, "gitnexus_recommended": True,
                 "lsp_recommended": ["Python"]}
        diag = {
            "grep_noise": {"grep_noise_files": 5, "most_imported": ""},
            "existing": {"gitnexus": {"indexed": True}},
            "lsp_assessment": [
                {"language": "Python", "recommend": True, "reason": "already recommended"}
            ],
        }
        diag_script = tmp_path / "diag.sh"
        diag_script.write_text("pass")
        mock_result = MagicMock(returncode=0, stdout=json.dumps(diag))
        with patch.object(hm, 'count_source_files', return_value=25), \
             patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.handle_growth_check(state, state_file)
        saved = json.loads(state_file.read_text())
        assert saved["retired"] is True
        # No messages → no output
        assert capsys.readouterr().out == ""

    def test_empty_diag_stdout(self, tmp_path, monkeypatch):
        """Line 398-399: empty stdout from diag script."""
        monkeypatch.chdir(tmp_path)
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        diag_script = tmp_path / "diag.sh"
        diag_script.write_text("pass")
        mock_result = MagicMock(returncode=0, stdout="")
        with patch.object(hm, 'count_source_files', return_value=25), \
             patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.handle_growth_check(state, state_file)
        saved = json.loads(state_file.read_text())
        assert saved["file_count"] == 25


class TestMainFunction:
    """Cover lines 438-460, 464: main() with different ctx inputs."""

    def test_invalid_json_stdin(self, monkeypatch):
        """Lines 439-441: invalid JSON from stdin."""
        monkeypatch.setattr('sys.stdin', MagicMock(read=MagicMock(return_value="not json")))
        import io
        monkeypatch.setattr('sys.stdin', io.StringIO("not json"))
        hm.main()  # Should return silently

    def test_not_bash_tool(self, monkeypatch):
        """Line 443: tool_name != 'Bash' → return."""
        import io
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({"tool_name": "Read"})))
        hm.main()  # Should return silently

    def test_not_dict(self, monkeypatch):
        """Line 443: ctx is not a dict → return."""
        import io
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(["not", "a", "dict"])))
        hm.main()  # Should return silently

    def test_not_git_operation(self, monkeypatch):
        """Line 446: not a git operation → return."""
        import io
        ctx = {"tool_name": "Bash", "tool_input": {"command": "pytest tests/"}}
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(ctx)))
        hm.main()  # Should return silently

    def test_no_project_id(self, monkeypatch):
        """Lines 449-450: no project ID → return."""
        import io
        ctx = {"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}}
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(ctx)))
        with patch.object(hm, 'get_project_id', return_value=""):
            hm.main()

    def test_main_branch_path(self, monkeypatch, tmp_path):
        """Lines 456-458: on main branch → handle_main_branch_update + handle_growth_check."""
        import io
        ctx = {"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}}
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(ctx)))
        state_file = tmp_path / "counters" / "test_project.json"
        with patch.object(hm, 'get_project_id', return_value="test_project"), \
             patch.object(hm, 'COUNTER_DIR', tmp_path / "counters"), \
             patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'handle_main_branch_update') as mock_update, \
             patch.object(hm, 'handle_growth_check') as mock_growth:
            hm.main()
            mock_update.assert_called_once_with("test_project")
            mock_growth.assert_called_once()

    def test_feature_branch_path(self, monkeypatch, tmp_path):
        """Lines 459-460: on feature branch → only handle_growth_check."""
        import io
        ctx = {"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}}
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(ctx)))
        with patch.object(hm, 'get_project_id', return_value="test_project"), \
             patch.object(hm, 'COUNTER_DIR', tmp_path / "counters"), \
             patch.object(hm, 'is_on_main_branch', return_value=False), \
             patch.object(hm, 'handle_main_branch_update') as mock_update, \
             patch.object(hm, 'handle_growth_check') as mock_growth:
            hm.main()
            mock_update.assert_not_called()
            mock_growth.assert_called_once()


class TestGetReadmeFirstLine:
    def test_with_readme(self, tmp_path):
        (tmp_path / "README.md").write_text("# Title\n\nThis is the project description.\n")
        assert hm.get_readme_first_line(tmp_path) == "This is the project description."

    def test_no_readme(self, tmp_path):
        assert hm.get_readme_first_line(tmp_path) == ""

    def test_only_headings(self, tmp_path):
        (tmp_path / "README.md").write_text("# Title\n## Subtitle\n")
        assert hm.get_readme_first_line(tmp_path) == ""

    def test_truncate(self, tmp_path):
        (tmp_path / "README.md").write_text("# H\n" + "x" * 200 + "\n")
        result = hm.get_readme_first_line(tmp_path)
        assert len(result) <= 80


class TestGetInitDocstring:
    def test_with_docstring(self, tmp_path):
        d = tmp_path / "mymod"
        d.mkdir()
        (d / "__init__.py").write_text('"""My module description."""\n')
        assert hm.get_init_docstring(d) == "My module description."

    def test_with_separator(self, tmp_path):
        d = tmp_path / "mod"
        d.mkdir()
        (d / "__init__.py").write_text('"""mod — The core module."""\n')
        assert hm.get_init_docstring(d) == "The core module."

    def test_no_init(self, tmp_path):
        d = tmp_path / "mod"
        d.mkdir()
        assert hm.get_init_docstring(d) == ""

    def test_no_docstring(self, tmp_path):
        d = tmp_path / "mod"
        d.mkdir()
        (d / "__init__.py").write_text("x = 1\n")
        assert hm.get_init_docstring(d) == ""


class TestGetSubdirList:
    def test_with_subdirs(self, tmp_path):
        d = tmp_path / "tests"
        d.mkdir()
        (d / "unit").mkdir()
        (d / "integration").mkdir()
        (d / "__pycache__").mkdir()
        result = hm.get_subdir_list(d)
        assert "integration" in result
        assert "unit" in result
        assert "__pycache__" not in result

    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert hm.get_subdir_list(d) == ""

    def test_only_files(self, tmp_path):
        d = tmp_path / "flat"
        d.mkdir()
        (d / "file.py").write_text("")
        assert hm.get_subdir_list(d) == ""


class TestBuildCodemapStructureWithUncovered:
    def test_appends_uncovered_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create dirs: src/ (covered by GitNexus) + docs/ (not covered)
        (tmp_path / "src").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("# Docs\n\nProject documentation and contracts.\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "unit").mkdir()
        (tmp_path / "tests" / "integration").mkdir()

        communities = {"Src": {"symbols": 100, "clusters": 5}}
        with patch.object(hm, 'build_area_to_dir', return_value={"Src": "src"}):
            content, stale = hm.build_codemap_structure(communities, {}, {})

        assert "### src/" in content
        assert "### docs/" in content
        assert "Project documentation" in content
        assert "### tests/" in content
        assert "integration" in content or "unit" in content
