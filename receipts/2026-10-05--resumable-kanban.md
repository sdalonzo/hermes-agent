---
slice: resumable-kanban-workers
pr: 0
created: "2026-10-05"
files:
  - hermes_cli/cli_agent_setup_mixin.py
  - hermes_cli/kanban_db.py
  - hermes_cli/kanban_db_connect.py
  - hermes_cli/kanban_db_dispatch.py
  - hermes_cli/kanban_db_runs.py
  - hermes_cli/kanban_worker_resume.py
  - receipts/2026-10-05--resumable-kanban.md
  - receipts/evidence/resumable-kanban/failure-case-comparison.txt
  - receipts/evidence/resumable-kanban/final-check.txt
  - receipts/evidence/resumable-kanban/final-focused.txt
  - receipts/evidence/resumable-kanban/mutant-claim-hole.txt
  - receipts/evidence/resumable-kanban/mutant-claim.txt
  - receipts/evidence/resumable-kanban/mutant-context.txt
  - receipts/evidence/resumable-kanban/mutant-journal-pid.txt
  - receipts/evidence/resumable-kanban/mutant-journal.txt
  - receipts/evidence/resumable-kanban/mutant-resume.txt
  - receipts/evidence/resumable-kanban/mutant-trailer.txt
  - receipts/evidence/resumable-kanban/mutant-transcript.txt
  - receipts/evidence/resumable-kanban/rebased-check-final.txt
  - receipts/evidence/resumable-kanban/rebased-check.txt
  - receipts/evidence/resumable-kanban/rebased-focused.txt
  - receipts/evidence/resumable-kanban/scope-kill-final.txt
  - receipts/evidence/resumable-kanban/scope-kill.txt
  - receipts/evidence/resumable-kanban/scope-term.txt
  - receipts/evidence/resumable-kanban/upstream-base-failure.txt
  - receipts/evidence/resumable-kanban/worktree-kill.txt
  - receipts/evidence/resumable-kanban/worktree-term.txt
  - scripts/probe_kanban_worker_resume.py
  - tests/e2e/core/kanban/test_kanban_worker_resume.py
  - tests/e2e/core/kanban/test_kanban_worker_sigkill.py
  - tests/hermes_cli/test_kanban_worker_resume.py
  - tools/kanban_tools.py
  - website/docs/user-guide/features/kanban.md
depends_on: []
supersedes: []
deployment_target: NousResearch/hermes-agent main (upstream source integration)
deployed_revision: pending upstream merge
probes:
  - name: scope-kill-retains-session-worktree-prefix
    run: .venv/bin/python scripts/probe_kanban_worker_resume.py --scratch /home/hermes/.hermes/profiles/backend-engineer/cache/scratch
    transcript: receipts/evidence/resumable-kanban/worktree-kill.txt
  - name: scope-term-retains-session-worktree-prefix
    run: .venv/bin/python scripts/probe_kanban_worker_resume.py --scratch /home/hermes/.hermes/profiles/backend-engineer/cache/scratch --signal SIGTERM
    transcript: receipts/evidence/resumable-kanban/worktree-term.txt
  - name: focused-regression-gate
    run: scripts/run_tests.sh tests/hermes_cli/test_kanban_worker_resume.py tests/e2e/core/kanban/test_kanban_worker_resume.py tests/e2e/core/kanban/test_kanban_worker_sigkill.py tests/hermes_cli/test_kanban_db.py tests/hermes_cli/test_kanban_worker_spawn_toolsets.py tests/hermes_cli/test_kanban_credential_startup_exit.py tests/hermes_cli/test_single_query_exit_contract.py tests/tools/test_kanban_tools.py tests/cron/test_cron_kanban_env_isolation.py tests/hermes_cli/test_subprocess_text_encoding.py -j 2 --tb=short
    transcript: receipts/evidence/resumable-kanban/final-focused.txt
  - name: repository-checks
    run: .venv/bin/python scripts/check
    transcript: receipts/evidence/resumable-kanban/final-check.txt
---

## Interpretation

Target repository: `NousResearch/hermes-agent`.
Authority: user-assigned card `t_a67d5034`, with Chief's approved Sol lane.
The card requires exact-session crash continuation, durable run lineage, and
journal-backed death classification. Its closeout target is upstream integration,
not an unauthorized patch of the installed runtime.

## Decision

Persist worker session ID, execution coordinates, and predecessor run inside the
claimed worker before inference. Selection in the dispatcher is read-only. The
worker writes under its exact task, run, and claim lock; the parent may carry a
legitimate descendant fence that cannot authorize that write.

Resume only across retry outcomes in the same profile, home, workspace, branch,
step, model/provider pins, skills, and goal mode. Skip empty reclaimed claims
without forgetting the last durable worker. Do not use the creator's
`tasks.session_id`. Refuse missing stores and empty resumed history.

