"""Durable, profile-local continuation coordinates for dispatcher workers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


def classify_dead_worker_exit(
    pid: int,
    claimer: str | None,
    *,
    task_id: str | None = None,
    board: str | None = None,
):
    """Exit status -> reclaim bookkeeping, before the worker's own words are folded in.

    The reap registry only knows children of THIS process; a per-tick dispatcher
    reads the exit trailer the worker left in its log instead, so the same death
    gets the same booking (protocol violation / rate-limit requeue / crash) as
    under the gateway-embedded dispatcher. A worker that never reached its exit
    epilogue (killed, OOM) leaves no trailer and stays a plain crash.
    """
    from hermes_cli.kanban_db_dispatch import (
        _classify_worker_exit, _worker_log_exit_code, _exit_code_kind,
        _DeadWorker, _PROTOCOL_VIOLATION_ERROR,
    )
    kind, code = _classify_worker_exit(pid)
    if task_id:
        death = observed_scope_death(task_id, board=board)
        if death:
            return _DeadWorker(
                "killed", None, f"pid {pid} killed ({death['reason']}); scope {death['scope']}",
                "crashed", {"pid": pid, "claimer": claimer, "exit_kind": "killed", **death})
    if kind == "unknown" and task_id:
        logged = _worker_log_exit_code(task_id, board=board)
        if logged is not None:
            kind, code = _exit_code_kind(logged)
    if kind == "clean_exit":
        # rc=0 while still ``running``: usually the work succeeded and only the
        # paperwork was skipped; the corrective sentence reaches the retry
        # worker via ``build_worker_context``.
        return _DeadWorker(
            kind, code, _PROTOCOL_VIOLATION_ERROR, "protocol_violation",
            # ``protocol_violation`` is the durable marker for
            # _protocol_violation_streak: _end_run copies this payload into the
            # run metadata.
            {"pid": pid, "claimer": claimer, "exit_code": code, "protocol_violation": True},
            protocol_violation=True,
        )
    if kind == "rate_limited":
        # Quota wall — NOT a task failure. Release to the source phase and do
        # NOT count a failure so a long quota window can't trip the breaker.
        return _DeadWorker(
            kind, code,
            f"pid {pid} exited rate-limited (quota wall) — requeued without counting a failure",
            "rate_limited",
            {"pid": pid, "claimer": claimer, "exit_code": code},
            rate_limited=True,
        )
    if kind == "terminal_provider":
        # The worker classified its own provider failure as unhealable (credential
        # revoked, model gone): every further spawn would hit the same wall, so
        # ``_account_crashes`` trips the breaker now instead of after ``failure_limit``.
        return _DeadWorker(
            kind, code,
            f"pid {pid} exited on a terminal provider error (exit {code}): the provider rejected "
            "this profile's credential or model — fix the configuration, then unblock.",
            "crashed",
            {"pid": pid, "claimer": claimer, "exit_kind": kind, "exit_code": code, "terminal_provider": True},
            terminal_provider=True,
        )
    if kind == "nonzero_exit":
        error_text = f"pid {pid} exited with code {code}"
    elif kind == "signaled":
        error_text = f"pid {pid} killed by signal {code}"
    else:
        error_text = f"pid {pid} not alive"
    event_payload = {"pid": pid, "claimer": claimer}
    if code is not None and kind != "unknown":
        event_payload["exit_kind"] = kind
        event_payload["exit_code"] = code
    return _DeadWorker(kind, code, error_text, "crashed", event_payload)



_RETRY_OUTCOMES = frozenset({"crashed", "timed_out", "reclaimed", "rate_limited", "spawn_failed"})


def _context(task, workspace, home):
    return {"workspace": str(Path(workspace).resolve()), "home": str(Path(home).resolve()),
            "profile": task.assignee, "branch": task.branch_name, "step": task.current_step_key,
            "model": task.model_override, "provider": task.provider_override,
            "skills": task.skills or [], "goal_mode": task.goal_mode}


def prepare_resume(task, workspace, home, *, board=None):
    """Bind spawn coordinates and return a safe retry's session, never the creator's session."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    if task.current_run_id is None:
        return None
    context = _context(task, workspace, home)
    with connect_closing(board=board) as conn, kb.write_txn(conn):
        current = conn.execute("SELECT * FROM task_runs WHERE id = ? AND task_id = ? AND ended_at IS NULL",
                               (task.current_run_id, task.id)).fetchone()
        if current is None:
            return None
        previous = conn.execute("SELECT * FROM task_runs WHERE task_id = ? AND id < ? "
                                "ORDER BY id DESC LIMIT 1", (task.id, task.current_run_id)).fetchone()
        session_id = resumed_from = None
        if previous and previous["outcome"] in _RETRY_OUTCOMES and previous["worker_session_id"]:
            if json.loads(previous["worker_context"] or "null") != context:
                raise ValueError(f"task {task.id}: retry context changed; refusing worker session resume")
            session_id, resumed_from = previous["worker_session_id"], previous["id"]
        conn.execute("UPDATE task_runs SET worker_context = ?, worker_session_id = ?, "
                     "resumed_from_run_id = ? WHERE id = ?",
                     (json.dumps(context, sort_keys=True), session_id, resumed_from, task.current_run_id))
        return session_id


