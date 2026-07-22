"""Tests für TED v3 Adapter — Parsing-Logik ohne echte HTTP-Aufrufe."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent3_tender.sources.ted import _build_query, _cpv, _date, _title, parse_notice

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ted_sample.json").read_text())
_NOTICES = _FIXTURE["notices"]

_BASE_CFG = {
    "countries": ["DEU", "AUT", "CHE"],
    "cpv": ["60000000", "64120000"],
    "lookback_days": 4,
    "notice_types": ["cn", "can", "pin"],
}


# ── parse_notice ──────────────────────────────────────────────────────────────

def test_parse_cn_standard():
    item = parse_notice(_NOTICES[0])
    assert item is not None
    assert item.source == "ted"
    assert item.external_id == "2024/S-001-000001"
    assert "Transportdienstleistungen" in item.title
    assert item.country == "DEU"
    assert item.notice_type == "cn"
    assert item.cpv == ["60000000"]
    assert item.buyer == "Bundesamt für Güterverkehr"
    assert item.deadline is not None
    assert item.deadline.year == 2025
    assert str(item.url).startswith("https://ted.europa.eu/en/notice/")
    assert "2024/S-001-000001" in str(item.url)
    assert item.signal_kind is None


def test_parse_austria_notice():
    item = parse_notice(_NOTICES[1])
    assert item is not None
    assert item.country == "AUT"
    assert item.notice_type == "cn"
    assert len(item.cpv) == 2
    assert "64120000" in item.cpv


def test_parse_can_without_deadline_gets_retender_signal():
    item = parse_notice(_NOTICES[2])
    assert item is not None
    assert item.notice_type == "can"
    assert item.deadline is None
    assert item.signal_kind == "re_tender"
    assert item.country == "CHE"


def test_parse_missing_pub_number_returns_none():
    item = parse_notice(_NOTICES[3])
    assert item is None


def test_parse_empty_title_returns_none():
    item = parse_notice(_NOTICES[4])
    assert item is None


def test_hash_stable():
    item1 = parse_notice(_NOTICES[0])
    item2 = parse_notice(_NOTICES[0])
    assert item1 is not None and item2 is not None
    assert item1.hash == item2.hash


def test_url_never_from_llm():
    """URL muss aus dem Rohdatensatz konstruiert sein, nicht halluziniert."""
    for notice in _NOTICES[:3]:
        item = parse_notice(notice)
        if item is not None:
            assert str(item.url).startswith("https://ted.europa.eu/en/notice/")
            assert item.external_id in str(item.url)


# ── Helper-Funktionen ─────────────────────────────────────────────────────────

def test_title_picks_german_first():
    assert _title({"deu": "Deutsch", "eng": "English"}) == "Deutsch"
    assert _title({"eng": "Only English"}) == "Only English"
    assert _title("Plain string") == "Plain string"
    assert _title({}) == ""
    assert _title(None) == ""


def test_cpv_extraction():
    raw = [{"code": "60000000"}, {"cpvCode": "63000000"}, "64120000"]
    assert _cpv(raw) == ["60000000", "63000000", "64120000"]
    assert _cpv([]) == []
    assert _cpv("not-a-list") == []


def test_date_with_timezone():
    dt = _date("2025-06-30T12:00:00Z")
    assert dt is not None
    assert dt.year == 2025
    assert dt.month == 6
    assert dt.tzinfo is not None


def test_date_without_time():
    dt = _date("2024-03-15")
    assert dt is not None
    assert dt.day == 15


def test_date_none():
    assert _date(None) is None
    assert _date("") is None


# ── Query-Builder ─────────────────────────────────────────────────────────────

def test_query_contains_all_countries():
    q = _build_query(_BASE_CFG)
    assert "DEU" in q
    assert "AUT" in q
    assert "CHE" in q


def test_query_contains_cpv():
    q = _build_query(_BASE_CFG)
    assert "60000000" in q
    assert "64120000" in q


def test_query_contains_date_filter():
    q = _build_query(_BASE_CFG)
    assert "PUBLICATION-DATE >=" in q


def test_query_contains_notice_types():
    q = _build_query(_BASE_CFG)
    assert "NOTICE-TYPE" in q
    assert "cn-standard" in q
    assert "can-standard" in q
