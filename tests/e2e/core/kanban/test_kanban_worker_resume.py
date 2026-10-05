"""Crash continuation through real dispatcher/CLI workers and a recording provider.

Regression for #77881. Only this fixture's disposable worker scope is killed.
"""
from __future__ import annotations

import json
import subprocess
import threading

import pytest

from tools.process_registry import systemd_user_bus_env
from tests.e2e.core.kanban._helpers import Board, pid_alive, wait_until
from tests.fakes.fake_llm_provider import FakeLLMServer, Hang, ToolCall, Text

pytestmark = [pytest.mark.platforms("linux"), pytest.mark.live_system_guard_bypass]
MARK = "DURABLE_RESUME_PREFIX_77881"


class ScopedBoard(Board):
    """Dispatch from a real disposable systemd unit, as production does."""

    def cli(self, *args, timeout=90.0, check=True):
        if not args or args[0] != "dispatch":
            return super().cli(*args, timeout=timeout, check=check)
        from tests.e2e.core.kanban._helpers import PY, REPO
        child_env = [f"{key}={value}" for key, value in self.env().items()
                     if key.startswith("HERMES_") or key in {"HOME", "PYTHONPATH", "PATH", "NO_COLOR", "TERM"}]
        proc = subprocess.run(
            ["systemd-run", "--user", "--wait", "--pipe", "--collect", "/usr/bin/env",
             *child_env, PY, "-m", "hermes_cli.main", "kanban", *args],
            cwd=REPO, capture_output=True, text=True, timeout=timeout, env=systemd_user_bus_env())
        if check:
            assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc


@pytest.mark.parametrize("kill_signal", ["SIGKILL", "SIGTERM"])
def test_sigkilled_scope_resumes_exact_session_and_workspace(tmp_path, kill_signal):
    from tools.process_registry import _systemd_run_user_scope_available
    if not _systemd_run_user_scope_available():
        pytest.skip("requires a reachable systemd user scope bus")
    hung = threading.Event()
    resumed = threading.Event()
    requests = []
    retry_heads = []
    retry = threading.Event()

    def reply(rec):
        messages = rec["body"]["messages"]
        requests.append(messages)
        if retry.is_set():
            if not retry_heads:
                retry_heads.append(subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True, encoding="utf-8").strip())
                resumed.set()
            if messages[-1]["role"] == "tool":
                return Text("resumed task settled")
            return ToolCall("kanban_complete", {"summary": "resumed with durable prefix"})
        if messages[-1]["role"] == "tool":
            hung.set()
            return Hang(300)
        return ToolCall("kanban_heartbeat", {"note": MARK}, text=MARK)

    with FakeLLMServer(reply) as server:
        board = ScopedBoard(tmp_path, server.base_url)
        repo, tree = tmp_path / "repo", tmp_path / "worktree"
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        (repo / "README.md").write_text("disposable continuation fixture\n")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-m", "fixture"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "resume-proof", str(tree)],
                       check=True, capture_output=True)
        try:
            tid = board.create("resume disposable scope", "--workspace", f"worktree:{tree}", "--branch", "resume-proof")
            board.dispatch("--failure-limit", "5")
            wait_until(hung.is_set, 90, "worker to persist heartbeat and hang")
            first = board.runs(tid)[-1]
            workspace = board.task(tid)["workspace_path"]
            assert workspace == str(tree)
            original_head = subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True, encoding="utf-8").strip()
            scope = f"hermes-worker-kanban-{tid}-run-{first['id']}.scope"
            live = subprocess.run(["systemctl", "--user", "show", scope, "-p", "ActiveState"],
                                  capture_output=True, text=True, check=True, env=systemd_user_bus_env())
            assert "ActiveState=active" in live.stdout, live.stdout
            subprocess.run(["systemctl", "--user", "kill", f"--signal={kill_signal}", scope], check=True, env=systemd_user_bus_env())
            wait_until(lambda: not pid_alive(first["worker_pid"]), 15, "killed scope to exit")
            retry.set()
            board.dispatch("--failure-limit", "5")
            wait_until(resumed.is_set, 90, "retry to reach provider")
            second = board.runs(tid)[-1]
            wait_until(lambda: board.task(tid)["status"] == "done", 90, "retry to complete")
            second = board.runs(tid)[-1]
            assert first.get("worker_session_id"), first
            assert second["worker_session_id"] == first["worker_session_id"], second
            assert second["resumed_from_run_id"] == first["id"], second
            assert board.task(tid)["workspace_path"] == workspace
            assert retry_heads[0] == original_head
            assert MARK in json.dumps(requests[2]), requests[2]
            old_history = [(m["role"], m.get("content"), m.get("tool_calls"))
                           for m in requests[1] if m["role"] != "system"]
            new_history = [(m["role"], m.get("content"), m.get("tool_calls"))
                           for m in requests[2] if m["role"] != "system"]
            assert new_history[:len(old_history)] == old_history
            killed = board.runs(tid)[0]
            assert "killed" in killed["error"].lower(), killed
            assert not json.loads(killed.get("metadata") or "{}").get("protocol_violation"), killed
            print(json.dumps({"session_id": second["worker_session_id"],
                              "resumed_from_run_id": second["resumed_from_run_id"],
                              "workspace": workspace, "death": killed["error"],
                              "prefix_present": True, "prefix_messages": len(old_history), "worktree_head": original_head}, sort_keys=True))
        finally:
            board.kill_workers()
