"""The easy-connect API: auto-free policy, custom-agent check, launch plan, quick connect,
custom agents through the normal agent routes, and fit on online model files."""

import itertools
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import connect, discovery, http
from backend.core import onboarding
from backend.core.engine.base import RuntimeState
from backend.core.security import get_or_create_access_token
from backend.storage import db as storage_db
from backend.storage.db import Agent, MLModel, Runtime

AUTH = {"Authorization": f"Bearer {get_or_create_access_token()}"}
GB = 1024**3
HW = {"memory": SimpleNamespace(total_mb=16384), "gpu": SimpleNamespace(available=True, vram_total_mb=8192)}
_PORTS = itertools.count(9100)
SPEC = {"name": "Mine", "executable": "mycli", "config": {"mode": "env_only"}, "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}"}}


@pytest.fixture(scope="module", autouse=True)
def _init():
    storage_db.init_db()


class FakeManager:
    """Stands in for the real RuntimeManager: no process is ever spawned."""

    def __init__(self, fail=None):
        self.fail = fail
        self.started = []
        self.stopped = []

    def get_hardware_snapshot(self):
        return HW

    def effective_context(self, runtime):
        return None

    def start(self, db, runtime):
        self.started.append(runtime.id)
        if self.fail:
            runtime.status = "ERROR"
            db.commit()
            return SimpleNamespace(state=RuntimeState.ERROR, endpoint=None, error_message=self.fail)
        runtime.status = "ONLINE"
        db.commit()
        return SimpleNamespace(state=RuntimeState.ONLINE, endpoint="http://127.0.0.1/v1", error_message=None)


@pytest.fixture
def manager(monkeypatch):
    fake = FakeManager()
    for mod in (connect, discovery, http):
        monkeypatch.setattr(mod, "runtime_manager", fake)
    monkeypatch.setattr(onboarding, "detect_llama_cpp", lambda: "/x/llama-server")
    return fake


@pytest.fixture
def client(manager):
    app = FastAPI()
    app.include_router(http.router)
    app.include_router(connect.router)
    app.include_router(discovery.router)
    return TestClient(app)


def make_model(size_gb=4):
    with storage_db.SessionLocal() as db:
        m = MLModel(name=f"M-{uuid.uuid4().hex[:6]}", file_path=f"/m/{uuid.uuid4().hex}.gguf", file_size_bytes=int(size_gb * GB))
        db.add(m)
        db.commit()
        return m.id


def make_runtime(model_id=None, status="OFFLINE"):
    with storage_db.SessionLocal() as db:
        rt = Runtime(name="r", executable_path="/x/llama-server", port=next(_PORTS), model_id=model_id, status=status)
        db.add(rt)
        db.commit()
        return rt.id


def make_agent(backend="custom_cli", config=None):
    with storage_db.SessionLocal() as db:
        a = Agent(name=f"A-{uuid.uuid4().hex[:5]}", agent_backend=backend, config_json=config if config is not None else {"cli_spec": SPEC})
        db.add(a)
        db.commit()
        return a.id


# -- auth ---------------------------------------------------------------------

def test_every_connect_route_requires_the_token(client):
    for r in connect.router.routes:
        path = r.path.replace("{runtime_id}", "1").replace("{model_id}", "1")
        for method in r.methods - {"HEAD", "OPTIONS"}:
            assert client.request(method, path).status_code == 401, f"{method} {path}"


# -- auto-free ----------------------------------------------------------------

def test_auto_free_defaults_to_off_and_round_trips(client):
    rid = make_runtime()
    assert client.get(f"/api/v1/connect/runtimes/{rid}/auto-free", headers=AUTH).json()["mode"] == "off"
    r = client.put(f"/api/v1/connect/runtimes/{rid}/auto-free", headers=AUTH, json={"mode": "idle", "idle_minutes": 5})
    assert r.status_code == 200 and r.json()["idle_minutes"] == 5
    assert client.get(f"/api/v1/connect/runtimes/{rid}/auto-free", headers=AUTH).json() == r.json()


def test_auto_free_rejects_bad_policies_and_unknown_runtimes(client):
    rid = make_runtime()
    for body in ({"mode": "sometimes"}, {"mode": "idle", "idle_minutes": 0}, {"mode": "idle", "idle_minutes": 10**6}):
        assert client.put(f"/api/v1/connect/runtimes/{rid}/auto-free", headers=AUTH, json=body).status_code == 400
    assert client.put("/api/v1/connect/runtimes/999999/auto-free", headers=AUTH, json={"mode": "off"}).status_code == 404
    assert client.get("/api/v1/connect/runtimes/999999/auto-free", headers=AUTH).status_code == 404


