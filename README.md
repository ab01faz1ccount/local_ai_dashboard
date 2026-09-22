# Local AI Control Center

Local-first dashboard to manage a `llama.cpp` inference server, built to
later connect to Synapse (see `backend/integrations/synapse/README.md`).

See `backend/storage/schema.sql` for the data model and
`backend/core/engine/base.py` / `backend/core/platform/base.py` for the
two abstraction points that keep engine and OS logic swappable.

## Running it

Backend:
```
cd backend
pip install -r ../requirements.txt
python -m backend.main   # binds 127.0.0.1:8420, writes local_config.json
```

Frontend:
```
cd frontend
npm install
npm run dev               # http://127.0.0.1:5173
```

On first load, the app asks for the access token from `local_config.json`
(created next to wherever you ran `backend.main` from), then walks you
through the onboarding wizard (find/install llama.cpp, pick a model,
configure your first runtime, create your first agent) before showing the
dashboard.

## Status
Work in progress — build log lives in project chat history.
