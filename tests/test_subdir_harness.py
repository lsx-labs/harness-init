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
