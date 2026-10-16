# Local AI Control Center — Setup, Usage and Troubleshooting Guide

This guide is the long version of the README. It covers installing and
starting the app, what every screen is for, how MCP works here, and a list of
problems we actually hit while building and testing it, with the fixes that
worked.

How to read the troubleshooting parts: each entry says what you will see, the
likely cause, and what to do. Entries marked *(confirmed)* were diagnosed and
fixed on a real machine. Entries marked *(likely)* are the most probable cause
but we did not see the final resolution ourselves, so verify with the check
listed.

---

## 1. Concepts in two minutes

- **Model**: a `.gguf` file (the format `llama.cpp` reads).
- **Runtime**: a running `llama-server` process serving one model on a local
  port with an OpenAI-compatible API. One model can have one runtime.
- **Agent**: a program that uses a model to do work (Hermes Agent, Open
  Interpreter, ...). The app doesn't replace it; it configures it to talk to
  your runtime, then proves the connection is real.
- **Pairing**: an agent bound to a runtime. The app writes the agent's own
  config, reads it back, and counts as "paired" only after the server
  actually receives a request from that agent.
- **MCP server**: a separate program that offers tools (read a file, query a
  database, search the web) over the Model Context Protocol.
- **Tool call**: the model asks for a tool; the app asks *you* (Permission
  Engine), then the MCP server runs it, and the result goes back to the model.

The data flow for a tool call:

```
chat message → model (llama-server) → "call tool X"
   → Permission Engine (you approve) → MCP server runs X → result
   → model continues → final answer
```

---

## 2. Installation

### 2.1 Prerequisites

| Need | Notes |
|---|---|
| Python 3.11+ | The code uses features from 3.11. Developed on 3.13. |
| Node.js 18+ | For the frontend (Vite 5). `npx` also launches most MCP servers. |
| `llama-server` | From llama.cpp releases. Pick the build for your hardware (see 2.2). |
| An agent CLI (optional) | Hermes Agent, Open Interpreter, or any CLI you describe with a spec. |

### 2.2 Choosing a llama.cpp build

| Hardware | Download |
|---|---|
| NVIDIA GPU | The CUDA build (`...bin-win-cuda-12.x-x64.zip`) **plus** the matching `cudart-...` zip, extracted into the same folder. Newer cards (RTX 50 series) need a recent CUDA 12.x build. |
| Any GPU, simplest | The Vulkan build (`...bin-win-vulkan-x64.zip`). A bit slower than CUDA on NVIDIA, but nothing else to install. |
| No GPU | The CPU build. Works, but everything runs on the processor. |

Check it before going further:

```powershell
.\llama-server.exe --list-devices
```

You should see your GPU. An empty list means a CPU-only build (see
troubleshooting: "GPU shows 0%").

### 2.3 Backend

From the project root (the folder containing `backend/` and `frontend/`):

```bash
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python -m backend.main
```

It listens on `http://127.0.0.1:8420`. Two files appear in the **current
folder**: `local_config.json` (your access token) and
`control_center.sqlite3` (all data). Always start the backend from the same
folder, or you will get a fresh empty database.

On Windows the agent terminal needs `pywinpty` (it wraps the Windows
pseudo-console). `requirements.txt` installs it automatically on Windows; if
you installed packages by hand, run `pip install pywinpty`.

Harmless warnings on startup: a `pynvml` deprecation `FutureWarning` and
FastAPI `on_event` `DeprecationWarning`s. Neither affects behaviour.

### 2.4 Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. If the page can't fetch the token by itself it
shows a box; paste the `access_token` from `local_config.json`.

If you serve the frontend from another origin, tell the backend:

```powershell
$env:LAI_ALLOWED_ORIGINS = "http://192.168.1.10:8080"
python -m backend.main
```

### 2.5 Tests

```bash
pip install pytest httpx
pytest tests/backend -q
cd frontend && npx vitest run
```

