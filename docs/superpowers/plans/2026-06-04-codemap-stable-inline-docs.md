# Stable CODE_MAP Inline Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Claude and Codex receive the same stable CODE_MAP context by rendering a number-free `CODE_MAP.md` into both root platform documents while keeping symbol counts in an ignored sidecar cache.

**Architecture:** Keep `CODE_MAP.md` as the human/model-facing semantic navigation source, but remove `(N symbols)` display counts from it. Store description-baseline symbol counts in a sidecar JSON file under the existing harness CODE_MAP cache, use that sidecar for stale-description decisions, and replace root `CLAUDE.md`/`AGENTS.md` whole-file sync with targeted CODE_MAP block rendering into both files after refresh.

**Tech Stack:** Python standard library only, existing `scripts/harness_shared.py`, `scripts/harness_monitor.py`, `scripts/harness_plan.py`, `scripts/generate_descriptions.py`, `scripts/session_context.py`, `scripts/sync_docs.py`, root platform docs, skill docs, README, and pytest.

---

## Approved Scope

This plan implements the conservative cross-platform design agreed in discussion:

- Keep `CODE_MAP.md` and the shared harness cache.
- Remove symbol-count numbers from `CODE_MAP.md`.
- Add `CODE_MAP.counts.json` as ignored machine state under the same shared cache key. The counts are description baselines, not last-observed counts.
- Render `CODE_MAP.md` into both root `CLAUDE.md` and root `AGENTS.md` after CODE_MAP refresh.
- Stop using root `CLAUDE.md` ↔ `AGENTS.md` whole-file sync as the mechanism for CODE_MAP consistency.
- Do not add SessionStart auto-refresh in this phase. SessionStart may materialize `CODE_MAP.md` from cache and warn, but it should not do heavy GitNexus/AI refresh work.
- Do not modify subdirectory platform docs in this phase.

## File Responsibilities

- Modify: `scripts/harness_shared.py`
  - Add constants and helpers for sidecar count cache path and read/write.
  - Preserve backwards-compatible `parse_codemap()` support for old count-bearing CODE_MAP files during migration.
  - Add helpers for replacing CODE_MAP blocks in platform docs.

- Modify: `scripts/harness_monitor.py`
  - Generate number-free CODE_MAP text.
  - Read old counts from sidecar JSON instead of CODE_MAP text.
  - Seed sidecar counts on bootstrap and update only refreshed description baselines after successful refresh.
  - Render the current `CODE_MAP.md` into root `CLAUDE.md` and `AGENTS.md` CODE_MAP blocks.
  - Remove or narrow root whole-file sync from the CODE_MAP refresh path.

- Modify: `scripts/generate_descriptions.py`
  - Ensure description writes work when CODE_MAP lines do not contain `(N symbols)`.
  - After writing descriptions, update CODE_MAP and cache only. Platform doc rendering stays in the branch-guarded monitor/sync orchestrators.

- Modify: `scripts/harness_plan.py`
  - Use sidecar counts as recorded counts when planning CODE_MAP refresh.
  - Preserve existing `plan_codemap()` behavior for descriptions and stale-count thresholds.

- Verify: `scripts/session_context.py`
  - Keep SessionStart lightweight; materialize `CODE_MAP.md` only and do not materialize local count sidecars. Likely needs no code change — it already materializes `CODE_MAP.md` only and never touched count sidecars.

- Modify: `scripts/sync_docs.py`
  - Stop treating root CODE_MAP consistency as whole-file copy.
  - Either leave subdirectory doc copy behavior intact or limit root behavior to CODE_MAP block rendering only.

- Modify: `README.md`, `skills/claude/SKILL.md`, `skills/codex/SKILL.md`
  - Document number-free CODE_MAP, sidecar counts, block rendering, and the end of root whole-file sync for CODE_MAP consistency.

- Test: `tests/test_harness_shared_gitnexus.py`
- Test: `tests/test_harness_monitor.py`
- Test: `tests/test_generate_descriptions.py`
- Test: `tests/test_harness_plan.py`
- Test: `tests/test_session_context.py`
- Test: `tests/test_sync_docs.py`
- Test: `tests/test_harness_init.py`

## Target Artifacts

`CODE_MAP.md` should look like this:

```md
# Code Map

> Auto-generated from GitNexus. Descriptions maintained by AI + GitNexus or 📌 manual.

### src/ — 核心源码：诊断、计划生成、Hook 与描述刷新
- **scripts/** — 自动化脚本：诊断、监控、同步和生成

### tests/ — 回归测试：覆盖 harness 初始化与 CODE_MAP 维护
```

`CODE_MAP.counts.json` should be stored outside git at:

```text
~/.local/share/harness-hooks/codemaps/<project-key>/CODE_MAP.counts.json
```

It should look like this. `described_counts` means "symbol counts when each directory's current description was accepted or seeded", not the most recent observed count:

```json
{
  "schema_version": 1,
  "described_counts": {
    "src": 200,
    "src/scripts": 87,
    "tests": 130
  }
}
```

Root `CLAUDE.md` and `AGENTS.md` should each contain:

```md
## CODE_MAP

<!-- codemap:start -->
# Code Map

### src/ — 核心源码：诊断、计划生成、Hook 与描述刷新
<!-- codemap:end -->
```

