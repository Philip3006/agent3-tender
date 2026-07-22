from datetime import UTC, datetime

import pytest

from agent3_tender.core.models import Item, Score
from agent3_tender.core.store import Store


def _item(external_id: str = "X001", deadline: datetime | None = None, source: str = "ted") -> Item:
    return Item(
        source=source,
        external_id=external_id,
        title=f"Test Ausschreibung {external_id}",
        country="DEU",
        deadline=deadline,
        url=f"https://ted.europa.eu/en/notice/{external_id}",
    )


def _score(spediteur: float = 0.7, kep: float = 0.5) -> Score:
    return Score(
        relevance=0.7,
        profile_spediteur=spediteur,
        profile_kep=kep,
        reasoning="Test-Begründung",
        tags=["Test"],
    )


def test_upsert_and_known_hashes(store: Store):
    item = _item()
    assert item.hash not in store.known_hashes()
    store.upsert_item(item)
    assert item.hash in store.known_hashes()


def test_upsert_idempotent(store: Store):
    item = _item()
    store.upsert_item(item)
    store.upsert_item(item)
    assert len(store.known_hashes()) == 1


def test_score_stored_and_retrieved(store: Store):
    item = _item()
    score = _score()
    store.upsert_item(item, score)
    results = store.all_scored_for_digest(min_score=0.0)
    assert len(results) == 1
    assert results[0][1].reasoning == "Test-Begründung"


def test_score_updated_on_upsert(store: Store):
    item = _item()
    store.upsert_item(item)
    score = _score(spediteur=0.9)
    store.upsert_item(item, score)
    results = store.all_scored_for_digest(min_score=0.0)
    assert results[0][1].profile_spediteur == pytest.approx(0.9)


def test_all_scored_sorted_by_deadline(store: Store):
    item_late = _item("B", deadline=datetime(2025, 12, 1, tzinfo=UTC))
    item_early = _item("A", deadline=datetime(2025, 6, 1, tzinfo=UTC))
    store.upsert_item(item_late, _score())
    store.upsert_item(item_early, _score())
    results = store.all_scored_for_digest(min_score=0.0)
    deadlines = [r[0].deadline for r in results]
    assert deadlines == sorted(deadlines)


def test_min_score_filter(store: Store):
    store.upsert_item(_item("HIGH"), _score(spediteur=0.9, kep=0.8))
    store.upsert_item(_item("LOW"), _score(spediteur=0.3, kep=0.2))
    results = store.all_scored_for_digest(min_score=0.55)
    ids = [r[0].external_id for r in results]
    assert "HIGH" in ids
    assert "LOW" not in ids


def test_items_without_score_excluded_from_digest(store: Store):
    store.upsert_item(_item("UNSCORED"))
    results = store.all_scored_for_digest(min_score=0.0)
    assert all(r[0].external_id != "UNSCORED" for r in results)


def test_record_run_creates_and_updates(store: Store):
    now = datetime.now(UTC)
    run_id = store.record_run(started_at=now, dry_run=True)
    assert isinstance(run_id, int)
    returned = store.record_run(
        run_id=run_id,
        started_at=now,
        dry_run=True,
        finished_at=now,
        items_total=10,
        items_new=5,
        items_scored=3,
        items_digest=2,
    )
    assert returned == run_id


def test_health_consecutive_below(store: Store):
    now = datetime.now(UTC)
    for _ in range(3):
        run_id = store.record_run(started_at=now, dry_run=False)
        store.record_health(source="ted", run_id=run_id, count=2, threshold=5)
    assert store.consecutive_below_threshold("ted", n=3) == 3


def test_health_not_3_consecutive(store: Store):
    now = datetime.now(UTC)
    run1 = store.record_run(started_at=now, dry_run=False)
    store.record_health(source="ted", run_id=run1, count=10, threshold=5)  # above
    run2 = store.record_run(started_at=now, dry_run=False)
    store.record_health(source="ted", run_id=run2, count=2, threshold=5)   # below
    run3 = store.record_run(started_at=now, dry_run=False)
    store.record_health(source="ted", run_id=run3, count=2, threshold=5)   # below
    assert store.consecutive_below_threshold("ted", n=3) < 3


def test_health_fewer_than_n_runs(store: Store):
    now = datetime.now(UTC)
    run_id = store.record_run(started_at=now, dry_run=False)
    store.record_health(source="ted", run_id=run_id, count=0, threshold=5)
    # Only 1 run, need 3 → should return 0, not trigger alarm
    assert store.consecutive_below_threshold("ted", n=3) == 0
