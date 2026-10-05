"""Continuation is scoped to the claimed run and its unchanged execution context."""
from dataclasses import replace

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_worker_resume import bind_worker_session, prepare_resume


@pytest.fixture
def retry(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kb.init_db()
    with connect_closing() as conn:
        tid = kb.create_task(conn, title="resume", assignee="default")
        task = kb.claim_task(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(workspace))
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(task.current_run_id))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", task.claim_lock)
    assert prepare_resume(task, str(workspace), str(home)) is None
    bind_worker_session("durable-session")
    with connect_closing() as conn, kb.write_txn(conn):
        kb._end_run(conn, tid, outcome="crashed")
        conn.execute("UPDATE tasks SET status = 'ready', claim_lock = NULL, claim_expires = NULL WHERE id = ?", (tid,))
    with connect_closing() as conn:
        task = kb.claim_task(conn, tid)
    return task, workspace, home


@pytest.mark.parametrize("change", ["profile", "model", "provider", "step", "branch", "workspace", "home"])
def test_resume_refuses_changed_execution_context(retry, change):
    task, workspace, home = retry
    assert prepare_resume(task, str(workspace), str(home)) == "durable-session"
    fields = {"profile": "assignee", "model": "model_override", "provider": "provider_override",
              "step": "current_step_key", "branch": "branch_name"}
    if change in fields:
        task = replace(task, **{fields[change]: "changed"})
    elif change == "workspace":
        workspace = workspace / "changed"
    else:
        home = home / "changed"
    with pytest.raises(ValueError, match="retry context changed"):
        prepare_resume(task, str(workspace), str(home))


def _bind_replacement(task, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(task.current_run_id))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", task.claim_lock)
    bind_worker_session("durable-session")


def test_reclaimed_claim_without_a_worker_keeps_last_durable_session(retry, monkeypatch):
    task, workspace, home = retry
    with connect_closing() as conn, kb.write_txn(conn):
        kb._end_run(conn, task.id, outcome="reclaimed")
        conn.execute("UPDATE tasks SET status = 'ready', claim_lock = NULL, claim_expires = NULL WHERE id = ?", (task.id,))
    with connect_closing() as conn:
        replacement = kb.claim_task(conn, task.id)
    assert prepare_resume(replacement, str(workspace), str(home)) == "durable-session"
    _bind_replacement(replacement, monkeypatch)
    with connect_closing() as conn:
        run = conn.execute("SELECT resumed_from_run_id FROM task_runs WHERE id = ?",
                           (replacement.current_run_id,)).fetchone()
        assert run["resumed_from_run_id"] < task.current_run_id


@pytest.mark.parametrize("store", [None, object()])
def test_resume_cannot_enter_inference_with_missing_transcript(store):
    from types import SimpleNamespace
    from hermes_cli.kanban_worker_resume import bind_cli_worker_session
    cli = SimpleNamespace(_session_db=store, _resumed=True, conversation_history=[], session_id="missing-history")
    with pytest.raises(RuntimeError, match="refusing a fresh conversation"):
        bind_cli_worker_session(cli)


@pytest.mark.platforms("linux")
def test_journal_signal_to_a_different_scope_process_is_not_worker_death(monkeypatch):
    import subprocess
    from hermes_cli.kanban_worker_resume import journal_death
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 0, "scope: Sending signal SIGTERM to process 99999 on client request."))
    assert journal_death("t_journal", 1, 100, 88888) is None
    assert journal_death("t_journal", 1, 100, 99999)["reason"] == "SIGTERM"


def test_exit_trailer_cannot_cross_an_invocation_boundary(tmp_path, monkeypatch):
    from hermes_cli.kanban_db_dispatch import _worker_log_exit_code
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    logs = kb.worker_logs_dir()
    logs.mkdir(parents=True)
    path = logs / "t_boundary.log"
    path.write_text("[kanban-worker-exit] rc=0\n=== HERMES_KANBAN_RUN task=t_boundary run=2 ===\n")
    assert _worker_log_exit_code("t_boundary") is None
    with path.open("a") as log:
        log.write("[kanban-worker-exit] rc=75\n")
    assert _worker_log_exit_code("t_boundary") == 75


def test_show_exposes_the_durable_resume_lineage(retry, monkeypatch):
    import json
    from tools.kanban_tools import _handle_show
    task, workspace, home = retry
    prepare_resume(task, str(workspace), str(home))
    _bind_replacement(task, monkeypatch)
    run = json.loads(_handle_show({"task_id": task.id}))["runs"][-1]
    assert run["worker_session_id"] == "durable-session"
    assert run["resumed_from_run_id"] < task.current_run_id


def test_stale_worker_cannot_bind_session_to_replacement_run(retry, monkeypatch):
    import os
    task, workspace, home = retry
    old_run, old_lock = os.environ["HERMES_KANBAN_RUN_ID"], os.environ["HERMES_KANBAN_CLAIM_LOCK"]
    _bind_replacement(task, monkeypatch)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", old_run)
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", old_lock)
    with pytest.raises(RuntimeError, match="lost worker claim"):
        bind_worker_session("stale-overwrite")
    with connect_closing() as conn:
        run = conn.execute("SELECT worker_session_id, resumed_from_run_id FROM task_runs WHERE id = ?",
                           (task.current_run_id,)).fetchone()
        assert run["worker_session_id"] == "durable-session"
        assert run["resumed_from_run_id"] != task.current_run_id