---

## 3. First run: get a model answering

1. **Setup → Run an LLM.** Three steps: find `llama-server` (the app looks
   on PATH and common places, or you browse to the `.exe`), pick a model,
   start it. Each step writes to the backend immediately and "Continue" stays
   disabled until the runtime is actually observed ONLINE.
2. Open **Chats**, pick the model, say something.
3. Look at **Dashboard**: the runtime card shows status, model, tokens/sec
   and gauges.

Shortcut: **Dashboard → Quick connect**. Choose a model (and optionally an
agent) and press connect. It picks launch settings from your hardware (GPU
layers, context size), reuses a runtime that already serves that model, and
starts it. You can still change every value later in Settings.

---

## 4. Screen by screen

### Dashboard
Hardware strip (CPU, RAM, GPU, VRAM, live over a WebSocket), one card per
runtime (start/stop/restart, status LED, metrics, auto-free control), and a
live map of which agent is using which model. Quick connect lives here.

### Chats
A chat exists for every model, with or without an agent. Streaming replies,
copy, edit-and-resend, retry/regenerate, search across chats and inside one
chat. A side panel shows the **Context Inspector** (what is filling the
model's context window: system text, tool definitions, history) and the
**Tools** panel where you attach MCP servers to the chat's agent. Pairing an
agent to a model happens here, in the agent terminal.

### Projects
Group chats under a workspace. Each project has Chats, Git (a graphical front
end over your real `git`; it never reimplements git) and Usage (an estimate of
hardware use per project — an estimate because one llama-server process is
shared by everything that talks to it).

### Models
Compare models side by side with facts that carry their source (model card,
base model, public benchmarks) so nothing is an unlabeled guess. **Verify**
checks a downloaded file's SHA-256 against Hugging Face's published hash
(opt-in, needs internet). A fit badge says whether a model should run on this
machine (GOOD / WARNING / LIKELY_TO_EXCEED / NOT_RECOMMENDED); it is a
heuristic with headroom, not a guarantee.

#### What the comparison table shows, and where each part comes from
- **Metadata / Read from the file itself**: read straight from each GGUF
  file's header (architecture, exact parameter count, which quantization
  types the weights really use, bits per weight, layers, trained context,
  vocabulary, KV-cache cost per 1k tokens). This is exact for *that file*, works
  offline, and is the part that really differs between, say, a Q4 and a Q6 of
  the same model. Rows whose values differ are highlighted.
- **Estimated system impact**: a heuristic from file size, labelled as such.
- **Real-world performance**: measured by this app from your own requests.
- **From the web**: the model card of the Hugging Face repository the model
  came from. A card describes the *whole repository*, so two quantizations
  downloaded from one repo show identical web facts. The table says so when
  that happens; use the file rows to tell them apart.

If a file can't be read (moved, truncated, not a GGUF) its column says why and
falls back to what the catalog knows. The header reader has hard limits, so a
corrupt or hostile file gives an error, never a crash.

### Model verification (how sure are we this file is the real one?)
Every model shows how it was checked against Hugging Face, in the Models page
and as a mark in the model dropdowns:

| Shown | Meaning | What it does *not* prove |
|---|---|---|
| **Verified by hash** (✓) | The file's SHA-256 equals the hash Hugging Face publishes for it. | That the publisher is trustworthy. A bad repo has a "correct" hash too. |
| **Size match only** | Hugging Face published no hash, so only the byte count was compared. | A swapped file of the same length. Press **Verify** to retry a hash check. |
| **Doesn't match** (⚠) | The file differs from Hugging Face's copy: corrupt or altered. | Delete it and download again. |
| **Not verified** | Never compared (a file you copied in by hand, or not linked to a repo). | Link it to its repo and press **Verify**. |

