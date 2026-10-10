import threading

from backend.core import chat_tasks


def _clean():
    for cid in list(chat_tasks._running):
        chat_tasks._running.pop(cid)


def test_cancel_sets_every_running_turn_of_that_chat_only():
    _clean()
    a, b, other = threading.Event(), threading.Event(), threading.Event()
    chat_tasks.register(1, a)
    chat_tasks.register(1, b)
    chat_tasks.register(2, other)
    assert chat_tasks.cancel_chat(1) == 2
    assert a.is_set() and b.is_set() and not other.is_set()
    _clean()


def test_cancel_with_nothing_running_is_a_no_op():
    _clean()
    assert chat_tasks.cancel_chat(99) == 0


def test_unregister_forgets_a_finished_turn_so_a_late_delete_does_not_touch_it():
    _clean()
    ev = threading.Event()
    chat_tasks.register(5, ev)
    chat_tasks.unregister(5, ev)
    assert chat_tasks.running_count(5) == 0
    assert chat_tasks.cancel_chat(5) == 0 and not ev.is_set()
    chat_tasks.unregister(5, ev)  # twice is harmless


def test_a_running_worker_really_stops_when_its_chat_is_cancelled():
    _clean()
    ev = threading.Event()
    chat_tasks.register(7, ev)
    stopped = threading.Event()

    def worker():
        while not ev.is_set():
            ev.wait(0.01)
        stopped.set()

    threading.Thread(target=worker, daemon=True).start()
    chat_tasks.cancel_chat(7)
    assert stopped.wait(2)
    _clean()
