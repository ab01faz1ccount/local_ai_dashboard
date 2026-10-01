"""
backend/api/http.py

REST API. Every route depends on `require_token`, per the non-negotiable
security requirement (day-0 local access token on every call).

`GET /api/v1/runtimes` and `GET /api/v1/models` are the two read-only,
Synapse-facing endpoints called out in the build prompt -- they stay
read-only and unopinionated about the caller (local frontend today,
Synapse or anything else later) on purpose.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core import agent_loop
from ..core import agents as agent_backends
from ..core import authenticity
from ..core import chat as chat_engine
from ..core import comparison
from ..core import connectivity
from ..core import events
from ..core import git_panel
from ..core import models as model_manager
from ..core import onboarding
from ..core import project_usage
from ..core.engine.base import RuntimeState
from ..core.runtime_manager import runtime_manager
from ..core.security import get_or_create_access_token
from ..storage import db as storage_db
from ..storage.db import Agent, MLModel, Project, Runtime

router = APIRouter(prefix="/api/v1")

_ACCESS_TOKEN = get_or_create_access_token()


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Expects `Authorization: Bearer <token>`. Runs on every route in this
    router via `dependencies=[Depends(require_token)]` on each endpoint
    (rather than a global app-level dependency) so it's obvious at each
    route definition that it's protected -- see main.py for how it's
    actually wired app-wide instead, which is the version that ships."""
    if authorization != f"Bearer {_ACCESS_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid or missing access token")


def get_db_session():
    yield from storage_db.get_db()


# ---------------------------------------------------------------------
# Schemas (request bodies only -- responses are hand-built dicts so the
# JSON shape can evolve without a matching Pydantic model for every read)
# ---------------------------------------------------------------------

class RuntimeCreate(BaseModel):
    name: str
    executable_path: str
    model_id: Optional[int] = None
    host: str = "127.0.0.1"
    port: int
    config_json: dict = {}


class RuntimeConfigUpdate(BaseModel):
    """Partial update for a runtime. `config_json`, when given, is merged
    into the existing dict (not replaced) -- so setting just
    `{"ctx_size": 16384}` from the Settings panel doesn't wipe out every
    other flag the user already tuned. `name`/`host`/`port`/`model_id`/
    `executable_path` replace outright (there's nothing to merge -- each
    is a single value). Everything here applies on the *next*
    start/restart; none of it touches an already-running process, which
    is why changing host/port/executable_path/model_id is blocked while
    the runtime is ONLINE/STARTING (see the handler)."""
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    model_id: Optional[int] = None
    executable_path: Optional[str] = None
    config_json: Optional[dict] = None


class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    agent_type: str = "generic"
    agent_backend: str = "generic"
    default_runtime_id: Optional[int] = None
    config_json: dict = {}


class AgentConfigUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_backend: Optional[str] = None
    config_json: Optional[dict] = None


class AgentConfigureRequest(BaseModel):
    """Triggers the real 'point this agent's own CLI at our runtime'
    step (e.g. Hermes's `hermes config set model.base_url ...`) -- see
    core/agents/*_adapter.py's `configure()`."""
    runtime_id: int
    model_name: Optional[str] = None
    api_key: str = "sk-no-key"  # llama.cpp's default: no real auth, just a non-empty placeholder most agent CLIs require


class ModelScanRequest(BaseModel):
    folder: str


class ModelUpdate(BaseModel):
    """Lets the user fill in metadata the folder scan can't read out of a
    .gguf header on its own, and link the model to its Hugging Face
    source -- required before comparison's benchmark lookup or
    authenticity verification can do anything beyond bare file metadata."""
    quantization: Optional[str] = None
    param_count: Optional[str] = None
    context_length: Optional[int] = None
    hf_repo_id: Optional[str] = None
    hf_filename: Optional[str] = None


class ModelCompareRequest(BaseModel):
    model_ids: list[int]


class OnboardingUpdate(BaseModel):
    llama_cpp_detected: Optional[bool] = None
    llama_cpp_path: Optional[str] = None
    first_model_added: Optional[bool] = None
    first_runtime_configured: Optional[bool] = None
    first_agent_created: Optional[bool] = None
    wizard_completed: Optional[bool] = None


class PathVerifyRequest(BaseModel):
    path: str


class ChatCreate(BaseModel):
    runtime_id: int
    agent_id: Optional[int] = None
    title: str = "New chat"


class ProjectCreate(BaseModel):
    name: str
    local_path: Optional[str] = None
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    local_path: Optional[str] = None
    description: Optional[str] = None


