# Settings → Browse (offline + online, models + agents)

## What's new

- **Offline browse**: `FileBrowserDialog.tsx` + `backend/core/fs_browser.py`
  — an in-app file/folder picker backed by a server-side directory
  listing (a plain `<input type="file">` can't reveal a real path, which
  the app needs for launching `llama-server` and remembering an agent's
  executable). Wired into Settings → Browse, the setup wizard, and as a
  "Browse…" button next to RuntimeCard's Executable path field.
- **Online model search/download** (`OnlineModelSearch.tsx` +
  `backend/core/model_discovery.py`): search Hugging Face by an
  approximate name, filtered to GGUF, pick a quantization, and download
  for real — resumable, disk-space-checked, verified against Hugging
  Face's own SHA256, then auto-registered into the models catalog with
  its HF link attached. Gated repos are shown but not downloadable here.
- **Online agent search/install** (`OnlineAgentSearch.tsx` +
  `backend/core/agent_discovery.py`): search GitHub, read the README's
  install instructions, and run one **only after explicit confirmation**.
  Two shapes of command are ever runnable: a plain package-manager
  install (pip/pipx/uv/npm -g/cargo/brew) or a strict
  `curl|wget <https url> | bash|sh` script installer — parsed and
  executed as two processes with `shell=False` (no shell involved, so a
  command string can't smuggle in a second command). Everything else is
  shown with a Copy button so the user can run it themselves.
- **Removed**: the old hardcoded 3-model "starter suggestions" list
  (`onboarding.py`'s `STARTER_MODEL_SUGGESTIONS`) and its endpoint —
  replaced by the online search in the wizard's step 1.
- **Two bug fixes** (unrelated to Browse, found along the way):
  1. `PATCH /agents/{id}` silently ignored `agent_backend` even though
     the request model had the field and the frontend was already
     sending it — switching an agent's backend from Settings never
     persisted. Now applied, with validation against the known adapters.
  2. `HermesAdapter` only ever looked on PATH for `hermes`. It now also
     checks a path the user picked via the offline browser
     (`core/agents/paths.py`), so an install that isn't on PATH still
     works.

## Wiring required in your project

`backend/main.py` now mounts the new router:

```python
from .api.discovery import router as discovery_router
...
app.include_router(discovery_router)
```

Nothing else changes about `main.py`'s existing structure or security
model — `discovery_router` declares its own
`dependencies=[Depends(require_token)]`, same protection as everything
under `http_router`.

No new third-party dependencies: `fs_browser.py`, `net_util.py`,
`model_discovery.py`, and `agent_discovery.py` are all stdlib-only
(`urllib`, `hashlib`, `subprocess`, `threading`). The frontend additions
use only what `RuntimeCard.tsx`/`OnboardingWizard.tsx` already had
available (React, your existing `api.ts` request helper).

`browse.css` ships its own `--browse-*` CSS variables (dark by default,
with a `prefers-color-scheme: light` override) so it drops in without
touching your theme. If you'd rather it match your existing theme
variables exactly, remap the `:root { --browse-*: ... }` block at the
top of the file to your own tokens.

## Tests

See `tests/backend/README.md` and `tests/frontend/README.md`.
155 backend tests / 42 frontend tests, all passing, including:
- mutation-tested security checks on the script-installer parser (sudo,
  command-chaining, cert-check bypass, download-failure-must-fail-the-job
  all verified to actually be caught, not just assumed)
- a real two-process pipeline test (no mocks) proving `curl|bash`-style
  installs run with no shell involved
- a regression test for the `agent_backend` PATCH fix
- an end-to-end test importing your actual `main.py` and hitting routes
  from both `http.py` and `discovery.py` through the same running app,
  to catch exactly the "forgot to mount the router" class of mistake
