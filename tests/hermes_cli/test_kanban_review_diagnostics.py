"""Review age is phase-local; recorded evidence must not widen recovery authority."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban, kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_diagnostics as kd


def test_ready_age_restarts_when_review_is_reopened(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text("kanban:\n  review_dispatch: false\n")
    kb.init_db()
    clock = [1_000_000]
    monkeypatch.setattr(kb.time, "time", lambda: clock[0])
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="Reopen review", assignee="worker")
        assert kb.request_review(conn, tid, summary="Ready for review")
        clock[0] += 36 * 3600
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        row_events = list(conn.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (tid,)))
        row_runs = list(conn.execute("SELECT * FROM task_runs WHERE task_id = ? ORDER BY id", (tid,)))
        review, = kd.compute_task_diagnostics(row, row_events, row_runs, now=clock[0],
                                             config=kd.config_from_runtime_config(
                                                 {"kanban": {"review_dispatch": False}}))
        assert review.data["age_seconds"] == 36 * 3600
        assert review.data["cause"] == "unknown"
        assert review.data["policy"] == "human-controlled"
        assert review.severity == "warning"
        # Real parser, command and config loader, with only clock/home isolated.
        cli_rows = json.loads(kanban.run_slash("diagnostics --json"))
        cli_review = next(r for r in cli_rows if r["task_id"] == tid)["diagnostics"]
        assert cli_review[0]["kind"] == "stale_in_review"
        assert cli_review[0]["data"]["policy"] == "human-controlled"
        assert cli_review[0]["severity"] == "warning"
        assert kb.reopen_review_task(conn, tid)
        cli_reopened = json.loads(kanban.run_slash("diagnostics --json"))
        assert not any(r["task_id"] == tid for r in cli_reopened)
        task = kb.get_task(conn, tid)
        events = kb.list_events(conn, tid)
        runs = kb.list_runs(conn, tid)
        assert not kd.compute_task_diagnostics(task, events, runs, now=clock[0] + 10)
        diag = kd.compute_task_diagnostics(
            task, events, runs, now=clock[0] + 10,
            config={"stranded_threshold_seconds": 1},
        )[0]
        assert diag.kind == "stranded_in_ready"
        assert diag.data["ready_since"] == clock[0]
        assert 0 <= diag.data["age_seconds"] < 60


@pytest.mark.parametrize("age,severity", [(24 * 3600 - 1, None), (24 * 3600, "warning"),
                                         (72 * 3600 - 1, "warning"), (72 * 3600, "error")])
@pytest.mark.parametrize("evidence,cause,gate", [
    ({}, "awaiting_verdict", "review verdict"),
    ({"verdict": "CHANGES_REQUESTED", "verdict_head": "abc"}, "red_return_overdue", "authorized rework"),
    ({"verdict": "APPROVED", "verdict_head": "abc"}, "merge_pending", "merge approval"),
    ({"merged": True, "pr_head": "abc"}, "delivery_pending", "delivery verification"),
    ({"verdict": "APPROVED", "verdict_head": "old"}, "unknown", "inspect review evidence"),
    ({"merged": True, "pr_head": "old"}, "unknown", "inspect review evidence"),
])
def test_stale_review_reports_only_current_evidence(age, severity, evidence, cause, gate):
    since = 1_000_000
    task = {"id": "t_review", "status": "review", "assignee": "worker", "created_at": 1}
    events = [{"kind": "review_requested", "created_at": since, "run_id": 2,
               "payload": json.dumps({"implementer": "worker", "reviewer": "reviewer"})}]
    metadata = {"pr": "https://github.com/acme/repo/pull/1", "reviewed_head": "abc", **evidence}
    runs = [{"id": 1, "metadata": {"merged": True, "pr_head": "abc"}},
            {"id": 2, "metadata": json.dumps(metadata)}]
    diags = kd.compute_task_diagnostics(task, events, runs, now=since + age)
    if severity is None:
        assert not diags
        return
    diag, = diags
    assert diag.kind == "stale_in_review"
    assert diag.severity == severity
    assert diag.data["cause"] == cause
    assert diag.data["next_gate"] == gate
    assert diag.data["review_since"] == since
    assert diag.data["age_seconds"] == age
    assert diag.data["pr"] == metadata["pr"]
    assert diag.data["head"] == "abc"
    assert diag.data["owner"] == ("worker" if cause in {"red_return_overdue", "delivery_pending"} else "reviewer")
    assert diag.data["policy"] == "unknown"
    assert all(action.kind == "comment" for action in diag.actions)
    assert "dispatcher fault" not in diag.detail.lower()
    # No review signal outside this phase, or with an unprovable review start.
    assert not kd.compute_task_diagnostics({**task, "status": "done"}, events, runs, now=since + age)
    assert not kd.compute_task_diagnostics(task, [], runs, now=since + age)
    # Unknown external PR state stays unknown, not a made-up verdict.
    missing = kd.compute_task_diagnostics(task, events, [], now=since + age)[0]
    assert missing.data["cause"] == "unknown"
    assert missing.data["pr"] is None and missing.data["head"] is None
    # Expected human holds stay advisory, including after a week.
    for hold, config in [(True, {}), (False, {"kanban": {"review_dispatch": False}})]:
        held_runs = [{"id": 2, "metadata": {**metadata, "human_hold": hold,
                                               "owner": "operator", "next_gate": "human decision"}}]
        held, = kd.compute_task_diagnostics(task, events, held_runs, now=since + 8 * 24 * 3600,
                                            config=config)
        assert held.severity == "warning"
        assert held.data["expected_human_hold"] is True
        assert held.data["owner"] == "operator"
        assert held.data["next_gate"] == "human decision"
        assert "human" in held.detail.lower()
    # A native red return after this handoff is evidence even without metadata.
    red_events = [*events, {"kind": "changes_requested", "created_at": since + 1,
                           "payload": {"implementer": "worker"}}]
    red = kd.compute_task_diagnostics(task, red_events, [], now=since + age)[0]
    assert red.data["cause"] == "red_return_overdue"
    # A later re-review clears native returns, even when events share a second.
    new_events = [*red_events, {**events[0], "created_at": since + 1}]
    renewed = kd.compute_task_diagnostics(task, new_events, runs, now=since + 1 + age)[0]
    assert renewed.data["cause"] == cause
    assert renewed.data["age_seconds"] == age
    # Resume starts a fresh review age; comments do not restart it.
    resumed = [*events, {"kind": "unblocked", "created_at": since + age - 10,
                        "payload": {"status": "review"}}]
    assert not kd.compute_task_diagnostics(task, resumed, runs, now=since + age)
    commented = [*events, {"kind": "commented", "created_at": since + age - 10,
                          "payload": ["not evidence"]}]
    assert kd.compute_task_diagnostics(task, commented, runs, now=since + age)[0].data["age_seconds"] == age
    # Invalid metadata does not hide the age signal or resurrect an older run.
    for invalid in ["[]", "not JSON", {"pr": 42, "head": []}]:
        unknown, = kd.compute_task_diagnostics(task, events, [{"id": 2, "metadata": invalid}],
                                               now=since + age)
        assert unknown.data["cause"] == "unknown"
