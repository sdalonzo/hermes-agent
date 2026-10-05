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


@pytest.mark.parametrize("store", [None, object()])
def test_resume_cannot_enter_inference_with_missing_transcript(store):
    from types import SimpleNamespace
    from hermes_cli.kanban_worker_resume import bind_cli_worker_session
    cli = SimpleNamespace(_session_db=store, _resumed=True, conversation_history=[], session_id="missing-history")
    with pytest.raises(RuntimeError, match="refusing a fresh conversation"):
        bind_cli_worker_session(cli)


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


def test_stale_worker_cannot_bind_session_to_replacement_run(retry):
    task, workspace, home = retry
    prepare_resume(task, str(workspace), str(home))
    with pytest.raises(RuntimeError, match="lost worker claim"):
        bind_worker_session("stale-overwrite")
    with connect_closing() as conn:
        run = conn.execute("SELECT worker_session_id, resumed_from_run_id FROM task_runs WHERE id = ?",
                           (task.current_run_id,)).fetchone()
        assert run["worker_session_id"] == "durable-session"
        assert run["resumed_from_run_id"] != task.current_run_id