Downloads are checked while they download and recorded automatically; a
mismatch deletes the file. A file already sitting in the destination folder
with the right size is **hash-checked too** (it used to be trusted by size
alone) and re-downloaded if the content differs. For split models the weakest
part decides the label. The hash proves integrity, so also look at the
publisher badge and download counts in search.

### Sessions
Every agent↔runtime link is a record: tokens, latency, chats touched, tool
calls. Export as JSON, JSONL or Markdown (the Markdown doubles as a readable
trace of what the agent did).

### Analytics
Charts over history sampled in the background (tokens/sec, latency, CPU, RAM,
GPU, VRAM), plus rankings. The sampler runs on the server, so history exists
even when no browser tab is open.

### MCP, Permissions, Logs
Covered in sections 6 and 7.

### Setup
Options first, then only the steps of the one you open: **Run an LLM**,
**Add a model** (a file you have, or search and download), **Add an agent**
(register an installed one, or search GitHub and install). A filter switches
between LLM and agent options.

### Settings
Two tabs. **Runtimes (llama.cpp)** exposes every launch flag the app knows
how to pass through, grouped like llama.cpp's own docs; changes apply on the
next start. **Agents** holds per-agent options and the backend each agent
uses. Both save into the row's `config_json`; a `null` value means "forget
this key".

The bar at the bottom of the sidebar switches the **background** (soft light
or soft dark) and the **colour** (blue or green). On a dark background the
accent gradient runs white → colour; on a light one it runs black → colour.
Choices are remembered in your browser.

---

## 5. Models and agents

### 5.1 Finding models online
Setup → Add a model → online. Type a name loosely: `qwen3.5 9b`, even with a
quantization (`qwen3.5-9B-Q6KM`). The app:

- reads size (`9b`) and quantization (`Q6_K_M`) out of your text,
- searches Hugging Face for GGUF repos, repairs obvious typos,
- ranks by relevance, **publisher trust** (well-known creators and
  quantizers), downloads, likes and followers,
- keeps plain conversions on top and puts fine-tunes and merges in a
  collapsed "modified versions" group,
- shows a memory estimate and fit verdict for your machine.

A quantization such as `Q6_K_M` is a *file* inside a repo, not part of the
repo name, so it is matched when you open the repo's files.

Pick a file and download. Downloads are resumable, checked for free disk
space and verified against Hugging Face's SHA-256. Gated repos are listed but
can't be downloaded in-app. Files go to **Save downloads to**: by default
`<your Downloads>/Local AI models`; change it with **Choose folder…**.

### 5.2 Finding agents online
Setup → Add an agent → online. The app tries several spellings (typed, joined,
hyphenated: `open interpreter`, `openinterpreter`, `open-interpreter`), looks
up a short list of well-known projects directly so they can't be missed, and
scores every hit (name match, "describes itself as an AI agent", stars,
recent activity, archived, license). Hits that aren't agents are collapsed,
not hidden.

Installing shows the exact command and needs your confirmation. Package
installs (`pip`, `npm -g`, `cargo`, ...) go to the package manager's own
location, so the app can't redirect them. Instead the **agent location bar**
shows where the executable ended up and lets you register it.

### 5.3 Pairing an agent with a model
In Chats, open the agent terminal for a chat whose runtime is running.

1. The adapter writes the agent's own config (base URL, model id, key).
2. The app reads every setting back and reports each as confirmed or ignored.
3. When the agent sends its first request, the runtime log shows it and the
   pairing counts as proven.

Built-in adapters: **Hermes Agent** (uses the keys `model.provider`,
`model.base_url`, `model.default`, and an API-key variable) and **Open
Interpreter** (`~/.openinterpreter/config.toml`, edited with an exact TOML
writer that changes only its own keys). Any other CLI can be connected with a
**custom agent spec** (where to find it, which settings file or command sets
the provider, how to launch it); the form validates the spec and previews
what it would write.

