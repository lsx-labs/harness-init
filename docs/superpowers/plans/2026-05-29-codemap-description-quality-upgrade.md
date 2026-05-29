# CODE_MAP Description Quality Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `harness-init` produce stable, high-quality CODE_MAP descriptions across code, tests, docs, examples, strategies, and generated-data directories.

**Architecture:** Replace the single "AI + GitNexus or fallback" path with a directory-aware description pipeline. Each directory gets an evidence bundle, a category, and a selected provider: project override, GitNexus/process AI, test summarizer, docs-title summarizer, filesystem/artifact summarizer, or low-confidence fallback. Refresh becomes incremental by directory fingerprint, with async background execution preserved.

**Tech Stack:** Python standard library only, existing `scripts/generate_descriptions.py`, `scripts/harness_monitor.py`, `scripts/harness_plan.py`, `scripts/harness_shared.py`, and current pytest suite.

---

## Problem Statement

Current CODE_MAP description generation works well for GitNexus-heavy source directories, but fails or produces weak descriptions for several important directory types:

- Artifact/data directories such as `data/cache/`, `data/results/`, `data/release_gate/` often have no GitNexus code symbols and should not spend AI/GitNexus budget.
- Test directories such as `tests/autoresearch/` have many functions/classes but no execution-flow processes, so process-oriented GitNexus prompts produce poor descriptions.
- Strategy directories such as `strategies/small_cap_100/` have enough symbols and processes, but current prompts can still collapse into function-name lists or time out.
- Documentation directories such as `docs/research/` and `docs/superpowers/` are best summarized from Markdown titles and filenames, not from code graph relationships.
- New or changed subdirectories should be refreshed independently without blocking the current session or regenerating the entire map.

The upgraded system must avoid data drift in committed docs: protected manual descriptions stay protected, generated descriptions must pass quality gates, failed AI attempts must not overwrite useful text with low-quality fallback, and the hook must run in a background job.

## Files

- Modify: `scripts/generate_descriptions.py`
  - Add evidence collection, directory classification, project overrides, provider selection, `--refresh-dir`, fingerprint support, and richer reports.
- Modify: `scripts/harness_monitor.py`
  - Keep background execution, pass targeted refresh information when available, and write job status with quality/report metadata.
- Modify: `scripts/harness_plan.py`
  - Report CODE_MAP refresh reasons and directory categories instead of only listing stale directories.
- Modify: `scripts/harness_shared.py`
  - Strengthen shared quality helpers and low-quality detection.
- Modify: `README.md`
  - Document CODE_MAP provider order, project override config, and incremental refresh behavior.
- Modify: `skills/codex/SKILL.md`
- Modify: `skills/claude/SKILL.md`
  - Update operational guidance for CODE_MAP refresh and background jobs.
- Test: `tests/test_generate_descriptions.py`
- Test: `tests/test_harness_monitor.py`
- Test: `tests/test_harness_plan.py`
- Test: `tests/test_harness_shared_quality.py`

No third-party dependency should be added. Project override config should use JSON, not YAML.

## Target Provider Order

1. Existing `📌` manual CODE_MAP description.
2. Project override from `.harness/codemap_descriptions.json`.
3. Trusted local documentation: README first paragraph, `AGENTS.md` / `CLAUDE.md` short summary, module docstring.
4. GitNexus/process AI for code directories with meaningful symbol/process coverage.
5. Test summarizer for `tests/**`.
6. Markdown-title summarizer for `docs/**`, `doc/**`, and research-note directories.
7. Example summarizer for `examples/**`.
8. Artifact/filesystem summarizer for ignored/generated directories such as `data/cache/**`.
9. Low-confidence fallback with `⚠️`, only when AI was not attempted or no trusted source exists.

## Description Quality Contract

Generated descriptions should follow this shape:

- Chinese by default for Chinese projects.
- One sentence or phrase, ideally `核心职责：关键能力1、关键能力2`.
- 8-70 Chinese characters for most directory lines.
- No raw function-name lists such as `load_module / make_task_spec`.
- No truncated tokens.
- No generic text such as `Tests for package`, `only staging area`, or `based backtest engine`.
- No invented claims unsupported by evidence.
- Include `⚠️` only for low-confidence deterministic fallback.

