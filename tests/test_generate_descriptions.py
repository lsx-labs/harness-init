"""Tests for generate_descriptions.py"""

import json
import os
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from generate_descriptions import (
    parse_codemap, write_descriptions,
    get_ai_cmd, fallback_generate, get_docstring, get_keywords,
    gitnexus_query, MANUAL_MARKER, filter_generated_descriptions,
    batch_dirs, ai_generate_batched, build_quality_report, _run_ai_command,
)
from harness_shared import parse_codemap_entry


class TestExtractDesc:
    def test_with_desc(self):
        desc, _ = parse_codemap_entry("(100 symbols) — Core module")
        assert desc == "Core module"

    def test_without_desc(self):
        desc, _ = parse_codemap_entry("(100 symbols)")
        assert desc == ""

    def test_with_stale(self):
        desc, _ = parse_codemap_entry("— ⚠️ 描述可能过期")
        assert desc.startswith("⚠️")

    def test_with_pin(self):
        desc, _ = parse_codemap_entry(f"— {MANUAL_MARKER} My desc")
        assert desc.startswith(MANUAL_MARKER)


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

    def test_generate_mode_includes_low_quality_descriptions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### engine/ (100 symbols) — run_combo / load_market_tensors / nav_to_metrics\n"
            "### scripts/ (50 symbols) — build_report / dict_or_empty\n"
            "### good/ (10 symbols) — 回测核心内核：rank 输入校验、持仓撮合、NAV/指标计算\n"
        )
        dirs = parse_codemap("--generate")
        assert "engine" in dirs
        assert "scripts" in dirs
        assert "good" not in dirs

    def test_generate_mode_includes_low_confidence_descriptions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### src/ (100 symbols) — ⚠️ run_combo / load_data\n"
        )
        assert parse_codemap("--generate") == ["src"]

    def test_dry_run_uses_generate_scope(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### good/ (10 symbols) — 回测核心内核：rank 输入校验、持仓撮合、NAV/指标计算\n"
            "### bad/ (10 symbols) — run_combo / load_data\n"
            "### empty/ (10 symbols)\n"
        )
        assert parse_codemap("--dry-run") == ["bad", "empty"]


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

    def test_write_top_level_without_symbol_count(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### docs/\n")
        changes = write_descriptions({"docs": "研究文档与运行手册"})
        assert changes == [{"dir": "docs", "desc": "研究文档与运行手册"}]
        assert "### docs/ — 研究文档与运行手册" in (tmp_path / "CODE_MAP.md").read_text()

    def test_write_sub_level_without_symbol_count(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### docs/\n- **research/**\n")
        changes = write_descriptions({"docs/research": "实验记录与性能报告"})
        assert changes == [{"dir": "docs/research", "desc": "实验记录与性能报告"}]
        assert "- **research/** — 实验记录与性能报告" in (tmp_path / "CODE_MAP.md").read_text()

    def test_write_sub_without_count_preserves_next_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### autoresearch/ (10 symbols)\n"
            "- **baseline_contracts/**\n"
            "\n"
            "### data/ (20 symbols)\n"
            "- **research/** (20 symbols)\n"
        )
        changes = write_descriptions({"autoresearch/baseline_contracts": "基线合约归档"})
        assert changes == [{"dir": "autoresearch/baseline_contracts", "desc": "基线合约归档"}]
        content = (tmp_path / "CODE_MAP.md").read_text()
        assert "- **baseline_contracts/** — 基线合约归档\n\n### data/" in content
        parsed = parse_codemap("--refresh")
        assert parsed == ["autoresearch", "autoresearch/baseline_contracts", "data", "data/research"]

    def test_write_nested_sub_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### scripts/ (10 symbols)\n"
            "- **research/quantile_mvp/** (2 symbols)\n"
        )
        changes = write_descriptions({"scripts/research/quantile_mvp": "分位数原型研究脚本"})
        assert changes == [{"dir": "scripts/research/quantile_mvp", "desc": "分位数原型研究脚本"}]
        assert "- **research/quantile_mvp/** — 分位数原型研究脚本 (2 symbols)" in (
            tmp_path / "CODE_MAP.md"
        ).read_text()


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

    def test_codex_environment_prefers_codex_over_claude(self, monkeypatch):
        monkeypatch.setenv("CODEX_THREAD_ID", "thread-1")
        with patch(
            'generate_descriptions.shutil.which',
            side_effect=lambda x: f"/usr/bin/{x}" if x in {"claude", "codex"} else None,
        ):
            assert get_ai_cmd() == "codex"

    def test_explicit_codex_platform_prefers_codex_over_claude(self, monkeypatch):
        monkeypatch.setenv("HARNESS_PLATFORM", "codex")
        with patch(
            'generate_descriptions.shutil.which',
            side_effect=lambda x: f"/usr/bin/{x}" if x in {"claude", "codex"} else None,
        ):
            assert get_ai_cmd() == "codex"

    def test_explicit_claude_platform_overrides_codex_environment(self, monkeypatch):
        monkeypatch.setenv("HARNESS_PLATFORM", "claude")
        monkeypatch.setenv("CODEX_THREAD_ID", "thread-1")
        with patch(
            'generate_descriptions.shutil.which',
            side_effect=lambda x: f"/usr/bin/{x}" if x in {"claude", "codex"} else None,
        ):
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
        assert not result["mymod"].startswith("⚠️")  # docstring is trusted

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        """fallback must never touch a dir that already has a good description."""
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mymod"
        d.mkdir()
        (d / "__init__.py").write_text('"""Fallback would write this."""\n')
        (tmp_path / "CODE_MAP.md").write_text("### mymod/ (50 symbols) — Good existing desc\n")
        result = fallback_generate(["mymod"])
        assert "mymod" not in result  # has good desc → skipped

    def test_keyword_fallback_marked_low_confidence(self, tmp_path, monkeypatch):
        """Keyword joins (no docstring) get ⚠️ prefix."""
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mymod"
        d.mkdir()  # no __init__.py → no docstring
        (tmp_path / "CODE_MAP.md").write_text("### mymod/ (50 symbols)\n")
        with patch('generate_descriptions.get_keywords', return_value="run_combo / load_data"):
            result = fallback_generate(["mymod"])
        assert result["mymod"].startswith("⚠️")
        assert "run_combo" in result["mymod"]

    def test_refresh_mode_still_only_fills_empty(self, tmp_path, monkeypatch):
        """Even when called with all dirs (refresh), only empty ones are filled."""
        monkeypatch.chdir(tmp_path)
        for name in ("filled", "empty"):
            (tmp_path / name).mkdir()
        (tmp_path / "CODE_MAP.md").write_text(
            "### filled/ (50 symbols) — Has desc\n"
            "### empty/ (30 symbols)\n"
        )
        with patch('generate_descriptions.get_keywords',
                   side_effect=lambda d: "kw_a / kw_b"):
            result = fallback_generate(["filled", "empty"])
        assert "filled" not in result
        assert "empty" in result

    def test_low_quality_existing_can_be_replaced_by_docstring(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "engine"
        d.mkdir()
        (d / "__init__.py").write_text(
            '"""回测核心内核：rank 输入校验、持仓撮合、NAV/指标计算."""\n',
        )
        (tmp_path / "CODE_MAP.md").write_text(
            "### engine/ (50 symbols) — run_combo / load_market_tensors / nav_to_metrics\n"
        )
        result = fallback_generate(["engine"])
        assert result["engine"].startswith("回测核心内核")

    def test_low_quality_existing_can_be_replaced_by_keyword_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "engine"
        d.mkdir()
        (tmp_path / "CODE_MAP.md").write_text(
            "### engine/ (50 symbols) — run_combo / load_market_tensors / nav_to_metrics\n"
        )
        with patch('generate_descriptions.get_keywords', return_value="run_combo / load_data"):
            result = fallback_generate(["engine"])
        assert result["engine"] == "⚠️ run_combo / load_data"


class TestFilterGeneratedDescriptions:
    def test_rejects_ai_function_name_lists(self):
        result, rejected = filter_generated_descriptions(
            {
                "engine": "run_combo / load_market_tensors / nav_to_metrics",
                "core": "回测核心内核：rank 输入校验、持仓撮合、NAV/指标计算",
            },
        )
        assert result == {"core": "回测核心内核：rank 输入校验、持仓撮合、NAV/指标计算"}
        assert rejected["engine"] == "low_quality"

    def test_allows_low_confidence_only_when_requested(self):
        result, rejected = filter_generated_descriptions(
            {"engine": "⚠️ run_combo / load_data"},
            allow_low_confidence=True,
        )
        assert result == {"engine": "⚠️ run_combo / load_data"}
        assert rejected == {}


class TestQualityReport:
    def test_build_quality_report_counts_description_states(self, tmp_path):
        codemap = tmp_path / "CODE_MAP.md"
        codemap.write_text(
            "### good/ (10 symbols) — 回测核心内核：rank 输入校验\n"
            "### low/ (10 symbols) — run_combo / load_data\n"
            "### warn/ (10 symbols) — ⚠️ run_combo / load_data\n"
            "### empty/ (10 symbols)\n",
        )
        report = build_quality_report(codemap)
        assert report == {
            "total": 4,
            "described": 3,
            "acceptable": 1,
            "low_quality": 2,
            "low_confidence": 1,
            "empty": 1,
            "needs_refresh": 3,
        }


class TestBatchAiGenerate:
    def test_batch_dirs_splits_in_order(self):
        assert batch_dirs(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]]
        assert batch_dirs(["a"], 0) == [["a"]]

    def test_ai_generate_batched_aggregates_success_and_failures(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()

        def fake_ai_generate(dirs, *, timeout):
            assert timeout == 123
            if dirs == ["c"]:
                return None
            return {d: f"{d} 语义描述" for d in dirs}

        with patch("generate_descriptions.get_ai_cmd", return_value="codex"), \
             patch("generate_descriptions.ai_generate", side_effect=fake_ai_generate):
            descriptions, report = ai_generate_batched(
                ["a", "b", "c"],
                batch_size=2,
                max_workers=1,
                timeout=123,
            )

        assert descriptions == {"a": "a 语义描述", "b": "b 语义描述"}
        assert report["attempted"] is True
        assert report["batch_size"] == 2
        assert report["max_workers"] == 1
        assert report["timeout_seconds"] == 123
        assert report["success_dirs"] == ["a", "b"]
        assert report["failed_dirs"] == ["c"]
        assert report["batches"][1]["status"] == "failed"


# ── Additional coverage tests ──

import subprocess
from generate_descriptions import ai_generate, main as gd_main


class TestParseCodemapSubLevelPinned:
    """Cover lines 58, 60: sub-level entries with pin/existing desc in generate mode."""

    def test_generate_mode_skips_sub_with_desc(self, tmp_path, monkeypatch):
        """Line 60: sub-level with existing desc skipped in --generate mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### src/ (100 symbols)\n"
            "- **api/** — Existing sub desc (50 symbols)\n"
            "- **core/** (30 symbols)\n"
        )
        dirs = parse_codemap("--generate")
        assert "src" in dirs
        assert "src/api" not in dirs
        assert "src/core" in dirs

    def test_generate_mode_skips_sub_with_pin(self, tmp_path, monkeypatch):
        """Line 58: sub-level with pin marker skipped."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### src/ (100 symbols)\n"
            f"- **api/** — {MANUAL_MARKER} Protected sub (50 symbols)\n"
        )
        dirs = parse_codemap("--refresh")
        assert "src" in dirs
        assert "src/api" not in dirs