def test_a_corrupt_stored_policy_reads_as_off(client):
    rid = make_runtime()
    with storage_db.SessionLocal() as db:
        db.add(storage_db.AppSetting(key=f"auto_free.{rid}", value="{broken"))
        db.commit()
    assert client.get(f"/api/v1/connect/runtimes/{rid}/auto-free", headers=AUTH).json()["mode"] == "off"


def test_the_reaper_reads_the_saved_policy(client):
    rid = make_runtime()
    client.put(f"/api/v1/connect/runtimes/{rid}/auto-free", headers=AUTH, json={"mode": "after_agent", "grace_minutes": 3})
    with storage_db.SessionLocal() as db:
        assert connect.read_policy(db, rid)["mode"] == "after_agent"


# -- custom agent check -------------------------------------------------------

def test_presets_are_listed(client):
    ids = [p["id"] for p in client.get("/api/v1/connect/custom-agent/presets", headers=AUTH).json()]
    assert "openai_env" in ids


def test_checking_a_spec_previews_without_leaking_the_key(client):
    r = client.post("/api/v1/connect/custom-agent/validate", headers=AUTH, json={"spec": SPEC})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["preview"]["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:8080/v1"
    assert body["preview"]["env"]["OPENAI_API_KEY"] == "***"
    assert "sk-example" not in r.text


def test_a_bad_spec_is_a_400_with_the_reason(client):
    r = client.post("/api/v1/connect/custom-agent/validate", headers=AUTH, json={"spec": {"name": "x"}})
    assert r.status_code == 400 and "executable" in r.text


# -- custom agents through the normal agent routes -----------------------------

def test_creating_a_custom_agent_validates_and_normalizes_its_spec(client):
    r = client.post("/api/v1/agents", headers=AUTH, json={"name": "c1", "agent_backend": "custom_cli", "config_json": {"cli_spec": SPEC}})
    assert r.status_code == 200 and r.json()["config_json"]["cli_spec"]["version_args"] == ["--version"]


def test_a_custom_agent_without_a_spec_or_with_a_bad_one_is_refused(client):
    assert client.post("/api/v1/agents", headers=AUTH, json={"name": "c2", "agent_backend": "custom_cli"}).status_code == 400
    bad = {"cli_spec": {"name": "x", "executable": "x", "config": {"mode": "magic"}}}
    assert client.post("/api/v1/agents", headers=AUTH, json={"name": "c3", "agent_backend": "custom_cli", "config_json": bad}).status_code == 400


def test_an_unknown_backend_is_still_refused(client):
    assert client.post("/api/v1/agents", headers=AUTH, json={"name": "c4", "agent_backend": "nope"}).status_code == 400


def test_editing_the_spec_replaces_it_instead_of_merging(client):
    aid = make_agent(config={"cli_spec": SPEC})
    new = {**SPEC, "env": {"ONLY": "{model}"}}
    r = client.patch(f"/api/v1/agents/{aid}", headers=AUTH, json={"config_json": {"cli_spec": new}})
    assert r.status_code == 200 and list(r.json()["config_json"]["cli_spec"]["env"]) == ["ONLY"]


def test_switching_an_agent_to_custom_cli_is_allowed(client):
    aid = make_agent(backend="generic", config={})
    assert client.patch(f"/api/v1/agents/{aid}", headers=AUTH, json={"agent_backend": "custom_cli"}).status_code == 200


def test_backends_list_offers_the_custom_one(client):
    assert "custom_cli" in [b["backend_id"] for b in client.get("/api/v1/agent-backends", headers=AUTH).json()]


# -- launch plan + quick connect ----------------------------------------------

def test_launch_plan_is_read_only_and_hardware_derived(client, manager):
    mid = make_model(4)
    before = len(client.get("/api/v1/runtimes", headers=AUTH).json())
    r = client.post(f"/api/v1/connect/models/{mid}/launch-plan", headers=AUTH)
    assert r.status_code == 200
    cfg = r.json()["runtime"]["config_json"]
    assert cfg["n_gpu_layers"] == 999 and cfg["ctx_size"] == 16384 and cfg["jinja"] is True
    assert len(client.get("/api/v1/runtimes", headers=AUTH).json()) == before and manager.started == []


def test_no_llama_server_anywhere_is_a_clear_400(client, monkeypatch):
    monkeypatch.setattr(onboarding, "detect_llama_cpp", lambda: None)
    with storage_db.SessionLocal() as db:
        db.query(Runtime).delete()
        db.commit()
    r = client.post(f"/api/v1/connect/models/{make_model()}/launch-plan", headers=AUTH)
    assert r.status_code == 400 and "llama-server" in r.text


def test_quick_connect_creates_starts_and_wires_the_agent(client, manager):
    mid, aid = make_model(), make_agent()
    r = client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": mid, "agent_id": aid})
    body = r.json()
    assert r.status_code == 200 and body["created_runtime"] and body["started"] and body["runtime"]["status"] == "ONLINE"
    assert body["agent"]["default_runtime_id"] == body["runtime"]["id"]
    assert body["agent"]["config_json"]["configured_runtime_id"] == body["runtime"]["id"]
    assert body["verification"]["ok"] is True
    assert body["endpoint"]["base_url"].endswith("/v1") and "sk-" not in r.text


def test_quick_connect_reuses_the_runtime_that_already_serves_the_model(client, manager):
    mid = make_model()
    first = client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": mid}).json()
    manager.started.clear()
    second = client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": mid}).json()
    assert second["created_runtime"] is False and second["runtime"]["id"] == first["runtime"]["id"]
    assert manager.started == []  # already online: not started twice


def test_a_failed_start_does_not_configure_the_agent(client, manager):
    manager.fail = "port busy"
    mid, aid = make_model(), make_agent()
    body = client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": mid, "agent_id": aid}).json()
    assert body["started"] is False and "port busy" in body["error"]
    assert body["agent"]["default_runtime_id"] is None and body["commands_ran"] == []


def test_quick_connect_refuses_unknown_ids_and_label_only_agents(client):
    mid = make_model()
    assert client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": 999999}).status_code == 404
    assert client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": mid, "agent_id": 999999}).status_code == 404
    generic = make_agent(backend="generic", config={})
    assert client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": mid, "agent_id": generic}).status_code == 400


def test_a_second_new_runtime_gets_a_different_port(client, manager):
    a = client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": make_model()}).json()
    b = client.post("/api/v1/connect/quick", headers=AUTH, json={"model_id": make_model()}).json()
    assert a["runtime"]["port"] != b["runtime"]["port"]


# -- fit on online files --------------------------------------------------------

def test_online_model_files_carry_fit_and_one_recommendation(client, monkeypatch):
    from backend.core import connectivity, model_discovery
    monkeypatch.setattr(connectivity, "check_internet", lambda force=False: True)
    files = [
        {"filename": "q4.gguf", "size_bytes": int(4 * GB)},
        {"filename": "q8.gguf", "size_bytes": int(30 * GB)},
        {"filename": "unknown.gguf", "size_bytes": None},
    ]
    monkeypatch.setattr(model_discovery, "list_gguf_candidates", lambda repo: files)
    out = client.get("/api/v1/discover/models/files?repo=a/b", headers=AUTH).json()
    by = {f["filename"]: f for f in out}
    assert by["q4.gguf"]["recommended"] and by["q4.gguf"]["hardware_fit"]["label"] == "GOOD"
    assert by["q8.gguf"]["hardware_fit"]["label"] == "NOT_RECOMMENDED" and not by["q8.gguf"]["recommended"]
    assert by["unknown.gguf"]["hardware_fit"] is None
    assert sum(f["recommended"] for f in out) == 1


def test_an_unreadable_machine_still_lists_the_files(client, manager, monkeypatch):
    from backend.core import connectivity, model_discovery
    monkeypatch.setattr(connectivity, "check_internet", lambda force=False: True)
    monkeypatch.setattr(model_discovery, "list_gguf_candidates", lambda repo: [{"filename": "a.gguf", "size_bytes": GB}])
    monkeypatch.setattr(manager, "get_hardware_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("no sensors")))
    out = client.get("/api/v1/discover/models/files?repo=a/b", headers=AUTH).json()
    assert out[0]["hardware_fit"]["label"] == "UNKNOWN" and out[0]["recommended"] is False
