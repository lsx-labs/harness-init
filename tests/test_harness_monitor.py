"""Tests for harness_monitor.py"""

import fcntl
import json
import os
import subprocess
import time
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import harness_monitor as hm
import harness_shared


def _hold_flock(lock_dir, lock_name):
    """Hold a real flock on a lock file (simulates another process holding it).
    Returns the fd — close it to release."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_dir / lock_name), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def read_post_tool_context(stdout: str) -> str:
    payload = json.loads(stdout)
    assert set(payload) <= {"continue", "hookSpecificOutput", "suppressOutput", "systemMessage"}
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PostToolUse"
    return hook_output["additionalContext"]


def test_monitor_uses_postponed_annotations_for_python39() -> None:
    source = Path(hm.__file__).read_text(encoding="utf-8")
    assert "from __future__ import annotations" in source
    assert source.index("from __future__ import annotations") < source.index("import io")


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

    def test_git_after_command_separator(self):
        assert hm.is_git_operation({"tool_input": {"command": "cd repo && git commit -m test"}}) is True

    def test_quoted_git_text_is_not_git_operation(self):
        assert hm.is_git_operation({"tool_input": {"command": 'echo "git commit"'}}) is False

    def test_many_git_options_without_target_command_returns_quickly(self):
        command = "git " + " ".join(["-x"] * 28) + " status"
        started = time.perf_counter()

        assert hm.is_git_operation({"tool_input": {"command": command}}) is False

        assert time.perf_counter() - started < 0.5

    def test_not_git(self):
        assert hm.is_git_operation({"tool_input": {"command": "pytest tests/"}}) is False

    def test_empty(self):
        assert hm.is_git_operation({"tool_input": {"command": ""}}) is False

    def test_sudo_prefix(self):
        assert hm.is_git_operation({"tool_input": {"command": "sudo git pull"}}) is True

    def test_env_assignment_prefix(self):
        assert hm.is_git_operation({"tool_input": {"command": "GIT_DIR=/x git commit -m y"}}) is True

    def test_env_wrapper_prefix(self):
        assert hm.is_git_operation({"tool_input": {"command": "env FOO=1 git checkout main"}}) is True

    def test_time_wrapper_prefix(self):
        assert hm.is_git_operation({"tool_input": {"command": "time git rebase main"}}) is True

    def test_subshell(self):
        assert hm.is_git_operation({"tool_input": {"command": "x=$(git merge feat)"}}) is True

    def test_echo_with_git_word_still_not_git(self):
        # a non-wrapper leading token must NOT count as a git operation
        assert hm.is_git_operation({"tool_input": {"command": "echo git commit"}}) is False

    def test_many_env_prefixes_returns_quickly(self):
        command = " ".join([f"V{i}=1" for i in range(40)]) + " echo done"
        started = time.perf_counter()
        assert hm.is_git_operation({"tool_input": {"command": command}}) is False
        assert time.perf_counter() - started < 0.5

    def test_git_C_path_with_separate_value(self):
        assert hm.is_git_operation({"tool_input": {"command": "git -C /repo commit -m x"}}) is True

    def test_git_c_config_with_separate_value(self):
        assert hm.is_git_operation({"tool_input": {"command": "git -c user.name=x commit -m y"}}) is True

    def test_leading_whitespace_before_git(self):
        assert hm.is_git_operation({"tool_input": {"command": "   git commit -m x"}}) is True

    def test_bare_parenthesized_subshell(self):
        assert hm.is_git_operation({"tool_input": {"command": "(git commit -m x)"}}) is True

    def test_config_subkey_named_commit_is_not_an_operation(self):
        # false-positive guard: `commit.gpgsign` is a config key, not the commit verb
        assert hm.is_git_operation({"tool_input": {"command": "git config commit.gpgsign true"}}) is False


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


class TestParseCodemapEntry:
    def test_desc_before_count(self):
        desc, count = harness_shared.parse_codemap_entry("(100 symbols) — My description")
        assert desc == "My description"
        assert count == 100

    def test_only_count(self):
        desc, count = harness_shared.parse_codemap_entry("(50 symbols)")
        assert desc == ""
        assert count == 50

    def test_empty(self):
        desc, count = harness_shared.parse_codemap_entry("")
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

    def test_drops_low_quality_existing_descriptions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CODE_MAP.md").write_text(
            "### engine/ (100 symbols) — run_combo / load_market_tensors / nav_to_metrics\n"
            "### good/ (10 symbols) — 回测核心内核：rank 输入校验、持仓撮合、NAV/指标计算\n"
        )
        descs, counts = hm.parse_existing_codemap(Path("CODE_MAP.md"))
        assert "engine" not in descs
        assert descs["good"].startswith("回测核心内核")
        assert counts["engine"] == 100


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


class TestGetProjectId:
    """get_project_id."""

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
    """get_gitnexus_communities."""

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
    """build_area_to_dir."""

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
    """build_codemap_structure."""

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


class TestSyncPlatformDocs:
    def test_equal_mtime_content_conflict_does_not_overwrite(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        agents = tmp_path / "AGENTS.md"
        claude.write_text("claude content", encoding="utf-8")
        agents.write_text("agent content", encoding="utf-8")
        timestamp = 1_700_000_000
        os.utime(claude, (timestamp, timestamp))
        os.utime(agents, (timestamp, timestamp))

        result = hm.sync_platform_docs(tmp_path)

        assert result == "conflict"
        assert claude.read_text(encoding="utf-8") == "claude content"
        assert agents.read_text(encoding="utf-8") == "agent content"

    def test_newer_claude_overwrites_agents(self, tmp_path):
        # This production path overwrites real files; the newer file's content must win.
        # A swapped copy direction (silent data loss) fails this assertion.
        claude = tmp_path / "CLAUDE.md"
        agents = tmp_path / "AGENTS.md"
        claude.write_text("NEW from claude", encoding="utf-8")
        agents.write_text("OLD agents", encoding="utf-8")
        os.utime(agents, (1_700_000_000, 1_700_000_000))
        os.utime(claude, (1_700_000_500, 1_700_000_500))  # claude is newer

        assert hm.sync_platform_docs(tmp_path) == "claude_to_agents"
        assert agents.read_text(encoding="utf-8") == "NEW from claude"
        assert claude.read_text(encoding="utf-8") == "NEW from claude"

    def test_newer_agents_overwrites_claude(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        agents = tmp_path / "AGENTS.md"
        claude.write_text("OLD claude", encoding="utf-8")
        agents.write_text("NEW from agents", encoding="utf-8")
        os.utime(claude, (1_700_000_000, 1_700_000_000))
        os.utime(agents, (1_700_000_500, 1_700_000_500))  # agents is newer

        assert hm.sync_platform_docs(tmp_path) == "agents_to_claude"
        assert claude.read_text(encoding="utf-8") == "NEW from agents"
        assert agents.read_text(encoding="utf-8") == "NEW from agents"

    def test_identical_content_is_noop(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        agents = tmp_path / "AGENTS.md"
        claude.write_text("same", encoding="utf-8")
        agents.write_text("same", encoding="utf-8")
        assert hm.sync_platform_docs(tmp_path) is None

    def test_missing_file_is_noop(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("only claude", encoding="utf-8")
        assert hm.sync_platform_docs(tmp_path) is None


class TestBackgroundDispatch:
    """Cover handle_main_branch_update (dispatcher) + lock mechanism."""

    def test_spawns_background_process(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        with patch.object(hm.subprocess, 'Popen') as mock_popen:
            hm.handle_main_branch_update("test_project")
        mock_popen.assert_called_once()
        args = mock_popen.call_args
        assert "--bg" in args[0][0]
        context = read_post_tool_context(capsys.readouterr().out)
        assert "Harness 更新已在后台启动" in context
        jobs = list((tmp_path / "jobs").glob("*.json"))
        assert len(jobs) == 1
        job = jobs[0]
        assert job.exists()
        payload = json.loads(job.read_text(encoding="utf-8"))
        assert f"job_id={payload['job_id']}" in context
        assert payload["status"] == "queued"
        assert payload["project_id"] == "test_project"

    def test_skips_if_already_running(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()
        monkeypatch.setattr(hm, 'LOCK_DIR', lock_dir)
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        fd = _hold_flock(lock_dir, "test_project.lock")  # another process holds the flock
        try:
            with patch.object(hm.subprocess, 'Popen') as mock_popen:
                hm.handle_main_branch_update("test_project")
            mock_popen.assert_not_called()
            context = read_post_tool_context(capsys.readouterr().out)
            assert "跳过重复启动" in context
        finally:
            os.close(fd)

    def test_replaces_stale_lock(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()
        (lock_dir / "test_project.lock").write_text("999999999")  # dead PID
        monkeypatch.setattr(hm, 'LOCK_DIR', lock_dir)
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        with patch.object(hm.subprocess, 'Popen'):
            hm.handle_main_branch_update("test_project")
        context = read_post_tool_context(capsys.readouterr().out)
        assert "Harness 更新已在后台启动" in context

    def test_acquire_release_lock(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        assert hm.acquire_lock("proj1")
        assert not hm.acquire_lock("proj1")  # we already hold the flock (different fd → denied)
        hm.release_lock("proj1")
        assert hm.acquire_lock("proj1")       # re-acquirable after release
        hm.release_lock("proj1")

    def test_release_of_unheld_lock_is_a_noop(self, tmp_path, monkeypatch):
        # releasing a lock we never acquired must not touch a file another process owns
        locks = tmp_path / "locks"
        locks.mkdir()
        monkeypatch.setattr(hm, 'LOCK_DIR', locks)
        lf = locks / "proj.lock"
        lf.write_text("99999", encoding="utf-8")
        hm.release_lock("proj")  # we don't hold it → no-op
        assert lf.exists()

    def test_release_frees_the_lock(self, tmp_path, monkeypatch):
        locks = tmp_path / "locks"
        locks.mkdir()
        monkeypatch.setattr(hm, 'LOCK_DIR', locks)
        assert hm.acquire_lock("proj")
        assert hm._lock_held("proj") is True   # we hold it
        hm.release_lock("proj")
        assert hm._lock_held("proj") is False   # freed

    def test_handle_skill_refresh_spawns_branch_agnostic_worker(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        with patch.object(hm, 'get_project_id', return_value="proj"), \
             patch.object(hm, '_lock_held', return_value=False), \
             patch.object(hm.subprocess, 'Popen') as popen:
            hm.handle_skill_refresh(str(tmp_path))
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "started"
        assert out["job_id"]
        assert "--bg-skill" in popen.call_args[0][0]  # any-branch worker, not --bg

    def test_handle_skill_refresh_skips_when_already_running(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        with patch.object(hm, 'get_project_id', return_value="proj"), \
             patch.object(hm, '_lock_held', return_value=True), \
             patch.object(hm.subprocess, 'Popen') as popen:
            hm.handle_skill_refresh(str(tmp_path))
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "already_running"
        popen.assert_not_called()

    def test_concurrent_acquire_grants_exactly_one(self, tmp_path, monkeypatch):
        # flock guarantees mutual exclusion across racing acquirers — including when a leftover
        # lock FILE from a dead process pre-exists (no stale-reclaim race to lose).
        import threading
        locks = tmp_path / "locks"
        locks.mkdir()
        monkeypatch.setattr(hm, 'LOCK_DIR', locks)
        offenders = []
        for _ in range(20):
            (locks / "proj.lock").write_text("999999999")  # leftover unheld file from a "prev run"
            n = 16
            barrier = threading.Barrier(n)
            winners = []
            guard = threading.Lock()

            def worker():
                barrier.wait()
                if hm.acquire_lock("proj"):
                    with guard:
                        winners.append(1)

            ts = [threading.Thread(target=worker) for _ in range(n)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            if sum(winners) != 1:
                offenders.append(sum(winners))
            hm.release_lock("proj")  # free for the next round
        assert not offenders, f"rounds where !=1 worker acquired the lock: {offenders}"


class TestCodemapRefreshTimeout:
    """The flat refresh cap covers one AI call plus its retry (sequential, no worker pool)."""

    def test_cap_covers_initial_plus_retry(self):
        assert hm.CODEMAP_REFRESH_TIMEOUT >= hm.CODEMAP_AI_TIMEOUT + max(hm.CODEMAP_AI_TIMEOUT, 240)


class TestJobPruning:
    def test_prunes_to_most_recent(self, tmp_path, monkeypatch):
        jobs = tmp_path / "jobs"
        jobs.mkdir()
        monkeypatch.setattr(hm, 'JOB_DIR', jobs)
        for i in range(60):
            f = jobs / f"proj-{i}.json"
            f.write_text("{}")
            os.utime(f, (1000 + i, 1000 + i))  # strictly increasing mtime
        hm._prune_old_jobs(keep=50)
        names = {p.name for p in jobs.glob("*.json")}
        assert len(names) == 50
        assert "proj-0.json" not in names    # oldest pruned
        assert "proj-59.json" in names       # newest kept

    def test_dispatch_prunes_jobs(self, tmp_path, monkeypatch):
        # a new dispatch must bound the jobs/ directory, not just append forever
        jobs = tmp_path / "jobs"
        jobs.mkdir()
        monkeypatch.setattr(hm, 'JOB_DIR', jobs)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        for i in range(60):
            (jobs / f"old-{i}.json").write_text("{}")
        with patch.object(hm.subprocess, 'Popen'):
            hm.handle_main_branch_update("test")
        assert len(list(jobs.glob("*.json"))) <= hm.JOB_RETENTION + 1


class TestDoMainBranchUpdate:
    """Cover do_main_branch_update (background worker)."""

    def test_skips_when_branch_changed_after_spawn(self, tmp_path, monkeypatch):
        # TOCTOU: if the user switched off main after the git op spawned the worker,
        # the worker must NOT write files. Re-check the branch and bail.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        (tmp_path / "CODE_MAP.md").write_text("# Old\n")
        with patch.object(hm, 'is_on_main_branch', return_value=False), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value=None) as gc:
            hm.do_main_branch_update("test_project", job_id="j1")
        assert (tmp_path / "CODE_MAP.md").read_text() == "# Old\n"
        gc.assert_not_called()
        status = json.loads((tmp_path / "jobs" / "j1.json").read_text())
        assert status["status"] == "skipped_branch_changed"

    def test_skips_writes_when_branch_changes_mid_run(self, tmp_path, monkeypatch):
        # main at worker start, but the user checks out a feature branch during the
        # reindex/structure prelude → CODE_MAP/docs must NOT be written on the feature branch.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        (tmp_path / "CODE_MAP.md").write_text("# Old\n")
        with patch.object(hm, 'is_on_main_branch', side_effect=[True, False]), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value={"communities": "x"}), \
             patch.object(hm, 'build_codemap_structure', return_value=("# New\n", [])), \
             patch.object(hm, 'sync_platform_docs') as sync:
            hm.do_main_branch_update("test_project", job_id="j_mid")
        assert (tmp_path / "CODE_MAP.md").read_text() == "# Old\n"  # not overwritten on feature branch
        sync.assert_not_called()

    def test_skill_path_refreshes_off_main_branch(self, tmp_path, monkeypatch):
        # require_main=False (the /harness-init skill path) refreshes on the CURRENT branch
        # instead of bailing like the hook path does off-main.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        (tmp_path / "CODE_MAP.md").write_text("# Old\n")
        with patch.object(hm, 'is_on_main_branch', return_value=False), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value={"x": 1}), \
             patch.object(hm, 'build_codemap_structure', return_value=("# New\n", [])), \
             patch.object(hm, 'sync_platform_docs'):
            hm.do_main_branch_update("test_project", job_id="j", require_main=False)
        assert (tmp_path / "CODE_MAP.md").read_text() == "# New\n"  # written despite feature branch

    def test_refreshes_descriptions_when_entries_need_refresh_despite_unchanged_structure(self, tmp_path, monkeypatch):
        # Self-healing: an entry with an empty/low-quality description must still trigger a
        # description run even when the GitNexus structure is byte-identical and no dir is stale.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        content = "# Code Map\n\n### src/ (100 symbols)\n"  # src/ has NO description
        (tmp_path / "CODE_MAP.md").write_text(content)
        desc_script = tmp_path / "generate_descriptions.py"
        desc_script.write_text("pass")
        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value={"src": {"symbols": 100, "clusters": 1}}), \
             patch.object(hm, 'build_codemap_structure', return_value=(content, [])), \
             patch.object(hm, 'DESC_SCRIPT', desc_script), \
             patch.object(hm.subprocess, 'run', return_value=MagicMock(returncode=0)) as mock_run:
            hm.do_main_branch_update("test_project")
        assert mock_run.called
        assert "--generate" in mock_run.call_args.args[0]

    def test_ensure_gitnexus_fresh_records_analyze_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        (tmp_path / ".gitnexus").mkdir()

        def fake_run(cmd, *a, **k):
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="stale", stderr="")
            raise subprocess.TimeoutExpired(cmd, 120)  # analyze hangs

        with patch.object(hm.subprocess, 'run', side_effect=fake_run):
            hm.ensure_gitnexus_fresh(job_id="j2")
        status = json.loads((tmp_path / "jobs" / "j2.json").read_text())
        assert status.get("gitnexus_analyze") in ("timeout", "failed")

    def test_no_communities(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value=None):
            hm.do_main_branch_update("test_project")
        assert not (tmp_path / "CODE_MAP.md").exists()

    def test_no_changes_is_a_noop(self, tmp_path, monkeypatch):
        # byte-identical structure, no stale dirs, all descriptions acceptable → early return.
        # The file must be left untouched and the mid-run write guard must never be reached.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        communities = {"scripts": {"symbols": 100, "clusters": 2}}
        content = "# Code Map\n\n### scripts/ (100 symbols) — 核心脚本：诊断与生成\n"
        (tmp_path / "CODE_MAP.md").write_text(content)

        with patch.object(hm, 'is_on_main_branch', return_value=True) as main_mock, \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value=communities), \
             patch.object(hm, 'build_codemap_structure', return_value=(content, [])), \
             patch.object(hm, 'sync_platform_docs') as sync:
            hm.do_main_branch_update("test_project")

        assert (tmp_path / "CODE_MAP.md").read_text() == content  # untouched
        # the no-op early-return fires before the mid-run write guard, so branch is checked
        # only once (at worker start), and the doc sync never runs
        assert main_mock.call_count == 1
        sync.assert_not_called()

    def test_full_update_with_descriptions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        old_content = "# Old Code Map\n"
        (tmp_path / "CODE_MAP.md").write_text(old_content)
        communities = {"auth": {"symbols": 100, "clusters": 2}}
        new_content = "# Code Map\n\n### src/ (100 symbols)\n"

        desc_script = tmp_path / "generate_descriptions.py"
        desc_script.write_text("pass")

        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value=communities), \
             patch.object(hm, 'build_codemap_structure', return_value=(new_content, ["src"])), \
             patch.object(hm, 'DESC_SCRIPT', desc_script), \
             patch.object(hm.subprocess, 'run', return_value=MagicMock(returncode=0)) as mock_run:
            hm.do_main_branch_update("test_project")

        assert (tmp_path / "CODE_MAP.md").read_text() == new_content
        assert not (tmp_path / "CODE_MAP.md.tmp").exists()
        desc_cmd = mock_run.call_args.args[0]
        assert "--use-fingerprints" in desc_cmd
        assert "--refresh-dir" in desc_cmd
        assert "src" in desc_cmd

    def test_desc_subprocess_timeout_accommodates_ai_timeout(self, tmp_path, monkeypatch):
        """The refresh subprocess cap must exceed the AI per-call budget it requests,
        otherwise generate_descriptions is killed before it can write AI descriptions."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        (tmp_path / "CODE_MAP.md").write_text("# Old\n")
        communities = {"auth": {"symbols": 100, "clusters": 2}}
        new_content = "# Code Map\n\n### src/ (100 symbols)\n"
        desc_script = tmp_path / "generate_descriptions.py"
        desc_script.write_text("pass")

        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value=communities), \
             patch.object(hm, 'build_codemap_structure', return_value=(new_content, ["src"])), \
             patch.object(hm, 'DESC_SCRIPT', desc_script), \
             patch.object(hm.subprocess, 'run', return_value=MagicMock(returncode=0)) as mock_run:
            hm.do_main_branch_update("test_project")

        desc_cmd = mock_run.call_args.args[0]
        assert "--ai-timeout" in desc_cmd
        ai_timeout = int(desc_cmd[desc_cmd.index("--ai-timeout") + 1])
        sub_timeout = mock_run.call_args.kwargs.get("timeout")
        assert sub_timeout is not None
        # Cap must cover one AI call PLUS its retry (max(ai_timeout, 240)), not just one call.
        assert sub_timeout >= ai_timeout + max(ai_timeout, 240)

    def test_update_no_desc_script(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        communities = {"auth": {"symbols": 100, "clusters": 2}}
        new_content = "# Code Map\n\n### src/ (100 symbols)\n"
        (tmp_path / "CODE_MAP.md").write_text("# Old Code Map")

        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value=communities), \
             patch.object(hm, 'build_codemap_structure', return_value=(new_content, [])):
            hm.do_main_branch_update("test_project")

        assert (tmp_path / "CODE_MAP.md").read_text() == new_content

    def test_lock_released_on_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.chdir(tmp_path)
        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh', side_effect=RuntimeError("boom")):
            try:
                hm.do_main_branch_update("test_project")
            except RuntimeError:
                pass
        # the finally released the flock → re-acquirable (flock leaves the file in place)
        assert hm._lock_held("test_project") is False
        assert hm.acquire_lock("test_project")
        hm.release_lock("test_project")

    def test_job_status_records_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.setattr(hm, 'JOB_DIR', tmp_path / "jobs")
        monkeypatch.chdir(tmp_path)
        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh', side_effect=RuntimeError("boom")):
            try:
                hm.do_main_branch_update("test_project", job_id="job-1")
            except RuntimeError:
                pass
        payload = json.loads((tmp_path / "jobs" / "job-1.json").read_text(encoding="utf-8"))
        assert payload["status"] == "failed"
        assert payload["error"] == "boom"