### 5.4 Freeing memory
Each runtime has an **auto-free** policy: keep loaded, stop after the last
agent terminal closes (with a grace period), or stop after N idle minutes
(1–1440). A background reaper applies it.

---

## 6. MCP

### 6.1 What it is in this app
MCP lets a model use tools that live outside the model. This app is the MCP
*client* and gatekeeper:

- It runs/connects MCP servers and lists their tools.
- It offers the model only the tools of servers you **attached to that
  agent** and that are **currently connected**.
- It never runs a tool without the Permission Engine saying yes.
- Today MCP tools are used by the **app's own chat loop**. Hermes and Open
  Interpreter do not receive the app's MCP servers (planned: write them into
  each agent's own config).

### 6.2 Adding a server
MCP page → add. Fields: name, transport, and either a command (stdio) or a
URL (http / sse).

stdio example (the official filesystem server):

| Field | Value |
|---|---|
| Transport | stdio |
| Command | `npx` |
| Args | `-y`, `@modelcontextprotocol/server-filesystem`, `E:/mcp_test` |

**Create the folder first.** If it doesn't exist the server exits at once and
you only see "Connection closed" (the app now also shows the server's own
last lines of output).

Allowed launchers for stdio: `npx`, `uvx`, `uv`, `node`, `python`, `python3`,
`py`, `deno`, `bun`, `bunx`, `docker`. Shells, `-c`/`-e` style inline code,
shell metacharacters and sensitive environment keys are rejected. Env values
and headers are write-only secrets: they are redacted in errors and never
returned by the API.

Connect: the LED goes green and the tool list appears (the filesystem server
exposes 14). A ping every 15 s detects a dead server; the card turns red and
a notification appears. Connecting needs **no** model and no agent.

### 6.3 Using tools in a chat
1. Start a runtime **with `--jinja`** (without it llama-server rejects the
   `tools` field). Use a model that supports tool calling.
2. Create or open an agent, and in the chat's **Tools** panel attach the MCP
   server.
3. Ask for something the tools can do: "List the files in the folder", then
   "Create hello.txt containing hello".
4. A permission prompt appears. Choose allow once / session / always, or deny.
5. The tool call and its result are stored as messages in the chat, and the
   model answers from the real output.

Limits: up to 8 tool round-trips per turn, 60 s per tool call. A call to a
tool that wasn't offered at the start of the turn fails as "unknown tool".
Errors (server died, bad arguments, denied) are written into the transcript;
the turn does not crash.

### 6.4 Picking a light model for testing
Small models differ a lot in tool calling. Good starting points are the
Qwen3 family: `Qwen3-1.7B` (about 1 GB at Q4) to test the plumbing,
`Qwen3-4B-Instruct-2507` (about 2.5 GB) if the small one picks wrong tools or
arguments. If a tool is never called, or called with wrong arguments, that is
the model, not the app.

### 6.5 Risk levels
When a server attaches hints to a tool, the app turns them into a default
risk: `destructiveHint` → CRITICAL; `readOnlyHint` → LOW; explicit
`destructiveHint: false` → MEDIUM; no hints → HIGH; `openWorldHint` raises it
one level. Hints are claims by the server, not facts, so they only pre-select
the prompt; the app always asks.

---

## 7. Permissions

Every risky action asks. A request blocks the tool call until you answer:

| Choice | Lasts |
|---|---|
| Allow once | This one call |
| Allow for session | The rest of that session |
| Allow always | Until you revoke it |
| Deny | This one call (recorded) |

**Permissions** page lists standing grants so you can review and revoke
them. "Allow once" and "Deny" are logged for history but can never silently
apply to a later request.

---

## 8. Logs and proof

- **Logs**: live tail plus paginated history of the structured event log
  (runtime started/crashed, model registered, agent created, inference
  started/completed/failed, tool started/completed/failed, MCP
  connected/lost, permission decisions). Filter by exact runtime.
