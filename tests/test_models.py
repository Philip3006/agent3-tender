import pytest
from pydantic import ValidationError

from agent3_tender.core.models import Item, Score


def _make_item(**kwargs) -> Item:
    defaults = dict(
        source="ted",
        external_id="2024/S-001-000001",
        title="Transportdienstleistungen für Bundesbehörde",
        country="DEU",
        url="https://ted.europa.eu/en/notice/2024/S-001-000001",
    )
    defaults.update(kwargs)
    return Item(**defaults)


def test_item_valid():
    item = _make_item()
    assert item.source == "ted"
    assert item.country == "DEU"


def test_item_hash_stable():
    assert _make_item().hash == _make_item().hash


def test_item_hash_differs_by_source():
    a = _make_item(source="ted")
    b = _make_item(source="oeffentlichevergabe")
    assert a.hash != b.hash


def test_item_hash_differs_by_id():
    a = _make_item(external_id="AAA")
    b = _make_item(external_id="BBB")
    assert a.hash != b.hash


def test_item_invalid_url():
    with pytest.raises(ValidationError):
        _make_item(url="not-a-url")


def test_item_invalid_source():
    with pytest.raises(ValidationError):
        _make_item(source="unknown_source")


def test_score_valid():
    s = Score(
        relevance=0.8,
        profile_spediteur=0.9,
        profile_kep=0.4,
        reasoning="Klassischer Sammelladungsverkehr, passt gut.",
        tags=["Regelverkehr"],
    )
    assert s.best == pytest.approx(0.9)


def test_score_best_takes_max():
    s = Score(relevance=0.5, profile_spediteur=0.3, profile_kep=0.8, reasoning="x")
    assert s.best == pytest.approx(0.8)


def test_score_above_1_rejected():
    with pytest.raises(ValidationError):
        Score(relevance=1.5, profile_spediteur=0.5, profile_kep=0.5, reasoning="x")


def test_score_negative_rejected():
    with pytest.raises(ValidationError):
        Score(relevance=0.5, profile_spediteur=-0.1, profile_kep=0.5, reasoning="x")
