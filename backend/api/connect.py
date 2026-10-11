"""
backend/api/connect.py

The "easy connect" surface: everything needed to go from "I have a model and
an agent" to "they are talking", without hand-editing flags or config files.

  GET  /connect/custom-agent/presets      starting points for describing any CLI
  POST /connect/custom-agent/validate     check a spec, and preview what it would write
  GET  /connect/runtimes/{id}/auto-free   when this runtime frees its memory
  PUT  /connect/runtimes/{id}/auto-free
  POST /connect/quick                     model (+ agent) -> runtime created, started, wired
  POST /connect/models/{id}/launch-plan   the settings quick-connect WOULD use (no side effects)

Which settings and which agent are decided by data (the hardware snapshot and
the agent's spec), not by code that knows a particular model or agent.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import agents as agent_backends
from ..core import endpoint as endpoint_mod
from ..core import events, idle_policy, onboarding, quick_connect
from ..core.agents.generic_cli import PRESETS
from ..core.engine.base import RuntimeState
from ..core.runtime_manager import runtime_manager
from ..storage import db as storage_db
from ..storage.db import Agent, AppSetting, MLModel, Runtime
from .http import AgentConfigureRequest, _agent_to_dict, _runtime_to_dict, configure_agent, get_db_session, require_token

router = APIRouter(prefix="/api/v1/connect", dependencies=[Depends(require_token)])

SAMPLE_BASE_URL = "http://127.0.0.1:8080/v1"


# -- custom agent specs ------------------------------------------------------

class SpecRequest(BaseModel):
    spec: dict


@router.get("/custom-agent/presets")
def custom_agent_presets():
    return [{"id": k, **v} for k, v in PRESETS.items()]


@router.post("/custom-agent/validate")
def validate_custom_agent(body: SpecRequest):
    """Never writes anything: reports whether the spec is valid, whether the CLI
    is installed, and exactly which settings / variables it would produce for a
    sample endpoint (secrets masked)."""
    try:
        adapter = agent_backends.GenericCliAdapter(body.spec)
    except agent_backends.SpecError as exc:
        raise HTTPException(400, str(exc))
    info = adapter.detect()
    settings = adapter.expected_settings(SAMPLE_BASE_URL, "sk-example", "my-model", 8192)
    env = adapter.launch_env("sk-example", base_url=SAMPLE_BASE_URL, model_name="my-model", context_length=8192)

    def mask(d: dict, sensitive_keys: set) -> dict:
        return {k: ("***" if k in sensitive_keys or "sk-example" in str(v) else v) for k, v in d.items()}

    return {
        "ok": True,
        "spec": adapter.spec,
        "detected": info.detected,
        "detected_path": info.detected_path,
        "detected_version": info.detected_version,
        "preview": {"settings": mask(settings, set(adapter.sensitive_settings)), "env": mask(env, set())},
    }


# -- auto-free policy --------------------------------------------------------

def _policy_key(runtime_id: int) -> str:
    return f"auto_free.{runtime_id}"


def read_policy(db: Session, runtime_id: int) -> dict:
    row = db.get(AppSetting, _policy_key(runtime_id))
    return idle_policy.parse_stored(row.value if row else None)


class AutoFreeBody(BaseModel):
    mode: str
    grace_minutes: Optional[float] = None
    idle_minutes: Optional[float] = None


@router.get("/runtimes/{runtime_id}/auto-free")
def get_auto_free(runtime_id: int, db: Session = Depends(get_db_session)):
    if db.get(Runtime, runtime_id) is None:
        raise HTTPException(404, "runtime not found")
    return read_policy(db, runtime_id)


@router.put("/runtimes/{runtime_id}/auto-free")
def put_auto_free(runtime_id: int, body: AutoFreeBody, db: Session = Depends(get_db_session)):
    if db.get(Runtime, runtime_id) is None:
        raise HTTPException(404, "runtime not found")
    try:
        policy = idle_policy.normalize_policy(body.model_dump(exclude_none=True))
    except idle_policy.PolicyError as exc:
        raise HTTPException(400, str(exc))
    row = db.get(AppSetting, _policy_key(runtime_id))
    if row is None:
        db.add(AppSetting(key=_policy_key(runtime_id), value=json.dumps(policy)))
    else:
        row.value = json.dumps(policy)
    db.commit()
    return policy


def build_reaper() -> idle_policy.IdleReaper:
    """The background loop main.py starts. Each callback opens its own short
    session: the loop runs on its own thread, outside any request."""

    def list_online() -> list[int]:
        with storage_db.SessionLocal() as db:
            ids = []
            for rt in db.query(Runtime).all():
                if runtime_manager.get_status(db, rt).state == RuntimeState.ONLINE:
                    ids.append(rt.id)
            return ids

    def get_policy(rid: int) -> dict:
        with storage_db.SessionLocal() as db:
            return read_policy(db, rid)

    def is_busy(rid: int) -> Optional[bool]:
        with storage_db.SessionLocal() as db:
            rt = db.get(Runtime, rid)
            return runtime_manager.is_busy(rt) if rt is not None else None

    def stop_runtime(rid: int, reason: str) -> None:
        with storage_db.SessionLocal() as db:
            rt = db.get(Runtime, rid)
            if rt is not None:
                runtime_manager.stop(db, rt, reason=f"auto_free: {reason}")

    return idle_policy.IdleReaper(
        list_online=list_online, get_policy=get_policy, is_busy=is_busy, stop_runtime=stop_runtime
    )


# -- quick connect -----------------------------------------------------------

class QuickConnectBody(BaseModel):
    model_id: int
    agent_id: Optional[int] = None
    start: bool = True


def _plan(db: Session, model: MLModel, for_agent: bool) -> dict:
    existing = db.query(Runtime).all()
    try:
        exe = quick_connect.pick_executable([r.executable_path for r in existing], onboarding.detect_llama_cpp())
        return quick_connect.runtime_for_model(
            model_name=model.name,
            file_size_bytes=model.file_size_bytes,
            trained_context=model.context_length,
            hardware=runtime_manager.get_hardware_snapshot(),
            executable_path=exe,
            used_ports={r.port for r in existing},
            for_agent=for_agent,
        )
    except quick_connect.QuickConnectError as exc:
        raise HTTPException(400, str(exc))


@router.post("/models/{model_id}/launch-plan")
def launch_plan(model_id: int, for_agent: bool = True, db: Session = Depends(get_db_session)):
    """Read-only preview of what quick-connect would create."""
    model = db.get(MLModel, model_id)
    if model is None:
        raise HTTPException(404, "model not found")
    return _plan(db, model, for_agent)


@router.post("/quick")
def quick(body: QuickConnectBody, db: Session = Depends(get_db_session)):
    model = db.get(MLModel, body.model_id)
    if model is None:
        raise HTTPException(404, "model not found")
    agent = None
    if body.agent_id is not None:
        agent = db.get(Agent, body.agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        if agent.agent_backend == "generic":
            raise HTTPException(400, "این agent یه backend واقعی نداره — اول یکی رو توی تنظیماتش انتخاب کن.")

    # Reuse the runtime that already serves this model instead of piling up duplicates.
    runtime = db.query(Runtime).filter(Runtime.model_id == model.id).order_by(Runtime.id).first()
    plan = None
    created = False
    if runtime is None:
        plan = _plan(db, model, for_agent=agent is not None)
        runtime = Runtime(model_id=model.id, **plan["runtime"])
        db.add(runtime)
        db.commit()
        db.refresh(runtime)
        storage_db.update_onboarding_state(db, first_runtime_configured=True)
        events.emit(events.EventType.RUNTIME_REGISTERED, runtime_id=runtime.id, metadata={"name": runtime.name, "via": "quick_connect"})
        created = True

    result: dict = {"created_runtime": created, "plan": plan, "started": False, "error": None}
    if body.start and runtime.status != RuntimeState.ONLINE.value:
        status = runtime_manager.start(db, runtime)
        result["started"] = status.state == RuntimeState.ONLINE
        result["error"] = status.error_message
        db.refresh(runtime)

    # Only wire the agent to a runtime that is really up: a config pointing at a dead
    # server is the "looks connected, isn't" state this app exists to prevent.
    if agent is not None and runtime.status == RuntimeState.ONLINE.value:
        configured = configure_agent(agent.id, AgentConfigureRequest(runtime_id=runtime.id), db)
        result["agent"] = configured["agent"]
        result["commands_ran"] = configured["commands_ran"]
        result["verification"] = configured["verification"]
    elif agent is not None:
        result["agent"] = _agent_to_dict(agent)
        result["commands_ran"] = []
        result["verification"] = None
        result["error"] = result["error"] or "The runtime is not online, so the agent was not configured."

    result["runtime"] = _runtime_to_dict(runtime)
    result["endpoint"] = endpoint_mod.for_runtime(runtime, model.name).public_dict()
    return result