- **Runtime inspector**: compares three sources for one runtime — the exact
  command line the app launched, what the server said at startup, and what
  `/props` answers now. Use it when a setting "doesn't seem to apply".
- **Hardware gauges**: CPU, RAM, GPU, VRAM from `psutil` and NVML.

---

## 9. Troubleshooting (bugs we actually hit)

### The frontend won't start: "Failed to resolve import ./components/NavIcon" *(confirmed)*
**Cause:** a file was missing after copying updates (`NavIcon.tsx`,
`PaletteSwitcher.tsx`, `theme.ts` were new).
**Fix:** copy the missing files into the same paths, then stop and restart
`npm run dev`. If a new import fails after an update, check that every file it
names exists.

### Every `/api/v1/connect/...` call returns 404 *(likely)*
You'll see 404s on `/connect/quick`, `/connect/runtimes/N/auto-free`,
`/connect/models/N/launch-plan`, and "Use this model" fails.
**Causes, in order of likelihood:** (1) the backend is still the old process,
started before the new files were copied; (2) `backend/api/connect.py` or
`backend/main.py` are old copies; (3) the route exists but returns a 404 by
design because the row is missing (`runtime not found`, `model not found`,
`agent not found`).
**Check:**
1. Stop the backend (Ctrl+C) and start it again from the project root.
2. Open `http://127.0.0.1:8420/docs` and look for `/api/v1/connect/...`.
3. In the browser's Network tab, read the response body of one 404:
   `{"detail":"Not Found"}` means the route is missing; a message like
   `runtime not found` means the data is missing.

### WebSocket errors in the browser console
"WebSocket is closed before the connection is established" appears in
development because React's StrictMode mounts effects twice. Harmless. It only
matters if the agent terminal genuinely doesn't open.

### Long tracebacks when I press Ctrl+C on the backend
`KeyboardInterrupt` / `CancelledError` in `events_ws` while shutting down with
sockets open. Harmless noise from stopping the server.

### GPU shows 0% while the model runs *(confirmed cause: CPU-only build)*
**Cause:** the `llama-server` build has no GPU backend.
**Check:** `.\llama-server.exe --list-devices` is empty; the runtime log has
no `ggml_cuda_init` / "offloaded N/N layers to GPU" lines; `nvidia-smi` does
not list `llama-server.exe` while it generates. The runtime details also show
a "this build is CPU-only" warning.
**Fix:** install the CUDA or Vulkan build (section 2.2), register the new
`llama-server.exe`, restart the runtime, run `--list-devices` again.
**Also:** Task Manager's default GPU graph is "3D". Switch a graph to
**Cuda** or **Compute** to see inference load.

### MCP: "McpError: Connection closed" *(confirmed)*
**Cause we hit:** the folder passed to the filesystem server didn't exist, so
the server exited before the handshake.
**Fix:** create the folder (`mkdir E:\mcp_test`) and connect again. To see the
real error, run the same command in a terminal:
`npx -y @modelcontextprotocol/server-filesystem E:/mcp_test`. A healthy server
prints a "running on stdio" line and waits. The app now appends the server's
last stderr lines to the error on the card.
**Other causes:** `npx` not on PATH (run `npx --version`), no internet on the
first `npx -y` download (the first connect can take a while; the timeout is
60 s), a wrong package name.

### MCP connects but the model never calls tools
- The runtime wasn't started with `--jinja`: restart it with that flag (the
  error text mentions it).
- The MCP server isn't **attached** to this chat's agent, or isn't connected
  right now. Only attached **and** connected servers are offered.
- The model can't do tool calling. Try a Qwen3 Instruct model.
- The permission prompt was denied or timed out; the transcript says so.

### Hermes banner still shows a cloud model *(confirmed)*
**Cause:** the config key was wrong. The app first wrote `model.name`, but
Hermes reads its model id from `model.default`, so the write "succeeded" and
was ignored.
**Fix (in the app):** the adapter writes `model.default`, and verification
reads every key back and flags any that didn't take effect.
**Lesson for new agents:** configs usually follow conventions but use
different key names. Always read back; never trust a successful write.

