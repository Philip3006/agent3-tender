from datetime import UTC, datetime

from agent3_tender.core.dedupe import _is_cross_source_duplicate, _normalize, filter_new
from agent3_tender.core.models import Item
from agent3_tender.core.store import Store


def _item(
    source="ted",
    external_id="X001",
    title="Transportdienstleistungen Berlin",
    buyer="Bundesamt",
    country="DEU",
    deadline: datetime | None = None,
) -> Item:
    return Item(
        source=source,
        external_id=external_id,
        title=title,
        buyer=buyer,
        country=country,
        deadline=deadline,
        url=f"https://ted.europa.eu/en/notice/{external_id}",
    )


def test_normalize_strips_diacritics():
    assert _normalize("Müller") == "muller"
    assert _normalize("Straßenverkehr") == "strassenverkehr"
    assert _normalize("Österreich") == "osterreich"


def test_normalize_collapses_whitespace():
    assert _normalize("  Hallo   Welt  ") == "hallo welt"


def test_normalize_none():
    assert _normalize(None) == ""


def test_filter_new_returns_unseen(store: Store):
    items = [_item(external_id="A1"), _item(external_id="A2")]
    assert len(filter_new(items, store)) == 2


def test_filter_new_skips_seen(store: Store):
    item = _item()
    store.upsert_item(item)
    assert filter_new([item], store) == []


def test_filter_new_deduplicates_within_batch(store: Store):
    item = _item()
    assert len(filter_new([item, item], store)) == 1


def test_cross_source_fuzzy_match_hits(store: Store):
    ted_item = _item(
        source="ted",
        external_id="TED001",
        title="Transportdienstleistungen Postdienst Berlin",
        buyer="Bundespost GmbH",
        deadline=datetime(2025, 6, 30, tzinfo=UTC),
    )
    store.upsert_item(ted_item)

    oev_item = _item(
        source="oeffentlichevergabe",
        external_id="OEV001",
        title="Transportdienstleistungen Postdienst Berlin",
        buyer="Bundespost GmbH",
        deadline=datetime(2025, 6, 30, tzinfo=UTC),
    )
    assert _is_cross_source_duplicate(oev_item, store.all_for_fuzzy())


def test_cross_source_no_false_positive(store: Store):
    ted_item = _item(
        source="ted",
        external_id="TED002",
        title="Reinigungsdienstleistungen Bundesministerium",
        buyer="Bundesministerium Finanzen",
        deadline=datetime(2025, 9, 30, tzinfo=UTC),
    )
    store.upsert_item(ted_item)

    oev_item = _item(
        source="oeffentlichevergabe",
        external_id="OEV002",
        title="Transportdienstleistungen Bundesbahn Hamburg",
        buyer="Deutsche Bahn AG",
        deadline=datetime(2025, 9, 30, tzinfo=UTC),
    )
    assert not _is_cross_source_duplicate(oev_item, store.all_for_fuzzy())


def test_cross_source_same_source_skipped(store: Store):
    item1 = _item(source="ted", external_id="T1", title="Transport X")
    store.upsert_item(item1)
    item2 = _item(source="ted", external_id="T2", title="Transport X")
    assert not _is_cross_source_duplicate(item2, store.all_for_fuzzy())
