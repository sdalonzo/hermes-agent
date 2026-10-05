#!/usr/bin/env python3
"""Run the disposable systemd scope kill/continuation probe without paid inference."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--signal", choices=("SIGKILL", "SIGTERM"), default="SIGKILL")
    args = parser.parse_args()
    from tests.e2e.core.kanban.test_kanban_worker_resume import test_sigkilled_scope_resumes_exact_session_and_workspace
    with TemporaryDirectory(prefix="kanban-resume-probe-", dir=args.scratch) as scratch:
        test_sigkilled_scope_resumes_exact_session_and_workspace(Path(scratch), args.signal)


if __name__ == "__main__":
    main()
