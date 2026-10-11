"""idle_policy: the stop decision, policy validation, the session registry and the reaper loop."""

import json

import pytest

from backend.core import idle_policy as ip

T = 10_000.0


def d(policy, **kw):
    base = dict(now=T, last_active=T, terminals_open=0, busy=False, agent_seen=False, last_agent_close=None)
    base.update(kw)
    return ip.decide(policy, **base)


IDLE = {"mode": "idle", "idle_minutes": 10}
AFTER = {"mode": "after_agent", "grace_minutes": 2}


# -- decide -------------------------------------------------------------------

def test_off_never_stops():
    assert not d({"mode": "off"}, last_active=T - 10**6).stop


def test_idle_stops_only_once_the_limit_is_reached():
    assert not d(IDLE, last_active=T - 599).stop
    assert d(IDLE, last_active=T - 600).stop


def test_an_open_terminal_or_a_busy_server_blocks_every_mode():
    for pol in (IDLE, AFTER):
        assert not d(pol, last_active=T - 10**6, terminals_open=1, agent_seen=True, last_agent_close=T - 10**6).stop
        assert not d(pol, last_active=T - 10**6, busy=True, agent_seen=True, last_agent_close=T - 10**6).stop


def test_unknown_busy_state_is_treated_as_active():
    assert not d(IDLE, last_active=T - 10**6, busy=None).stop


def test_after_agent_waits_for_the_grace_period_after_the_last_close():
    kw = dict(agent_seen=True)
    assert not d(AFTER, last_agent_close=T - 119, **kw).stop
    assert d(AFTER, last_agent_close=T - 120, **kw).stop


def test_after_agent_never_stops_a_runtime_no_agent_used():
    assert not d(AFTER, agent_seen=False, last_agent_close=None, last_active=T - 10**6).stop


# -- policy -------------------------------------------------------------------

def test_default_policy_is_off():
    assert ip.normalize_policy(None)["mode"] == "off"


def test_policy_validation():
    assert ip.normalize_policy({"mode": "idle", "idle_minutes": 5})["idle_minutes"] == 5
    for bad in ({"mode": "sometimes"}, {"mode": "idle", "idle_minutes": 0}, {"mode": "idle", "idle_minutes": "5"},
                {"mode": "idle", "idle_minutes": True}, {"mode": "idle", "idle_minutes": 10**6}, "idle"):
        with pytest.raises(ip.PolicyError):
            ip.normalize_policy(bad)


def test_a_corrupt_stored_policy_means_off():
    assert ip.parse_stored("{not json")["mode"] == "off"
    assert ip.parse_stored(json.dumps({"mode": "nope"}))["mode"] == "off"
    assert ip.parse_stored(None)["mode"] == "off"
    assert ip.parse_stored(json.dumps(AFTER))["mode"] == "after_agent"


# -- registry -----------------------------------------------------------------

def test_registry_counts_and_never_goes_negative():
    clock = [5.0]
    r = ip.AgentSessionRegistry(clock=lambda: clock[0])
    r.opened(1); r.opened(1)
    r.closed(1)
    assert r.open_count(1) == 1 and r.last_close(1) == 5.0
    r.closed(1); r.closed(1)
    assert r.open_count(1) == 0
    assert r.seen(1)


def test_forget_resets_for_the_next_start():
    r = ip.AgentSessionRegistry()
    r.opened(1); r.closed(1)
    r.forget(1)
    assert not r.seen(1) and r.last_close(1) is None


# -- reaper -------------------------------------------------------------------

class Harness:
    def __init__(self, policy, busy=False):
        self.now = 1000.0
        self.online = [1]
        self.policy = policy
        self.busy = busy
        self.stopped = []
        self.reg = ip.AgentSessionRegistry(clock=lambda: self.now)
        self.reaper = ip.IdleReaper(
            list_online=lambda: list(self.online), get_policy=lambda rid: self.policy,
            is_busy=lambda rid: self.busy, stop_runtime=lambda rid, why: self.stopped.append((rid, why)),
            registry=self.reg, clock=lambda: self.now,
        )

    def advance(self, seconds):
        self.now += seconds
        return self.reaper.tick()


def test_idle_runtime_is_stopped_after_the_limit_with_a_reason():
    h = Harness(IDLE)
    assert h.advance(0) == []          # first sighting counts as activity
    assert h.advance(599) == []
    assert h.advance(2) == [1]
    assert h.stopped[0][0] == 1 and "idle" in h.stopped[0][1]


def test_activity_resets_the_idle_clock():
    h = Harness(IDLE)
    h.advance(0)
    h.advance(500)
    h.busy = True
    h.advance(10)                      # busy: clock reset to now
    h.busy = False
    assert h.advance(500) == []
    assert h.advance(101) == [1]


def test_an_open_agent_terminal_keeps_the_runtime_alive_then_the_grace_period_applies():
    h = Harness(AFTER)
    h.reg.opened(1)
    assert h.advance(10_000) == []     # terminal open: never
    h.reg.closed(1)
    assert h.advance(60) == []
    assert h.advance(61) == [1]


def test_off_policy_never_stops_and_never_asks_whether_busy():
    h = Harness({"mode": "off"})
    h.reaper._is_busy = lambda rid: (_ for _ in ()).throw(AssertionError("must not poll"))
    assert h.advance(10**6) == []


def test_a_failing_stop_does_not_block_the_others_or_crash_the_loop():
    h = Harness(IDLE)
    h.online = [1, 2]
    calls = []

    def stop(rid, why):
        calls.append(rid)
        if rid == 1:
            raise RuntimeError("boom")

    h.reaper._stop_runtime = stop
    h.advance(0)
    assert h.advance(700) == [2] and calls == [1, 2]


def test_a_runtime_that_stopped_elsewhere_is_forgotten():
    h = Harness(AFTER)
    h.reg.opened(1); h.reg.closed(1)
    h.advance(0)
    h.online = []
    h.advance(1)
    assert not h.reg.seen(1)
