"""Tests for generate_subdir_harness.py."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_subdir_harness as gsh


def test_render_fact_block_sorts_rows_stably() -> None:
    facts = {
        "caller_counts": [
            {"target": "beta", "count": 2},
            {"target": "alpha", "count": 2},
            {"target": "gamma", "count": 9},
        ],
        "affected_modules": [
            {"module": "zeta", "count": 1},
            {"module": "alpha", "count": 1},
        ],
        "processes": [
            {"process": "Build", "count": 1},
            {"process": "Analyze", "count": 1},
        ],
        "symbol_count": 12,
    }

    rendered = gsh.render_fact_block(facts)

    assert rendered.startswith("## GitNexus 事实\n")
    assert rendered.index("gamma: 9") < rendered.index("alpha: 2") < rendered.index("beta: 2")
    assert rendered.index("alpha: 1") < rendered.index("zeta: 1")
    assert rendered.index("Analyze: 1") < rendered.index("Build: 1")


def test_render_managed_block_contains_only_harness_markers() -> None:
    block = gsh.render_managed_block("## GitNexus 事实\n\n暂无已验证图谱事实。")

    assert block == (
        "<!-- harness:start -->\n"
        "## GitNexus 事实\n\n"
        "暂无已验证图谱事实。\n"
        "<!-- harness:end -->"
    )


def test_replace_existing_harness_block_preserves_surrounding_text() -> None:
    doc = "# src\n\nmanual before\n\n<!-- harness:start -->\nold\n<!-- harness:end -->\n\nmanual after\n"
    block = gsh.render_managed_block("## GitNexus 事实\n\n暂无已验证图谱事实。")

    rendered = gsh.replace_or_insert_harness_block(doc, block)

    assert "manual before" in rendered
    assert "manual after" in rendered
    assert "old" not in rendered
    assert rendered.count("<!-- harness:start -->") == 1


def test_replace_existing_harness_block_replaces_duplicate_blocks() -> None:
    doc = (
        "# src\n\n"
        "<!-- harness:start -->\nold one\n<!-- harness:end -->\n\n"
        "middle\n\n"
        "<!-- harness:start -->\nold two\n<!-- harness:end -->\n"
    )
    block = gsh.render_managed_block("## GitNexus 事实\n\n暂无已验证图谱事实。")

    rendered = gsh.replace_or_insert_harness_block(doc, block)

    assert "old one" not in rendered
    assert "old two" not in rendered
    assert rendered.count("<!-- harness:start -->") == 1


def test_structural_check_rejects_legacy_prose_block() -> None:
    body = (
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "\n"
        "## 危险操作（基于 GitNexus impact 分析）\n"
        "- **parser.py**: 高影响修改\n"
    )

    result = gsh.structural_fact_block_check(body)

    assert result["ok"] is False
    assert result["reason"] == "legacy_prose"


def test_structural_stale_block_routes_to_refresh_not_manual_migration() -> None:
    existing = "## GitNexus 事实\n\n- 被调用: Parser: 23\n- 相关模块: core: 1\n- 相关流程: Analyze: 1"
    current_facts = {
        "caller_counts": [{"target": "Parser", "count": 40}],
        "affected_modules": [{"module": "core", "count": 1}],
        "processes": [{"process": "Analyze", "count": 1}],
        "symbol_count": 40,
    }

    action = gsh.plan_existing_block_action(existing, current_facts, baseline={"gitnexus_fingerprint": "sha256:old"})

    assert action["action"] == "refresh_facts"
    assert action["reason"] == "freshness_changed"


def test_current_structural_block_without_baseline_routes_to_rebaseline() -> None:
    current_facts = {
        "caller_counts": [{"target": "Parser", "count": 40}],
        "affected_modules": [{"module": "core", "count": 1}],
        "processes": [{"process": "Analyze", "count": 1}],
        "symbol_count": 40,
    }
    existing = gsh.render_fact_block(current_facts)

    action = gsh.plan_existing_block_action(existing, current_facts, baseline=None)

    assert action["action"] == "rebaseline"
    assert action["reason"] == "structural_fact_block_current_missing_sidecar"


def test_write_rebaseline_state_does_not_change_doc(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").mkdir()
    written: dict = {}
    monkeypatch.setattr(gsh, "read_subdir_harness_state", lambda project_dir=".": {"schema_version": 1, "dirs": {}})
    monkeypatch.setattr(gsh, "write_subdir_harness_state", lambda project_dir, payload: written.update(payload) or True)
    doc = project / "src" / "CLAUDE.md"
    doc.parent.mkdir()
    facts = {
        "caller_counts": [{"target": "Parser", "count": 40}],
        "affected_modules": [],
        "processes": [],
        "symbol_count": 40,
    }
    original = gsh.render_managed_block(gsh.render_fact_block(facts))
    doc.write_text(original, encoding="utf-8")

    result = gsh.write_rebaseline_state(project, "src", facts, ["CLAUDE.md"])

    assert result["action"] == "rebaseline"
    assert doc.read_text(encoding="utf-8") == original
    assert written["dirs"]["src"]["gitnexus_fingerprint"].startswith("sha256:")


def test_manual_migration_preserves_legacy_body_outside_block(tmp_path) -> None:
    doc_path = tmp_path / "src" / "CLAUDE.md"
    doc_path.parent.mkdir()
    doc_path.write_text(
        "# src\n\n"
        "<!-- harness:start -->\n"
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "<!-- harness:end -->\n",
        encoding="utf-8",
    )
    fact_block = gsh.render_fact_block({"caller_counts": [], "affected_modules": [], "processes": [], "symbol_count": 0})

    result = gsh.migrate_legacy_doc_to_facts(doc_path, fact_block)

    text = doc_path.read_text(encoding="utf-8")
    assert result == "updated"
    assert "## GitNexus 事实" in text
    assert "### 从旧 harness 块迁移" in text
    assert "Parser 被多个调用方使用" in text
    assert text.index("## GitNexus 事实") < text.index("### 从旧 harness 块迁移")


def _patch_state(monkeypatch):
    written: dict = {}
    monkeypatch.setattr(gsh, "read_subdir_harness_state", lambda project_dir=".": {"schema_version": 1, "dirs": {}})
    monkeypatch.setattr(gsh, "write_subdir_harness_state", lambda project_dir, payload: written.update(payload) or True)
    return written


def test_plan_directory_reports_refresh_for_stale_structural_block(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    old_facts = {"caller_counts": [{"target": "Parser", "count": 23}], "affected_modules": [], "processes": [], "symbol_count": 23}
    new_facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    (doc_dir / "CLAUDE.md").write_text(gsh.render_managed_block(gsh.render_fact_block(old_facts)), encoding="utf-8")
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: new_facts)

    result = gsh.plan_directory(project, "src", ["CLAUDE.md"], mode="background")

    assert result["action"] == "refresh_facts"
    assert result["files"] == ["CLAUDE.md"]
    assert result["file_actions"] == [{"file": "CLAUDE.md", "action": "refresh_facts", "reason": "freshness_changed"}]


def test_plan_directory_does_not_overwrite_mixed_legacy_doc(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    (doc_dir / "CLAUDE.md").write_text(gsh.render_managed_block(gsh.render_fact_block(facts)), encoding="utf-8")
    (doc_dir / "AGENTS.md").write_text(
        "<!-- harness:start -->\n"
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "<!-- harness:end -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: facts)

    result = gsh.plan_directory(project, "src", ["CLAUDE.md", "AGENTS.md"], mode="background")

    assert result["action"] == "manual_migration"
    assert result["files"] == ["AGENTS.md"]
    assert result["file_actions"] == [
        {"file": "CLAUDE.md", "action": "rebaseline", "reason": "structural_fact_block_current_missing_sidecar"},
        {"file": "AGENTS.md", "action": "manual_migration", "reason": "legacy_prose"},
    ]


def test_refresh_directory_updates_only_managed_block(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    old_facts = {"caller_counts": [{"target": "Parser", "count": 23}], "affected_modules": [], "processes": [], "symbol_count": 23}
    new_facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    (doc_dir / "CLAUDE.md").write_text(
        "# src\n\nmanual before\n\n"
        + gsh.render_managed_block(gsh.render_fact_block(old_facts))
        + "\n\nmanual after\n",
        encoding="utf-8",
    )
    written = _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: new_facts)

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="background")

    text = (doc_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["action"] == "refresh_facts"
    assert "manual before" in text
    assert "manual after" in text
    assert "Parser: 40" in text
    assert "Parser: 23" not in text
    assert written["dirs"]["src"]["rendered"]["CLAUDE.md"]["status"] == "updated"


def test_manual_bootstrap_creates_missing_platform_doc(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    (project / "src").mkdir(parents=True)
    (project / ".git").mkdir()
    facts = {"caller_counts": [], "affected_modules": [], "processes": [], "symbol_count": 0}
    written = _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: facts)

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="manual", bootstrap=True)

    text = (project / "src" / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["action"] == "bootstrap"
    assert "# src/ — GitNexus 事实" in text
    assert "## 测试" in text
    assert "未识别专用测试命令" in text
    assert "<!-- harness:start -->" in text
    assert "暂无已验证图谱事实。" in text
    assert "## 补充约束（手动维护）" in text
    assert written["dirs"]["src"]["rendered"]["CLAUDE.md"]["status"] == "created"


def test_manual_bootstrap_uses_pytest_command_when_project_has_tests(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    (project / "scripts").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "tests" / "test_harness.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (project / ".git").mkdir()
    facts = {"caller_counts": [], "affected_modules": [], "processes": [], "symbol_count": 0}
    _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: facts)

    result = gsh.refresh_directory(project, "scripts", ["AGENTS.md"], mode="manual", bootstrap=True)

    text = (project / "scripts" / "AGENTS.md").read_text(encoding="utf-8")
    assert result["action"] == "bootstrap"
    assert "- `python3 -m pytest -q`" in text
    assert "未识别专用测试命令" not in text


def test_manual_migrate_routes_legacy_doc_through_migration(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    (doc_dir / "CLAUDE.md").write_text(
        "# src\n\n"
        "<!-- harness:start -->\n"
        "## 约束（基于 GitNexus 事实）\n"
        "- Parser 被多个调用方使用，应谨慎修改。\n"
        "<!-- harness:end -->\n",
        encoding="utf-8",
    )
    facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: facts)

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="manual", migrate=True)

    text = (doc_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["action"] == "manual_migration"
    assert result["status"] == "updated"
    assert "Parser: 40" in text
    assert "### 从旧 harness 块迁移" in text
    assert "Parser 被多个调用方使用" in text


def test_extract_dir_facts_uses_schema_tolerant_gitnexus_rows(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    outputs = iter([
        [["Parser", "40", "src/parser.py"]],
        [["Core", "3"]],
        [["AnalyzeProject", "2"]],
        [["128"]],
    ])
    monkeypatch.setattr(gsh, "_run_gitnexus_cypher", lambda project_dir, cypher: next(outputs))
    monkeypatch.setattr(gsh, "source_fingerprint", lambda project_dir, paths=None: "sha256:source")

    facts = gsh.extract_dir_facts(project, "src")

    assert facts["caller_counts"] == [{"target": "Parser", "count": 40, "path": "src/parser.py"}]
    assert facts["affected_modules"] == [{"module": "Core", "count": 3}]
    assert facts["processes"] == [{"process": "AnalyzeProject", "count": 2}]
    assert facts["symbol_count"] == 128
    assert facts["repo_source_fingerprint"] == "sha256:source"
    assert facts["source_fingerprint"] == "sha256:source"
    assert facts["known_caller_source_fingerprint"] == "sha256:source"


def test_extract_dir_facts_marks_gitnexus_unavailable_on_query_failure(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    outputs = iter([
        [["Parser", "40", "src/parser.py"]],
        None,
        [["AnalyzeProject", "2"]],
        [["128"]],
    ])
    monkeypatch.setattr(gsh, "_run_gitnexus_cypher", lambda project_dir, cypher: next(outputs))
    monkeypatch.setattr(gsh, "source_fingerprint", lambda project_dir, paths=None: "sha256:source")

    facts = gsh.extract_dir_facts(project, "src")

    assert facts["gitnexus_available"] is False
    assert facts["gitnexus_error"] == "gitnexus_unavailable"


def test_refresh_directory_does_not_overwrite_existing_block_when_gitnexus_unavailable(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    old_facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    original = gsh.render_managed_block(gsh.render_fact_block(old_facts))
    (doc_dir / "CLAUDE.md").write_text(original, encoding="utf-8")
    written: dict = {}
    monkeypatch.setattr(gsh, "read_subdir_harness_state", lambda project_dir=".": {"schema_version": 1, "dirs": {}})
    monkeypatch.setattr(gsh, "write_subdir_harness_state", lambda project_dir, payload: written.update(payload) or True)
    monkeypatch.setattr(
        gsh,
        "extract_dir_facts",
        lambda project_dir, dir_path, source_snapshot=None: {
            "caller_counts": [],
            "affected_modules": [],
            "processes": [],
            "symbol_count": 0,
            "gitnexus_available": False,
            "gitnexus_error": "gitnexus_unavailable",
            "repo_source_fingerprint": "sha256:repo",
            "source_fingerprint": "sha256:src",
            "known_caller_source_fingerprint": "sha256:callers",
        },
    )

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="background")

    assert result["action"] == "skip"
    assert result["reason"] == "gitnexus_unavailable"
    assert (doc_dir / "CLAUDE.md").read_text(encoding="utf-8") == original
    assert written == {}


def test_gitnexus_repo_name_matches_meta_repo_path_from_list(tmp_path, monkeypatch) -> None:
    project = tmp_path / "linked-worktree"
    (project / ".gitnexus").mkdir(parents=True)
    (project / ".gitnexus" / "meta.json").write_text(
        '{"repoPath": "/real/repo/path"}',
        encoding="utf-8",
    )

    class Result:
        returncode = 0
        stdout = "\n  harness-init  (/real/repo/path)\n    Path:    /real/repo/path\n"
        stderr = ""

    monkeypatch.setattr(gsh.subprocess, "run", lambda *args, **kwargs: Result())

    assert gsh.gitnexus_repo_name(project) == "harness-init"


def test_source_fingerprint_changes_when_source_file_changes(tmp_path) -> None:
    project = tmp_path / "repo"
    (project / "src").mkdir(parents=True)
    source = project / "src" / "parser.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    first = gsh.source_fingerprint(project, ["src/parser.py"])
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = gsh.source_fingerprint(project, ["src/parser.py"])

    assert first.startswith("sha256:")
    assert second.startswith("sha256:")
    assert first != second


def test_plan_directory_skips_graph_when_repo_source_fingerprint_unchanged(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    fact_block = gsh.render_fact_block(facts)
    (doc_dir / "CLAUDE.md").write_text(gsh.render_managed_block(fact_block), encoding="utf-8")
    monkeypatch.setattr(gsh, "source_fingerprint", lambda project_dir, paths=None: "sha256:same")
    monkeypatch.setattr(
        gsh,
        "read_subdir_harness_state",
        lambda project_dir=".": {
            "schema_version": 1,
            "dirs": {
                "src": {
                    "repo_source_fingerprint": "sha256:same",
                    "gitnexus_fingerprint": gsh.gitnexus_fingerprint(facts),
                    "block_hash": gsh._sha256_text(fact_block),
                    "fact_block": fact_block,
                }
            },
        },
    )
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: (_ for _ in ()).throw(AssertionError("graph should be skipped")))

    result = gsh.plan_directory(project, "src", ["CLAUDE.md"], mode="background")

    assert result["action"] == "skip"
    assert result["reason"] == "repo_source_fingerprint_unchanged"


def test_refresh_directory_skip_returns_before_graph_extraction(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    fact_block = gsh.render_fact_block(facts)
    (doc_dir / "CLAUDE.md").write_text(gsh.render_managed_block(fact_block), encoding="utf-8")
    monkeypatch.setattr(gsh, "source_fingerprint", lambda project_dir, paths=None: "sha256:same")
    monkeypatch.setattr(
        gsh,
        "read_subdir_harness_state",
        lambda project_dir=".": {
            "schema_version": 1,
            "dirs": {
                "src": {
                    "repo_source_fingerprint": "sha256:same",
                    "gitnexus_fingerprint": gsh.gitnexus_fingerprint(facts),
                    "block_hash": gsh._sha256_text(fact_block),
                    "fact_block": fact_block,
                }
            },
        },
    )
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: (_ for _ in ()).throw(AssertionError("graph should be skipped")))

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="background")

    assert result["action"] == "skip"
    assert result["reason"] == "repo_source_fingerprint_unchanged"


def test_main_builds_source_snapshot_once_for_multiple_dirs(tmp_path, monkeypatch, capsys) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    snapshot = {"source_files": ["src/a.py", "tests/test_a.py"], "repo_source_fingerprint": "sha256:repo"}
    snapshots_seen = []
    monkeypatch.setattr(gsh, "build_source_snapshot", lambda project_dir: snapshot)

    def fake_plan_directory(project_dir, dir_path, files, mode="background", source_snapshot=None, expected_branch=None):
        snapshots_seen.append(source_snapshot)
        return {"dir": dir_path, "action": "skip", "files": []}

    monkeypatch.setattr(gsh, "plan_directory", fake_plan_directory)

    assert gsh.main([str(project), "--plan", "--dirs", "src", "tests", "--max-dirs", "2"]) == 0

    assert snapshots_seen == [snapshot, snapshot]


def test_refresh_directory_refuses_tracked_doc_write_after_branch_change(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    doc_dir = project / "src"
    doc_dir.mkdir(parents=True)
    old_facts = {"caller_counts": [{"target": "Parser", "count": 23}], "affected_modules": [], "processes": [], "symbol_count": 23}
    new_facts = {"caller_counts": [{"target": "Parser", "count": 40}], "affected_modules": [], "processes": [], "symbol_count": 40}
    original = gsh.render_managed_block(gsh.render_fact_block(old_facts))
    (doc_dir / "CLAUDE.md").write_text(original, encoding="utf-8")
    _patch_state(monkeypatch)
    monkeypatch.setattr(gsh, "extract_dir_facts", lambda project_dir, dir_path, source_snapshot=None: new_facts)
    monkeypatch.setattr(gsh, "current_branch", lambda project_dir: "feature")

    result = gsh.refresh_directory(project, "src", ["CLAUDE.md"], mode="background", expected_branch="main")

    assert result["status"] == "branch_changed"
    assert (doc_dir / "CLAUDE.md").read_text(encoding="utf-8") == original