## Project Override Format

Create support for this optional file:

```json
{
  "descriptions": {
    "data/": {
      "description": "本地数据、研究产物、缓存、release gate 与回测结果目录",
      "source": "project_override"
    },
    "tests/": {
      "description": "测试套件：AutoResearch、VBT、因子、策略与发布门禁",
      "source": "project_override"
    }
  }
}
```

Rules:

- `📌` in CODE_MAP still has the highest priority.
- Override descriptions are treated as protected generated content unless `--refresh --ignore-overrides` is explicitly added in a future version. Do not add `--ignore-overrides` in this phase.
- Invalid JSON should not break CODE_MAP generation. Emit `override_error` in JSON output and continue.

## Directory Classification

Add deterministic category assignment:

```text
manual_protected      existing CODE_MAP desc starts with 📌
project_override      .harness/codemap_descriptions.json has this dir
code_process          GitNexus process count > 0
code_symbols          Python/source symbols exist but no process
test                  dir starts with tests/
docs                  dir starts with docs/ or doc/
example               dir starts with examples/
artifact              dir is gitignored or under data/cache, data/results, data/release_gate, data/bundle, data/factor
empty_or_marker       only marker files such as .gitkeep, AGENTS.md, CLAUDE.md
unknown               none of the above
```

Provider mapping:

```text
manual_protected -> preserve
project_override -> override
code_process -> GitNexus AI, then trusted docs, then low-confidence fallback
code_symbols -> local code/test/doc summarizer, then GitNexus AI only if useful
test -> test summarizer
docs -> markdown-title summarizer
example -> example summarizer
artifact -> artifact summarizer
empty_or_marker -> child-dir/filesystem summarizer or leave blank
unknown -> trusted docs, then low-confidence fallback
```

## Task 1: Baseline Fixture And Evidence Model

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Test: `tests/test_generate_descriptions.py`

- [ ] Add a `DirectoryEvidence` dataclass with fields:

```python
@dataclass(frozen=True)
class DirectoryEvidence:
    dir_path: str
    file_count: int
    py_count: int
    md_count: int
    json_count: int
    gitignored: bool
    gitnexus_files: int
    gitnexus_functions: int
    gitnexus_methods: int
    gitnexus_classes: int
    gitnexus_processes: int
    readme_summary: str
    module_docstring: str
    markdown_titles: tuple[str, ...]
    test_names: tuple[str, ...]
    child_dirs: tuple[str, ...]
```

- [ ] Add `collect_directory_evidence(dir_path: str) -> DirectoryEvidence`.

Implementation constraints:

- Use `rg --files` if available, otherwise fall back to `Path.rglob`.
- Use `git check-ignore -q <dir>` and known artifact path prefixes to mark generated directories.
- Do not require GitNexus. If graph queries fail, return zero GitNexus counts and include the failure in the report.

- [ ] Write tests:

```python
def test_collect_evidence_counts_test_files(tmp_path, monkeypatch):
    d = tmp_path / "tests" / "autoresearch"
    d.mkdir(parents=True)
    (d / "test_runner.py").write_text("def test_start_session():\\n    pass\\n")
    monkeypatch.chdir(tmp_path)

    evidence = gd.collect_directory_evidence("tests/autoresearch/")

    assert evidence.file_count == 1
    assert evidence.py_count == 1
    assert "test_start_session" in evidence.test_names
```

```python
def test_collect_evidence_reads_markdown_titles(tmp_path, monkeypatch):
    d = tmp_path / "docs" / "research"
    d.mkdir(parents=True)
    (d / "report.md").write_text("# Stage2 Profile\\n\\n## Summary\\n")
    monkeypatch.chdir(tmp_path)

    evidence = gd.collect_directory_evidence("docs/research/")

    assert evidence.md_count == 1
    assert evidence.markdown_titles[:2] == ("Stage2 Profile", "Summary")
```

- [ ] Run: `pytest -q tests/test_generate_descriptions.py`
- [ ] Commit: `feat: add CODE_MAP directory evidence model`

## Task 2: Project Override Support

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Test: `tests/test_generate_descriptions.py`