Legacy `@CODE_MAP.md` in root platform docs should be replaced by the CODE_MAP block. Existing `<!-- codemap:start -->` / `<!-- codemap:end -->` blocks should be updated in place by marker range. The renderer must not assume `## CODE_MAP` is adjacent to the marker.

## Task 1: Add Sidecar Count Helpers

**Files:**
- Modify: `scripts/harness_shared.py`
- Test: `tests/test_harness_shared_gitnexus.py`

- [ ] **Step 1: Write failing tests for sidecar count path and round trip**

Add tests:

```python
def test_codemap_counts_cache_path_shares_codemap_cache_key(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    common = project / ".git"
    common.mkdir()
    monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": common)

    path = harness_shared.codemap_counts_cache_path(project)

    assert path == tmp_path / "cache" / harness_shared.path_key(common) / "CODE_MAP.counts.json"
```

```python
def test_read_write_codemap_counts_round_trip(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")

    assert harness_shared.write_codemap_counts(project, {"src": 10, "src/api": 3}) is True
    assert harness_shared.read_codemap_counts(project) == {"src": 10, "src/api": 3}


def test_read_codemap_counts_accepts_legacy_counts_key(tmp_path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(harness_shared, "CODEMAP_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(harness_shared, "_git_common_dir", lambda project_dir=".": project / ".git")
    cache = harness_shared.codemap_counts_cache_path(project)
    cache.parent.mkdir(parents=True)
    cache.write_text('{"schema_version": 1, "counts": {"src": 10}}\n', encoding="utf-8")

    assert harness_shared.read_codemap_counts(project) == {"src": 10}
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest -q tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_codemap_counts_cache_path_shares_codemap_cache_key tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_read_write_codemap_counts_round_trip tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_read_codemap_counts_accepts_legacy_counts_key
```

Expected: both tests fail because `codemap_counts_cache_path`, `write_codemap_counts`, and `read_codemap_counts` do not exist.

- [ ] **Step 3: Implement count sidecar helpers**

Add in `scripts/harness_shared.py`:

```python
CODEMAP_COUNTS_FILENAME = "CODE_MAP.counts.json"
```

Add helpers:

```python
def codemap_counts_cache_path(project_dir: str | Path = ".") -> Path:
    """Shared CODE_MAP count sidecar path for a repo."""
    common = _git_common_dir(project_dir)
    cache_key = path_key(common if common is not None else project_dir)
    return CODEMAP_CACHE_ROOT / cache_key / CODEMAP_COUNTS_FILENAME


def read_codemap_counts(project_dir: str | Path = ".") -> dict[str, int]:
    """Read description-baseline CODE_MAP symbol counts from the shared sidecar cache."""
    path = codemap_counts_cache_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    counts = data.get("described_counts", data.get("counts", {}))
    if not isinstance(counts, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in counts.items():
        if isinstance(key, str) and isinstance(value, int) and value >= 0:
            result[key.strip("/")] = value
    return result


def write_codemap_counts(project_dir: str | Path = ".", counts: dict[str, int] | None = None) -> bool:
    """Persist description-baseline CODE_MAP symbol counts into the shared sidecar cache."""
    clean_counts = {
        str(key).strip("/"): int(value)
        for key, value in (counts or {}).items()
        if str(key).strip("/") and isinstance(value, int) and value >= 0
    }
    payload = {
        "schema_version": 1,
        "described_counts": dict(sorted(clean_counts.items())),
    }
    try:
        atomic_write_text(
            codemap_counts_cache_path(project_dir),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError:
        return False
    return True
```

- [ ] **Step 4: Run helper tests and verify GREEN**

Run:

```bash
pytest -q tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection
```

Expected: all tests in `TestCodemapLocalProjection` pass.

## Task 2: Generate Number-Free CODE_MAP Text

**Files:**
- Modify: `scripts/harness_monitor.py`
- Test: `tests/test_harness_monitor.py`

- [ ] **Step 1: Write failing tests for number-free output and returned counts**

Add tests:

```python
def test_build_codemap_structure_omits_symbol_counts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    communities = {"auth": {"symbols": 100, "clusters": 3}}
    area_to_dir = {"auth": "src/auth"}

    with patch.object(hm, "build_area_to_dir", return_value=area_to_dir):
        content, stale, counts = hm.build_codemap_structure(communities, {}, {})

    assert "(100 symbols)" not in content
    assert "### src/" in content
    assert "- **auth/**" in content
    assert counts == {"src": 100, "src/auth": 100}
```

```python
def test_build_codemap_structure_uses_sidecar_old_counts_for_stale(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    communities = {"auth": {"symbols": 200, "clusters": 3}}
    area_to_dir = {"auth": "src/auth"}

    with patch.object(hm, "build_area_to_dir", return_value=area_to_dir):
        _, stale, counts = hm.build_codemap_structure(
            communities,
            {"src": "Old desc"},
            {"src": 100},
        )

    assert "src" in stale
    assert counts["src"] == 200
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
pytest -q tests/test_harness_monitor.py::TestBuildCodemapStructure::test_build_codemap_structure_omits_symbol_counts tests/test_harness_monitor.py::TestBuildCodemapStructure::test_build_codemap_structure_uses_sidecar_old_counts_for_stale
```

Expected: fail because `build_codemap_structure()` still writes `(N symbols)` and returns only two values.

