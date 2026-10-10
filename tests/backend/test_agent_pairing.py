"""
tests/backend/test_agent_pairing.py

core/agents/pairing.py -- the server-side proof that an agent really talks to
a runtime: watch llama-server's own request log for inference requests that
arrive after we started watching.
"""

from backend.core.agents.pairing import PairingMonitor, is_inference_request_line, new_lines_since

REQ = "srv  log_server_r: request: POST /v1/chat/completions 127.0.0.1 200"


def _noise(n, start=0):
    return [f"slot update_slots: id  0 | task {i} | prompt processing progress" for i in range(start, start + n)]


# ---------- which lines count ----------

def test_inference_request_lines_match():
    for line in [
        REQ,
        "request: POST /v1/chat/completions 127.0.0.1 200",
        "request: POST /completion 127.0.0.1 200",
        "request: POST /completions 127.0.0.1 200",
        "request: POST /v1/completions 127.0.0.1 200",
        "request: POST /chat/completions 127.0.0.1 200",
        "request: POST /v1/responses 127.0.0.1 200",
    ]:
        assert is_inference_request_line(line), line


def test_polls_and_the_dashboards_own_calls_do_not_count():
    for line in [
        "request: GET /slots 127.0.0.1 200",
        "request: GET /health 127.0.0.1 200",
        "request: GET /props 127.0.0.1 200",
        "request: POST /tokenize 127.0.0.1 200",  # the context inspector
        "request: POST /v1/embeddings 127.0.0.1 200",
        "request: GET /v1/models 127.0.0.1 200",
        "main: server is listening on http://127.0.0.1:8080",
    ]:
        assert not is_inference_request_line(line), line


# ---------- incremental window reading ----------

def test_new_lines_when_window_grows():
    prev = _noise(30)
    cur = prev + ["a", "b"]
    assert new_lines_since(prev, cur) == ["a", "b"]


def test_new_lines_when_window_has_rotated():
    prev = _noise(50)
    cur = prev[10:] + ["x", "y", "z"]  # oldest 10 dropped, 3 appended
    assert new_lines_since(prev, cur) == ["x", "y", "z"]


def test_no_new_lines_when_nothing_changed():
    prev = _noise(30)
    assert new_lines_since(prev, list(prev)) == []


def test_unalignable_windows_return_none_instead_of_guessing():
    assert new_lines_since(_noise(30), _noise(30, start=500)) is None  # fully rotated / restarted
    assert new_lines_since(_noise(30), []) is None


def test_empty_previous_means_everything_is_new():
    assert new_lines_since([], ["a", "b"]) == ["a", "b"]


def test_one_repeated_line_cannot_fake_an_alignment():
    # previous ends with the same line text that appears early in current;
    # a 1-line overlap must not be accepted.
    prev = _noise(10) + ["same"]
    cur = ["same"] + _noise(20, start=900)
    assert new_lines_since(prev, cur) is None


# ---------- the monitor ----------

def test_not_watching_until_started():
    m = PairingMonitor()
    assert m.poll(1, 1, [])["status"] == "not_watching"


def test_waiting_then_paired_on_first_new_request():
    m = PairingMonitor()
    base = _noise(10)
    m.start(1, 2, base)
    assert m.poll(1, 2, base)["status"] == "waiting"
    after = base + _noise(3, start=100) + [REQ]
    r = m.poll(1, 2, after)
    assert r["status"] == "paired" and r["requests"] == 1 and r["first_request_at"] is not None


def test_requests_logged_before_watching_started_do_not_count():
    m = PairingMonitor()
    base = _noise(5) + [REQ] + _noise(5, start=50)  # an old request already in the log
    m.start(1, 2, base)
    assert m.poll(1, 2, base)["status"] == "waiting"


def test_unrelated_traffic_after_start_does_not_pair():
    m = PairingMonitor()
    base = _noise(10)
    m.start(1, 2, base)
    cur = base + ["request: GET /slots 127.0.0.1 200", "request: POST /tokenize 127.0.0.1 200"]
    assert m.poll(1, 2, cur)["status"] == "waiting"