class TestCountSourceFiles:
    """count_source_files."""

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
    """Cover handle_growth_check (dispatcher — fast path only)."""

    def test_retired_state(self, tmp_path):
        state_file = tmp_path / "state.json"
        state = {"retired": True, "file_count": 100}
        hm.handle_growth_check(state, state_file)
        assert not state_file.exists()

    def test_below_threshold(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1")
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        hm.handle_growth_check(state, state_file)
        saved = json.loads(state_file.read_text())
        assert saved["file_count"] == 0

    def test_growth_accumulates_across_small_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        diag_script = tmp_path / "diag.sh"
        diag_script.write_text("pass")

        with patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'Popen') as mock_popen:
            for i in range(19):
                (tmp_path / f"mod{i}.py").write_text("x = 1")
            hm.handle_growth_check(state, state_file)
            assert json.loads(state_file.read_text())["file_count"] == 0
            mock_popen.assert_not_called()

            (tmp_path / "mod19.py").write_text("x = 1")
            hm.handle_growth_check(json.loads(state_file.read_text()), state_file)

        mock_popen.assert_called_once()
        assert json.loads(state_file.read_text())["file_count"] == 20

    def test_no_diag_script(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for i in range(25):
            (tmp_path / f"mod{i}.py").write_text("x = 1")
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        with patch.object(hm, 'DIAG_SCRIPT', Path("/nonexistent/harness-init.py")):
            hm.handle_growth_check(state, state_file)
        saved = json.loads(state_file.read_text())
        assert saved["file_count"] == 0

    def test_spawns_background_when_threshold_exceeded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_file = tmp_path / "state.json"
        state = {"file_count": 0, "retired": False}
        diag_script = tmp_path / "diag.sh"
        diag_script.write_text("pass")
        with patch.object(hm, 'count_source_files', return_value=25), \
             patch.object(hm, 'DIAG_SCRIPT', diag_script), \
             patch.object(hm.subprocess, 'Popen') as mock_popen:
            hm.handle_growth_check(state, state_file)
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert "--bg-growth" in args


class TestDoGrowthCheck:
    """Cover do_growth_check (background worker)."""

    def test_diag_script_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        state_file = tmp_path / "state.json"
        state = {"file_count": 25, "retired": False}
        state_file.write_text(json.dumps(state))
        mock_result = MagicMock(returncode=1, stdout="")
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.do_growth_check(str(state_file), str(tmp_path))

    def test_diag_script_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        state_file = tmp_path / "state.json"
        state = {"file_count": 25, "retired": False}
        state_file.write_text(json.dumps(state))
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired("python", 60)):
            hm.do_growth_check(str(state_file), str(tmp_path))

    def test_empty_diag_stdout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        state_file = tmp_path / "state.json"
        state = {"file_count": 25, "retired": False}
        state_file.write_text(json.dumps(state))
        mock_result = MagicMock(returncode=0, stdout="")
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.do_growth_check(str(state_file), str(tmp_path))

    def test_gitnexus_recommendation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        notify_dir = tmp_path / "notifications"
        monkeypatch.setattr(hm, 'NOTIFY_DIR', notify_dir)
        state_file = tmp_path / "state.json"
        state = {"file_count": 25, "retired": False,
                 "gitnexus_recommended": False, "lsp_recommended": []}
        state_file.write_text(json.dumps(state))
        diag = {
            "grep_noise": {"grep_noise_files": 30, "most_imported": "auth_module"},
            "existing": {"gitnexus": {"indexed": False}},
            "lsp_assessment": [],
        }
        mock_result = MagicMock(returncode=0, stdout=json.dumps(diag))
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.do_growth_check(str(state_file), str(tmp_path))
        saved = json.loads(state_file.read_text())
        assert saved["gitnexus_recommended"] is True
        notify_file = notify_dir / f"{hm.path_key(str(tmp_path))}.json"
        assert notify_file.exists()
        messages = json.loads(notify_file.read_text())
        assert any("GitNexus" in m for m in messages)

    def test_lsp_recommendation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        notify_dir = tmp_path / "notifications"
        monkeypatch.setattr(hm, 'NOTIFY_DIR', notify_dir)
        state_file = tmp_path / "state.json"
        state = {"file_count": 25, "retired": False,
                 "gitnexus_recommended": True, "lsp_recommended": []}
        state_file.write_text(json.dumps(state))
        diag = {
            "grep_noise": {"grep_noise_files": 5, "most_imported": ""},
            "existing": {"gitnexus": {"indexed": True}},
            "lsp_assessment": [
                {"language": "Python", "recommend": True, "reason": "类型覆盖 50%"}
            ],
        }
        mock_result = MagicMock(returncode=0, stdout=json.dumps(diag))
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.do_growth_check(str(state_file), str(tmp_path))
        notify_file = notify_dir / f"{hm.path_key(str(tmp_path))}.json"
        assert notify_file.exists()
        messages = json.loads(notify_file.read_text())
        assert any("Python" in m for m in messages)

    def test_retirement(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        notify_dir = tmp_path / "notifications"
        monkeypatch.setattr(hm, 'NOTIFY_DIR', notify_dir)
        state_file = tmp_path / "state.json"
        state = {"file_count": 25, "retired": False,
                 "gitnexus_recommended": True, "lsp_recommended": ["Python"]}
        state_file.write_text(json.dumps(state))
        diag = {
            "grep_noise": {"grep_noise_files": 5, "most_imported": ""},
            "existing": {"gitnexus": {"indexed": True}},
            "lsp_assessment": [
                {"language": "Python", "recommend": True, "reason": "already recommended"}
            ],
        }
        mock_result = MagicMock(returncode=0, stdout=json.dumps(diag))
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.do_growth_check(str(state_file), str(tmp_path))
        saved = json.loads(state_file.read_text())
        assert saved["retired"] is True
        notify_file = notify_dir / f"{hm.path_key(str(tmp_path))}.json"
        assert not notify_file.exists()

    def test_growth_lock_keyed_on_full_path(self, tmp_path, monkeypatch):
        # two repos with the same basename must not share a growth-check lock
        captured = []
        monkeypatch.setattr(hm, 'acquire_lock', lambda pid: captured.append(pid) or False)
        hm.do_growth_check(str(tmp_path / "s.json"), "/home/a/proj")
        hm.do_growth_check(str(tmp_path / "s.json"), "/home/b/proj")
        assert captured[0] != captured[1]
        assert all("proj" in c for c in captured)

    def test_grep_failure_sentinel_does_not_retire(self, tmp_path, monkeypatch):
        # A failed grep returns -1 — inconclusive, NOT "low noise". It must not permanently
        # retire growth detection (a transient failure would otherwise disable it forever).
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        monkeypatch.setattr(hm, 'NOTIFY_DIR', tmp_path / "notifications")
        state_file = tmp_path / "state.json"
        state = {"file_count": 25, "retired": False,
                 "gitnexus_recommended": False, "lsp_recommended": []}
        state_file.write_text(json.dumps(state))
        diag = {
            "grep_noise": {"grep_noise_files": -1, "most_imported": "auth"},
            "existing": {"gitnexus": {"indexed": False}},
            "lsp_assessment": [],
        }
        mock_result = MagicMock(returncode=0, stdout=json.dumps(diag))
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.do_growth_check(str(state_file), str(tmp_path))
        saved = json.loads(state_file.read_text())
        assert saved["retired"] is False

    def test_skips_if_locked(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()
        monkeypatch.setattr(hm, 'LOCK_DIR', lock_dir)
        fd = _hold_flock(lock_dir, f"{hm.path_key(str(tmp_path))}_growth.lock")  # another holder
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"file_count": 25, "retired": False}))
        try:
            with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
                 patch.object(hm.subprocess, 'run') as mock_run:
                hm.do_growth_check(str(state_file), str(tmp_path))
            mock_run.assert_not_called()
        finally:
            os.close(fd)


