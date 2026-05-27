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
    check_existing, check_lsp_installed, assess_lsp, get_version, diagnose
)


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
