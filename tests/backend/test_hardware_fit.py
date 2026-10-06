"""
tests/backend/test_hardware_fit.py

core/hardware_fit.py: every threshold edge of the pure verdict function,
GPU-vs-CPU selection, the RAM reserve, and the comparison/API wiring.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.core import comparison, hardware_fit as hf
from backend.core.platform.base import CpuMetrics, GpuMetrics, MemoryMetrics
from backend.core.security import get_or_create_access_token
from backend.storage import db as storage_db
from backend.storage.db import MLModel

AUTH = {"Authorization": f"Bearer {get_or_create_access_token()}"}


def fit(estimated, ram=16384.0, gpu=False, vram=None):
    return hf.assess_fit(estimated, ram_total_mb=ram, gpu_available=gpu, vram_total_mb=vram)


# ---------------------------------------------------------------------
# RAM reserve
# ---------------------------------------------------------------------

def test_ram_reserve_is_2gb_on_a_big_machine():
    assert hf.ram_capacity_mb(16384) == 16384 - 2048


def test_ram_reserve_is_20_percent_on_a_small_machine():
    assert hf.ram_capacity_mb(4096) == pytest.approx(4096 * 0.8)


def test_ram_capacity_never_negative():
    assert hf.ram_capacity_mb(0) == 0


# ---------------------------------------------------------------------
# bands (CPU only): capacity = 16384 - 2048 = 14336
# ---------------------------------------------------------------------

CAP = 14336.0


@pytest.mark.parametrize(
    "ratio,label",
    [(0.10, hf.GOOD), (0.70, hf.GOOD), (0.7001, hf.WARNING), (0.90, hf.WARNING),
     (0.9001, hf.LIKELY_TO_EXCEED), (1.00, hf.LIKELY_TO_EXCEED), (1.0001, hf.NOT_RECOMMENDED), (3.0, hf.NOT_RECOMMENDED)],
)
def test_band_edges(ratio, label):
    assert fit(CAP * ratio)["label"] == label


def test_zero_estimate_is_good():
    assert fit(0)["label"] == hf.GOOD


def test_result_reports_ratio_capacity_and_target():
    r = fit(CAP * 0.5)
    assert r["target"] == "cpu" and r["capacity_mb"] == CAP and r["ratio"] == 0.5 and r["estimated_mb"] == round(CAP * 0.5, 1)


def test_reason_mentions_percentage_and_ram():
    r = fit(CAP * 0.5)
    assert "50%" in r["reason"] and "RAM" in r["reason"]


def test_likely_to_exceed_reason_explains_why():
    assert "go over" in fit(CAP * 0.95)["reason"]


def test_not_recommended_reason_says_it_exceeds():
    r = fit(CAP * 2)
    assert "exceeds" in r["reason"] and "RAM" in r["reason"]


# ---------------------------------------------------------------------
# GPU vs CPU selection
# ---------------------------------------------------------------------

def test_fits_in_vram_is_judged_against_vram():
    r = fit(2000, gpu=True, vram=8192)
    assert r["target"] == "gpu" and r["label"] == hf.GOOD and r["capacity_mb"] == 8192


def test_not_fitting_vram_but_fitting_ram_falls_back_to_cpu_with_a_speed_warning():
    r = fit(10000, ram=32768, gpu=True, vram=8192)
    assert r["target"] == "cpu" and r["label"] == hf.GOOD
    assert "VRAM" in r["reason"] and "CPU" in r["reason"]


def test_fits_neither_is_not_recommended_and_mentions_vram():
    r = fit(40000, ram=16384, gpu=True, vram=8192)
    assert r["label"] == hf.NOT_RECOMMENDED
    assert "VRAM" in r["reason"]


def test_gpu_preferred_on_a_tie():
    # 4000 MB: GOOD on both an 8192 MB GPU and 16 GB RAM -> GPU wins the tie
    assert fit(4000, gpu=True, vram=8192)["target"] == "gpu"


def test_better_label_wins_even_if_it_is_the_cpu():
    # tight on VRAM (WARNING), comfortable on RAM (GOOD)
    r = fit(7000, ram=65536, gpu=True, vram=8192)
    assert r["target"] == "cpu" and r["label"] == hf.GOOD


def test_gpu_flag_false_ignores_vram_numbers():
    r = fit(2000, gpu=False, vram=99999)
    assert r["target"] == "cpu"


def test_gpu_available_but_no_vram_total_is_cpu_only():
    assert fit(2000, gpu=True, vram=None)["target"] == "cpu"
    assert fit(2000, gpu=True, vram=0)["target"] == "cpu"


# ---------------------------------------------------------------------
# unknown hardware
# ---------------------------------------------------------------------

@pytest.mark.parametrize("ram", [None, 0, -5])
def test_no_ram_reading_is_unknown(ram):
    r = hf.assess_fit(1000, ram_total_mb=ram, gpu_available=False, vram_total_mb=None)
    assert r["label"] == hf.UNKNOWN and r["target"] is None


def test_assess_from_snapshot_uses_the_real_dataclasses():
    snap = {"cpu": CpuMetrics(percent=1, core_count=4), "memory": MemoryMetrics(used_mb=1, total_mb=16384, percent=1),
            "gpu": GpuMetrics(available=True, utilization_percent=0, vram_used_mb=0, vram_total_mb=8192)}
    assert hf.assess_from_snapshot(2000, snap)["target"] == "gpu"


@pytest.mark.parametrize("bad", [None, {}, {"memory": None, "gpu": None}, "nope"])
def test_assess_from_snapshot_tolerates_garbage(bad):
    assert hf.assess_from_snapshot(1000, bad)["label"] == hf.UNKNOWN


# ---------------------------------------------------------------------
# comparison + API wiring
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    fresh_engine = storage_db.make_engine(str(tmp_path / "t.sqlite3"))
    monkeypatch.setattr(storage_db, "engine", fresh_engine)
    monkeypatch.setattr(
        storage_db, "SessionLocal", sessionmaker(bind=fresh_engine, autoflush=False, expire_on_commit=False, future=True)
    )
    storage_db.init_db(fresh_engine)
    from backend.core.events import device as device_module

    device_module._reset_cache_for_tests()
    yield


def make_model(size_mb=4000, ctx=4096, name="m") -> int:
    with storage_db.SessionLocal() as s:
        m = MLModel(name=name, file_path=f"/models/{name}.gguf", file_size_bytes=int(size_mb * 1024 * 1024), context_length=ctx)
        s.add(m)
        s.commit()
        return m.id


SNAP = {"cpu": CpuMetrics(percent=1, core_count=4), "memory": MemoryMetrics(used_mb=1, total_mb=16384, percent=1),
        "gpu": GpuMetrics(available=False)}


def test_compare_rows_carry_a_hardware_fit(monkeypatch):
    from backend.core import connectivity

    monkeypatch.setattr(connectivity, "check_internet", lambda *a, **k: False)
    mid = make_model(size_mb=4000)
    with storage_db.SessionLocal() as s:
        out = comparison.compare_models(s, [mid], hardware=SNAP)
    row = out["rows"][0]
    est = row["estimated_system_impact"]["estimated_total_mb"]
    assert row["hardware_fit"] == hf.assess_from_snapshot(est, SNAP)
    assert row["hardware_fit"]["label"] == hf.GOOD


def test_compare_without_hardware_is_unknown_not_an_error(monkeypatch):
    from backend.core import connectivity

    monkeypatch.setattr(connectivity, "check_internet", lambda *a, **k: False)
    mid = make_model()
    with storage_db.SessionLocal() as s:
        assert comparison.compare_models(s, [mid])["rows"][0]["hardware_fit"]["label"] == hf.UNKNOWN


def test_a_huge_model_is_not_recommended_in_the_comparison(monkeypatch):
    from backend.core import connectivity

    monkeypatch.setattr(connectivity, "check_internet", lambda *a, **k: False)
    mid = make_model(size_mb=40000)
    with storage_db.SessionLocal() as s:
        assert comparison.compare_models(s, [mid], hardware=SNAP)["rows"][0]["hardware_fit"]["label"] == hf.NOT_RECOMMENDED


@pytest.fixture
def client(monkeypatch):
    from backend.core.metrics import metrics_recorder
    from backend.core.runtime_manager import runtime_manager

    monkeypatch.setattr(metrics_recorder, "start", lambda: None)
    monkeypatch.setattr(metrics_recorder, "stop", lambda: None)
    monkeypatch.setattr(runtime_manager, "get_hardware_snapshot", lambda: SNAP)
    from backend.main import app

    with TestClient(app) as c:
        yield c


def test_fit_endpoint(client):
    mid = make_model(size_mb=4000)
    r = client.get(f"/api/v1/models/{mid}/fit", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["hardware_fit"]["label"] == "GOOD" and body["hardware_fit"]["target"] == "cpu"
    assert body["estimated_system_impact"]["estimated_base_mb"] == 4000.0


def test_fit_endpoint_unknown_model_is_404(client):
    assert client.get("/api/v1/models/999999/fit", headers=AUTH).status_code == 404


def test_fit_endpoint_requires_token(client):
    assert client.get("/api/v1/models/1/fit").status_code == 401


def test_compare_endpoint_includes_the_fit(client, monkeypatch):
    from backend.core import connectivity

    monkeypatch.setattr(connectivity, "check_internet", lambda *a, **k: False)
    mid = make_model(size_mb=4000)
    r = client.post("/api/v1/models/compare", headers=AUTH, json={"model_ids": [mid]})
    assert r.json()["rows"][0]["hardware_fit"]["label"] == "GOOD"
