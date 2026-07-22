"""Tests für oeffentlichevergabe.de BKMSql-Adapter — Parsing-Logik ohne HTTP-Aufrufe."""
from __future__ import annotations

import json
from pathlib import Path

from agent3_tender.sources.oeffentlichevergabe import (
    _cpv_prefixes,
    _date,
    _first_text,
    parse_element,
)

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "oev_sample.json").read_text())
_ELEMENTS = _FIXTURE["elements"]


# ── parse_element ─────────────────────────────────────────────────────────────

def test_parse_cn_element():
    item = parse_element(_ELEMENTS[0])
    assert item is not None
    assert item.source == "oeffentlichevergabe"
    assert "dffb17a9" in item.external_id
    assert "Speditionsleistungen" in item.title
    assert item.country == "DEU"
    assert item.notice_type == "cn"
    assert item.cpv == ["60000000"]
    assert item.buyer == "Stadtwerke München GmbH"
    assert item.deadline is not None
    assert item.deadline.year == 2025


def test_parse_cn_url_contains_notice_id():
    item = parse_element(_ELEMENTS[0])
    assert item is not None
    assert "dffb17a9" in str(item.url)
    assert "oeffentlichevergabe.de" in str(item.url)


def test_parse_can_element():
    item = parse_element(_ELEMENTS[1])
    assert item is not None
    assert item.notice_type == "can"
    assert "Hamburg" in item.title
    assert item.deadline is None  # CAN notices typically have no deadline


def test_parse_missing_notice_id_returns_none():
    item = parse_element(_ELEMENTS[2])
    assert item is None


def test_parse_missing_title_returns_none():
    item = parse_element(_ELEMENTS[3])
    assert item is None


def test_hash_stable():
    item1 = parse_element(_ELEMENTS[0])
    item2 = parse_element(_ELEMENTS[0])
    assert item1 is not None and item2 is not None
    assert item1.hash == item2.hash


def test_external_id_includes_lot():
    item = parse_element(_ELEMENTS[0])
    assert item is not None
    assert "LOT-0001" in item.external_id


def test_lot_id_in_external_id_for_uniqueness():
    # Two lots from same notice would have different external_ids
    el1 = {**_ELEMENTS[0], "lotIdentifier": "LOT-0001"}
    el2 = {**_ELEMENTS[0], "lotIdentifier": "LOT-0002"}
    item1 = parse_element(el1)
    item2 = parse_element(el2)
    assert item1 is not None and item2 is not None
    assert item1.external_id != item2.external_id


# ── _first_text ───────────────────────────────────────────────────────────────

def test_first_text_german_preferred():
    entries = [
        {"value": "English title", "languageId": "ENG"},
        {"value": "Deutscher Titel", "languageId": "DEU"},
    ]
    assert _first_text(entries) == "Deutscher Titel"


def test_first_text_fallback_to_first():
    entries = [{"value": "Only English", "languageId": "ENG"}]
    assert _first_text(entries) == "Only English"


def test_first_text_empty():
    assert _first_text([]) is None
    assert _first_text(None) is None


# ── _date ─────────────────────────────────────────────────────────────────────

def test_date_iso_with_offset():
    dt = _date("2025-04-30T12:00:00+02:00")
    assert dt is not None and dt.month == 4 and dt.day == 30


def test_date_iso_with_z():
    dt = _date("2025-04-30T12:00:00Z")
    assert dt is not None and dt.year == 2025


def test_date_date_only():
    dt = _date("2025-04-30")
    assert dt is not None and dt.day == 30


def test_date_none():
    assert _date(None) is None


def test_date_empty():
    assert _date("") is None


# ── _cpv_prefixes ─────────────────────────────────────────────────────────────

def test_cpv_prefixes_deduplicates():
    codes = ["60000000", "60100000", "63000000", "63100000"]
    prefixes = _cpv_prefixes(codes)
    assert prefixes == ["60", "63"]


def test_cpv_prefixes_single():
    assert _cpv_prefixes(["64120000"]) == ["64"]


def test_cpv_prefixes_sorted():
    codes = ["63000000", "60000000"]
    assert _cpv_prefixes(codes) == ["60", "63"]
