"""model_recommend: fit on every file, the single recommended file, and launch settings
-- judged against an 8 GB GPU / 16 GB RAM machine and a CPU-only one."""

from types import SimpleNamespace

from backend.core import hardware_fit, model_recommend as mr

GB = 1024**3


def hw(vram_gb=8, ram_gb=16, gpu=True):
    return {
        "memory": SimpleNamespace(total_mb=ram_gb * 1024),
        "gpu": SimpleNamespace(available=gpu, vram_total_mb=vram_gb * 1024 if gpu else None),
    }


def f(name, gb):
    return {"filename": name, "size_bytes": int(gb * GB)}


def test_every_file_gets_a_verdict_and_unknown_sizes_get_none():
    out = mr.annotate_files([f("a", 4), {"filename": "b", "size_bytes": None}], hw())
    assert out[0]["hardware_fit"]["label"] == hardware_fit.GOOD
    assert out[1]["hardware_fit"] is None and out[1]["gpu_fit"] is None


def test_the_input_files_are_not_mutated():
    files = [f("a", 4)]
    mr.annotate_files(files, hw())
    assert "hardware_fit" not in files[0]


def test_recommends_the_largest_file_that_fits_the_gpu_comfortably():
    files = mr.annotate_and_recommend([f("q2", 3), f("q4", 4.5), f("q8", 8.5)], hw())
    assert [x["filename"] for x in files if x["recommended"]] == ["q4"]


def test_a_gpu_fit_beats_a_bigger_file_that_only_fits_ram():
    # 6.5 GB: tight on the GPU (WARNING) but comfortable in RAM -> overall "GOOD / cpu".
    files = mr.annotate_and_recommend([f("small", 4), f("big", 6.5)], hw())
    big = next(x for x in files if x["filename"] == "big")
    assert big["hardware_fit"]["label"] == hardware_fit.GOOD and big["gpu_fit"] == hardware_fit.WARNING
    assert next(x for x in files if x["recommended"])["filename"] == "small"


def test_a_tight_gpu_fit_is_used_when_nothing_fits_comfortably():
    files = mr.annotate_and_recommend([f("tight", 6.5), f("huge", 30)], hw(ram_gb=8))
    assert next(x for x in files if x["recommended"])["filename"] == "tight"


def test_cpu_only_machine_recommends_the_largest_that_fits_ram():
    files = mr.annotate_and_recommend([f("a", 3), f("b", 7), f("c", 20)], hw(gpu=False, ram_gb=16))
    assert next(x for x in files if x["recommended"])["filename"] == "b"


def test_nothing_is_recommended_when_nothing_fits():
    files = mr.annotate_and_recommend([f("huge", 60)], hw())
    assert not any(x["recommended"] for x in files)


def test_unreadable_hardware_recommends_nothing_instead_of_guessing():
    files = mr.annotate_and_recommend([f("a", 4)], None)
    assert files[0]["hardware_fit"]["label"] == hardware_fit.UNKNOWN and not files[0]["recommended"]


def test_launch_config_offloads_to_gpu_and_takes_the_biggest_context_that_fits():
    out = mr.suggest_launch_config(4 * GB, hw())
    assert out["config"]["n_gpu_layers"] == 999
    assert out["config"]["ctx_size"] == 16384  # 32768 would be only "tight"
    assert out["config"]["jinja"] is True


def test_launch_config_is_capped_by_the_models_trained_context():
    assert mr.suggest_launch_config(4 * GB, hw(), trained_context=8192)["config"]["ctx_size"] == 8192


def test_cpu_only_launch_config_does_not_offload():
    cfg = mr.suggest_launch_config(4 * GB, hw(gpu=False))["config"]
    assert "n_gpu_layers" not in cfg and cfg["ctx_size"] >= 4096


def test_a_model_too_big_for_the_machine_says_so():
    out = mr.suggest_launch_config(60 * GB, hw())
    assert any("too big" in n for n in out["notes"]) and "n_gpu_layers" not in out["config"]


def test_agent_flag_controls_jinja():
    assert "jinja" not in mr.suggest_launch_config(4 * GB, hw(), for_agent=False)["config"]


def test_shared_estimate_matches_the_catalog_heuristic():
    est = hardware_fit.estimate_memory_mb(GB, 4096)
    assert round(est["total_mb"], 1) == round(1024 + 4.096 * 80, 1)
