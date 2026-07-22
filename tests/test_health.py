"""Tests for core/health.py — alarm logic based on consecutive below-threshold runs."""
from __future__ import annotations

from datetime import UTC, datetime

from agent3_tender.core.health import check_alarms
from agent3_tender.core.models import Item, Score

_CFG = {
    "health_min_count": {
        "ted": 5,
        "oeffentlichevergabe": 3,
    }
}


def _item(source: str = "ted", n: int = 1) -> Item:
    return Item(
        source=source,
        external_id=f"{source}-{n}",
        title=f"Item {n}",
        country="DEU",
        cpv=[],
        notice_type="cn",
        url=f"https://example.com/{n}",
    )


def _score() -> Score:
    return Score(
        relevance=0.8, profile_spediteur=0.7, profile_kep=0.2,
        reasoning="ok", tags=[],
    )


def _record_run_health(store, source: str, count: int, threshold: int, run_id: int | None = None):
    """Helper: record a health entry directly via the store's internal DB."""
    rid = store.record_run(started_at=datetime(2024, 1, 1, tzinfo=UTC), dry_run=False)
    store._conn.execute(
        "INSERT OR REPLACE INTO health (source, run_id, count, below_threshold) VALUES (?,?,?,?)",
        (source, rid, count, 1 if count < threshold else 0),
    )
    store._conn.commit()
    return rid


# ── no alarm when above threshold ─────────────────────────────────────────────

def test_no_alarm_when_all_above_threshold(store):
    for _ in range(3):
        _record_run_health(store, "ted", count=10, threshold=5)
    alarms = check_alarms(store, _CFG)
    assert alarms == []


# ── alarm after 3 consecutive below ───────────────────────────────────────────

def test_alarm_after_3_consecutive_below(store):
    for _ in range(3):
        _record_run_health(store, "ted", count=2, threshold=5)
    alarms = check_alarms(store, _CFG)
    assert len(alarms) == 1
    assert "ted" in alarms[0]


def test_alarm_message_mentions_3_runs(store):
    for _ in range(3):
        _record_run_health(store, "ted", count=0, threshold=5)
    alarms = check_alarms(store, _CFG)
    assert "3" in alarms[0]


# ── no alarm when only 2 consecutive below ────────────────────────────────────

def test_no_alarm_when_only_2_consecutive(store):
    for _ in range(2):
        _record_run_health(store, "ted", count=2, threshold=5)
    alarms = check_alarms(store, _CFG)
    assert alarms == []


# ── reset when one run recovers ────────────────────────────────────────────────

def test_no_alarm_when_recovery_in_middle(store):
    _record_run_health(store, "ted", count=2, threshold=5)
    _record_run_health(store, "ted", count=10, threshold=5)  # recovery
    _record_run_health(store, "ted", count=2, threshold=5)
    alarms = check_alarms(store, _CFG)
    assert alarms == []


# ── multiple sources ──────────────────────────────────────────────────────────

def test_alarm_only_for_failing_source(store):
    for _ in range(3):
        _record_run_health(store, "ted", count=0, threshold=5)
        _record_run_health(store, "oeffentlichevergabe", count=10, threshold=3)
    alarms = check_alarms(store, _CFG)
    assert len(alarms) == 1
    assert "ted" in alarms[0]


def test_alarm_for_both_failing_sources(store):
    for _ in range(3):
        _record_run_health(store, "ted", count=0, threshold=5)
        _record_run_health(store, "oeffentlichevergabe", count=0, threshold=3)
    alarms = check_alarms(store, _CFG)
    assert len(alarms) == 2


# ── no sources configured ─────────────────────────────────────────────────────

def test_no_alarms_when_no_health_config(store):
    alarms = check_alarms(store, {})
    assert alarms == []
