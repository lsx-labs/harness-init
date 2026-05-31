"""Tests for shared CODE_MAP description quality helpers."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import harness_shared


def test_low_quality_detects_function_name_lists() -> None:
    assert harness_shared.is_low_quality_description(
        "run_combo / load_market_tensors / nav_to_metrics",
    )


def test_low_quality_detects_truncated_tokens() -> None:
    assert harness_shared.is_low_quality_description(
        "configure_module / resolve_trade_lookup_cache_dir / resolve_",
    )


def test_low_quality_detects_camel_case_function_names() -> None:
    assert harness_shared.is_low_quality_description("getFactor option_value")


def test_low_quality_detects_single_code_name_description() -> None:
    assert harness_shared.is_low_quality_description("build_user_profile")


def test_low_quality_does_not_reject_chinese_description_with_identifier() -> None:
    assert not harness_shared.is_low_quality_description("数据加载模块：getData 输入适配与缓存管理")
    assert not harness_shared.is_low_quality_description("配置模块：build_config 参数校验")


def test_low_quality_does_not_reject_english_description_with_snake_case_term() -> None:
    assert not harness_shared.is_low_quality_description("Handles user_id validation")
    assert not harness_shared.is_low_quality_description("factor_cache local materials")


def test_low_quality_detects_generic_test_descriptions() -> None:
    assert harness_shared.is_low_quality_description("Tests for engine_vbt package.")


def test_manual_descriptions_are_not_low_quality() -> None:
    assert not harness_shared.is_low_quality_description("📌 手工固定描述")


def test_semantic_chinese_description_is_acceptable() -> None:
    assert harness_shared.is_acceptable_description(
        "回测核心内核：rank 输入校验、持仓撮合、NAV/指标计算",
    )


def test_low_confidence_descriptions_need_refresh() -> None:
    assert harness_shared.needs_description_refresh("⚠️ run_combo / load_data")


def test_slash_separated_list_is_low_quality() -> None:
    # a " / "-joined dir listing is navigation noise, not a description → must refresh
    assert harness_shared.is_low_quality_description("unit / integration / e2e")