def test_counts_each_new_request_and_does_not_double_count_on_repoll():
    m = PairingMonitor()
    base = _noise(10)
    m.start(1, 2, base)
    cur = base + [REQ] + _noise(2, start=60) + [REQ]
    assert m.poll(1, 2, cur)["requests"] == 2
    assert m.poll(1, 2, cur)["requests"] == 2  # same window again: nothing new


def test_paired_is_latched_even_after_the_log_window_rotates_away():
    m = PairingMonitor()
    base = _noise(10)
    m.start(1, 2, base)
    m.poll(1, 2, base + [REQ])
    rotated = _noise(40, start=700)  # the request line is long gone
    r = m.poll(1, 2, rotated)
    assert r["status"] == "paired" and r["requests"] == 1 and r["rebaselined"] is True


def test_runtime_restart_rebaselines_without_false_pairing():
    m = PairingMonitor()
    m.start(1, 2, _noise(30))
    # restarted runtime: brand-new log whose startup lines happen to contain a request
    r = m.poll(1, 2, ["main: starting"] + _noise(5, start=900) + [REQ])
    assert r["status"] == "waiting" and r["rebaselined"] is True


def test_watches_are_independent_per_agent_and_runtime():
    m = PairingMonitor()
    base = _noise(10)
    m.start(1, 1, base)
    m.start(2, 1, base)
    m.poll(1, 1, base + [REQ])
    assert m.poll(1, 1, base + [REQ])["status"] == "paired"
    assert m.poll(2, 1, base)["status"] == "waiting"


def test_restarting_a_watch_resets_it():
    m = PairingMonitor()
    base = _noise(10)
    m.start(1, 1, base)
    m.poll(1, 1, base + [REQ])
    m.start(1, 1, base + [REQ])
    assert m.poll(1, 1, base + [REQ])["status"] == "waiting"


def test_stop_forgets_the_watch_and_carries_verification():
    m = PairingMonitor()
    m.start(1, 1, [], verification={"ok": True})
    assert m.poll(1, 1, [])["verification"] == {"ok": True}
    m.stop(1, 1)
    assert m.poll(1, 1, [])["status"] == "not_watching"


# ---- "in flight": the server is mid-request but hasn't logged it yet ----

def test_busy_server_with_no_logged_request_is_in_flight_not_waiting():
    m = PairingMonitor()
    m.start(1, 1, ["a", "b", "c"])
    assert m.poll(1, 1, ["a", "b", "c"], busy=True)["status"] == "in_flight"
    assert m.poll(1, 1, ["a", "b", "c"], busy=False)["status"] == "waiting"
    assert m.poll(1, 1, ["a", "b", "c"])["status"] == "waiting"  # unknown busy-ness


def test_a_logged_request_beats_in_flight():
    m = PairingMonitor()
    m.start(1, 1, ["a", "b", "c"])
    res = m.poll(1, 1, ["a", "b", "c", "slot done", "POST /v1/chat/completions 200"], busy=True)
    assert res["status"] == "paired"


# ---- newer llama-server: no HTTP line, requests appear as slot tasks (REAL log lines) ----

SLOT_START = "0.29.944.195 I slot launch_slot_: id  0 | task 5 | processing task, is_child = 0"
SLOT_NOISE = [
    "0.29.943.751 I slot get_availabl: id  0 | task -1 | selected slot by LRU, t_last = -1",
    "0.34.595.663 I slot print_timing: id  0 | task 5 | prompt processing, n_tokens =    241, progress = 0.94",
    "0.36.663.102 I slot      release: id  0 | task 5 | stop processing: n_tokens = 264, truncated = 0",
]


def test_slot_task_line_counts_as_a_request():
    assert is_inference_request_line(SLOT_START)


def test_other_slot_lines_do_not_count():
    assert not any(is_inference_request_line(l) for l in SLOT_NOISE)


def test_pairs_on_a_slot_task_even_with_no_http_line_in_the_log():
    m = PairingMonitor()
    base = _noise(10)
    m.start(1, 2, base)
    r = m.poll(1, 2, base + [SLOT_START] + SLOT_NOISE)
    assert r["status"] == "paired" and r["requests"] == 1