- [ ] **Step 3: Update `build_codemap_structure()`**

Change return shape from:

```python
return "\n".join(lines) + "\n", stale_dirs
```

to:

```python
return "\n".join(lines) + "\n", stale_dirs, counts
```

Build `counts` while generating:

```python
counts: dict[str, int] = {}
...
counts[top_dir] = total_syms
...
counts[sub_key] = syms
```

Change rendered lines:

```python
lines.append(f"### {top_dir}/ — {desc}")
lines.append(f"### {top_dir}/")
lines.append(f"- **{sub}/** — {sub_desc}")
lines.append(f"- **{sub}/**")
```

Do not include `(N symbols)` in generated text.

- [ ] **Step 4: Update existing direct-unpack tests for new return shape**

`build_codemap_structure()` has two distinct kinds of call sites in the suite. This step covers
only the **direct-unpack** sites — tests that call the real function and unpack its result. There
are **9**, all in `tests/test_harness_monitor.py` (`TestBuildCodemapStructure` and
`TestBuildCodemapStructureWithUncovered`): lines **347, 358, 368, 378, 385, 394, 1284, 1346, 1360**.

Locate them with:

```bash
rg -n "= hm\.build_codemap_structure\(" tests/
```

For tests that currently do:

```python
content, stale = hm.build_codemap_structure(...)
```

change to:

```python
content, stale, counts = hm.build_codemap_structure(...)
```

Where the test does not care about counts, use `_`.

> The **other** kind of call site — the 8 `patch.object(hm, 'build_codemap_structure', return_value=(...))`
> mocks in `TestDoMainBranchUpdate` — is intentionally NOT touched here. Those still return 2-tuples
> and keep passing while the production caller still 2-unpacks. They must be widened to 3-tuples in
> **Task 3 Step 4**, at the same time the production caller becomes a 3-unpack. See the note there.

- [ ] **Step 5: Run monitor structure tests**

Run:

```bash
pytest -q tests/test_harness_monitor.py::TestBuildCodemapStructure
```

Expected: all `TestBuildCodemapStructure` tests pass and no generated content contains `(N symbols)`.

## Task 3: Use Description-Baseline Counts In Refresh And Plan Paths

**Files:**
- Modify: `scripts/harness_monitor.py`
- Modify: `scripts/harness_plan.py`
- Test: `tests/test_harness_monitor.py`
- Test: `tests/test_harness_plan.py`

- [ ] **Step 1: Write failing tests for count baseline semantics**

Add tests near main update tests:

```python
def test_main_update_does_not_advance_count_baseline_for_small_count_only_drift(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitnexus").mkdir()
    content = "# Code Map\n\n### src/ — Stable desc\n"
    (tmp_path / "CODE_MAP.md").write_text(content, encoding="utf-8")

    with patch.object(hm, "ensure_gitnexus_fresh"), \
         patch.object(hm, "materialize_codemap_projection"), \
         patch.object(hm, "parse_existing_codemap", return_value=({"src": "Stable desc"}, {})), \
         patch.object(hm, "read_codemap_counts", return_value={"src": 100}), \
         patch.object(hm, "get_gitnexus_communities", return_value={"src": {"symbols": 105, "clusters": 1}}), \
         patch.object(hm, "build_codemap_structure", return_value=(content, [], {"src": 105})), \
         patch.object(hm, "cache_codemap_projection"), \
         patch.object(hm, "write_codemap_counts") as write_counts:
        hm._do_main_branch_update_inner(require_main=False)

    write_counts.assert_not_called()
```

```python
def test_main_update_seeds_count_baseline_when_sidecar_is_missing(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitnexus").mkdir()
    content = "# Code Map\n\n### src/ — Stable desc\n"
    (tmp_path / "CODE_MAP.md").write_text(content, encoding="utf-8")

    with patch.object(hm, "ensure_gitnexus_fresh"), \
         patch.object(hm, "materialize_codemap_projection"), \
         patch.object(hm, "parse_existing_codemap", return_value=({"src": "Stable desc"}, {})), \
         patch.object(hm, "read_codemap_counts", return_value={}), \
         patch.object(hm, "get_gitnexus_communities", return_value={"src": {"symbols": 105, "clusters": 1}}), \
         patch.object(hm, "build_codemap_structure", return_value=(content, [], {"src": 105})), \
         patch.object(hm, "cache_codemap_projection"), \
         patch.object(hm, "write_codemap_counts") as write_counts:
        hm._do_main_branch_update_inner(require_main=False)

    write_counts.assert_called_once_with(".", {"src": 105})
```

