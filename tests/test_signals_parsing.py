"""Tests für sources/signals.py — Parsing-Logik und Web-News-Items ohne HTTP-Aufrufe."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent3_tender.sources.signals import (
    _buyer_str,
    _cpv_list,
    _fetch_ams_jobs,
    _fetch_ba_jobs,
    _fetch_retender,
    _parse_date,
    _title_str,
    _web_news_items,
)

# ── _parse_date ───────────────────────────────────────────────────────────────

def test_parse_date_iso_with_z():
    dt = _parse_date("2025-04-15T10:00:00Z")
    assert dt is not None
    assert dt.year == 2025 and dt.month == 4 and dt.day == 15


def test_parse_date_iso_no_tz():
    dt = _parse_date("2025-06-01T00:00:00")
    assert dt is not None
    assert dt.year == 2025 and dt.month == 6


def test_parse_date_date_only():
    dt = _parse_date("2025-12-31")
    assert dt is not None
    assert dt.year == 2025 and dt.month == 12 and dt.day == 31


def test_parse_date_none():
    assert _parse_date(None) is None


def test_parse_date_empty_string():
    assert _parse_date("") is None


def test_parse_date_invalid():
    assert _parse_date("not-a-date") is None


# ── _title_str ────────────────────────────────────────────────────────────────

def test_title_str_dict_german_first():
    assert _title_str({"deu": "Deutsch", "eng": "English"}) == "Deutsch"


def test_title_str_dict_fallback_to_eng():
    assert _title_str({"eng": "English", "fra": "Francais"}) == "English"


def test_title_str_plain_string():
    assert _title_str("Logistik GmbH") == "Logistik GmbH"


def test_title_str_none():
    assert _title_str(None) == ""


# ── _buyer_str ────────────────────────────────────────────────────────────────

def test_buyer_str_list_with_dict():
    raw = [{"name": "Bundesamt", "roles": ["buyer"]}]
    assert _buyer_str(raw) == "Bundesamt"


def test_buyer_str_list_of_strings():
    assert _buyer_str(["Stadtwerke München"]) == "Stadtwerke München"


def test_buyer_str_plain_string():
    assert _buyer_str("Beschaffungsamt") == "Beschaffungsamt"


def test_buyer_str_none():
    assert _buyer_str(None) is None


# ── _cpv_list ─────────────────────────────────────────────────────────────────

def test_cpv_list_dict_entries():
    raw = [{"code": "60000000"}, {"cpvCode": "63000000"}]
    assert _cpv_list(raw) == ["60000000", "63000000"]


def test_cpv_list_string_entries():
    assert _cpv_list(["60000000", "63000000"]) == ["60000000", "63000000"]


def test_cpv_list_empty():
    assert _cpv_list([]) == []


def test_cpv_list_not_a_list():
    assert _cpv_list("60000000") == []


# ── _web_news_items ───────────────────────────────────────────────────────────

def test_web_news_items_not_empty():
    items = _web_news_items()
    assert len(items) > 0


def test_web_news_items_have_signal_kind():
    for item in _web_news_items():
        assert item.signal_kind == "web_news"


def test_web_news_items_source_is_signals():
    for item in _web_news_items():
        assert item.source == "signals"


def test_web_news_items_have_url():
    for item in _web_news_items():
        assert item.url is not None


def test_web_news_items_include_switzerland():
    countries = {item.country for item in _web_news_items()}
    assert "CHE" in countries


def test_web_news_items_have_deadline():
    for item in _web_news_items():
        assert item.deadline is not None


# ── _fetch_retender (mocked HTTP) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_retender_returns_items():
    fake_response = {
        "notices": [
            {
                "publication-number": "123456-2021",
                "notice-title": {"deu": "Transportvertrag DACH"},
                "buyer-name": [{"name": "Bundesministerium"}],
                "publication-date": "2021-07-01",
                "notice-type": "can-standard",
                "cpv-list": [{"code": "60000000"}],
                "country-code": "DEU",
            }
        ]
    }
    mock = AsyncMock(return_value=fake_response)
    cfg = {"retender_years": 4, "countries": ["DEU"], "cpv": ["60000000"]}
    with patch("agent3_tender.sources.signals.post_json", new=mock):
        items = await _fetch_retender(cfg)
    assert len(items) == 1
    assert items[0].signal_kind == "re_tender"
    assert items[0].source == "signals"
    assert "Re-Tender-Prognose" in items[0].title
    assert items[0].external_id.startswith("retender-")


@pytest.mark.asyncio
async def test_fetch_retender_skips_missing_pub_number():
    fake_response = {
        "notices": [{"notice-title": {"deu": "Kein Pub-Nr"}, "country-code": "DEU"}]
    }
    mock = AsyncMock(return_value=fake_response)
    with patch("agent3_tender.sources.signals.post_json", new=mock):
        items = await _fetch_retender({})
    assert items == []


@pytest.mark.asyncio
async def test_fetch_retender_api_error_returns_empty():
    with patch(
        "agent3_tender.sources.signals.post_json",
        new=AsyncMock(side_effect=Exception("network error")),
    ):
        items = await _fetch_retender({})
    assert items == []


# ── _fetch_ba_jobs (mocked HTTP) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_ba_jobs_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("BA_JOBS_API_KEY", raising=False)
    items = await _fetch_ba_jobs({})
    assert items == []


@pytest.mark.asyncio
async def test_fetch_ba_jobs_returns_items(monkeypatch):
    monkeypatch.setenv("BA_JOBS_API_KEY", "test-key")
    token_resp = {"access_token": "mock-token"}
    jobs_resp = {
        "stellenangebote": [
            {
                "refnr": "REF-001",
                "titel": "Logistikleiter (m/w/d)",
                "arbeitgeber": "Spedition GmbH",
                "aktuelleVeroeffentlichungsdatum": "2025-04-01",
            }
        ]
    }

    call_count = 0

    async def fake_get_json(url, *, params=None, headers=None):
        nonlocal call_count
        call_count += 1
        if "oauth" in url:
            return token_resp
        return jobs_resp

    with patch("agent3_tender.sources.signals.get_json", new=fake_get_json):
        items = await _fetch_ba_jobs({})

    assert any(item.external_id == "ba-job-REF-001" for item in items)
    assert any(item.signal_kind == "job" for item in items)
    assert any(item.country == "DEU" for item in items)


# ── _fetch_ams_jobs (mocked HTTP) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_ams_jobs_returns_items():
    jobs_resp = {
        "content": [
            {
                "id": "AT-9876",
                "title": "Speditionsleiter",
                "company": "Österreich Logistik GmbH",
                "publishedAt": "2025-03-15T00:00:00Z",
                "url": "https://jobs.ams.or.at/jobs/AT-9876",
            }
        ]
    }

    with patch("agent3_tender.sources.signals.get_json", new=AsyncMock(return_value=jobs_resp)):
        items = await _fetch_ams_jobs({})

    assert any(item.external_id == "ams-job-AT-9876" for item in items)
    assert any(item.country == "AUT" for item in items)
    assert any(item.signal_kind == "job" for item in items)


@pytest.mark.asyncio
async def test_fetch_ams_jobs_skips_missing_id():
    jobs_resp = {"content": [{"title": "Kein ID"}]}
    with patch("agent3_tender.sources.signals.get_json", new=AsyncMock(return_value=jobs_resp)):
        items = await _fetch_ams_jobs({})
    assert items == []


@pytest.mark.asyncio
async def test_fetch_ams_jobs_api_error_returns_empty():
    with patch(
        "agent3_tender.sources.signals.get_json",
        new=AsyncMock(side_effect=Exception("timeout")),
    ):
        items = await _fetch_ams_jobs({})
    assert items == []