class ChatPromoteRequest(BaseModel):
    """Either attach the chat to an existing project (`project_id`) or
    create a new one on the spot (`new_project_name`, optionally with
    `local_path` right away) -- matches the "promote any chat from the
    list, whenever you decide it deserves to be a project" flow, rather
    than requiring a project to be picked up front."""
    project_id: Optional[str] = None
    new_project_name: Optional[str] = None
    local_path: Optional[str] = None


class GitCommitRequest(BaseModel):
    message: str


class SendMessageRequest(BaseModel):
    content: str
    temperature: float = 0.8
    max_tokens: Optional[int] = None


# ---------------------------------------------------------------------
# Runtimes
# ---------------------------------------------------------------------

def _runtime_to_dict(rt: Runtime) -> dict:
    return {
        "id": rt.id,
        "name": rt.name,
        "engine_type": rt.engine_type,
        "model_id": rt.model_id,
        "model_name": rt.model.name if rt.model else None,
        "host": rt.host,
        "port": rt.port,
        "executable_path": rt.executable_path,
        "status": rt.status,
        "pid": rt.pid,
        "last_error": rt.last_error,
        "config_json": rt.config_json,
        "source_system": rt.source_system,
    }


@router.get("/runtimes")
def list_runtimes(db: Session = Depends(get_db_session)):
    """Read-only, Synapse-facing. Returns every configured runtime and its
    current status -- does not start/stop anything."""
    return [_runtime_to_dict(r) for r in db.query(Runtime).all()]


@router.get("/runtimes/{runtime_id}")
def get_runtime(runtime_id: int, db: Session = Depends(get_db_session)):
    rt = db.get(Runtime, runtime_id)
    if rt is None:
        raise HTTPException(404, "runtime not found")
    return _runtime_to_dict(rt)


@router.post("/runtimes")
def create_runtime(body: RuntimeCreate, db: Session = Depends(get_db_session)):
    rt = Runtime(**body.model_dump())
    db.add(rt)
    db.commit()
    db.refresh(rt)
    storage_db.update_onboarding_state(db, first_runtime_configured=True)
    events.emit(events.EventType.RUNTIME_REGISTERED, runtime_id=rt.id, metadata={"name": rt.name})
    return _runtime_to_dict(rt)


def _get_runtime_or_404(db: Session, runtime_id: int) -> Runtime:
    rt = db.get(Runtime, runtime_id)
    if rt is None:
        raise HTTPException(404, "runtime not found")
    return rt


@router.patch("/runtimes/{runtime_id}")
def update_runtime_config(runtime_id: int, body: RuntimeConfigUpdate, db: Session = Depends(get_db_session)):
    """Backs the Settings panel's per-runtime llama.cpp flags AND the
    Dashboard's "edit runtime" form (name, host, port, executable path,
    which model it launches) -- see core/engine/llama_cpp_engine.py's
    _build_args for the config_json flag mapping. Merges `config_json`
    rather than replacing it, so the panel can save one changed field
    without the caller having to resend every other one it doesn't know
    about (e.g. flags set by Synapse).

    Changing host/port/executable_path/model_id is blocked while the
    runtime is actually running -- those describe what the *next* launch
    does, and silently editing them out from under a live process would
    just make the dashboard lie about what's actually running."""
    rt = _get_runtime_or_404(db, runtime_id)

    identity_fields_changed = any(
        getattr(body, f) is not None for f in ("host", "port", "executable_path", "model_id")
    )
    if identity_fields_changed and rt.status in (RuntimeState.ONLINE.value, RuntimeState.STARTING.value):
        raise HTTPException(400, f"«{rt.name}» در حال اجراست — اول متوقفش کن، بعد مسیر/پورت/مدلش رو عوض کن.")

    if body.name is not None:
        rt.name = body.name
    if body.host is not None:
        rt.host = body.host
    if body.port is not None:
        collision = (
            db.query(Runtime).filter(Runtime.port == body.port, Runtime.id != rt.id).first()
        )
        if collision is not None:
            raise HTTPException(400, f"پورت {body.port} از قبل برای «{collision.name}» استفاده شده.")
        rt.port = body.port
    if body.executable_path is not None:
        rt.executable_path = body.executable_path
    if body.model_id is not None:
        rt.model_id = body.model_id
    if body.config_json is not None:
        rt.config_json = {**rt.config_json, **body.config_json}

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"پورت {body.port} قبلاً برای یه runtime دیگه استفاده شده.")

    db.refresh(rt)
    return _runtime_to_dict(rt)