def bind_cli_worker_session(cli):
    """No model entry with a missing store or an empty resumed transcript."""
    if cli._session_db is None or (cli._resumed and not cli.conversation_history):
        raise RuntimeError("cannot restore kanban worker session; refusing a fresh conversation")
    bind_worker_session(cli.session_id)


def bind_worker_session(session_id):
    """Persist before the first provider call, guarded by the exact current claim."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    task_id = os.environ.get("HERMES_KANBAN_TASK")
    if not task_id:
        return
    run_id = int(os.environ["HERMES_KANBAN_RUN_ID"])
    lock = os.environ["HERMES_KANBAN_CLAIM_LOCK"]
    with connect_closing() as conn, kb.write_txn(conn):
        cur = conn.execute("UPDATE task_runs SET worker_session_id = ? WHERE id = ? AND task_id = ? "
                           "AND claim_lock = ? AND ended_at IS NULL AND EXISTS "
                           "(SELECT 1 FROM tasks WHERE id = ? AND current_run_id = ? "
                           "AND claim_lock = ? AND status = 'running')",
                           (session_id, run_id, task_id, lock, task_id, run_id, lock))
        if cur.rowcount != 1:
            raise RuntimeError(f"task {task_id}: lost worker claim before session binding")


def observed_scope_death(task_id, *, board=None):
    """Find the exact active run before its crash transaction closes it."""
    from hermes_cli.kanban_db_connect import connect_closing

    with connect_closing(board=board) as conn:
        run = conn.execute("SELECT r.id, r.started_at FROM task_runs r JOIN tasks t "
                           "ON t.current_run_id = r.id WHERE t.id = ?", (task_id,)).fetchone()
    return journal_death(task_id, run["id"], run["started_at"]) if run else None


def journal_death(task_id, run_id, started_at):
    """Read only this run's scope. Missing journal evidence remains unknown."""
    from tools.process_registry import _IS_LINUX, systemd_user_bus_env

    if not _IS_LINUX or not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        return None
    scope = f"hermes-worker-kanban-{task_id}-run-{int(run_id)}.scope"
    try:
        result = subprocess.run(
            ["journalctl", "--user", "--unit", scope, "--since", f"@{int(started_at)}",
             "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=3, env=systemd_user_bus_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "oom-kill" in line or "OOM killer" in line:
            return {"scope": scope, "reason": "oom-kill", "journal_evidence": line}
        match = re.search(r"(?:Killed unit cgroup with|Sending signal) (SIGKILL|SIGTERM)\b", line)
        if match:
            return {"scope": scope, "reason": match[1], "journal_evidence": line}
    return None
