from app.queue import EventQueue


def test_dedup_by_event_id(tmp_path):
    q = EventQueue(tmp_path / "q.db")
    assert q.enqueue("Ev1", {"a": 1}) is True
    assert q.enqueue("Ev1", {"a": 1}) is False  # Slack retry
    assert q.depth() == 1


def test_durable_across_restart(tmp_path):
    EventQueue(tmp_path / "q.db").enqueue("Ev1", {"a": 1})
    job = EventQueue(tmp_path / "q.db").claim()
    assert job and job.event_id == "Ev1" and job.payload == {"a": 1}


def test_bounded_retry_then_dead(tmp_path):
    q = EventQueue(tmp_path / "q.db")
    q.enqueue("Ev1", {})
    statuses = []
    for _ in range(3):
        q._conn.execute("UPDATE events SET next_at=0")  # bỏ qua backoff trong test
        job = q.claim()
        statuses.append(q.fail(job, "boom", max_attempts=3))
    assert statuses == ["pending", "pending", "dead"]
    q._conn.execute("UPDATE events SET next_at=0")
    assert q.claim() is None


def test_expired_lease_is_reclaimed(tmp_path):
    q = EventQueue(tmp_path / "q.db", lease_seconds=-1)  # worker "chết" ngay
    q.enqueue("Ev1", {})
    assert q.claim().attempts == 1
    assert q.claim().attempts == 2