- [ ] Add `load_project_overrides(root: Path) -> tuple[dict[str, str], dict]`.

Rules:

- Read `.harness/codemap_descriptions.json`.
- Normalize keys so `data`, `data/`, and `./data/` resolve to `data/`.
- Accept either string values or object values with `description`.
- Reject empty, low-quality, or too-long descriptions.
- Return diagnostics:

```json
{
  "path": ".harness/codemap_descriptions.json",
  "loaded": 3,
  "rejected": {"bad/": "low_quality"},
  "error": null
}
```

- [ ] Add tests:

```python
def test_project_override_wins_over_low_quality_existing(tmp_path, monkeypatch):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "codemap_descriptions.json").write_text(
        json.dumps({"descriptions": {"tests/": "测试套件：AutoResearch 与发布门禁"}})
    )
    (tmp_path / "CODE_MAP.md").write_text("### tests/ — ⚠️ load_module / make_task_spec\\n")
    monkeypatch.chdir(tmp_path)

    overrides, report = gd.load_project_overrides(tmp_path)

    assert overrides["tests/"] == "测试套件：AutoResearch 与发布门禁"
    assert report["loaded"] == 1
```

```python
def test_invalid_project_override_reports_error(tmp_path, monkeypatch):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "codemap_descriptions.json").write_text("{")
    monkeypatch.chdir(tmp_path)

    overrides, report = gd.load_project_overrides(tmp_path)

    assert overrides == {}
    assert report["error"]
```

- [ ] Wire overrides into `main()` before AI generation.
- [ ] Preserve existing `📌` descriptions above override.
- [ ] Run: `pytest -q tests/test_generate_descriptions.py`
- [ ] Commit: `feat: support CODE_MAP project description overrides`

## Task 3: Directory Classifier And Provider Selection

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Test: `tests/test_generate_descriptions.py`

- [ ] Add `classify_directory(evidence, has_override, existing_desc) -> str`.
- [ ] Add `select_provider(category: str) -> str`.

Expected mapping:

```python
assert select_provider("test") == "test_summary"
assert select_provider("docs") == "markdown_titles"
assert select_provider("artifact") == "artifact_summary"
assert select_provider("code_process") == "ai_gitnexus"
```

- [ ] Add tests:

```python
def test_classifier_marks_tests_without_processes_as_test():
    evidence = DirectoryEvidence(
        dir_path="tests/autoresearch/",
        file_count=3,
        py_count=3,
        md_count=0,
        json_count=0,
        gitignored=False,
        gitnexus_files=3,
        gitnexus_functions=20,
        gitnexus_methods=0,
        gitnexus_classes=0,
        gitnexus_processes=0,
        readme_summary="",
        module_docstring="",
        markdown_titles=(),
        test_names=("test_session",),
        child_dirs=(),
    )

    assert gd.classify_directory(evidence, has_override=False, existing_desc="") == "test"
```

```python
def test_classifier_marks_strategy_with_processes_as_code_process():
    evidence = make_evidence("strategies/small_cap_100/", py_count=12, gitnexus_processes=7)
    assert gd.classify_directory(evidence, has_override=False, existing_desc="") == "code_process"
```

```python
def test_classifier_marks_data_cache_as_artifact():
    evidence = make_evidence("data/cache/", file_count=10, json_count=1, gitignored=True)
    assert gd.classify_directory(evidence, has_override=False, existing_desc="") == "artifact"
```

- [ ] Add provider/category to JSON dry-run output:

```json
{
  "dirs_needing": ["tests/"],
  "classification": {
    "tests/": {"category": "test", "provider": "test_summary"}
  }
}
```

- [ ] Run: `pytest -q tests/test_generate_descriptions.py tests/test_harness_plan.py`
- [ ] Commit: `feat: classify CODE_MAP directories before generation`

## Task 4: Deterministic Summarizers

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Test: `tests/test_generate_descriptions.py`

- [ ] Add `summarize_test_dir(evidence) -> str`.

Rules:

- Prefer module name and child dirs.
- Use test names only to infer topic, not as raw function list.
- Output examples:
  - `tests/autoresearch/` -> `AutoResearch 测试：分布式、权重网格、缓存与发布门禁`
  - `tests/engine_vbt/` -> `VBT 引擎测试：排名矩阵、回测结果与边界条件`

