"""
backend/api/discovery.py

REST routes behind Settings -> Browse. Kept in its own router (rather
than growing http.py further) and mounted by main.py alongside it:

    from .api import discovery
    app.include_router(discovery.router)

Every route here depends on `require_token` explicitly -- same token
check as the rest of the API -- so the file is safe even if the app-level
wiring in main.py ever changes.

Two families:
  OFFLINE  (no internet needed)
    GET  /discover/fs/list                  server-side file/folder picker
    POST /discover/models/register-local    add a picked .gguf (or scan a picked folder)
    POST /discover/agents/register-local    add a picked agent executable

  ONLINE   (checks connectivity first, like the other internet features)
    GET  /discover/models/search            Hugging Face GGUF search
    GET  /discover/models/files             quantizations available in one repo
    POST /discover/models/download          start a resumable, verified download
    GET  /discover/downloads[/{id}]         progress; POST .../cancel to stop
    GET  /discover/agents/search            GitHub agent search
    GET  /discover/agents/install-candidates  install commands found in a README
    POST /discover/agents/install           run ONE confirmed, allow-listed command
    GET  /discover/agents/install/{id}      its live log / status
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import agent_discovery
from ..core import agents as agent_backends
from ..core import connectivity, fs_browser
from ..core import events
from ..core import knowledge
from ..core import model_discovery
from ..core import model_recommend
from ..core import models as model_manager
from ..core.agents import paths as agent_paths
from ..core.net_util import DiscoveryError
from ..core.runtime_manager import runtime_manager
from ..storage import db as storage_db
from ..storage.db import Agent, AppSetting
from .http import _agent_to_dict, _model_to_dict, get_db_session, require_token

router = APIRouter(prefix="/api/v1/discover", dependencies=[Depends(require_token)])

MODELS_DIR_SETTING = "models_dir"

_STATUS_FOR_KIND = {
    "bad_input": 400,
    "not_found": 404,
    "gated": 403,
    "rate": 429,
    "network": 502,
    "error": 502,
}


def _raise(exc: DiscoveryError) -> NoReturn:
    raise HTTPException(_STATUS_FOR_KIND.get(exc.kind, 502), str(exc))


def _require_online() -> None:
    if not connectivity.check_internet(force=True):
        raise HTTPException(400, "این بخش نیاز به اتصال اینترنت دارد.")


# ---------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------

class RegisterLocalRequest(BaseModel):
    path: str


class ModelsDirRequest(BaseModel):
    path: str


class ModelDownloadRequest(BaseModel):
    repo_id: str
    filename: str


class AgentInstallRequest(BaseModel):
    repo: str
    command: str
    # The UI sets this only after the user has read the exact command and
    # pressed the confirm button. Requiring it here means a stray/buggy
    # client call can't trigger an install by accident.
    confirmed: bool = False


# ---------------------------------------------------------------------
# OFFLINE: file picker + local registration
# ---------------------------------------------------------------------

@router.get("/fs/list")
def list_directory(
    path: Optional[str] = Query(default=None),
    kind: str = Query(default="any"),
    ext: Optional[str] = Query(default=None, description="comma-separated, e.g. .gguf"),
    executable_only: bool = False,
    hidden: bool = False,
):
    extensions = [e.strip() for e in ext.split(",") if e.strip()] if ext else None
    try:
        return fs_browser.list_directory(
            path, kind=kind, extensions=extensions, executable_only=executable_only, show_hidden=hidden
        )
    except fs_browser.FsBrowseError as exc:
        raise HTTPException(400, str(exc))


@router.post("/models/register-local")
def register_local_model(body: RegisterLocalRequest, db: Session = Depends(get_db_session)):
    """A picked .gguf file is added on its own; a picked folder is scanned
    (same behavior as the existing scan endpoint)."""
    cleaned = body.path.strip().strip('"').strip("'")
    try:
        if Path(cleaned).is_dir():
            models = model_manager.scan_models_folder(db, cleaned)
        else:
            models = [model_manager.register_model_file(db, cleaned)]
    except (NotADirectoryError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    if models:
        storage_db.update_onboarding_state(db, first_model_added=True)
    return [_model_to_dict(m) for m in models]


@router.post("/agents/register-local")
def register_local_agent(body: RegisterLocalRequest, db: Session = Depends(get_db_session)):
    """The user picked an agent's executable. If its file name matches an
    agent backend this app knows how to drive, remember the path (so the
    adapter uses it even when it isn't on PATH) and make sure an Agent
    exists for it."""
    cleaned = body.path.strip().strip('"').strip("'")
    p = Path(cleaned)
    if not p.is_file():
        raise HTTPException(400, f"'{cleaned}' یه فایل معتبر نیست.")

    stem = p.stem.lower()
    adapter = next((a for a in agent_backends.list_adapters() if a.backend_id == stem), None)
    if adapter is None:
        known = "، ".join(a.backend_id for a in agent_backends.list_adapters()) or "—"
        raise HTTPException(
            400,
            f"اسم این فایل با هیچ backend شناخته‌شده‌ای نمی‌خونه. فعلاً فقط اینا پشتیبانی می‌شن: {known}.",
        )
    if os.name != "nt" and not os.access(p, os.X_OK):
        raise HTTPException(400, "این فایل قابل اجرا نیست (executable bit نداره).")

    resolved = str(p.resolve())
    previous = agent_paths.get_path(adapter.backend_id)
    agent_paths.set_path(adapter.backend_id, resolved)
    info = adapter.detect()
    if not info.detected:
        if previous:
            agent_paths.set_path(adapter.backend_id, previous)
        else:
            agent_paths.clear_path(adapter.backend_id)
        raise HTTPException(400, "این فایل به عنوان agent شناسایی نشد.")

    existing = db.query(Agent).filter(Agent.agent_backend == adapter.backend_id).first()
    created = existing is None
    agent = existing
    if created:
        agent = Agent(name=adapter.display_name, agent_backend=adapter.backend_id, config_json={})
        db.add(agent)
        db.commit()
        db.refresh(agent)
        storage_db.update_onboarding_state(db, first_agent_created=True)
        events.emit(
            events.EventType.AGENT_CREATED,
            agent_id=agent.id,
            metadata={"name": agent.name, "agent_backend": agent.agent_backend, "via": "browse"},
        )

    return {
        "agent": _agent_to_dict(agent),
        "created": created,
        "path": resolved,
        "detected_version": info.detected_version,
    }


# ---------------------------------------------------------------------
# Models dir setting (where online downloads land)
# ---------------------------------------------------------------------

def _get_models_dir(db: Session) -> Path:
    row = db.get(AppSetting, MODELS_DIR_SETTING)
    if row and row.value:
        return Path(row.value)
    return Path(storage_db.DEFAULT_DB_PATH).resolve().parent / "models"


@router.get("/models/dir")
def get_models_dir(db: Session = Depends(get_db_session)):
    return {"path": str(_get_models_dir(db))}


@router.put("/models/dir")
def set_models_dir(body: ModelsDirRequest, db: Session = Depends(get_db_session)):
    cleaned = body.path.strip().strip('"').strip("'")
    if not cleaned:
        raise HTTPException(400, "مسیر خالیه.")
    target = Path(cleaned).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"ساختن پوشه ممکن نشد: {exc.strerror or exc}")
    if not os.access(target, os.W_OK):
        raise HTTPException(400, "توی این پوشه اجازه‌ی نوشتن نداریم.")

    resolved = str(target.resolve())
    row = db.get(AppSetting, MODELS_DIR_SETTING)
    if row:
        row.value = resolved
    else:
        db.add(AppSetting(key=MODELS_DIR_SETTING, value=resolved))
    db.commit()
    return {"path": resolved}


# ---------------------------------------------------------------------
# ONLINE: models (Hugging Face)
# ---------------------------------------------------------------------

@router.get("/models/search")
def search_models(q: str = Query(..., min_length=1)):
    _require_online()
    try:
        return model_discovery.search_models(q)
    except DiscoveryError as exc:
        _raise(exc)


@router.get("/models/files")
def list_model_files(repo: str = Query(..., min_length=3)):
    _require_online()
    try:
        files = model_discovery.list_gguf_candidates(repo)
    except DiscoveryError as exc:
        _raise(exc)
    # Judge every file against THIS machine before it is downloaded, and mark the
    # one worth taking. A hardware read failure only costs the badges, never the list.
    try:
        hardware = runtime_manager.get_hardware_snapshot()
    except Exception:  # noqa: BLE001
        hardware = None
    return model_recommend.annotate_and_recommend(files, hardware)


def _register_finished_download(job: model_discovery.DownloadJob) -> dict:
    """Runs on the download thread when a file finishes: adds it to the
    catalog with its Hugging Face link already filled in (which is what
    later lets "verify authenticity" work without the user typing the
    repo by hand)."""
    db = storage_db.SessionLocal()
    try:
        model = model_manager.register_model_file(
            db,
            job.local_path or "",
            hf_repo_id=job.repo_id,
            hf_filename=job.primary_filename,
            quantization=model_discovery.guess_quantization(job.primary_filename),
            param_count=model_discovery.guess_param_count(job.repo_id),
        )
        storage_db.update_onboarding_state(db, first_model_added=True)
        return {"registered": True, "model_id": model.id, "model_name": model.name}
    finally:
        db.close()


@router.post("/models/download")
def start_model_download(body: ModelDownloadRequest, db: Session = Depends(get_db_session)):
    _require_online()
    try:
        candidate = model_discovery.find_candidate(body.repo_id, body.filename)
    except DiscoveryError as exc:
        _raise(exc)

    dest_root = _get_models_dir(db)
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"پوشه‌ی مدل‌ها در دسترس نیست: {exc.strerror or exc}")

    job = model_discovery.download_manager.start(
        body.repo_id, candidate, dest_root, on_complete=_register_finished_download
    )
    return job.to_dict()


@router.get("/downloads")
def list_downloads():
    return [j.to_dict() for j in model_discovery.download_manager.list()]


@router.get("/downloads/{job_id}")
def get_download(job_id: str):
    job = model_discovery.download_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "download not found")
    return job.to_dict()


@router.post("/downloads/{job_id}/cancel")
def cancel_download(job_id: str):
    if not model_discovery.download_manager.cancel(job_id):
        raise HTTPException(404, "download not found or already finished")
    return {"cancelling": True}


# ---------------------------------------------------------------------
# ONLINE: agents (GitHub)
# ---------------------------------------------------------------------

@router.get("/agents/search")
def search_agents(q: str = Query(..., min_length=1)):
    _require_online()
    try:
        return agent_discovery.search_agents(q)
    except DiscoveryError as exc:
        _raise(exc)


class AgentRepoRequest(BaseModel):
    repo: str


def _agent_repo(agent: Agent) -> tuple[Optional[str], Optional[str]]:
    """(repo, where it came from): the one saved on the agent, else the one the
    app knows for its backend, else nothing."""
    saved = (agent.config_json or {}).get("repo")
    if isinstance(saved, str) and saved:
        return saved, "saved"
    known = knowledge.KNOWN_AGENT_REPOS.get(agent.agent_backend)
    return (known, "known") if known else (None, None)


@router.get("/agents/{agent_id}/profile")
def get_agent_profile(agent_id: int, db: Session = Depends(get_db_session)):
    """What GitHub says about this agent (capabilities, licence, activity), each
    fact with its source. `status` is "unlinked" when no repository is known
    yet -- the UI then offers a search -- and "error" when the lookup failed."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    repo, origin = _agent_repo(agent)
    if repo is None:
        return {"agent_id": agent_id, "status": "unlinked", "repo": None, "repo_origin": None, "profile": None, "error": None}
    if not connectivity.check_internet(force=True):
        return {"agent_id": agent_id, "status": "offline", "repo": repo, "repo_origin": origin, "profile": None, "error": None}
    try:
        profile = knowledge.fetch_agent_profile(repo)
    except DiscoveryError as exc:
        return {"agent_id": agent_id, "status": "error", "repo": repo, "repo_origin": origin, "profile": None, "error": str(exc)}
    return {"agent_id": agent_id, "status": "ok", "repo": repo, "repo_origin": origin, "profile": profile, "error": None}


@router.put("/agents/{agent_id}/repo")
def set_agent_repo(agent_id: int, body: AgentRepoRequest, db: Session = Depends(get_db_session)):
    """Remember which GitHub repository an agent is (so its profile can be looked up)."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    repo = body.repo.strip()
    if not agent_discovery.REPO_FULL_NAME_RE.match(repo):
        raise HTTPException(400, "repo must look like owner/name")
    agent.config_json = {**(agent.config_json or {}), "repo": repo}
    db.commit()
    return _agent_to_dict(agent)


@router.get("/models/profile")
def get_model_profile(repo: str = Query(..., min_length=3), filename: Optional[str] = None):
    """The sourced facts for one Hugging Face repo (used by the online search results)."""
    _require_online()
    try:
        return knowledge.fetch_model_profile(repo, filename)
    except DiscoveryError as exc:
        _raise(exc)


@router.get("/agents/install-candidates")
def agent_install_candidates(repo: str = Query(..., min_length=3)):
    _require_online()
    try:
        return agent_discovery.get_install_candidates(repo)
    except DiscoveryError as exc:
        _raise(exc)


@router.post("/agents/install")
def install_agent(body: AgentInstallRequest):
    if not body.confirmed:
        raise HTTPException(400, "نصب فقط بعد از تایید صریح کاربر شروع می‌شه.")
    try:
        job = agent_discovery.install_manager.start(body.repo, body.command)
    except DiscoveryError as exc:
        _raise(exc)
    return job.to_dict()


@router.get("/agents/install/{job_id}")
def get_agent_install(job_id: str):
    job = agent_discovery.install_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "install job not found")
    return job.to_dict()