class TestMainFunction:
    """main() with different ctx inputs."""

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

    def test_skips_table_rows_blockquotes_and_html(self, tmp_path):
        # markdown table rows / blockquotes / HTML are not prose descriptions
        (tmp_path / "README.md").write_text(
            "# Title\n\n<!-- comment -->\n| col | val |\n|---|---|\n> a quote\n真正的项目描述\n")
        assert hm.get_readme_first_line(tmp_path) == "真正的项目描述"


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
        # must join with "、" (not " / ", which the quality gate flags → refresh loop)
        assert "、" in result
        assert " / " not in result
        assert harness_shared.is_acceptable_description(result)

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


class TestCoverageGaps:
    """Fill remaining coverage gaps."""

    def test_get_readme_first_line_oserror(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("# H\ncontent\n")
        with patch.object(Path, 'read_text', side_effect=OSError("disk error")):
            assert hm.get_readme_first_line(tmp_path) == ""

    def test_get_init_docstring_syntax_error(self, tmp_path):
        d = tmp_path / "mod"
        d.mkdir()
        (d / "__init__.py").write_text("def broken(:\n")
        assert hm.get_init_docstring(d) == ""

    def test_ensure_gitnexus_fresh_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        hm.ensure_gitnexus_fresh()  # no .gitnexus → returns immediately

    def test_ensure_gitnexus_fresh_up_to_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        mock_result = MagicMock(stdout="Status: up-to-date", stderr="")
        with patch.object(hm.subprocess, 'run', return_value=mock_result) as mock_run:
            hm.ensure_gitnexus_fresh()
        assert mock_run.call_count == 1  # only status, no analyze

    def test_ensure_gitnexus_fresh_stale(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        status_result = MagicMock(stdout="Status: stale", stderr="")
        analyze_result = MagicMock(returncode=0)
        with patch.object(hm.subprocess, 'run', side_effect=[status_result, analyze_result]) as mock_run:
            hm.ensure_gitnexus_fresh()
        assert mock_run.call_count == 2

    def test_ensure_gitnexus_fresh_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(hm.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired("npx", 5)):
            hm.ensure_gitnexus_fresh()  # should not raise

    def test_build_codemap_uncovered_subdir_with_readme(self, tmp_path, monkeypatch):
        """Cover L344-354: uncovered sub-dirs inside a GitNexus top-level dir."""
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        core = src / "core"
        core.mkdir()
        (core / "README.md").write_text("# Core\n\nCore module for everything.\n")
        communities = {"Src": {"symbols": 50, "clusters": 1}}
        with patch.object(hm, 'build_area_to_dir', return_value={"Src": "src"}):
            content, _ = hm.build_codemap_structure(communities, {}, {})
        assert "core" in content
        assert "Core module" in content

    def test_build_codemap_non_gitnexus_subdir(self, tmp_path, monkeypatch):
        """Cover L368-379: non-GitNexus dir with sub-directories."""
        monkeypatch.chdir(tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        api = docs / "api"
        api.mkdir()
        (api / "README.md").write_text("# API\n\nAPI documentation.\n")
        communities = {}
        with patch.object(hm, 'build_area_to_dir', return_value={}):
            content, _ = hm.build_codemap_structure(communities, {}, {})
        assert "### docs/" in content
        assert "api" in content

    def test_handle_main_branch_update_leftover_lock_file_does_not_wedge(self, tmp_path, monkeypatch, capsys):
        """A lock FILE left by a dead process (no flock held) must not wedge updates — flock
        auto-released on death, so the dispatcher proceeds."""
        monkeypatch.chdir(tmp_path)
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()
        (lock_dir / "test.lock").write_text("999999999")  # leftover from a crashed worker
        monkeypatch.setattr(hm, 'LOCK_DIR', lock_dir)
        with patch.object(hm.subprocess, 'Popen'):
            hm.handle_main_branch_update("test")
        context = read_post_tool_context(capsys.readouterr().out)
        assert "Harness 更新已在后台启动" in context

    def test_bg_cli_mode(self, tmp_path, monkeypatch):
        """Cover __main__ --bg entry point."""
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        with patch.object(hm, 'is_on_main_branch', return_value=True), \
             patch.object(hm, 'ensure_gitnexus_fresh'), \
             patch.object(hm, 'get_gitnexus_communities', return_value=None):
            hm.do_main_branch_update("test")

    def test_bg_growth_cli_mode(self, tmp_path, monkeypatch):
        """Cover __main__ --bg-growth entry point."""
        monkeypatch.setattr(hm, 'LOCK_DIR', tmp_path / "locks")
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"file_count": 25, "retired": False}))
        mock_result = MagicMock(returncode=1, stdout="")
        with patch.object(hm, 'DIAG_SCRIPT', tmp_path / "diag.sh"), \
             patch.object(hm.subprocess, 'run', return_value=mock_result):
            hm.do_growth_check(str(state_file), str(tmp_path))