@router.delete("/runtimes/{runtime_id}")
def delete_runtime(runtime_id: int, db: Session = Depends(get_db_session)):
    """Blocked while the runtime is actually running (stop it first, same
    policy as model deletion). Unlike deleting a model, this genuinely
    does cascade: every chat that belongs to this runtime -- and their
    messages, and this runtime's request_logs/metrics_snapshots/
    llm_agent_sessions -- is deleted with it (`ondelete="CASCADE"` in the
    schema). That's the right call for a stray test runtime, but it does
    mean real chat history under the wrong runtime is genuinely gone, not
    just unlinked -- callers should confirm with the user first."""
    rt = _get_runtime_or_404(db, runtime_id)
    if rt.status in (RuntimeState.ONLINE.value, RuntimeState.STARTING.value):
        raise HTTPException(400, f"«{rt.name}» در حال اجراست — اول متوقفش کن.")
    # Emitted BEFORE the delete, not after: an events row inserted with
    # runtime_id=rt.id once the runtime is gone would violate the FK
    # constraint immediately (foreign_keys=ON). Existing events that
    # already reference this runtime are unaffected -- ON DELETE SET NULL,
    # not CASCADE, keeps their history even once the runtime itself is gone.
    events.emit(events.EventType.RUNTIME_REMOVED, runtime_id=rt.id, metadata={"name": rt.name})
    db.delete(rt)
    db.commit()
    return {"deleted": True}


@router.post("/runtimes/{runtime_id}/start")
def start_runtime(runtime_id: int, db: Session = Depends(get_db_session)):
    rt = _get_runtime_or_404(db, runtime_id)
    status = runtime_manager.start(db, rt)
    return {"state": status.state.value, "endpoint": status.endpoint, "error": status.error_message}


@router.post("/runtimes/{runtime_id}/stop")
def stop_runtime(runtime_id: int, db: Session = Depends(get_db_session)):
    rt = _get_runtime_or_404(db, runtime_id)
    status = runtime_manager.stop(db, rt)
    return {"state": status.state.value, "error": status.error_message}


@router.post("/runtimes/{runtime_id}/restart")
def restart_runtime(runtime_id: int, db: Session = Depends(get_db_session)):
    rt = _get_runtime_or_404(db, runtime_id)
    status = runtime_manager.restart(db, rt)
    return {"state": status.state.value, "endpoint": status.endpoint, "error": status.error_message}


@router.get("/runtimes/{runtime_id}/metrics")
def get_runtime_metrics(runtime_id: int, db: Session = Depends(get_db_session)):
    rt = _get_runtime_or_404(db, runtime_id)
    m = runtime_manager.get_metrics(rt)
    return {
        "tokens_per_sec": m.tokens_per_sec,
        "last_latency_ms": m.last_latency_ms,
        "active_slots": m.active_slots,
    }


@router.get("/runtimes/{runtime_id}/logs")
def get_runtime_logs(runtime_id: int, n: int = Query(default=200, le=2000), db: Session = Depends(get_db_session)):
    rt = _get_runtime_or_404(db, runtime_id)
    return {"lines": runtime_manager.get_logs(rt, n)}


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------

def _model_to_dict(m: MLModel) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "file_path": m.file_path,
        "file_size_bytes": m.file_size_bytes,
        "quantization": m.quantization,
        "param_count": m.param_count,
        "context_length": m.context_length,
        "hf_repo_id": m.hf_repo_id,
        "hf_filename": m.hf_filename,
        "verification_status": m.verification_status,
        "verification_checked_at": m.verification_checked_at,
        "verification_details": m.verification_details_json,
    }


def _get_model_or_404(db: Session, model_id: int) -> MLModel:
    model = db.get(MLModel, model_id)
    if model is None:
        raise HTTPException(404, "model not found")
    return model


@router.get("/models")
def list_models(db: Session = Depends(get_db_session)):
    """Read-only, Synapse-facing."""
    return [_model_to_dict(m) for m in db.query(MLModel).all()]


@router.get("/models/{model_id}")
def get_model(model_id: int, db: Session = Depends(get_db_session)):
    return _model_to_dict(_get_model_or_404(db, model_id))


