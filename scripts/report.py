"""Thin CLI shim — the report lives in `ecvt.reporting`.

Kept so `python scripts/report.py <run_dir>` keeps working. The logic moved
into the package because `ecvt run --record` needs to call it, and because
M3's Reporting agent (a08) will build on the same module.
"""

from __future__ import annotations

import sys

from ecvt.reporting import main

if __name__ == "__main__":
    sys.exit(main())
