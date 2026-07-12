"""Live, non-canonical operations probe: temporary DB/raw archive only."""
from __future__ import annotations

import tempfile
from pathlib import Path

from .core import collect_main

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="operations-probe-") as directory:
        root = Path(directory)
        raise SystemExit(collect_main(["--once", "--db", str(root / "probe.db"), "--raw-dir", str(root / "raw"), "--out", str(root / "operations_export.json")]))
