import time

from kalico_monitor.events import Alert
from kalico_monitor.storage import Outbox


def test_atomic_outbox_dedup_and_reopen(tmp_path):
    path = tmp_path / "monitor.sqlite3"
    store = Outbox(path, "PEEK-01")
    state = {"version": 1, "generation": 1, "state": "complete"}
    alert = Alert(kind="complete", generation=1, filename="job.gcode",
                  severity="info", detail="Print finished")
    store.transition(state, [alert, alert])
    assert len(store.pending()) == 1
    pending_id = store.pending()[0]["id"]
    store.close()
    store = Outbox(path, "PEEK-01")
    assert store.get_state() == state
    assert store.pending()[0]["id"] == pending_id
    store.delivered(pending_id)
    assert store.pending() == []
    assert store.summary()["delivered"] == 1
    store.close()


def test_retry_is_scheduled_not_discarded(tmp_path):
    store = Outbox(tmp_path / "alerts.sqlite3", "printer")
    alert = Alert(kind="error", generation=1, filename="", severity="critical", detail="bad")
    key = store.enqueue(alert)
    store.failed(key, "timeout")
    assert store.pending() == []
    future = store.pending(now=time.time() + 120)
    assert len(future) == 1 and future[0]["attempts"] == 1
    store.close()
