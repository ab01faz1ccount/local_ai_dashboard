"""Proof that a runtime really did what Settings says -- built around the REAL
log a user pasted from a CPU-only llama-server that had -ngl 99 configured."""

from backend.core.engine import runtime_inspect as ri
from backend.core.engine.llama_cpp_caps import parse_list_devices

REAL_CPU_LOG = [
    "0.00.012.511 I cmn  common_param: common_params_print_info: verbosity = 3",
    "0.00.020.218 I srv    load_model: loading model 'C:\\Users\\asus\\Downloads\\Qwen3.5-9B-Q4_K_M.gguf'",
    "0.03.220.475 I cmn          init: llama threadpool init, n_threads = 6",
    "0.06.198.225 I srv    load_model: initializing, n_slots = 1, n_ctx_slot = 64000, kv_unified = 'false'",
    "0.06.311.013 I srv  llama_server: listening on http://127.0.0.1:8000",
]

GPU_LOG = [
    "load_backend: loaded CUDA backend from C:\\x\\ggml-cuda.dll",
    "load_backend: loaded CPU backend from C:\\x\\ggml-cpu.dll",
    "0.00.020 I srv    load_model: loading model 'm.gguf'",
    "llama_model_load_from_file_impl: using device CUDA0 (NVIDIA GeForce RTX 5060 Ti) - 15000 MiB free",
    "load_tensors: offloaded 33/33 layers to GPU",
    "load_tensors:        CUDA0 model buffer size =  5300.00 MiB",
    "load_tensors:   CPU_Mapped model buffer size =   300.00 MiB",
    "llama_kv_cache:      CUDA0 KV buffer size =   1000.00 MiB",
    "init: llama threadpool init, n_threads = 6",
    "initializing, n_slots = 1, n_ctx_slot = 16384, kv_unified = 'false'",
]

NO_GPU = {"probed": True, "devices": [], "gpus": []}


def test_parse_real_log():
    o = ri.parse_startup_log(REAL_CPU_LOG)
    assert o["threads"] == 6 and o["slots"] == 1 and o["ctx_per_slot"] == 64000


def test_gpu_log_is_gpu_with_kv_on_gpu():
    o = ri.parse_startup_log(GPU_LOG)
    assert (o["layers_offloaded"], o["layers_total"]) == (33, 33)
    assert ri.placement(o) == ri.GPU
    assert o["kv_mib"]["gpu"] == 1000.0 and o["kv_mib"]["cpu"] == 0.0
    assert o["devices_used"] and "CUDA0" in o["devices_used"][0]
    assert "CUDA" in o["backends_loaded"]


def test_zero_layers_offloaded_is_cpu():
    assert ri.placement(ri.parse_startup_log(["offloaded 0/33 layers to GPU"])) == ri.CPU


def test_partial_offload():
    assert ri.placement(ri.parse_startup_log(["offloaded 20/33 layers to GPU"])) == ri.PARTIAL


def test_cpu_only_backend_without_offload_line_is_cpu():
    assert ri.placement(ri.parse_startup_log(["load_backend: loaded CPU backend from x"])) == ri.CPU


def test_nothing_to_go_on_is_unknown_not_cpu():
    assert ri.placement(ri.parse_startup_log(REAL_CPU_LOG)) == ri.PLACEMENT_UNKNOWN


def test_the_users_case_ngl99_but_cpu_only_build_is_flagged():
    cfg = {"n_gpu_layers": 99, "ctx_size": 16384, "threads": 6, "parallel_slots": 1}
    cmd = ["llama-server", "--n-gpu-layers", "99", "--ctx-size", "16384", "--threads", "6", "--parallel", "1"]
    rep = ri.build_report(config=cfg, command=cmd, log_lines=REAL_CPU_LOG, devices=NO_GPU)
    assert not rep["ok"]
    assert any("list-devices" in w for w in rep["warnings"])
    by = {c["key"]: c for c in rep["checks"]}
    assert by["n_gpu_layers -> command line"]["status"] == ri.OK  # it WAS sent
    assert by["ctx_size -> server"]["status"] == ri.MISMATCH  # 64000 != 16384
    assert by["ctx_size -> server"]["actual"] == 64000
    assert by["threads -> server"]["status"] == ri.OK
    assert by["model on GPU"]["status"] == ri.UNKNOWN


def test_flag_missing_from_command_line_is_caught():
    rep = ri.build_report(config={"n_gpu_layers": 99}, command=["llama-server"], log_lines=[])
    c = rep["checks"][0]
    assert c["status"] == ri.MISMATCH and c["actual"] is None


def test_healthy_gpu_run_is_ok():
    cfg = {"n_gpu_layers": 99, "ctx_size": 16384, "threads": 6, "parallel_slots": 1}
    cmd = ["s", "--n-gpu-layers", "99", "--ctx-size", "16384", "--threads", "6", "--parallel", "1"]
    rep = ri.build_report(config=cfg, command=cmd, log_lines=GPU_LOG)
    assert rep["ok"], rep
    assert rep["placement"] == ri.GPU


def test_props_is_preferred_and_total_ctx_is_per_slot_times_slots():
    props = {"total_slots": 2, "default_generation_settings": {"n_ctx": 8192}}
    rep = ri.build_report(config={"ctx_size": 16384, "parallel_slots": 2}, command=None, log_lines=[], props=props)
    by = {c["key"]: c for c in rep["checks"]}
    assert by["ctx_size -> server"]["status"] == ri.OK
    assert by["parallel_slots -> server"]["status"] == ri.OK


def test_cpu_requested_means_no_gpu_complaint():
    rep = ri.build_report(config={"n_gpu_layers": 0}, command=None, log_lines=["offloaded 0/33 layers to GPU"])
    assert rep["warnings"] == []


def test_kv_cache_in_ram_with_gpu_model_warns():
    log = ["offloaded 33/33 layers to GPU", "CUDA0 model buffer size = 5000.00 MiB", "CPU KV buffer size = 2000.00 MiB"]
    rep = ri.build_report(config={"n_gpu_layers": 99}, command=None, log_lines=log)
    assert any("KV" in w for w in rep["warnings"])


def test_partial_offload_is_a_mismatch_with_a_warning():
    rep = ri.build_report(config={"n_gpu_layers": 99}, command=None, log_lines=["offloaded 20/33 layers to GPU"])
    assert rep["placement"] == ri.PARTIAL and not rep["ok"] and rep["warnings"]


def test_list_devices_parsing():
    txt = "Available devices:\n  CUDA0: NVIDIA GeForce RTX 5060 Ti (16310 MiB, 15000 MiB free)\n"
    devs = parse_list_devices(txt)
    assert [d["id"] for d in devs] == ["CUDA0"] and devs[0]["is_gpu"]
    assert parse_list_devices("Available devices:\n") == []
    assert parse_list_devices("error: invalid argument: --list-devices\n") == []