```python
def test_main_update_updates_count_baseline_for_refreshed_dirs_only(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitnexus").mkdir()
    old_content = "# Code Map\n\n### src/ — Old desc\n### tests/ — Stable tests\n"
    new_content = "# Code Map\n\n### src/ — New desc\n### tests/ — Stable tests\n"
    (tmp_path / "CODE_MAP.md").write_text(old_content, encoding="utf-8")

    with patch.object(hm, "ensure_gitnexus_fresh"), \
         patch.object(hm, "materialize_codemap_projection"), \
         patch.object(hm, "parse_existing_codemap", return_value=({"src": "Old desc", "tests": "Stable tests"}, {})), \
         patch.object(hm, "read_codemap_counts", return_value={"src": 100, "tests": 50}), \
         patch.object(hm, "get_gitnexus_communities", return_value={"src": {"symbols": 140, "clusters": 1}, "tests": {"symbols": 60, "clusters": 1}}), \
         patch.object(hm, "build_codemap_structure", return_value=(new_content, ["src"], {"src": 140, "tests": 60})), \
         patch.object(hm, "ensure_codemap_gitignore"), \
         patch.object(hm, "cache_codemap_projection"), \
         patch.object(hm, "write_codemap_counts") as write_counts, \
         patch.object(hm, "sync_platform_docs"), \
         patch.object(hm.subprocess, "run") as run:
        run.return_value.returncode = 0
        hm._do_main_branch_update_inner(require_main=False)

    write_counts.assert_called_once_with(".", {"src": 140, "tests": 50})
```

```python
def test_main_update_does_not_update_stale_baseline_when_description_refresh_fails(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitnexus").mkdir()
    old_content = "# Code Map\n\n### src/ — Old desc\n"
    new_content = "# Code Map\n\n### src/ — Old desc\n"
    (tmp_path / "CODE_MAP.md").write_text(old_content, encoding="utf-8")

    with patch.object(hm, "ensure_gitnexus_fresh"), \
         patch.object(hm, "materialize_codemap_projection"), \
         patch.object(hm, "parse_existing_codemap", return_value=({"src": "Old desc"}, {})), \
         patch.object(hm, "read_codemap_counts", return_value={"src": 100}), \
         patch.object(hm, "get_gitnexus_communities", return_value={"src": {"symbols": 140, "clusters": 1}}), \
         patch.object(hm, "build_codemap_structure", return_value=(new_content, ["src"], {"src": 140})), \
         patch.object(hm, "ensure_codemap_gitignore"), \
         patch.object(hm, "cache_codemap_projection"), \
         patch.object(hm, "write_codemap_counts") as write_counts, \
         patch.object(hm, "sync_platform_docs"), \
         patch.object(hm.subprocess, "run") as run:
        run.return_value.returncode = 1
        hm._do_main_branch_update_inner(require_main=False)

    write_counts.assert_not_called()
```

- [ ] **Step 2: Write failing plan test for sidecar counts**

Add to `tests/test_harness_plan.py`:

```python
def test_main_uses_sidecar_counts_for_codemap_plan(self, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CODE_MAP.md").write_text("### src/ — Existing desc\n", encoding="utf-8")
    monkeypatch.setattr(hp, "platform_files", lambda platform: ("CLAUDE.md", "AGENTS.md"))
    monkeypatch.setattr(hp, "read_codemap_counts", lambda project_dir=".": {"src": 100})
    monkeypatch.setattr(hp, "_get_live_symbol_counts", lambda: {"src": 200})
    monkeypatch.setattr(hp, "plan_gitnexus", lambda diagnostic: {"action": "skip"})
    monkeypatch.setattr(hp, "plan_lsp", lambda diagnostic: [])
    monkeypatch.setattr(hp, "plan_codex_gitnexus_wrapper", lambda diagnostic, platform: {"action": "skip"})
    monkeypatch.setattr("sys.argv", ["hp", str(tmp_path), "--platform", "claude"])

    hp.main()

    out = json.loads(capsys.readouterr().out)
    assert out["codemap"]["action"] == "refresh"
    assert out["codemap"]["dirs_needing"] == ["src"]
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
pytest -q tests/test_harness_monitor.py::TestMainUpdate::test_main_update_does_not_advance_count_baseline_for_small_count_only_drift tests/test_harness_monitor.py::TestMainUpdate::test_main_update_seeds_count_baseline_when_sidecar_is_missing tests/test_harness_monitor.py::TestMainUpdate::test_main_update_updates_count_baseline_for_refreshed_dirs_only tests/test_harness_monitor.py::TestMainUpdate::test_main_update_does_not_update_stale_baseline_when_description_refresh_fails tests/test_harness_plan.py::TestMain::test_main_uses_sidecar_counts_for_codemap_plan
```

Expected: fail because refresh paths do not read/write description-baseline counts and plan does not read sidecar counts.

- [ ] **Step 4: Wire monitor to sidecar counts**

In `scripts/harness_monitor.py`, import from shared:

```python
read_codemap_counts, write_codemap_counts
```

In `_do_main_branch_update_inner()` replace old count source:

```python
existing_descs, old_counts = parse_existing_codemap(codemap_file)
```

with:

```python
existing_descs, legacy_counts = parse_existing_codemap(codemap_file)
sidecar_counts = read_codemap_counts(".")
old_counts = sidecar_counts or legacy_counts
seed_counts = not sidecar_counts
```

Then consume the new return shape:

```python
new_content, stale_dirs, new_counts = build_codemap_structure(communities, existing_descs, old_counts)
```

> Once this 3-unpack lands, the 8 `patch.object(hm, 'build_codemap_structure', return_value=(...))`
> mocks in `tests/test_harness_monitor.py::TestDoMainBranchUpdate` (lines **667, 683, 699, 718, 764,
> 789, 819, 846**) will raise `ValueError: not enough values to unpack` because they still return
> 2-tuples. Widen each mock's `return_value` to a 3-tuple by appending a counts dict — e.g.
> `return_value=(new_content, ["src"], {"src": 200})`, using `{}` where the test does not assert on
> counts. Find them with `rg -n "build_codemap_structure', return_value=" tests/`.

