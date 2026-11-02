"""Thin CLI shim — the eval runner lives in `ecvt.evals`.

Kept so `python scripts/eval_runner.py <dataset>` keeps working. The logic is
in the package because gate G1 and, later, CI call it directly.
"""

from __future__ import annotations

import sys

from ecvt.evals import main

if __name__ == "__main__":
    sys.exit(main())