- [ ] Add `summarize_docs_dir(evidence) -> str`.

Rules:

- Use Markdown title nouns and directory name.
- Avoid report IDs as the only description.
- Output examples:
  - `docs/research/` -> `研究记录：性能验证、架构决策与实验报告`
  - `docs/superpowers/` -> `Superpowers 计划与规格：实现步骤、设计草案、审查记录`

- [ ] Add `summarize_artifact_dir(evidence) -> str`.

Rules:

- Do not inspect large generated files.
- Use path semantics and file extension counts.
- Output examples:
  - `data/cache/` -> `本地缓存产物：计算中间结果与可复用运行状态`
  - `data/results/` -> `回测结果产物：best、summary、verdict 与实验输出`

- [ ] Add `summarize_examples_dir(evidence) -> str`.

Rules:

- Use script filenames and imports when available.
- Output example:
  - `examples/` -> `示例入口：最小 VBT 排名回测脚本`

- [ ] Add tests for each summarizer and pass their outputs through `is_acceptable_description`.
- [ ] Run: `pytest -q tests/test_generate_descriptions.py tests/test_harness_shared_quality.py`
- [ ] Commit: `feat: add deterministic CODE_MAP summarizers`

## Task 5: Evidence-Aware AI Prompt

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Test: `tests/test_generate_descriptions.py`

- [ ] Change `ai_generate()` prompt to pass an evidence JSON block per directory.

Prompt must require:

```text
如果 category 是 test/docs/artifact/example，优先使用 evidence，不要强制调用 GitNexus。
如果 category 是 code_process，必须使用 GitNexus 查询或已有 evidence 中的 process/symbol 信息。
输出 JSON，key 必须完全等于输入目录，value 为中文描述，≤ 50 字。
禁止输出函数名列表、截断 token、泛化测试描述。
```

- [ ] Keep batch size and timeout behavior unchanged.
- [ ] Update `ai_report` with:

```json
{
  "provider_counts": {
    "override": 2,
    "ai_gitnexus": 4,
    "test_summary": 3,
    "markdown_titles": 2,
    "artifact_summary": 5
  }
}
```

- [ ] Add test that the prompt includes category and evidence:

```python
def test_ai_prompt_includes_evidence_and_category(monkeypatch, tmp_path):
    captured = {}
    def fake_run(args, timeout):
        captured["prompt"] = args[-1]
        return CompletedProcess(args, 0, '{"src/":"核心模块：加载与执行"}', "")

    monkeypatch.setattr(gd, "_run_ai_command", fake_run)
    gd.ai_generate(["src/"], timeout=10, evidence_by_dir={"src/": make_evidence("src/", gitnexus_processes=2)})

    assert '"dir_path": "src/"' in captured["prompt"]
    assert "category" in captured["prompt"]
```

- [ ] Run: `pytest -q tests/test_generate_descriptions.py`
- [ ] Commit: `feat: make CODE_MAP AI prompts evidence-aware`

## Task 6: Incremental Refresh And Fingerprints

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Modify: `scripts/harness_monitor.py`
- Test: `tests/test_generate_descriptions.py`
- Test: `tests/test_harness_monitor.py`

- [ ] Add CLI:

```bash
python3 generate_descriptions.py . --generate --refresh-dir tests/autoresearch
python3 generate_descriptions.py . --dry-run --refresh-dir strategies/small_cap_100
```

Rules:

- `--refresh-dir` may be repeated.
- Refresh only matching CODE_MAP entries.
- Still preserve `📌` and overrides.

- [ ] Add fingerprint calculation:

```python
def build_dir_fingerprint(dir_path: str) -> str:
    # hash of git ls-files -s output under dir plus CODE_MAP line shape
```

- [ ] Store local fingerprints outside the repo:

```text
~/.local/share/harness-hooks/projects/{project_id}/codemap_fingerprints.json
```

- [ ] Add `--use-fingerprints`:

Rules:

- If no fingerprint exists, refresh the directory.
- If fingerprint changes, refresh only that directory.
- If content is unchanged, skip unless description is low-quality.
- Ignored artifact directories may use a stable path-based fingerprint so generated cache churn does not cause CODE_MAP churn.