Add a small merge helper in `scripts/harness_monitor.py`:

```python
def merge_codemap_count_baseline(
    old_counts: dict[str, int],
    new_counts: dict[str, int],
    refreshed_dirs: list[str],
    *,
    seed_missing: bool = False,
) -> dict[str, int]:
    """Update count baselines only for refreshed descriptions, plus bootstrap missing sidecar."""
    merged = dict(old_counts)
    if seed_missing:
        for dir_path, count in new_counts.items():
            merged.setdefault(dir_path, count)
    for dir_path in refreshed_dirs:
        if dir_path in new_counts:
            merged[dir_path] = new_counts[dir_path]
    return merged
```

Before the existing early return, seed the sidecar only when it is missing:

```python
if new_content == old_content and not stale_dirs and not entries_need_refresh:
    cache_codemap_projection(".")
    if seed_counts:
        write_codemap_counts(".", merge_codemap_count_baseline(old_counts, new_counts, [], seed_missing=True))
    return
```

Track whether the description refresh subprocess succeeded. A stale directory's baseline must not advance when `generate_descriptions.py` fails or times out:

```python
description_refresh_succeeded = not stale_dirs
...
if desc_script:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=CODEMAP_REFRESH_TIMEOUT)
        description_refresh_succeeded = result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        description_refresh_succeeded = False
```

After the description refresh path, update refreshed directory baselines only when refresh succeeded. Bootstrap seeding can still occur when no sidecar exists and there are no stale dirs:

```python
should_write_counts = (seed_counts and not stale_dirs) or (stale_dirs and description_refresh_succeeded)
if should_write_counts:
    write_codemap_counts(
        ".",
        merge_codemap_count_baseline(
            old_counts,
            new_counts,
            stale_dirs if description_refresh_succeeded else [],
            seed_missing=seed_counts,
        ),
    )
```

- [ ] **Step 5: Wire plan to sidecar counts**

In `scripts/harness_plan.py`, import:

```python
read_codemap_counts
```

In `main()`, after reading entries:

```python
recorded_counts = read_codemap_counts(".")
if recorded_counts:
    for entry in entries:
        entry["symbols"] = recorded_counts.get(entry["dir"])
```

Keep `plan_codemap(entries, live_counts)` unchanged so the stale threshold behavior stays local to the existing function. When `recorded_counts` is empty on a new machine or immediately after migration, `plan_codemap()` cannot do count-drift refresh decisions; it should still refresh empty/low-quality descriptions, and the monitor refresh path will seed sidecar baselines.

- [ ] **Step 6: Run targeted tests**

Run:

```bash
pytest -q tests/test_harness_monitor.py::TestMainUpdate tests/test_harness_plan.py::TestMain
```

Expected: targeted tests pass.

## Task 4: Keep Description Writes Working Without Counts

**Files:**
- Modify: `scripts/generate_descriptions.py`
- Test: `tests/test_generate_descriptions.py`

- [ ] **Step 1: Write failing tests for number-free rewrite**

Add tests:

```python
def test_write_top_description_without_symbol_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CODE_MAP.md").write_text("### src/\n", encoding="utf-8")

    changes = write_descriptions({"src": "核心源码：诊断与生成"})

    assert changes == [{"dir": "src", "desc": "核心源码：诊断与生成"}]
    assert (tmp_path / "CODE_MAP.md").read_text(encoding="utf-8") == "### src/ — 核心源码：诊断与生成\n"
```

```python
def test_write_sub_description_without_symbol_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CODE_MAP.md").write_text("### src/\n- **api/**\n", encoding="utf-8")

    changes = write_descriptions({"src/api": "接口层：请求处理与响应转换"})

    assert changes == [{"dir": "src/api", "desc": "接口层：请求处理与响应转换"}]
    assert "- **api/** — 接口层：请求处理与响应转换\n" in (tmp_path / "CODE_MAP.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests and verify RED or existing PASS**

Run:

```bash
pytest -q tests/test_generate_descriptions.py::TestWriteDescriptions::test_write_top_description_without_symbol_count tests/test_generate_descriptions.py::TestWriteDescriptions::test_write_sub_description_without_symbol_count
```

Expected: if tests already pass, keep them as regression coverage; if they fail due count-specific rewrite logic, continue to Step 3.

- [ ] **Step 3: Keep `rewrite_line()` count-compatible but not count-producing**

Ensure `rewrite_line()` accepts old legacy count-bearing lines during migration but removes the count from the rewritten output. For new number-free CODE_MAP lines, the output should not append a count:

```python
def rewrite_line(line: str, desc: str) -> str:
    body, newline = split_newline(line)
    body = re.sub(r'\s+\(\d+\s+symbols?\)\s*$', "", body).rstrip()
    base = body.split("—", 1)[0].rstrip()
    return f"{base} — {desc}{newline}"
