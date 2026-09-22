# Backend tests

These pytest files test the real modules under `backend/` as delivered.
They were developed against a mirror of your project that also included
a few backend modules this task didn't touch but that `http.py`/`main.py`
import (`core/security.py`, `core/connectivity.py`, `core/engine/*`,
`core/platform/*`, `core/agents/__init__.py` registering the adapters,
and a handful of empty stand-in modules for routes unrelated to this
change: `authenticity.py`, `chat.py`, `comparison.py`, `git_panel.py`,
`project_usage.py`).

Drop these files into your real project's `tests/` (or wherever your
existing test suite lives) — they need nothing beyond what your project
already has. Install test deps with:

    pip install pytest fastapi sqlalchemy pydantic httpx --break-system-packages

Then run:

    pytest tests/ -q

All 155 tests should pass (1 skipped when run as root, since the
permission-denied test can't simulate that case for root).