- [ ] Update `harness_monitor.py` background job to call:

```bash
python3 generate_descriptions.py . --generate --use-fingerprints
```

- [ ] Add tests:

```python
def test_refresh_dir_limits_generation_to_one_entry(tmp_path, monkeypatch):
    (tmp_path / "CODE_MAP.md").write_text(
        "### tests/\\n### strategies/ — 策略配置：因子组合与回测入口\\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gd, "ai_generate_batched", lambda dirs, **kwargs: ({"tests/": "测试套件：核心流程"}, {"attempted": True}))

    gd.main([".", "--generate", "--refresh-dir", "tests/"])

    text = (tmp_path / "CODE_MAP.md").read_text()
    assert "测试套件：核心流程" in text
    assert "策略配置：因子组合与回测入口" in text
```

- [ ] Run: `pytest -q tests/test_generate_descriptions.py tests/test_harness_monitor.py`
- [ ] Commit: `feat: support incremental CODE_MAP refresh`

## Task 7: Quality Report And Failure Modes

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Modify: `scripts/harness_plan.py`
- Test: `tests/test_generate_descriptions.py`
- Test: `tests/test_harness_plan.py`

- [ ] Expand `quality_before` / `quality_after`:

```json
{
  "total": 45,
  "described": 32,
  "acceptable": 30,
  "low_quality": 2,
  "low_confidence": 4,
  "empty": 9,
  "needs_refresh": 11,
  "by_category": {
    "test": {"total": 8, "acceptable": 6},
    "artifact": {"total": 6, "acceptable": 5}
  },
  "by_provider": {
    "project_override": 4,
    "test_summary": 5,
    "markdown_titles": 3
  }
}
```

- [ ] Add explicit report fields:

```json
{
  "not_indexed_dirs": ["data/cache/"],
  "indexed_but_no_process_dirs": ["tests/autoresearch/"],
  "ai_failed_dirs": ["strategies/"],
  "fallback_used_dirs": ["examples/"]
}
```

- [ ] Ensure `harness-plan.py` includes refresh reasons:

```json
{
  "codemap": {
    "action": "refresh",
    "dirs_needing": ["tests/"],
    "reasons": {"tests/": "low_quality:test_category"},
    "categories": {"tests/": "test"}
  }
}
```

- [ ] Add tests that low-quality function-list descriptions remain refreshable.
- [ ] Add tests that failed AI does not overwrite with function-list fallback.
- [ ] Run: `pytest -q tests/test_generate_descriptions.py tests/test_harness_plan.py tests/test_harness_shared_quality.py`
- [ ] Commit: `feat: report CODE_MAP description quality by provider`

## Task 8: Documentation And Skill Guidance

**Files:**
- Modify: `README.md`
- Modify: `skills/codex/SKILL.md`
- Modify: `skills/claude/SKILL.md`
- Test: `tests/test_install.py`

- [ ] Document the provider order.
- [ ] Document `.harness/codemap_descriptions.json`.
- [ ] Document targeted refresh examples:

```bash
python3 ~/.local/share/harness-hooks/generate_descriptions.py . --generate --refresh-dir tests/autoresearch
python3 ~/.local/share/harness-hooks/generate_descriptions.py . --dry-run --use-fingerprints
```

- [ ] Document that hook-triggered CODE_MAP jobs run in the background and should not block the current session.
- [ ] Document when to use overrides:

```text
Use overrides for generated artifacts, ignored cache/result directories, and business-domain summaries that are stable but not visible to GitNexus.
Do not use overrides to hide stale or wrong source-code descriptions.
```

- [ ] Run: `pytest -q tests/test_install.py`
- [ ] Commit: `docs: document CODE_MAP description providers`

## Task 9: GMatrix Validation

**Files:**
- No committed GMatrix code changes are required for this task.
- Optional generated local file for validation: `/Users/lishixiang/projects/GMatrix/.harness/codemap_descriptions.json`

- [ ] Install the local harness-init build into the active user environment.
- [ ] In GMatrix, run:

```bash
python3 ~/.local/share/harness-hooks/generate_descriptions.py . --dry-run
```

