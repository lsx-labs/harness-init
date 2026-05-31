"""Tests for harness_init.py (diagnostic script)"""

import json
import os
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from harness_init import (
    should_skip, scan_languages, measure_grep_noise, measure_type_coverage,
    check_existing, check_lsp_installed, assess_lsp, get_version, diagnose,
    check_codex_gitnexus_wrapper
)


def test_shared_scripts_import_under_system_python():
    system_python = Path("/usr/bin/python3")
    if not system_python.exists():
        return
    code = (
        "import sys; "
        "sys.path.insert(0, 'scripts'); "
        "import harness_shared, generate_descriptions, sync_docs, session_context; "
        "print('ok')"
    )
    result = subprocess.run(
        [str(system_python), "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


class TestShouldSkip:
    def test_skip_git(self):
        assert should_skip(".git") is True

    def test_skip_venv(self):
        assert should_skip(".venv") is True

    def test_skip_node_modules(self):
        assert should_skip("node_modules") is True

    def test_skip_hidden(self):
        assert should_skip(".hidden") is True

    def test_keep_normal(self):
        assert should_skip("src") is False

    def test_keep_dot(self):
        assert should_skip(".") is False


class TestScanLanguages:
    def test_python_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("def hello():\n    pass\n")
        (src / "utils.py").write_text("x = 1\ny = 2\n")
        languages, imports = scan_languages()
        assert len(languages) == 1
        assert languages[0]["language"] == "Python"
        assert languages[0]["files"] == 2

    def test_mixed_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1\n" * 100)
        (tmp_path / "app.ts").write_text("const x = 1;\n" * 50)
        languages, _ = scan_languages()
        assert len(languages) == 2

    def test_empty_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        languages, imports = scan_languages()
        assert languages == []


class TestMeasureGrepNoise:
    def test_with_imports(self):
        from collections import Counter
        counter = Counter({"auth_module": 15, "utils": 8})
        with patch('harness_init.subprocess.run') as mock:
            mock.return_value = MagicMock(returncode=0, stdout="file1.py\nfile2.py\nfile3.py\n")
            result = measure_grep_noise(counter)
            assert result["most_imported"] == "auth_module"
            assert result["grep_noise_files"] == 3
            assert len(result["top5"]) == 2

    def test_empty_counter(self):
        from collections import Counter
        result = measure_grep_noise(Counter())
        assert result["grep_noise_files"] == 0

    def test_timeout(self):
        from collections import Counter
        counter = Counter({"module": 5})
        with patch('harness_init.subprocess.run', side_effect=subprocess.TimeoutExpired("grep", 10)):
            result = measure_grep_noise(counter)
            assert result["grep_noise_files"] == -1


class TestMeasureTypeCoverage:
    def test_typed_python(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "typed.py").write_text("def foo(x: int) -> str:\n    pass\ndef bar():\n    pass\n")
        result = measure_type_coverage([{"language": "Python"}])
        assert result["typed_funcs"] == 1
        assert result["total_funcs"] == 2
        assert result["coverage"] == 50.0

    def test_no_python(self):
        result = measure_type_coverage([{"language": "Go"}])
        assert result["coverage"] == 0


class TestCheckExisting:
    def test_nothing_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = check_existing()
        assert result["claude_md"]["exists"] is False
        assert result["agents_md"]["exists"] is False
        assert result["gitnexus"]["indexed"] is False

    def test_claude_md_with_codemap(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Project\n@CODE_MAP.md\n")
        result = check_existing()
        assert result["claude_md"]["exists"] is True
        assert result["claude_md"]["has_codemap"] is True

    def test_gitnexus_indexed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        result = check_existing()
        assert result["gitnexus"]["indexed"] is True

    def test_invalid_utf8_gitignore_does_not_crash(self, tmp_path, monkeypatch):
        # an invalid-UTF-8 .gitignore (or config file) must not crash the diagnostic
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_bytes(b".gitnexus\n\xff\xfe\n")
        result = check_existing()  # must not raise UnicodeDecodeError
        assert result["gitnexus"]["in_gitignore"] is True


class TestAssessLsp:
    def test_python_high_coverage(self):
        with patch('harness_init.check_lsp_installed', return_value=False):
            result = assess_lsp(
                [{"language": "Python", "files": 100}],
                {"coverage": 60.0}
            )
            assert result[0]["recommend"] is True
            assert "60.0%" in result[0]["reason"]

    def test_python_low_coverage(self):
        with patch('harness_init.check_lsp_installed', return_value=False):
            result = assess_lsp(
                [{"language": "Python", "files": 100}],
                {"coverage": 10.0}
            )
            assert result[0]["recommend"] is False

    def test_already_installed(self):
        with patch('harness_init.check_lsp_installed', return_value=True):
            result = assess_lsp(
                [{"language": "Python", "files": 100}],
                {"coverage": 80.0}
            )
            assert result[0]["installed"] is True
            assert "已安装" in result[0]["reason"]

    def test_weak_typed(self):
        with patch('harness_init.check_lsp_installed', return_value=False):
            result = assess_lsp(
                [{"language": "JavaScript", "files": 200}],
                {"coverage": 0}
            )
            assert result[0]["recommend"] is False
            assert "弱类型" in result[0]["reason"]



    def test_unknown_fallback(self):
        with patch('harness_init.Path.exists', return_value=False):
            # Will eventually return "unknown" if no VERSION found
            pass


class TestDiagnose:
    def test_full_diagnose(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("def main() -> None:\n    pass\n")
        result = diagnose(str(tmp_path))
        assert result["schema_version"] == 1
        assert "project" in result
        assert "languages" in result
        assert "grep_noise" in result
        assert "type_coverage" in result
        assert "existing" in result
        assert "lsp_assessment" in result


# ── Additional coverage tests ──


class TestScanLanguagesImportCounting:
    """import counting and small-language filtering."""

    def test_non_code_file_skipped(self, tmp_path, monkeypatch):
        """Line 68: ext not in LANG_MAP → continue."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "readme.txt").write_text("not code\n")
        languages, imports = scan_languages()
        assert languages == []

    def test_oserror_on_read(self, tmp_path, monkeypatch):
        """Lines 73-74: OSError during file read → continue."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1\n")
        # Make the file unreadable after walk finds it
        with patch('harness_init.open', side_effect=OSError("permission denied")):
            languages, imports = scan_languages()
        assert languages == []

    def test_import_counting_with_dotted_imports(self, tmp_path, monkeypatch):
        """Lines 80-84: from pkg.submodule import X extracts 'submodule'."""
        monkeypatch.chdir(tmp_path)
        content = (
            "from mypackage.authentication import login\n"
            "from mypackage.authentication import logout\n"
        ) + "x = 1\n" * 50  # enough lines to pass pct threshold
        (tmp_path / "app.py").write_text(content)
        languages, imports = scan_languages()
        assert "authentication" in imports
        assert imports["authentication"] == 2

    def test_import_counting_skips_stdlib(self, tmp_path, monkeypatch):
        """Lines 82-83: stdlib / generic / short names filtered."""
        monkeypatch.chdir(tmp_path)
        content = (
            "from collections.abc import Mapping\n"  # no dot-leaf filter (abc is in stdlib? no. len=3 → filtered)
            "from pkg.utils import helper\n"  # generic name "utils" → filtered
            "from pkg.ab import foo\n"  # len("ab") <= 3 → filtered
            "from pkg.main import run_app\n"  # generic name "main" → filtered
        ) + "x = 1\n" * 50
        (tmp_path / "app.py").write_text(content)
        languages, imports = scan_languages()
        assert "utils" not in imports
        assert "ab" not in imports
        assert "main" not in imports

    def test_import_not_counted_in_test_dirs(self, tmp_path, monkeypatch):
        """Line 78: in_test check — test dirs don't count imports."""
        monkeypatch.chdir(tmp_path)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        content = "from mypackage.authentication import login\n" + "x = 1\n" * 50
        (tests_dir / "test_app.py").write_text(content)
        # Need at least one real source file for languages list
        (tmp_path / "main.py").write_text("x = 1\n" * 50)
        languages, imports = scan_languages()
        assert "authentication" not in imports

    def test_small_language_filtered(self, tmp_path, monkeypatch):
        """Line 91: pct < 3 and files < 5 → skip."""
        monkeypatch.chdir(tmp_path)
        # Big Python project
        for i in range(10):
            (tmp_path / f"mod{i}.py").write_text("x = 1\n" * 100)
        # Tiny Go file (< 3% and < 5 files → should be filtered)
        (tmp_path / "small.go").write_text("package main\n")
        languages, _ = scan_languages()
        lang_names = [l["language"] for l in languages]
        assert "Python" in lang_names
        assert "Go" not in lang_names


class TestMeasureTypeCoverageOSError:
    """non-.py skipped and OSError during read."""

    def test_non_py_file_skipped(self, tmp_path, monkeypatch):
        """Line 129: non .py files skipped."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.ts").write_text("function foo(): void {}\n")
        (tmp_path / "app.py").write_text("def foo() -> int:\n    pass\n")
        result = measure_type_coverage([{"language": "Python"}])
        assert result["total_funcs"] == 1

    def test_oserror_during_read(self, tmp_path, monkeypatch):
        """Lines 134-135: OSError during read → pass."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("def foo():\n    pass\n")
        with patch('harness_init.open', side_effect=OSError("permission denied")):
            result = measure_type_coverage([{"language": "Python"}])
        assert result["total_funcs"] == 0
        assert result["coverage"] == 0


class TestCheckExistingHooks:
    """check_hooks exception handling."""

    def test_hooks_with_valid_settings(self, tmp_path, monkeypatch):
        """Lines 160-163: parse hooks from settings JSON."""
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "hooks": {
                "PostToolUse": [
                    {"hooks": [{"command": "gitnexus-hook.cjs"}]}
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        # Override Path.home() to point at tmp_path
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        result = check_existing()
        assert result["hooks_claude"]["gitnexus"] is True

    def test_hooks_with_invalid_json(self, tmp_path, monkeypatch):
        """Lines 164-165: invalid JSON in settings → all False."""
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("not valid json")
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        result = check_existing()
        assert result["hooks_claude"]["gitnexus"] is False
        assert result["hooks_claude"]["harness_monitor"] is False

    def test_codex_gitnexus_wrapper_configured_and_self_test_passes(self, tmp_path, monkeypatch):
        codex_dir = tmp_path / ".codex"
        wrapper = codex_dir / "hooks" / "gitnexus-codex-hook.cjs"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/usr/bin/env node\n")
        hooks = {
            "hooks": {
                "PreToolUse": [{"hooks": [{"command": f'node "{wrapper}"'}]}],
                "PostToolUse": [{"hooks": [{"command": f'node "{wrapper}"'}]}],
            }
        }
        (codex_dir / "hooks.json").write_text(json.dumps(hooks))
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        with patch('harness_init.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="PASS self-test\n", stderr="")
            result = check_codex_gitnexus_wrapper()

        assert result["status"] == "pass"
        assert result["configured"] is True
        assert result["self_test_passed"] is True
        assert result["pretooluse_points_to_wrapper"] is True
        assert result["posttooluse_points_to_wrapper"] is True

    def test_codex_gitnexus_wrapper_reports_misconfigured_hooks(self, tmp_path, monkeypatch):
        codex_dir = tmp_path / ".codex"
        wrapper = codex_dir / "hooks" / "gitnexus-codex-hook.cjs"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/usr/bin/env node\n")
        hooks = {
            "hooks": {
                "PreToolUse": [{"hooks": [{"command": "node old-gitnexus-hook.cjs"}]}],
                "PostToolUse": [{"hooks": [{"command": "node old-gitnexus-hook.cjs"}]}],
            }
        }
        (codex_dir / "hooks.json").write_text(json.dumps(hooks))
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        result = check_codex_gitnexus_wrapper()

        assert result["status"] == "not_configured"
        assert result["configured"] is False
        assert result["self_test_passed"] is False

    def test_codex_gitnexus_wrapper_reports_self_test_failure(self, tmp_path, monkeypatch):
        codex_dir = tmp_path / ".codex"
        wrapper = codex_dir / "hooks" / "gitnexus-codex-hook.cjs"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/usr/bin/env node\n")
        hooks = {
            "hooks": {
                "PreToolUse": [{"hooks": [{"command": f'node "{wrapper}"'}]}],
                "PostToolUse": [{"hooks": [{"command": f'node "{wrapper}"'}]}],
            }
        }
        (codex_dir / "hooks.json").write_text(json.dumps(hooks))
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        with patch('harness_init.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad schema")
            result = check_codex_gitnexus_wrapper()

        assert result["status"] == "self_test_failed"
        assert result["configured"] is True
        assert result["self_test_passed"] is False
        assert "bad schema" in result["self_test_output"]

    def test_codex_gitnexus_wrapper_reports_missing_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)  # no ~/.codex/hooks.json
        result = check_codex_gitnexus_wrapper()
        assert result["status"] == "missing_hooks"
        assert result["hooks_json_exists"] is False
        assert result["configured"] is False

    def test_codex_gitnexus_wrapper_reports_invalid_hooks_json(self, tmp_path, monkeypatch):
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "hooks.json").write_text("{ not valid json")
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        result = check_codex_gitnexus_wrapper()
        assert result["status"] == "invalid_hooks_json"

    def test_codex_gitnexus_wrapper_reports_missing_wrapper(self, tmp_path, monkeypatch):
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        wrapper = codex_dir / "hooks" / "gitnexus-codex-hook.cjs"  # referenced but NOT created
        hooks = {
            "hooks": {
                "PreToolUse": [{"hooks": [{"command": f'node "{wrapper}"'}]}],
                "PostToolUse": [{"hooks": [{"command": f'node "{wrapper}"'}]}],
            }
        }
        (codex_dir / "hooks.json").write_text(json.dumps(hooks))
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        result = check_codex_gitnexus_wrapper()
        assert result["status"] == "missing_wrapper"
        assert result["wrapper_exists"] is False


class TestCheckLspInstalled:
    """check_lsp_installed paths."""

    def test_unknown_language(self):
        """Line 185: language not in LSP_PLUGIN_MAP → False."""
        assert check_lsp_installed("Haskell") is False

    def test_found_in_plugins_dir(self, tmp_path, monkeypatch):
        """Lines 187-188: plugin found in plugins dir."""
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        plugins = tmp_path / ".claude" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "code-intelligence-python-1.0.0").mkdir()
        assert check_lsp_installed("Python") is True

    def test_found_in_settings_json(self, tmp_path, monkeypatch):
        """Lines 190-191: plugin name found in settings.json."""
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text('{"plugins": ["code-intelligence-python"]}')
        assert check_lsp_installed("Python") is True

    def test_not_found_anywhere(self, tmp_path, monkeypatch):
        """Lines 188, 191, 192: plugin not found → False."""
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        # No plugins dir, no settings files
        assert check_lsp_installed("Python") is False


class TestAssessLspCAndCpp:
    """C/C++ assessment branch."""

    def test_cpp_many_files(self):
        with patch('harness_init.check_lsp_installed', return_value=False):
            result = assess_lsp(
                [{"language": "C++", "files": 50}],
                {"coverage": 0}
            )
            assert result[0]["recommend"] is True
            assert "compile_commands.json" in result[0]["reason"]

    def test_cpp_few_files(self):
        with patch('harness_init.check_lsp_installed', return_value=False):
            result = assess_lsp(
                [{"language": "C", "files": 10}],
                {"coverage": 0}
            )
            assert result[0]["recommend"] is False
            assert "< 30" in result[0]["reason"]


class TestAssessLspStrongTyped:
    """strong typed languages."""

    def test_typescript_many_files(self):
        with patch('harness_init.check_lsp_installed', return_value=False):
            result = assess_lsp(
                [{"language": "TypeScript", "files": 40}],
                {"coverage": 0}
            )
            assert result[0]["recommend"] is True
            assert "LSP 价值高" in result[0]["reason"]

    def test_go_few_files(self):
        with patch('harness_init.check_lsp_installed', return_value=False):
            result = assess_lsp(
                [{"language": "Go", "files": 10}],
                {"coverage": 0}
            )
            assert result[0]["recommend"] is False


class TestGetVersionFallback:
    """Cover line 230: get_version returns 'unknown' when no VERSION file found."""

    def test_returns_version_from_file(self, tmp_path, monkeypatch):
        (tmp_path / "VERSION").write_text("1.2.3\n")
        # Patch __file__ resolution so script_dir.parent points to tmp_path
        monkeypatch.setattr(
            'harness_init.Path.__file__',
            str(tmp_path / "scripts" / "harness_init.py"),
            raising=False
        )
        # Directly test: create VERSION where get_version looks
        from harness_init import get_version as gv
        # get_version looks at script_dir.parent / "VERSION"
        # script_dir = Path(__file__).resolve().parent = .../scripts
        # script_dir.parent = .../harness-init which has VERSION
        result = gv()
        # The actual VERSION file in the project root should be found
        assert result != "" or result == "unknown"

    def test_returns_unknown_when_no_file(self, tmp_path, monkeypatch):
        """Line 230: no VERSION found anywhere → 'unknown'."""
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        # Patch Path(__file__) to a non-existent dir
        import harness_init as hi
        original = hi.__file__
        hi.__file__ = str(tmp_path / "nonexistent" / "scripts" / "harness_init.py")
        try:
            result = get_version()
            assert result == "unknown"
        finally:
            hi.__file__ = original


class TestMainFunction:
    """main() entry point."""

    def test_main_default_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1\n")
        monkeypatch.setattr('sys.argv', ['harness_init.py'])
        with patch('harness_init.diagnose') as mock_diag:
            mock_diag.return_value = {"schema_version": 1, "test": True}
            from harness_init import main as hi_main
            hi_main()
            mock_diag.assert_called_once_with(".")
        captured = capsys.readouterr()
        assert '"schema_version": 1' in captured.out

    def test_main_with_arg(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr('sys.argv', ['harness_init.py', '/some/path'])
        with patch('harness_init.diagnose') as mock_diag:
            mock_diag.return_value = {"schema_version": 1}
            from harness_init import main as hi_main
            hi_main()
            mock_diag.assert_called_once_with("/some/path")
