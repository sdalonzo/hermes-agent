---
slice: resumable-kanban-workers
pr: 0
created: "2026-10-05"
status: blocked
files:
  - hermes_cli/cli_agent_setup_mixin.py
  - hermes_cli/kanban_db.py
  - hermes_cli/kanban_db_connect.py
  - hermes_cli/kanban_db_dispatch.py
  - hermes_cli/kanban_db_runs.py
  - hermes_cli/kanban_worker_resume.py
  - receipts/2026-10-05--resumable-kanban-publication-blocked.md
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
supersedes:
  - receipts/2026-10-05--resumable-kanban.md
probes:
  - name: real-worktree-hard-kill
    run: .venv/bin/python scripts/probe_kanban_worker_resume.py --scratch /home/hermes/.hermes/profiles/backend-engineer/cache/scratch
    transcript: receipts/evidence/resumable-kanban/worktree-kill.txt
  - name: real-worktree-sigterm
    run: .venv/bin/python scripts/probe_kanban_worker_resume.py --scratch /home/hermes/.hermes/profiles/backend-engineer/cache/scratch --signal SIGTERM
    transcript: receipts/evidence/resumable-kanban/worktree-term.txt
---

## Interpretation

Target: `NousResearch/hermes-agent`, card `t_a67d5034`.
The implementation and execution evidence are published to the authorized fork.
An upstream PR does not exist. This record corrects the prior receipt's publication
status without changing the accepted scope or its verification results.

## Decision

Keep the working code and probes pushed; do not alter credentials, bypass the
fork-scoped helper, or substitute an unaccepted local plugin for upstream delivery.
The fork helper resolves through its documented default-home bootstrap, with
`BSW_AGENT=kernel`. No credential value is printed, stored, or placed in argv.

## Verification

- Fork branch `sdalonzo/hermes-agent:kernel/resumable-kanban` read back at
  `dd40eced35c38129868cb1eb87e49359637f1374` before this receipt.
- Both the configured gh token and the fork-helper token reject GraphQL
  `createPullRequest` with `Resource not accessible by personal access token`.
- REST creation with the fork-helper token returns HTTP 403 for the same resource.
- The exact upstream head-filtered pull-request query returns an empty array.
- Working implementation: 827 focused tests pass; all 11 checks pass.
  The prior receipt records the full-suite failures, baseline replay, two fixed
  regressions, two pass-on-retry flakes, and the remaining limitations.
- Full suite and base comparison are task attachments 12 and 13 on this card.
  They also exist at the immutable historical fork commit named in the prior receipt.

## Confidence

The source behavior is demonstrated. Upstream publication and acceptance are not.
No PR number is invented; `pr: 0` remains an explicitly unbound publication draft.

## Reviewer Guidance

Use the pushed branch, scope probes, and prior reasoning receipt.
Chief/Steve must create the upstream PR or approve an appropriate API permission.
Then bind the allocated number and the receipt link before review.
The other handoff gate remains: effective `kanban.review_dispatch` is true for
both default and backend-engineer, contrary to the required human-controlled lane.
No live profile edit, service restart, merge, or deployment is authorized here.