@router.patch("/models/{model_id}")
def update_model(model_id: int, body: ModelUpdate, db: Session = Depends(get_db_session)):
    model = _get_model_or_404(db, model_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    for key, value in updates.items():
        setattr(model, key, value)
    db.commit()
    db.refresh(model)
    return _model_to_dict(model)


@router.delete("/models/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db_session)):
    """Removes the catalog entry only -- the .gguf file on disk is never
    touched, since it was scanned from a folder the app doesn't own, not
    copied in. Meant for cleaning up duplicate/test scans, which is easy
    to end up with since re-scanning a folder that's already been scanned
    re-adds anything not already in the catalog by path.

    Blocked only when a runtime pointing at this model is actually
    running (ONLINE/STARTING) -- stop it first. If it's referenced by
    OFFLINE/ERROR runtimes, those just lose the link (`model_id` -> NULL,
    enforced at the SQLite level) rather than blocking the delete."""
    model = _get_model_or_404(db, model_id)

    blocking = [
        rt
        for rt in db.query(Runtime).filter(Runtime.model_id == model_id).all()
        if rt.status in (RuntimeState.ONLINE.value, RuntimeState.STARTING.value)
    ]
    if blocking:
        names = "، ".join(rt.name for rt in blocking)
        raise HTTPException(
            400, f"این مدل توسط runtime در حال اجرا استفاده می‌شه ({names}) — اول متوقفش کن."
        )

    unlinked_runtime_ids = [
        rt.id for rt in db.query(Runtime).filter(Runtime.model_id == model_id).all()
    ]
    events.emit(events.EventType.MODEL_REMOVED, metadata={"model_id": model_id, "name": model.name})
    db.delete(model)
    db.commit()
    return {"deleted": True, "unlinked_runtime_ids": unlinked_runtime_ids}


@router.post("/models/scan")
def scan_models(body: ModelScanRequest, db: Session = Depends(get_db_session)):
    try:
        models = model_manager.scan_models_folder(db, body.folder)
    except NotADirectoryError as exc:
        raise HTTPException(400, str(exc))
    if models:
        storage_db.update_onboarding_state(db, first_model_added=True)
    return [{"id": m.id, "name": m.name, "file_path": m.file_path} for m in models]


# ---------------------------------------------------------------------
# Model comparison
# ---------------------------------------------------------------------

@router.post("/models/compare")
def compare_models(body: ModelCompareRequest, db: Session = Depends(get_db_session)):
    """Combines metadata, an estimated system-impact figure, real-world
    performance observed by this app, and (if the model is linked to a
    Hugging Face repo) public benchmark data from that repo's card."""
    if not body.model_ids:
        raise HTTPException(400, "model_ids must be a non-empty list")
    return comparison.compare_models(db, body.model_ids)


@router.get("/system/connectivity")
def get_connectivity(force: bool = False):
    """Backs the "internet-dependent features" banner/disabled-state on
    the frontend. `force=true` bypasses the short cache -- used right
    before the user tries an internet-dependent action, so a "you're
    back online" doesn't take up to CACHE_TTL_SECONDS to be noticed."""
    return {"online": connectivity.check_internet(force=force)}


# ---------------------------------------------------------------------
# Model authenticity verification (opt-in)
# ---------------------------------------------------------------------

def _run_verification(model_id: int) -> None:
    """Runs in a FastAPI BackgroundTask -- hashing a multi-GB GGUF file
    takes real time, so this must not block the request that triggered
    it. Opens its own short-lived DB session since the request's session
    is closed by the time this runs."""
    db = storage_db.SessionLocal()
    try:
        model = db.get(MLModel, model_id)
        if model is None:
            return
        result = authenticity.verify_model_authenticity(model)
        storage_db.update_model_verification(
            db,
            model_id,
            verification_status=result["status"],
            verification_checked_at=storage_db.utcnow_iso(),
            verification_details_json=result,
        )
        events.emit(
            events.EventType.MODEL_VERIFICATION_COMPLETED,
            metadata={"model_id": model_id, "status": result["status"]},
        )
    finally:
        db.close()


@router.post("/models/{model_id}/verify")
def start_model_verification(
    model_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db_session)
):
    """Opt-in: verification only ever runs when this endpoint is called.
    Marks the model 'pending' immediately and kicks off the actual
    hash/lookup work in the background; poll `GET /models/{id}` (or
    `GET /models/{id}/verify`) for the result."""
    model = _get_model_or_404(db, model_id)
    if not model.hf_repo_id or not model.hf_filename:
        raise HTTPException(
            400, "link the model to a Hugging Face repo/file first (PATCH /models/{id})"
        )
    if not connectivity.check_internet(force=True):
        raise HTTPException(400, "این بخش نیاز به اتصال اینترنت دارد.")
    storage_db.update_model_verification(db, model_id, verification_status="pending")
    background_tasks.add_task(_run_verification, model_id)
    return {"model_id": model_id, "verification_status": "pending"}


@router.get("/models/{model_id}/verify")
def get_model_verification(model_id: int, db: Session = Depends(get_db_session)):
    model = _get_model_or_404(db, model_id)
    return {
        "model_id": model.id,
        "verification_status": model.verification_status,
        "verification_checked_at": model.verification_checked_at,
        "verification_details": model.verification_details_json,
    }


# ---------------------------------------------------------------------
# Agents + LLM<->Agent analytics
# ---------------------------------------------------------------------

def _agent_to_dict(a: Agent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "agent_type": a.agent_type,
        "agent_backend": a.agent_backend,
        "default_runtime_id": a.default_runtime_id,
        "config_json": a.config_json,
        "status": a.status,
    }


@router.get("/agents")
def list_agents(db: Session = Depends(get_db_session)):
    return [_agent_to_dict(a) for a in db.query(Agent).all()]


@router.get("/agents/{agent_id}")
def get_agent(agent_id: int, db: Session = Depends(get_db_session)):
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    return _agent_to_dict(agent)


@router.post("/agents")
def create_agent(body: AgentCreate, db: Session = Depends(get_db_session)):
    agent = Agent(**body.model_dump())
    db.add(agent)
    db.commit()
    db.refresh(agent)
    storage_db.update_onboarding_state(db, first_agent_created=True)
    events.emit(events.EventType.AGENT_CREATED, agent_id=agent.id, metadata={"name": agent.name})
    return _agent_to_dict(agent)


@router.patch("/agents/{agent_id}")
def update_agent_config(agent_id: int, body: AgentConfigUpdate, db: Session = Depends(get_db_session)):
    """Backs the Settings panel's per-agent behavior (tools, tool
    permissions, planning/loop limits, memory, system prompt, context
    management, timeouts, confirmation/safety, concurrency -- all
    framework-dependent, so they live as free-form keys in `config_json`
    rather than fixed columns). Merges `config_json`, same reasoning as
    the runtime version above."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.agent_backend is not None:
        # "generic" (no real CLI wired up) is always allowed; anything
        # else has to be a backend this app actually knows how to drive
        # -- same set list_agent_backends() advertises -- so a bad value
        # here can't silently leave /ws/agent-terminal unable to find an
        # adapter later.
        known = {a.backend_id for a in agent_backends.list_adapters()}
        if body.agent_backend != "generic" and body.agent_backend not in known:
            raise HTTPException(400, f"backend '{body.agent_backend}' شناخته‌شده نیست.")
        agent.agent_backend = body.agent_backend
    if body.config_json is not None:
        agent.config_json = {**agent.config_json, **body.config_json}
    db.commit()
    db.refresh(agent)
    return _agent_to_dict(agent)


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: int, db: Session = Depends(get_db_session)):
    """Unlike deleting a runtime, this never cascades away chat history:
    chats/request_logs that reference this agent just lose the reference
    (`agent_id` -> NULL) and keep existing as plain chats. Only the
    agent<->runtime session links (`llm_agent_sessions`) are actually
    removed with it, since those only mean something in relation to the
    agent itself."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    # Emitted BEFORE the delete for the same FK reason as runtime removal
    # above: an events row can't reference an agent_id that no longer
    # exists at insert time (foreign_keys=ON).
    events.emit(events.EventType.AGENT_DELETED, agent_id=agent.id, metadata={"name": agent.name})
    db.delete(agent)
    db.commit()
    return {"deleted": True}


@router.get("/agent-backends")
def list_agent_backends():
    """Which real agent CLIs this app knows how to configure/launch, and
    whether each is actually installed on this machine -- backs the
    backend picker in the agent create/edit UI and the Settings panel's
    'Connect & Launch' step."""
    backends = [
        {
            "backend_id": "generic",
            "display_name": "Generic (no real CLI — config only)",
            "brand_color": "#7a7a7a",
            "detected": True,
            "detected_path": None,
            "detected_version": None,
        }
    ]
    for adapter in agent_backends.list_adapters():
        info = adapter.detect()
        backends.append(
            {
                "backend_id": info.backend_id,
                "display_name": info.display_name,
                "brand_color": info.brand_color,
                "detected": info.detected,
                "detected_path": info.detected_path,
                "detected_version": info.detected_version,
            }
        )
    return backends


@router.post("/agents/{agent_id}/configure")
def configure_agent(agent_id: int, body: AgentConfigureRequest, db: Session = Depends(get_db_session)):
    """The real 'point this agent's own CLI at our runtime' step -- runs
    the agent's actual config commands (e.g. Hermes's `hermes config set
    model.base_url ...`) instead of leaving the user to do it by hand.
    Requires the agent to have a real `agent_backend` (not "generic") and
    the target runtime to exist; doesn't require the runtime to be
    ONLINE right now (a CLI's config doesn't care whether the server it
    points at happens to be running yet)."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    if agent.agent_backend == "generic":
        raise HTTPException(400, "این agent یه backend واقعی نداره — اول یکی رو توی تنظیماتش انتخاب کن.")

    adapter = agent_backends.get_adapter(agent.agent_backend)
    if adapter is None:
        raise HTTPException(400, f"backend '{agent.agent_backend}' شناخته‌شده نیست.")

    runtime = db.get(Runtime, body.runtime_id)
    if runtime is None:
        raise HTTPException(404, "runtime not found")

    base_url = f"http://{runtime.host}:{runtime.port}/v1"
    model_name = body.model_name
    if model_name is None and runtime.model_id is not None:
        model = db.get(MLModel, runtime.model_id)
        model_name = model.name if model else None

    try:
        commands_ran = adapter.configure(base_url, body.api_key, model_name)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))

    agent.default_runtime_id = runtime.id
    agent.config_json = {**agent.config_json, "configured_runtime_id": runtime.id, "configured_base_url": base_url}
    db.commit()
    db.refresh(agent)
    return {"agent": _agent_to_dict(agent), "commands_ran": commands_ran}


@router.post("/runtimes/{runtime_id}/agents/{agent_id}/attach")
def attach_agent(runtime_id: int, agent_id: int, db: Session = Depends(get_db_session)):
    """Opens an active llm_agent_sessions link -- the event that makes
    'Agent2 is now using LLM1' become true. This one DB action is,
    semantically, both a session starting AND that agent beginning to
    operate through this runtime -- so it emits both session.started and
    agent.started, rather than picking one."""
    link = storage_db.start_llm_agent_session(db, runtime_id, agent_id)
    events.emit(events.EventType.SESSION_STARTED, runtime_id=runtime_id, agent_id=agent_id, session_id=link.id)
    events.emit(events.EventType.AGENT_STARTED, runtime_id=runtime_id, agent_id=agent_id, session_id=link.id)
    return {"session_link_id": link.id, "started_at": link.started_at}


@router.post("/sessions/{link_id}/detach")
def detach_session(link_id: int, db: Session = Depends(get_db_session)):
    link = storage_db.end_llm_agent_session(db, link_id)
    if link is None:
        raise HTTPException(404, "session link not found")
    events.emit(
        events.EventType.SESSION_COMPLETED, runtime_id=link.runtime_id, agent_id=link.agent_id, session_id=link.id
    )
    events.emit(
        events.EventType.AGENT_STOPPED, runtime_id=link.runtime_id, agent_id=link.agent_id, session_id=link.id
    )
    return {"session_link_id": link.id, "ended_at": link.ended_at}


@router.get("/analytics/current-mapping")
def current_mapping(db: Session = Depends(get_db_session)):
    """The exact question from the spec: which agents are using which LLM
    right now, keyed by runtime_id."""
    return storage_db.get_current_mapping(db)


@router.get("/analytics/history")
def usage_history(
    runtime_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    limit: int = Query(default=100, le=1000),
    db: Session = Depends(get_db_session),
):
    links = storage_db.get_usage_history(db, runtime_id=runtime_id, agent_id=agent_id, limit=limit)
    return [
        {
            "id": l.id,
            "runtime_id": l.runtime_id,
            "agent_id": l.agent_id,
            "started_at": l.started_at,
            "ended_at": l.ended_at,
            "status": l.status,
            "requests_count": l.requests_count,
            "avg_latency_ms": l.avg_latency_ms,
            "avg_tokens_per_sec": l.avg_tokens_per_sec,
        }
        for l in links
    ]


# ---------------------------------------------------------------------
# Structured event log (backend/core/events/) -- the same audit trail
# GET /ws/events (api/ws.py) tails live. Read-only here: nothing under
# /events ever writes -- see core/events/bus.py's EventBus for the one
# place that does.
# ---------------------------------------------------------------------

def _event_to_dict(e) -> dict:
    return {
        "id": e.id,
        "event_id": e.event_id,
        "event_type": e.event_type,
        "timestamp": e.timestamp,
        "device_id": e.device_id,
        "runtime_id": e.runtime_id,
        "agent_id": e.agent_id,
        "session_id": e.session_id,
        "source": {
            "system": e.source_system,
            "project_id": e.source_project_id,
            "agent_id": e.source_agent_id,
            "session_id": e.source_session_id,
            "task_id": e.source_task_id,
        },
        "metadata": e.metadata_json,
    }


@router.get("/events")
def list_events(
    event_type: Optional[str] = Query(
        default=None,
        description="An exact type ('runtime.started'), a comma-separated list, or a "
        "prefix ending in a dot ('runtime.') to match a whole category.",
    ),
    runtime_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    session_id: Optional[int] = None,
    since: Optional[str] = Query(default=None, description="ISO-8601 timestamp lower bound (inclusive)."),
    before_id: Optional[int] = Query(default=None, description="Pagination cursor: the smallest id seen so far."),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db_session),
):
    event_types = None
    type_prefix = None
    if event_type:
        if event_type.endswith("."):
            type_prefix = event_type
        else:
            event_types = [t.strip() for t in event_type.split(",") if t.strip()]
    rows = storage_db.list_events(
        db,
        event_types=event_types,
        type_prefix=type_prefix,
        runtime_id=runtime_id,
        agent_id=agent_id,
        session_id=session_id,
        since=since,
        before_id=before_id,
        limit=limit,
    )
    return [_event_to_dict(e) for e in rows]


@router.get("/events/types")
def get_event_types():
    """The full taxonomy with a short description each -- backs a filter
    dropdown in the frontend without hardcoding the list a second time."""
    return events.describe_all()


# ---------------------------------------------------------------------
# Onboarding wizard
# ---------------------------------------------------------------------

def _onboarding_to_dict(state) -> dict:
    return {
        "llama_cpp_detected": bool(state.llama_cpp_detected),
        "llama_cpp_path": state.llama_cpp_path,
        "first_model_added": bool(state.first_model_added),
        "first_runtime_configured": bool(state.first_runtime_configured),
        "first_agent_created": bool(state.first_agent_created),
        "wizard_completed": bool(state.wizard_completed),
    }


@router.get("/onboarding/state")
def get_onboarding_state(db: Session = Depends(get_db_session)):
    """Lets the frontend resume the wizard where the user left off, or
    skip it entirely once wizard_completed is true."""
    return _onboarding_to_dict(storage_db.get_onboarding_state(db))


@router.patch("/onboarding/state")
def patch_onboarding_state(body: OnboardingUpdate, db: Session = Depends(get_db_session)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    state = storage_db.update_onboarding_state(db, **updates)
    return _onboarding_to_dict(state)


@router.post("/onboarding/detect-llama-cpp")
def run_llama_cpp_detection(db: Session = Depends(get_db_session)):
    """Runs server-side detection (PATH + common install dirs) and
    persists the result onto onboarding_state in the same call, so the
    frontend doesn't need a separate PATCH round-trip."""
    path = onboarding.detect_llama_cpp()
    state = storage_db.update_onboarding_state(
        db, llama_cpp_detected=path is not None, llama_cpp_path=path
    )
    return _onboarding_to_dict(state)


@router.post("/onboarding/verify-path")
def verify_llama_cpp_path(body: PathVerifyRequest, db: Session = Depends(get_db_session)):
    """Validates a manually-typed executable path. This is what the
    wizard's Continue button actually gates on for a custom path -- typing
    something into the box is not, by itself, proof llama.cpp is there."""
    resolved = onboarding.verify_executable_path(body.path)
    if resolved:
        storage_db.update_onboarding_state(db, llama_cpp_detected=True, llama_cpp_path=resolved)
    return {"valid": resolved is not None, "path": resolved}


@router.get("/onboarding/install-guidance")
def install_guidance():
    return onboarding.get_llama_cpp_install_guidance()


# ---------------------------------------------------------------------
# Chats + search
# ---------------------------------------------------------------------

def _message_to_dict(m) -> dict:
    return {
        "id": m.id,
        "chat_id": m.chat_id,
        "role": m.role,
        "content": m.content,
        "prompt_tokens": m.prompt_tokens,
        "completion_tokens": m.completion_tokens,
        "latency_ms": m.latency_ms,
        "created_at": m.created_at,
        "tool_meta": m.tool_meta_json or {},
    }


def _chat_to_dict(c) -> dict:
    return {
        "id": c.id,
        "runtime_id": c.runtime_id,
        "agent_id": c.agent_id,
        "title": c.title,
        "project_id": c.project_id,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


@router.post("/chats")
def create_chat(body: ChatCreate, db: Session = Depends(get_db_session)):
    chat = storage_db.create_chat(db, runtime_id=body.runtime_id, agent_id=body.agent_id, title=body.title)
    return _chat_to_dict(chat)


@router.get("/chats")
def list_chats(
    runtime_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    project_id: Optional[str] = None,
    unassigned: bool = False,
    db: Session = Depends(get_db_session),
):
    return [
        _chat_to_dict(c)
        for c in storage_db.list_chats(
            db, runtime_id=runtime_id, agent_id=agent_id, project_id=project_id, unassigned_only=unassigned
        )
    ]


@router.post("/chats/{chat_id}/promote")
def promote_chat(chat_id: int, body: ChatPromoteRequest, db: Session = Depends(get_db_session)):
    """Turns an existing chat into (or attaches it to) a project. This is
    the only way a chat gets a project in this app -- project selection
    is never required at chat-creation time."""
    project_id = body.project_id
    if project_id is None:
        if not body.new_project_name:
            raise HTTPException(400, "either project_id or new_project_name is required")
        project = storage_db.create_project(db, name=body.new_project_name, local_path=body.local_path)
        project_id = project.id
    else:
        if db.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")

    chat = storage_db.promote_chat_to_project(db, chat_id, project_id)
    if chat is None:
        raise HTTPException(404, "chat not found")
    return _chat_to_dict(chat)


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: int, db: Session = Depends(get_db_session)):
    if not storage_db.delete_chat(db, chat_id):
        raise HTTPException(404, "chat not found")
    return {"deleted": True}


@router.get("/chats/{chat_id}/messages")
def get_chat_messages(chat_id: int, db: Session = Depends(get_db_session)):
    return [_message_to_dict(m) for m in storage_db.get_chat_messages(db, chat_id)]


@router.get("/chats/{chat_id}/search")
def search_chat(chat_id: int, q: str = Query(..., min_length=1), db: Session = Depends(get_db_session)):
    return [_message_to_dict(m) for m in storage_db.search_in_chat(db, chat_id, q)]


@router.get("/search")
def search_all_chats(q: str = Query(..., min_length=1), db: Session = Depends(get_db_session)):
    """Global search across every chat, for the sidebar search box."""
    return storage_db.global_search(db, q)


@router.post("/chats/{chat_id}/messages")
def send_chat_message(chat_id: int, body: SendMessageRequest, db: Session = Depends(get_db_session)):
    from ..storage.db import Chat as _Chat  # local import to avoid widening the module-level import list

    chat = db.get(_Chat, chat_id)
    if chat is None:
        raise HTTPException(404, "chat not found")

    runtime = _get_runtime_or_404(db, chat.runtime_id)
    status = runtime_manager.get_status(db, runtime)
    if status.state != RuntimeState.ONLINE or not status.endpoint:
        raise HTTPException(409, f"runtime is not online (state: {status.state.value})")

    # Store the user's message first: if the turn below fails partway
    # through, the user's side of the conversation -- and anything the
    # agent loop already wrote (earlier tool steps) -- is still saved
    # rather than lost.
    storage_db.add_chat_message(db, chat_id, role="user", content=body.content)

    try:
        written = agent_loop.run_agent_turn(
            db, chat, status.endpoint, temperature=body.temperature, max_tokens=body.max_tokens
        )
    except chat_engine.ChatCompletionError as exc:
        raise HTTPException(502, str(exc))

    return _message_to_dict(written[-1])


# ---------------------------------------------------------------------
# Projects + Git panel
# ---------------------------------------------------------------------

def _project_to_dict(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "local_path": p.local_path,
        "description": p.description,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@router.get("/projects")
def list_projects(db: Session = Depends(get_db_session)):
    return [_project_to_dict(p) for p in storage_db.list_projects(db)]


@router.post("/projects")
def create_project(body: ProjectCreate, db: Session = Depends(get_db_session)):
    project = storage_db.create_project(db, name=body.name, local_path=body.local_path, description=body.description)
    return _project_to_dict(project)


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db_session)):
    return _project_to_dict(_get_project_or_404(db, project_id))


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, db: Session = Depends(get_db_session)):
    _get_project_or_404(db, project_id)
    updated = storage_db.update_project(
        db, project_id, name=body.name, local_path=body.local_path, description=body.description
    )
    return _project_to_dict(updated)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db_session)):
    if not storage_db.delete_project(db, project_id):
        raise HTTPException(404, "project not found")
    return {"deleted": True}


