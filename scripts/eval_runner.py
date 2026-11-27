"""Thin CLI shim — the eval runner lives in `ecvt.evals`.

Kept so `python scripts/eval_runner.py <dataset>` keeps working. The logic is
in the package because gate G1 and, later, CI call it directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This checkout's code, not whatever `pip install -e` points at: from a second
# worktree the editable install resolved `ecvt` to the other checkout, and an
# a01 eval measured that checkout's Manager instead of this one (M4-T3).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ecvt.evals import main

if __name__ == "__main__":
    sys.exit(main())
