"""
tests/backend/conftest.py

Two things every backend test needs, done once here instead of in every
file:

1. The project root (the folder containing `backend/`) on sys.path, so
   `from backend.api import http` etc. resolves regardless of where
   pytest is invoked from.
2. A fresh, throwaway working directory. `storage/db.py`'s
   DEFAULT_DB_PATH and `core/events/device.py`/`core/agents/paths.py`'s
   sidecar files are all cwd-relative by design (so a real end-user
   install just works without an env var) -- which means tests MUST run
   from a scratch directory, or they'd read/write the real
   control_center.sqlite3 sitting in the repo root.
"""

import os
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])  # .../tests/backend/conftest.py -> repo root
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_TMP = tempfile.mkdtemp(prefix="lai-test-")
os.chdir(_TMP)