@router.get("/projects/{project_id}/hardware-usage")
def get_project_hardware_usage(project_id: str, db: Session = Depends(get_db_session)):
    _get_project_or_404(db, project_id)
    return project_usage.get_project_hardware_usage(db, project_id)


@router.get("/projects/{project_id}/git/status")
def get_project_git_status(project_id: str, db: Session = Depends(get_db_session)):
    project = _get_project_or_404(db, project_id)
    context = git_panel.git_context(project.local_path)
    if not context["available"]:
        return context
    return {**context, **git_panel.get_status(project.local_path)}


@router.get("/projects/{project_id}/git/diff")
def get_project_git_diff(
    project_id: str, staged: bool = False, db: Session = Depends(get_db_session)
):
    project = _get_project_or_404(db, project_id)
    context = git_panel.git_context(project.local_path)
    if not context["available"]:
        return context
    return {**context, **git_panel.get_diff_stat(project.local_path, staged=staged)}


@router.get("/projects/{project_id}/git/log")
def get_project_git_log(
    project_id: str, limit: int = 20, db: Session = Depends(get_db_session)
):
    project = _get_project_or_404(db, project_id)
    context = git_panel.git_context(project.local_path)
    if not context["available"]:
        return context
    return {**context, "commits": git_panel.get_commit_log(project.local_path, limit=limit)}


@router.post("/projects/{project_id}/git/commit")
def commit_project_git(
    project_id: str, body: GitCommitRequest, db: Session = Depends(get_db_session)
):
    project = _get_project_or_404(db, project_id)
    context = git_panel.git_context(project.local_path)
    if not context["available"]:
        raise HTTPException(400, context["message"])
    try:
        result = git_panel.commit_all(project.local_path, body.message)
    except git_panel.GitError as exc:
        raise HTTPException(400, str(exc))
    return result