Expected:

- JSON output includes `classification`.
- `data/cache/`, `data/results/`, `data/release_gate/` are `artifact`.
- `tests/autoresearch/` is `test`.
- `strategies/small_cap_100/` is `code_process`.
- `docs/research/` is `docs`.

- [ ] Add a temporary GMatrix override file:

```json
{
  "descriptions": {
    "data/": "本地数据、研究产物、缓存、release gate 与回测结果目录",
    "tests/": "测试套件：AutoResearch、VBT、因子、策略与发布门禁",
    "docs/research/": "研究记录：性能验证、架构决策与实验报告"
  }
}
```

- [ ] Run targeted refresh:

```bash
python3 ~/.local/share/harness-hooks/generate_descriptions.py . \
  --generate \
  --refresh-dir data \
  --refresh-dir tests \
  --refresh-dir strategies \
  --refresh-dir docs/research \
  --batch-size 2 \
  --max-workers 2 \
  --ai-timeout 240
```

Expected:

- `quality_after.acceptable > quality_before.acceptable`.
- `low_quality` decreases.
- `strategies/` does not become a raw function-name list.
- `data/*` descriptions come from override/artifact provider, not AI timeout.

- [ ] Run full harness check:

```bash
python3 ~/.local/bin/harness-init.py /Users/lishixiang/projects/GMatrix
python3 ~/.local/bin/harness-plan.py /Users/lishixiang/projects/GMatrix --platform codex
```

Expected:

- No LSP false recommendation.
- CODE_MAP refresh list is smaller and includes reasons.
- Hook state/job status remains valid JSON.

- [ ] Revert temporary GMatrix override unless the user explicitly wants it committed.
- [ ] Commit harness-init validation notes only if a repo-local docs file is intentionally updated.

## Task 10: Release

**Files:**
- Modify only release metadata if the repository already uses it.

- [ ] Run all tests:

```bash
pytest -q
python3 -m py_compile scripts/*.py
git diff --check
```

Expected:

- All tests pass.
- No syntax errors.
- No whitespace errors.

- [ ] Run a no-network smoke in a temp project with:

```bash
python3 scripts/generate_descriptions.py /tmp/harness-smoke --dry-run
```

- [ ] Merge to `main`.
- [ ] Tag release as the next minor version, because this changes CODE_MAP behavior:

```bash
git tag v3.3.0
git push origin main --tags
gh release create v3.3.0 --generate-notes
```

- [ ] Install the release locally and rerun GMatrix dry-run.

## Acceptance Criteria

- GMatrix `data/*` artifact directories no longer appear as unexplained empty CODE_MAP entries when an override or artifact summary is available.
- GMatrix `tests/*` descriptions are generated from test purpose, not raw function names.
- GMatrix `strategies/*` descriptions either come from GitNexus/process evidence or remain unchanged; they are not overwritten by low-quality fallback.
- GMatrix docs/example directories get useful deterministic descriptions without requiring GitNexus process coverage.
- `--refresh-dir` can update one directory without touching unrelated CODE_MAP entries.
- Background hook execution remains asynchronous and writes job status.
- JSON reports clearly explain which directories were not indexed, indexed-without-processes, AI-generated, override-backed, or fallback-backed.
- All existing tests pass.

## Execution Order

1. Task 1: Evidence model.
2. Task 2: Project overrides.
3. Task 3: Classification/provider selection.
4. Task 4: Deterministic summarizers.
5. Task 5: Evidence-aware AI prompt.
6. Task 6: Incremental refresh/fingerprints.
7. Task 7: Quality reporting.
8. Task 8: Docs/skill update.
9. Task 9: GMatrix validation.
10. Task 10: Release.

## Risk Controls

- Keep all new behavior behind existing `--generate` / hook paths; `--dry-run` must stay read-only.
- Do not add PyYAML or other dependencies.
- Never overwrite `📌` manual descriptions.
- If override JSON is invalid, report and continue.
- If GitNexus/MCP/CLI fails, do not block deterministic providers.
- If AI times out, kill the process group and do not write function-list fallback.
- Treat local fingerprint state as cache only; losing it should cause extra refresh, not incorrect descriptions.