Read the exact run scope's user journal, bounded to its start and a three-second
query timeout. Record observed OOM/SIGKILL/SIGTERM separately from clean exit.
A process-specific signal must name the recorded worker PID. Unknown stays unknown.
Invocation headers prevent a prior attempt's exit trailer from classifying a retry.

This uses existing CLI resume and adds no model tool or behavior setting.
PR #60472 adds checkpoint prompts and graceful SIGTERM handling; it does not wire
this exact-session SIGKILL path. PRs #75951 and #95799 cover block/unblock.
Issue #77881 has broader requirements; this change addresses its continuation part.

Move the public Run model and private exit classifier into small siblings to keep
the existing file-size ratchets. Preserve the `kanban_db.Run` public import.
No merge, installed-source patch, profile edit, or service restart occurred.

## Verification

- Final focused gate: 827 passed, zero failed, one Windows-only skip.
  The existing SIGKILL suite retains two tracked expected failures; no assertion
  was removed. Its director now identifies processes explicitly instead of
  assuming every attempt has an empty conversation.
- Repository check: all 11 checks pass. `git diff --check` passes.
- SIGKILL: session `20261005_173234_9f4800`, predecessor run 1, unchanged real Git
  worktree HEAD, and all three prior messages before the new user turn.
- SIGTERM: session `20261005_173242_631f88`, with the same properties.
- Both probes run real dispatcher services and worker scopes as the service user.
  A local recording provider supplies the controlled replies; no paid inference
  is used by the probe.
- Full suite at implementation `6b3b024bc6803dd20c33e22509d6dabe32b8f94d`:
  5,313 files, 56,885 passed, 124 failed, 756 skipped, 3018.9 seconds.
  Replay of all 35 failed/error files on upstream base
  `c53536929e485a2064d915d83593e3b50c86e4fc` reproduces 122 failed cases.
  The full gate found two candidate regressions: the inherited-fence write and
  missing explicit UTF-8 decoding. Both are fixed and pass the final focused gate.
  Two other candidate-only failure records are pass-on-retry flakes.
- Full and base logs are retained at commit `19af8df84557e28800974c1aada4551f8da435db`
  under `receipts/evidence/resumable-kanban/{full-suite,base-comparison}.txt`.
  They are excluded from the final review diff. The compact case comparison
  remains in the diff. The full suite is not claimed green or rerun after the fixes.

### Deliberate break attempts

Each disabled check fails its named probe; transcripts are in the evidence folder.

- Disable resume argv: the real worker starts another session.
- Remove context equality: changed execution coordinates are accepted.
- Remove claim rejection: a stale worker no longer raises.
- Ignore journal evidence: the killed scope becomes an unexplained dead PID.
- Ignore invocation boundary: a dead retry inherits rc=0.
- Ignore missing history: an empty continuation no longer raises.
- Stop at an empty reclaimed claim: the durable predecessor is lost.
- Accept another process's signal: a sibling PID is mistaken for worker death.

## Confidence

High for the demonstrated SIGKILL/SIGTERM continuation, real worktree retention,
exact message prefix, claim ownership, and scoped signal classification.
The full suite remains red on upstream failures and test-environment limitations.
No claim is made about untested operating systems or compression-chain edge cases.

## Accepted risks

- Exactly-once external tool effects are not promised by session continuation.
- No live gateway restart or forced OOM was performed. OOM recognition uses the
  documented journal signature; the hard-kill proof uses a real SIGKILL.
- Effective profile configuration changes are not hashed; the guard checks task
  model/provider pins and profile/home identity, not every mutable profile setting.
- Legacy runs without the new durable coordinates start fresh.
- Older worker-output diagnostics can remain stale (#119618 / PR #129663).
  This change bounds exit trailers, not the separate diagnostic-text pipeline.

## Reviewer Guidance

Replay the two scope probes with a prepared dev/test environment.
Check that lineage writes occur only inside the granted worker and remain guarded
by the exact active run and claim. Check additive migration and `kanban_show`
exposure of session ID and predecessor. Check that retry selection does not cross
terminal outcomes, changed owners, or changed execution coordinates.

Hotspot: `hermes_cli/kanban_db_dispatch.py` also appears in upstream PR #129663.
Consolidate its diagnostic header work without losing the trailer boundary here.
Lyra's card tier is execution-only. Upstream acceptance and current-head CI remain
pending; this receipt does not assert readiness to merge.

## Remaining delivery gates

The PR must be accepted and merged upstream. Chief verifies the accepted revision
and its kill transcript before closing the source-integration card. Steve owns
merges and any later fleet installation; update approval and rollback to the prior
approved release are separate from this source-only work.

Same-card review is not safe yet: effective `kanban.review_dispatch` is `true` for
both default and backend-engineer. The Kernel agreement requires human-controlled
review first. Chief/Steve must approve that configuration before review handoff.
Do not restart a unit or alter a live profile to work around this gate.