### Search returns odd or low-quality models first *(confirmed, fixed)*
Cause: Hugging Face search only matches repo names as text. Typing
`Q6KM` surfaces repos that put it in their *name* (mostly fine-tunes) and
buries the plain conversions. Ranking now strips the quantization from the
query, repairs typos, and weighs publisher, downloads, likes and followers.
If a known good repo is missing, try without the quantization and open its
files.

### Agent search can't find a project I know exists *(confirmed, fixed)*
Cause: GitHub matches words; `open interpreter` doesn't match a repo named
`openinterpreter`. Search now tries joined and hyphenated spellings and looks
up well-known projects directly. For anything else, paste `owner/name` or the
GitHub URL.

### Agent installs, but the app can't find it
`pip`/`npm`/`cargo` installs can't be redirected to a chosen folder. Use the
**agent location bar** to see detected executables and register the one you
want (it's remembered in `agent_paths.json` next to the database).

### Open Interpreter doesn't pick up the model
Its config is `~/.openinterpreter/config.toml`. Check that file's `model` and
provider keys point at your runtime's base URL. (Look only at key names; the
file or its `.env` may hold secrets.) Use the pairing proof to confirm the
request really arrived.

### `--flash-attn` behaves differently across builds
Older builds take a bare `-fa`; newer ones take a value
(`--flash-attn on|off|auto`) and a bare flag swallows the next argument. The
app asks your `llama-server --help` which syntax it wants, so keep the
Settings value instead of hand-writing the flag.

### I changed Settings and nothing changed
Runtime flags apply on the **next start**, not to a running process. Restart
the runtime. To see what was really used, open the runtime inspector.

### Two databases / "my data disappeared"
`control_center.sqlite3` and `local_config.json` live in the folder you
started the backend from. Start it from the same folder every time, or move
both files together.

### "Gated" models can't be downloaded
They need an accepted licence on Hugging Face. Download from the website, then
use **Add a model → this computer** and pick the file.

### Rate-limited agent search
GitHub allows about 10 unauthenticated searches per minute; each agent search
uses up to 3 requests. Wait a minute.

### Windows: agent terminal shows nothing
The terminal is a real pseudo-terminal (ConPTY via `pywinpty` on Windows).
If it stays blank, confirm `pip show pywinpty` works in the backend's venv,
check the backend console for the agent's launch error, and confirm
the agent's executable path in the location bar.

---

## 10. Reference

### Files created at runtime
| File | Purpose |
|---|---|
| `local_config.json` | Access token (owner-only on POSIX) |
| `control_center.sqlite3` | Runtimes, models, agents, chats, events, grants, MCP servers |
| `agent_paths.json` | Where you told the app each agent lives |
| `<Downloads>/Local AI models` | Default model download folder |

### Environment variables
| Variable | Effect |
|---|---|
| `LAI_ALLOWED_ORIGINS` | Extra browser origins allowed by CORS (comma-separated) |
| `LAI_REMOTE_MODE` | Only logs a warning; remote mode isn't implemented |

### Main endpoints
`/api/v1/runtimes`, `/api/v1/models`, `/api/v1/agents`, `/api/v1/connect/*`
(quick connect, launch plan, auto-free, custom agents), `/api/v1/mcp/servers`,
`/api/v1/tools`, `/api/v1/permissions/*`, `/api/v1/events`; WebSockets
`/ws/metrics`, `/ws/events`, `/ws/agent-terminal/{id}`. Interactive docs at
`http://127.0.0.1:8420/docs`. All need the access token.

### Known limits
- GGUF only; other formats are planned.
- MCP is not yet written into external agents' own configs.
- Ranking weights are hand-set.
- Hardware fit and per-project usage are estimates.