```

- [ ] **Step 4: Run description tests**

Run:

```bash
pytest -q tests/test_generate_descriptions.py::TestWriteDescriptions
```

Expected: all `TestWriteDescriptions` tests pass with number-free output expectations.

## Task 5: Render CODE_MAP Blocks Into Both Root Platform Docs

**Files:**
- Modify: `scripts/harness_shared.py`
- Modify: `scripts/harness_monitor.py`
- Test: `tests/test_harness_shared_gitnexus.py`
- Test: `tests/test_harness_monitor.py`

- [ ] **Step 1: Write failing block-render helper tests**

Add tests:

```python
def test_render_codemap_block_replaces_legacy_at_reference(tmp_path):
    doc = "# Project\n\n@CODE_MAP.md\n"
    codemap = "# Code Map\n\n### src/ — Core\n"

    rendered = harness_shared.render_codemap_block(doc, codemap)

    assert "@CODE_MAP.md" not in rendered
    assert "<!-- codemap:start -->" in rendered
    assert "### src/ — Core" in rendered
```

```python
def test_render_codemap_block_updates_existing_block_without_touching_other_text():
    doc = "# Project\n\nKeep this.\n\n<!-- codemap:start -->\nold\n<!-- codemap:end -->\n"
    codemap = "# Code Map\n\n### src/ — Core\n"

    rendered = harness_shared.render_codemap_block(doc, codemap)

    assert "Keep this." in rendered
    assert "old" not in rendered
    assert rendered.count("<!-- codemap:start -->") == 1