class TestAiGenerate:
    """Cover lines 103, 109-161: ai_generate function."""

    def test_no_ai_cmd(self, tmp_path, monkeypatch):
        """Line 110: no AI command → None."""
        monkeypatch.chdir(tmp_path)
        with patch('generate_descriptions.get_ai_cmd', return_value=""):
            assert ai_generate(["src"]) is None

    def test_no_gitnexus_dir(self, tmp_path, monkeypatch):
        """Line 110: no .gitnexus dir → None."""
        monkeypatch.chdir(tmp_path)
        with patch('generate_descriptions.get_ai_cmd', return_value="claude"):
            assert ai_generate(["src"]) is None

    def test_claude_path_json_output(self, tmp_path, monkeypatch):
        """claude --output-format json: result field contains the JSON."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        claude_json = json.dumps({"type": "result", "subtype": "success",
                                  "result": '{"src": "Core module"}'})
        mock_result = MagicMock(returncode=0, stdout=claude_json, stderr="")
        with patch('generate_descriptions.get_ai_cmd', return_value="claude"), \
             patch('generate_descriptions._run_ai_command', return_value=mock_result):
            result = ai_generate(["src"])
            assert result == {"src": "Core module"}

    def test_claude_path_command_failed(self, tmp_path, monkeypatch):
        """claude errors → stdout not valid JSON → None with stderr logged."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(returncode=1, stdout="", stderr="requires --verbose")
        with patch('generate_descriptions.get_ai_cmd', return_value="claude"), \
             patch('generate_descriptions._run_ai_command', return_value=mock_result):
            result = ai_generate(["src"])
            assert result is None

    def test_claude_path_missing_result_key(self, tmp_path, monkeypatch):
        """claude JSON without result key → None."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(returncode=0, stdout='{"type": "result"}', stderr="")
        with patch('generate_descriptions.get_ai_cmd', return_value="claude"), \
             patch('generate_descriptions._run_ai_command', return_value=mock_result):
            result = ai_generate(["src"])
            assert result is None

    def test_codex_path(self, tmp_path, monkeypatch):
        """Lines 143-145: codex exec path."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(returncode=0, stdout='{"src": "Core logic"}')
        with patch('generate_descriptions.get_ai_cmd', return_value="codex"), \
             patch('generate_descriptions._run_ai_command', return_value=mock_result):
            result = ai_generate(["src"], timeout=77)
            assert result == {"src": "Core logic"}
        assert mock_result is not None

    def test_codex_path_uses_configured_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(returncode=0, stdout='{"src": "Core logic"}')
        with patch('generate_descriptions.get_ai_cmd', return_value="codex"), \
             patch('generate_descriptions._run_ai_command', return_value=mock_result) as mock_run:
            assert ai_generate(["src"], timeout=211) == {"src": "Core logic"}
        assert mock_run.call_args.args[1] == 211

    def test_timeout(self, tmp_path, monkeypatch):
        """Line 146-147: subprocess timeout → None."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        with patch('generate_descriptions.get_ai_cmd', return_value="claude"), \
             patch('generate_descriptions._run_ai_command',
                   side_effect=subprocess.TimeoutExpired("claude", 20)):
            result = ai_generate(["src"])
            assert result is None

    def test_no_json_in_response(self, tmp_path, monkeypatch):
        """Lines 153-157: AI response with no JSON → None."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(returncode=0, stdout='just plain text no json')
        with patch('generate_descriptions.get_ai_cmd', return_value="codex"), \
             patch('generate_descriptions._run_ai_command', return_value=mock_result):
            result = ai_generate(["src"])
            assert result is None

    def test_invalid_json_in_response(self, tmp_path, monkeypatch):
        """Lines 159-161: regex matches but JSON is invalid → None."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        # This has braces and colon but invalid JSON
        mock_result = MagicMock(returncode=0, stdout='{"src": undefined}')
        with patch('generate_descriptions.get_ai_cmd', return_value="codex"), \
             patch('generate_descriptions._run_ai_command', return_value=mock_result):
            result = ai_generate(["src"])
            assert result is None

    def test_run_ai_command_kills_process_group_on_timeout(self):
        process = MagicMock()
        process.pid = 1234
        process.communicate.side_effect = subprocess.TimeoutExpired(["codex"], 5)
        with patch('generate_descriptions.subprocess.Popen', return_value=process) as mock_popen, \
             patch('generate_descriptions.os.getpgid', return_value=4321) as mock_getpgid, \
             patch('generate_descriptions.os.killpg') as mock_killpg:
            try:
                _run_ai_command(["codex", "exec", "prompt"], 5)
            except subprocess.TimeoutExpired:
                pass

        mock_popen.assert_called_once()
        assert mock_popen.call_args.kwargs["start_new_session"] is True
        mock_getpgid.assert_called_once_with(1234)
        mock_killpg.assert_called()


class TestGetAiCmdCodexApp:
    """Cover line 103: Codex.app fallback path."""

    def test_finds_codex_app(self):
        with patch('generate_descriptions.shutil.which', return_value=None):
            with patch('generate_descriptions.os.path.isfile', return_value=True):
                result = get_ai_cmd()
                assert result == "/Applications/Codex.app/Contents/Resources/codex"


class TestGitnexusQuery:
    """Cover lines 169-182: gitnexus_query function."""

    def test_successful_query(self):
        md_output = json.dumps({
            "markdown": "| f.name |\n| --- |\n| authenticate_user |\n| create_session |"
        })
        mock_result = MagicMock(returncode=0, stdout=md_output, stderr="")
        with patch('generate_descriptions.subprocess.run', return_value=mock_result):
            rows = gitnexus_query("MATCH (f:Function) RETURN f.name")
            assert len(rows) == 2
            assert rows[0] == ["authenticate_user"]

    def test_empty_output(self):
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch('generate_descriptions.subprocess.run', return_value=mock_result):
            rows = gitnexus_query("MATCH (f:Function) RETURN f.name")
            assert rows == []

    def test_too_few_lines(self):
        md_output = json.dumps({"markdown": "| f.name |\n| --- |"})
        mock_result = MagicMock(returncode=0, stdout=md_output, stderr="")
        with patch('generate_descriptions.subprocess.run', return_value=mock_result):
            rows = gitnexus_query("MATCH (f:Function) RETURN f.name")
            assert rows == []

    def test_timeout(self):
        with patch('generate_descriptions.subprocess.run',
                   side_effect=subprocess.TimeoutExpired("npx", 10)):
            rows = gitnexus_query("MATCH (f:Function) RETURN f.name")
            assert rows == []

    def test_invalid_json(self):
        mock_result = MagicMock(returncode=0, stdout="not json", stderr="")
        with patch('generate_descriptions.subprocess.run', return_value=mock_result):
            rows = gitnexus_query("MATCH (f:Function) RETURN f.name")
            assert rows == []

    def test_stderr_fallback(self):
        """Cover output = r.stdout.strip() or r.stderr.strip()."""
        md_output = json.dumps({
            "markdown": "| f.name |\n| --- |\n| func_one |"
        })
        mock_result = MagicMock(returncode=0, stdout="", stderr=md_output)
        with patch('generate_descriptions.subprocess.run', return_value=mock_result):
            rows = gitnexus_query("MATCH (f:Function) RETURN f.name")
            assert len(rows) == 1


class TestGetDocstringSyntaxError:
    """Cover lines 198-199: SyntaxError in docstring extraction."""

    def test_syntax_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "badmod"
        d.mkdir()
        (d / "__init__.py").write_text("def broken(:\n")
        assert get_docstring("badmod") == ""


class TestWriteDescriptionsSubLevel:
    """Cover sub-level write path more thoroughly."""

    def test_write_sub_with_existing_desc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### src/ (100 symbols) — Old desc\n"
            "- **api/** — Old sub desc (50 symbols)\n"
        )
        changes = write_descriptions({"src/api": "New sub desc"})
        assert len(changes) == 1
        content = (tmp_path / "CODE_MAP.md").read_text()
        assert "New sub desc" in content

    def test_skip_non_string_desc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        changes = write_descriptions({"src": None})
        assert len(changes) == 0


class TestMainFunction:
    """Cover lines 229-257, 261: main() in different modes."""

    def test_main_all_described(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols) — Described\n")
        monkeypatch.setattr('sys.argv', ['generate_descriptions.py', str(tmp_path), '--generate'])
        gd_main()
        out = capsys.readouterr().out
        assert '"status": "all_described"' in out

    def test_main_dry_run(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        monkeypatch.setattr('sys.argv', ['generate_descriptions.py', str(tmp_path), '--dry-run'])
        gd_main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "dry_run"
        assert "src" in data["dirs_needing"]

    def test_main_ai_success(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        monkeypatch.setattr(
            'sys.argv',
            [
                'generate_descriptions.py',
                str(tmp_path),
                '--generate',
                '--batch-size',
                '2',
                '--max-workers',
                '2',
                '--ai-timeout',
                '180',
            ],
        )
        with patch(
            'generate_descriptions.ai_generate_batched',
            return_value=(
                {"src": "Core module"},
                {
                    "attempted": True,
                    "batch_size": 2,
                    "max_workers": 2,
                    "timeout_seconds": 180,
                    "success_dirs": ["src"],
                    "failed_dirs": [],
                    "batches": [],
                },
            ),
        ):
            gd_main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "updated"
        assert data["source"] == "ai+gitnexus"
        assert data["ai_report"]["batch_size"] == 2
        assert data["ai_report"]["max_workers"] == 2
        assert data["ai_report"]["timeout_seconds"] == 180
        assert data["quality_before"]["acceptable"] == 0
        assert data["quality_after"]["acceptable"] == 1

    def test_main_ai_all_rejected_uses_trusted_fallback(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "src"
        d.mkdir()
        (d / "__init__.py").write_text('"""语义清晰的目录描述."""\n')
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        monkeypatch.setattr('sys.argv', ['generate_descriptions.py', str(tmp_path), '--generate'])
        with patch(
            'generate_descriptions.ai_generate_batched',
            return_value=(
                {"src": "run_combo / load_market_tensors / nav_to_metrics"},
                {"attempted": True, "success_dirs": ["src"], "failed_dirs": [], "batches": []},
            ),
        ):
            gd_main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "updated"
        assert data["source"] == "trusted_fallback"
        assert data["count"] == 1

    def test_main_ai_failure_does_not_use_keyword_fallback(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "src"
        d.mkdir()
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        monkeypatch.setattr('sys.argv', ['generate_descriptions.py', str(tmp_path), '--generate'])
        with patch(
            'generate_descriptions.ai_generate_batched',
            return_value=(
                {},
                {"attempted": True, "success_dirs": [], "failed_dirs": ["src"], "batches": []},
            ),
        ), patch('generate_descriptions.get_keywords', return_value="run_combo / load_data"):
            gd_main()
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "ai_failed"
        assert data["source"] == "ai+gitnexus"
        assert data["count"] == 0
        assert data["ai_report"]["failed_dirs"] == ["src"]
        assert "⚠️" not in (tmp_path / "CODE_MAP.md").read_text()

    def test_main_fallback_success(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "mymod"
        d.mkdir()
        (d / "__init__.py").write_text('"""My module description."""\n')
        (tmp_path / "CODE_MAP.md").write_text("### mymod/ (50 symbols)\n")
        monkeypatch.setattr('sys.argv', ['generate_descriptions.py', str(tmp_path), '--generate'])
        with patch(
            'generate_descriptions.ai_generate_batched',
            return_value=({}, {"attempted": False, "reason": "unavailable"}),
        ):
            gd_main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "updated"
        assert data["source"] == "fallback"

    def test_main_no_changes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols)\n")
        monkeypatch.setattr('sys.argv', ['generate_descriptions.py', str(tmp_path), '--generate'])
        with patch('generate_descriptions.ai_generate', return_value=None), \
             patch('generate_descriptions.fallback_generate', return_value={}):
            gd_main()
        out = capsys.readouterr().out
        assert '"status": "no_changes"' in out

    def test_main_default_args(self, tmp_path, monkeypatch, capsys):
        """Line 229-230: default project_dir and mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text("### src/ (100 symbols) — Described\n")
        monkeypatch.setattr('sys.argv', ['generate_descriptions.py'])
        gd_main()
        out = capsys.readouterr().out
        assert "all_described" in out
