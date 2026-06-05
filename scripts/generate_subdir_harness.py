#!/usr/bin/env python3
"""Deterministic subdirectory harness fact generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness_shared import (
    HARNESS_BLOCK_END,
    HARNESS_BLOCK_START,
    HARNESS_FACT_HEADING,
    SOURCE_EXTS,
    STALE_THRESHOLD,
    _atomic_write_text,
    candidate_codemap_dirs,
    gitnexus_markdown_rows,
    parse_codemap,
    parse_gitnexus_markdown,
    read_codemap_counts,
    read_subdir_harness_state,
    should_skip,
    subdir_harness_state_cache_path,
    write_subdir_harness_state,
)

MAX_FACT_ROWS = 5
DEFAULT_MAX_DIRS = 5
SUPPORTED_FACT_PREFIXES = ("- 被调用:", "- 影响面:", "- 相关模块:", "- 相关流程:", "- 截断:")
EMPTY_FACT_LINE = "暂无已验证图谱事实。"
LEGACY_PROSE_HEADINGS = (
    "## 约束（基于 GitNexus 事实）",
    "## 危险操作（基于 GitNexus impact 分析）",
)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_rel_dir(dir_path: str) -> str:
    return str(dir_path).strip().strip("/").replace("\\", "/")


def _ranked(items: list[dict], name_key: str, count_key: str = "count") -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            -int(item.get(count_key, 0) or 0),
            str(item.get(name_key, "")),
            str(item.get("path", "")),
            str(item.get("id", "")),
        ),
    )


def render_fact_block(facts: dict, *, max_rows: int = MAX_FACT_ROWS) -> str:
    caller_counts = _ranked(list(facts.get("caller_counts", [])), "target")[:max_rows]
    modules = _ranked(list(facts.get("affected_modules", [])), "module")[:max_rows]
    processes = _ranked(list(facts.get("processes", [])), "process")[:max_rows]

    lines = [HARNESS_FACT_HEADING, ""]
    if not caller_counts and not modules and not processes:
        lines.append(EMPTY_FACT_LINE)
        return "\n".join(lines).strip()
    if caller_counts:
        lines.extend(f"- 被调用: {row['target']}: {int(row.get('count', 0))}" for row in caller_counts)
    if modules:
        lines.extend(f"- 相关模块: {row['module']}: {int(row.get('count', 0))}" for row in modules)
    if processes:
        lines.extend(f"- 相关流程: {row['process']}: {int(row.get('count', 0))}" for row in processes)
    return "\n".join(lines).strip()


def render_managed_block(fact_block: str) -> str:
    return f"{HARNESS_BLOCK_START}\n{fact_block.strip()}\n{HARNESS_BLOCK_END}"


def replace_or_insert_harness_block(doc_text: str, managed_block: str) -> str:
    pattern = re.compile(
        rf"{re.escape(HARNESS_BLOCK_START)}.*?{re.escape(HARNESS_BLOCK_END)}",
        re.DOTALL | re.MULTILINE,
    )
    if pattern.search(doc_text):
        rendered = pattern.sub(lambda _: managed_block, doc_text)
        first = rendered.find(managed_block)
        if first == -1:
            return rendered
        before = rendered[: first + len(managed_block)]
        after = rendered[first + len(managed_block) :].replace(managed_block, "")
        return before + after
    marker = "## 补充约束（手动维护）"
    if marker in doc_text:
        return doc_text.replace(marker, f"{managed_block}\n\n{marker}", 1)
    suffix = "" if doc_text.endswith("\n") else "\n"
    return f"{doc_text}{suffix}\n{managed_block}\n"