```

```python
def test_render_codemap_block_does_not_duplicate_existing_heading_with_intervening_text():
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
```

```python
def test_update_root_codemap_docs_writes_both_docs_when_changed(tmp_path):
    (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### src/ — Core\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Project\n\n@CODE_MAP.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Project\n\n@CODE_MAP.md\n", encoding="utf-8")

    result = harness_shared.update_root_codemap_docs(tmp_path)

    assert result == {"CLAUDE.md": "updated", "AGENTS.md": "updated"}
    assert "### src/ — Core" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "### src/ — Core" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run helper tests and verify RED**

Run:

```bash
pytest -q tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_render_codemap_block_replaces_legacy_at_reference tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_render_codemap_block_updates_existing_block_without_touching_other_text tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_render_codemap_block_does_not_duplicate_existing_heading_with_intervening_text tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection::test_update_root_codemap_docs_writes_both_docs_when_changed
```

Expected: fail because render/update helpers do not exist.

- [ ] **Step 3: Implement block render helpers**

Add constants:

```python
CODEMAP_BLOCK_START = "<!-- codemap:start -->"
CODEMAP_BLOCK_END = "<!-- codemap:end -->"
ROOT_PLATFORM_DOCS = ("CLAUDE.md", "AGENTS.md")
```

Add helper:

```python
def render_codemap_block(doc_text: str, codemap_text: str) -> str:
    managed_block = f"{CODEMAP_BLOCK_START}\n{codemap_text.strip()}\n{CODEMAP_BLOCK_END}"
    section = f"## CODE_MAP\n\n{managed_block}"
    pattern = re.compile(
        rf"{re.escape(CODEMAP_BLOCK_START)}.*?{re.escape(CODEMAP_BLOCK_END)}",
        re.DOTALL | re.MULTILINE,
    )
    if pattern.search(doc_text):
        return pattern.sub(managed_block, doc_text, count=1)
    if "@CODE_MAP.md" in doc_text:
        return doc_text.replace("@CODE_MAP.md", section, 1)
    suffix = "" if doc_text.endswith("\n") else "\n"
    return f"{doc_text}{suffix}\n{section}\n"
```

Add root updater:

```python
def update_root_codemap_docs(project_dir: str | Path = ".") -> dict[str, str]:
    root = Path(project_dir)
    codemap = root / CODEMAP_FILENAME
    if not codemap.exists():
        return {}
    try:
        codemap_text = codemap.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    results: dict[str, str] = {}
    for name in ROOT_PLATFORM_DOCS:
        path = root / name
        if not path.exists():
            continue
        try:
            old = path.read_text(encoding="utf-8", errors="replace")
            new = render_codemap_block(old, codemap_text)
        except OSError:
            continue
        if new != old:
            atomic_write_text(path, new)
            results[name] = "updated"
        else:
            results[name] = "unchanged"
    return results
```

- [ ] **Step 4: Wire updater after CODE_MAP refresh under the monitor branch guard**

In `scripts/harness_monitor.py`, import `update_root_codemap_docs` and call it after CODE_MAP cache/count updates inside `_do_main_branch_update_inner()`. This call must stay after `_branch_ok()` has passed, because `CLAUDE.md` and `AGENTS.md` are tracked platform docs:

```python
update_root_codemap_docs(".")
```

Do not call `update_root_codemap_docs()` from `scripts/generate_descriptions.py`. That script may be run manually on feature branches, and writing tracked platform docs there would bypass the monitor's branch guard. It should keep writing only `CODE_MAP.md` and the shared CODE_MAP cache.

- [ ] **Step 5: Run block render tests**

Run:

```bash
pytest -q tests/test_harness_shared_gitnexus.py::TestCodemapLocalProjection tests/test_harness_monitor.py::TestMainUpdate
```

Expected: tests pass and block render helper only writes files when content actually changes. Platform doc writes happen through monitor/sync orchestration, not through standalone description generation.

## Task 6: Replace Root Whole-File Sync With Block Rendering

**Files:**
- Modify: `scripts/harness_monitor.py`
- Modify: `scripts/sync_docs.py`
- Test: `tests/test_harness_monitor.py`
- Test: `tests/test_sync_docs.py`

- [ ] **Step 1: Write failing test that root sync does not overwrite platform-specific text**

Add to `tests/test_sync_docs.py`:

```python
def test_root_sync_updates_codemap_block_without_copying_whole_file(tmp_path):
    (tmp_path / "CODE_MAP.md").write_text("# Code Map\n\n### src/ — Core\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Claude Rules\n\nClaude-only text.\n\n@CODE_MAP.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Codex Rules\n\nCodex-only text.\n\n@CODE_MAP.md\n", encoding="utf-8")

    result = sd.sync_one(str(tmp_path), "CLAUDE.md", "AGENTS.md")

    assert result["action"] == "codemap_block"
    assert "Claude-only text." in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Codex-only text." in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "### src/ — Core" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "### src/ — Core" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
```

This test intentionally does not `chdir(tmp_path)`. `sync_one()` must classify root behavior from `dir_path` itself, not from process cwd.

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
pytest -q tests/test_sync_docs.py::TestSyncOne::test_root_sync_updates_codemap_block_without_copying_whole_file
```

Expected: fail because `sync_one()` currently performs mtime-based whole-file copy.

- [ ] **Step 3: Change root sync behavior**

In `scripts/sync_docs.py`, import:

```python
from harness_shared import should_skip, platform_files, update_root_codemap_docs
```

At the start of `sync_one()`, if `dir_path` contains the root `CODE_MAP.md` and the requested files are the root platform doc names, render blocks instead of copying. Do not compare `dir_path` to `Path(".")`; callers and tests may pass absolute paths without chdir:

```python
root = Path(dir_path)
if (
    (root / "CODE_MAP.md").exists()
    and {own_file, other_file} == {"CLAUDE.md", "AGENTS.md"}
):
    result = update_root_codemap_docs(dir_path)
    if any(value == "updated" for value in result.values()):
        return {"dir": dir_path, "action": "codemap_block", "files": result}
    return None
```

Subdirectory sync may continue copying platform files in this phase because subdirectory docs do not contain the root CODE_MAP block.

- [ ] **Step 4: Update monitor comments and calls**

Change monitor comments from root `CLAUDE.md ↔ AGENTS.md sync` to root CODE_MAP block render.

If `sync_platform_docs()` remains in `scripts/harness_monitor.py`, narrow it so it delegates to `update_root_codemap_docs()` for root docs and does not copy whole files.

- [ ] **Step 5: Run sync tests**

Run:

```bash
pytest -q tests/test_sync_docs.py tests/test_harness_monitor.py::TestSyncPlatformDocs
```

Expected: root CODE_MAP blocks update without overwriting platform-specific text. Existing subdirectory copy behavior remains covered.

## Task 7: Enforce Entry Point Boundaries

**Files:**
- Verify: `scripts/harness_shared.py`
- Verify: `scripts/session_context.py`
- Verify: `scripts/generate_descriptions.py`
- Test: `tests/test_session_context.py`
- Test: `tests/test_harness_shared_gitnexus.py`
- Test: `tests/test_generate_descriptions.py`

- [ ] **Step 1: Write a test that SessionStart does not materialize local count sidecars**

Add to `tests/test_session_context.py` inside `TestMainFunction`:

```python
def test_main_does_not_materialize_local_codemap_counts(self, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    with patch("session_context.materialize_codemap_projection", return_value=False), \
         patch("session_context.get_branch", return_value="main"), \
         patch("session_context.get_ahead_behind", return_value="(↑0 ↓0 vs main)"), \
         patch("session_context.get_dirty_files", return_value=(0, "")), \
         patch("session_context.get_recent_commits", return_value=[]), \
         patch("session_context.check_gitnexus_stale", return_value=None), \
         patch("session_context.check_codemap_stale", return_value=None), \
         patch("session_context.check_codemap_migration", return_value=None), \
         patch("session_context.read_pending_notifications", return_value=[]):
        sc_main()

    assert not (tmp_path / ".harness" / "CODE_MAP.counts.json").exists()
```

- [ ] **Step 2: Write a test that standalone description writes do not render platform docs**

Add to `tests/test_generate_descriptions.py`:

```python
def test_write_descriptions_does_not_update_platform_docs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CODE_MAP.md").write_text("### src/\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Claude\n\n@CODE_MAP.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Codex\n\n@CODE_MAP.md\n", encoding="utf-8")

    write_descriptions({"src": "核心源码：诊断与生成"})

    assert "@CODE_MAP.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@CODE_MAP.md" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
```

- [ ] **Step 3: Run boundary tests**

```bash
pytest -q tests/test_session_context.py::TestMainFunction::test_main_does_not_materialize_local_codemap_counts tests/test_generate_descriptions.py::TestWriteDescriptions::test_write_descriptions_does_not_update_platform_docs
```

Expected: pass after the plan's implementation keeps SessionStart and standalone description generation lightweight. If class names differ, run the exact test names with `pytest -q tests/test_session_context.py tests/test_generate_descriptions.py -k "codemap_counts or platform_docs"`.

- [ ] **Step 4: Keep counts cache-only**

Do not add `local_codemap_counts_path()` or `materialize_codemap_counts()`. `read_codemap_counts()` reads the shared cache sidecar directly:

```python
def read_codemap_counts(project_dir: str | Path = ".") -> dict[str, int]:
    path = codemap_counts_cache_path(project_dir)
    ...
```

The sidecar is machine state for harness cache. It is not needed for model context, so there is no local `.harness/CODE_MAP.counts.json` projection in this phase.

- [ ] **Step 5: Keep `generate_descriptions.py` platform-doc neutral**

`write_descriptions()` should continue to write only `CODE_MAP.md` and `cache_codemap_projection()`. Do not import or call `update_root_codemap_docs()` from `scripts/generate_descriptions.py`.

- [ ] **Step 6: Run SessionStart and description tests**

Run:

```bash
pytest -q tests/test_session_context.py tests/test_generate_descriptions.py::TestWriteDescriptions
```

Expected: tests pass; SessionStart remains lightweight, and standalone description generation does not dirty tracked platform docs.

## Task 8: Update Diagnostics, Docs, And Skill Guidance

**Files:**
- Modify: `scripts/harness_init.py`
- Modify: `README.md`
- Modify: `skills/claude/SKILL.md`
- Modify: `skills/codex/SKILL.md`
- Test: `tests/test_harness_init.py`

- [ ] **Step 1: Update diagnostic tests**

Add or adjust tests so `has_codemap` accepts the block as the canonical mechanism and treats `@CODE_MAP.md` as legacy:

```python
def test_claude_md_with_codemap_block(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\n<!-- codemap:start -->\n# Code Map\n<!-- codemap:end -->\n",
        encoding="utf-8",
    )

    result = check_existing()

    assert result["claude_md"]["has_codemap"] is True
```

- [ ] **Step 2: Run diagnostic test and verify expected state**

Run:

```bash
pytest -q tests/test_harness_init.py::TestCheckExisting::test_claude_md_with_codemap_block
```

Expected: pass if current diagnostic already supports the block; keep it as regression coverage.

- [ ] **Step 3: Update documentation**

In `README.md`, replace statements that say platform docs use `@CODE_MAP.md` with:

```md
CLAUDE.md / AGENTS.md 通过受管 `<!-- codemap:start/end -->` 块内联 CODE_MAP 内容；`CODE_MAP.md` 本身是 ignored 本地投影和 cache 源，不再依赖平台 `@file` import 语义。
```

Add count sidecar explanation:

```md
`CODE_MAP.counts.json` 是 harness cache 下的机器状态，用于判断目录 symbol count 是否超过刷新阈值；它不进入 Git、不内联进平台文档。
```

In both skill files, update the root template from `@CODE_MAP.md` to the CODE_MAP block and update the storage model table so Claude and Codex both say "内联块" instead of "@引用".

- [ ] **Step 4: Run docs smoke check**

Run:

```bash
rg -n "@CODE_MAP.md|@引用|CLAUDE↔AGENTS 同步" README.md skills/claude/SKILL.md skills/codex/SKILL.md scripts
```

Expected: no remaining references that claim `@CODE_MAP.md` is the active platform-doc mechanism. Historical tests may still mention legacy migration behavior.

## Task 9: Full Targeted Verification

**Files:**
- No implementation files should be modified in this task.

- [ ] **Step 1: Run targeted test suite**

Run:

```bash
pytest -q tests/test_harness_shared_gitnexus.py tests/test_harness_monitor.py tests/test_generate_descriptions.py tests/test_harness_plan.py tests/test_session_context.py tests/test_sync_docs.py tests/test_harness_init.py
```

Expected: all targeted tests pass.

- [ ] **Step 2: Run import compatibility test**

Run:

```bash
pytest -q tests/test_harness_init.py::test_shared_scripts_import_under_system_python
```

Expected: pass under `/usr/bin/python3` when available.

- [ ] **Step 3: Run full test suite**

Run:

```bash
pytest -q
```

Expected: full suite passes.

- [ ] **Step 4: Run GitNexus change impact audit**

Run:

```python
gitnexus_detect_changes({"repo": "harness-init", "scope": "all"})
```

Expected: changed symbols map to CODE_MAP sidecar helpers, CODE_MAP structure/rendering, platform doc block rendering, and docs/tests only. Unexpected unrelated flows should be investigated before committing.

- [ ] **Step 5: Inspect diff for churn boundaries**

Run:

```bash
git diff --stat
git diff -- README.md skills/claude/SKILL.md skills/codex/SKILL.md scripts tests
```

Expected: no unrelated rewrites; root `CLAUDE.md` and `AGENTS.md` changes should only be incidental generated metadata or the explicit CODE_MAP block migration if implementation intentionally updates them.

## Risk Notes

- `sync_platform_docs` previously had HIGH impact because it sits in the background update chain. Keep the replacement narrow: root CODE_MAP block rendering only, no mtime-based whole-file overwrite.
- `parse_codemap()` is widely used. Preserve backwards-compatible parsing of old `(N symbols)` text during migration, but stop generating counts in new CODE_MAP output.
- Codex still reads `AGENTS.md` only at run/session start. Updating the CODE_MAP block fixes future sessions; it does not mutate an already-loaded Codex context.
- If `CODE_MAP.md` grows beyond Codex `project_doc_max_bytes` after inlining, the platform doc can still be truncated. This plan reduces churn but does not solve prompt-size budgeting.
