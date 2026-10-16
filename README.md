# Local AI Control Center

A local-first dashboard for running AI on your own machine. It starts and
watches a `llama.cpp` server, pairs **any** local model with **any** agent
CLI, lets models use tools through **MCP**, and shows what actually happened
(hardware load, requests, tool calls, permissions) instead of what a config
file merely says.

Everything runs on your computer. The backend binds to `127.0.0.1` only, every
API call needs a local access token, and the only network traffic is what you
ask for (searching Hugging Face / GitHub, downloading a model you picked).

> Full setup, usage, a tour of every screen and a troubleshooting list from
> real bugs: **[docs/GUIDE.md](docs/GUIDE.md)**.

## What it does

| Area | What you get |
|---|---|
| **Runtimes** | Start / stop / restart `llama-server` with every launch flag exposed in Settings; live status, logs, crash detection; proof of what the process really received (command line, server log, `/props`). |
| **Models** | Scan a folder, or search Hugging Face and download for real: ranked by publisher trust, downloads, likes and followers; quantization and size parsed from your query; resumable, disk-checked, SHA-256 verified. Hardware fit verdict before you download. |
| **Agents** | Pair a model with an agent CLI (Hermes Agent, Open Interpreter, or any CLI described by a small spec). The app writes the agent's own config, reads it back to verify, and proves the link by watching the server receive the agent's first request. |
| **Easy connect** | "Use this model": picks sensible launch settings from your hardware, reuses a runtime that already serves the model, and connects an agent in one step. |
| **Chat** | A chat for every model, with or without an agent: streaming, edit, retry, copy, search, projects with a git panel. |
| **MCP** | Add MCP servers (stdio / http / sse), see their tools, attach them to an agent, and let a local model call them. Every call passes the Permission Engine first. |
| **Permissions** | Allow once / for the session / always, or deny. Standing grants are reviewable and revocable. Risk level comes from the tool's declared hints, never used to skip asking. |
| **Observability** | Hardware gauges, tokens/sec and latency history, structured event log (live + history), sessions with JSON/JSONL/Markdown export, context-window inspector. |
| **Memory care** | Auto-free policy: stop a runtime when the agent leaves or after N idle minutes so the GPU is free again. |
| **Look** | Dark or light (both soft, never pure black or white) and two colour palettes (blue, green). |

## Requirements

- Python 3.11+ (developed on 3.13)
- Node.js 18+ and npm (for the frontend)
- A `llama-server` build from [llama.cpp](https://github.com/ggml-org/llama.cpp).
  **Get a CUDA or Vulkan build if you have a GPU** — the CPU-only build runs
  fine but never touches the GPU (see the guide, "GPU shows 0%").
- Optional: `npx` / `uvx` for MCP servers, and an agent CLI such as Hermes
  Agent or Open Interpreter.

Windows is the most exercised platform. Linux has a platform provider too but
has seen less real-world use.

## Quick start

From the **project root** (the folder that contains `backend/` and `frontend/`):

```bash
# 1. Backend
python -m venv venv
# Windows:  venv\Scripts\activate        Linux/macOS:  source venv/bin/activate
pip install -r requirements.txt
python -m backend.main        # http://127.0.0.1:8420

# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev                   # http://127.0.0.1:5173
```

Open `http://127.0.0.1:5173`. The frontend fetches the access token from the
backend by itself; if it can't, it asks you to paste it from
`local_config.json`.

Run the backend **from the project root**, always from the same folder:
`control_center.sqlite3` and `local_config.json` are created in the current
directory, so a different folder means a different (empty) database.

Then open **Setup** and follow "Run an LLM": find `llama-server`, pick or
download a model, start a runtime. Pair an agent from **Chats**.

## Tests

```bash
pip install pytest httpx
pytest tests/backend -q          # from the project root

cd frontend
npx vitest run                   # component + utility tests
```

The MCP tests spawn a real MCP server (`tests/backend/mcp_fixture_server.py`,
official SDK) to exercise the actual handshake, listing, crash detection and
tool calls.

## Project layout

```
backend/
  main.py            app wiring, CORS allowlist, startup/shutdown
  api/               REST + WebSocket routes (http, connect, discovery, mcp, permissions, tools, ws)
  core/
    engine/          InferenceEngine contract + llama.cpp implementation and capability probing
    platform/        PlatformProvider: the only place that branches on the OS (windows/, linux/)
    agents/          agent adapters (Hermes, Open Interpreter, generic CLI), pairing proof, verification
    mcp/             MCP manager (client sessions) and config validation
    permissions/     Permission Engine
    tools/           tool registry synced from MCP servers
    events/          structured event bus (persisted + live)
    model_*.py       search ranking, discovery/download, recommendation, hardware fit
    agent_*.py       GitHub agent search/install, agent loop (chat + tools)
  storage/           SQLAlchemy models, SQLite
frontend/src/        React + TypeScript + Vite (pages, components, *-utils.ts pure logic)
tests/               pytest (backend) and vitest (frontend)
scripts/             probe_hf_search.py, a read-only Hugging Face probe
```

Two design rules keep it generic: nothing outside `core/engine/` imports a
concrete engine, and nothing outside `core/platform/` checks the operating
system. Adapters only ever see an `Endpoint` (base URL, key, model id,
context), so another engine or a hosted provider can be added without touching
the agents.

## Security model

- Listens on `127.0.0.1:8420` only. Remote mode is not implemented.
- A random token in `local_config.json` is required on every REST call and
  WebSocket.
- CORS allowlist: `localhost`/`127.0.0.1` on ports 5173 and 3000. Add more
  with `LAI_ALLOWED_ORIGINS=http://host:port,...`.
- Online features only talk to an allowlist of hosts (Hugging Face, GitHub).
- Installing an agent shows the exact command and needs your confirmation;
  only plain package-manager installs and strict `curl | sh` installers run,
  as two processes without a shell.
- MCP stdio servers: launcher allowlist (`npx`, `uvx`, `uv`, `node`, `python`,
  `deno`, `bun`, `docker`), no shells, no inline-code flags, no risky env keys
  (`PATH`, `LD_PRELOAD`, ...). Secrets in env/headers are redacted from errors.
- Every MCP tool call is gated by the Permission Engine.

## Status and honest limits

- MCP is verified end to end: the filesystem server connected, a local model
  called `write_file`, and the file appeared on disk.
- MCP tools reach the app's **own** chat loop. External agents (Hermes, Open
  Interpreter) don't see the app's MCP servers yet; wiring them through each
  agent's own config is the next step.
- Models: GGUF only. safetensors / PyTorch / ONNX / MLX are planned, as a
  property of each runtime ("which formats can it run").
- Search ranking weights are hand-set and not tuned against a golden set.
- GitHub allows about 10 unauthenticated searches a minute; one agent search
  uses up to three requests.
- Hugging Face follower counts are best-effort and may show "n/a".
- Synapse integration is architecture-ready (a `source` field and stable
  read-only endpoints) but not wired.
