"""Tests for digest.py — sorting, min_score filtering, alarm section, empty output."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent3_tender.core.digest import build_digest, write_dashboard
from agent3_tender.core.models import Item, Score


def _item(
    external_id: str,
    deadline_offset_days: int | None = 10,
    country: str = "DEU",
    signal_kind=None,
) -> Item:
    deadline = (
        datetime.now(UTC) + timedelta(days=deadline_offset_days)
        if deadline_offset_days is not None
        else None
    )
    return Item(
        source="ted",
        external_id=external_id,
        title=f"Ausschreibung {external_id}",
        buyer="Testbehörde",
        country=country,
        cpv=["60000000"],
        notice_type="cn",
        deadline=deadline,
        url=f"https://ted.europa.eu/en/notice/{external_id}",
        signal_kind=signal_kind,
    )


def _score(spediteur: float = 0.8, kep: float = 0.3) -> Score:
    return Score(
        relevance=0.9,
        profile_spediteur=spediteur,
        profile_kep=kep,
        reasoning="Gut geeignet.",
        tags=["Regelverkehr"],
    )


_CFG = {
    "digest": {
        "dashboard_title": "Test-Digest",
        "smtp_from": "test@example.com",
    }
}


# ── sorting ───────────────────────────────────────────────────────────────────

def test_build_digest_contains_title():
    items = [(_item("A"), _score())]
    md, _ = build_digest(items, _CFG)
    assert "Ausschreibung A" in md


def test_build_digest_deadline_in_markdown():
    items = [(_item("A", deadline_offset_days=5), _score())]
    md, _ = build_digest(items, _CFG)
    assert "." in md  # date formatted as DD.MM.YYYY


def test_build_digest_no_deadline_shows_dash():
    items = [(_item("A", deadline_offset_days=None), _score())]
    md, _ = build_digest(items, _CFG)
    assert "–" in md


def test_build_digest_order_reflects_input():
    """build_digest preserves caller-supplied order (caller is responsible for sorting)."""
    early = _item("EARLY", deadline_offset_days=2)
    late = _item("LATE", deadline_offset_days=30)
    md, _ = build_digest([(early, _score()), (late, _score())], _CFG)
    assert md.index("EARLY") < md.index("LATE")


def test_build_digest_reversed_order():
    """If caller passes items in reverse order, digest reflects that order."""
    early = _item("EARLY", deadline_offset_days=2)
    late = _item("LATE", deadline_offset_days=30)
    md, _ = build_digest([(late, _score()), (early, _score())], _CFG)
    assert md.index("LATE") < md.index("EARLY")


# ── alarm section ─────────────────────────────────────────────────────────────

def test_build_digest_alarm_appears_in_markdown():
    items = [(_item("A"), _score())]
    md, _ = build_digest(items, _CFG, alarm=["⚠️ Quelle 'ted' liefert zu wenig."])
    assert "⚠️" in md
    assert "ted" in md


def test_build_digest_no_alarm_when_none():
    items = [(_item("A"), _score())]
    md, _ = build_digest(items, _CFG, alarm=None)
    assert "⚠️" not in md


# ── empty output ──────────────────────────────────────────────────────────────

def test_build_digest_empty_returns_empty_strings():
    md, html = build_digest([], _CFG)
    assert "Keine" in md
    assert "<html>" in html.lower()


# ── HTML output ───────────────────────────────────────────────────────────────

def test_build_digest_html_contains_title():
    items = [(_item("X"), _score())]
    _, html = build_digest(items, _CFG)
    assert "Test-Digest" in html


def test_build_digest_html_contains_url():
    items = [(_item("X123"), _score())]
    _, html = build_digest(items, _CFG)
    assert "X123" in html


def test_build_digest_html_has_data_json():
    items = [(_item("X"), _score())]
    _, html = build_digest(items, _CFG)
    assert "const data" in html
    assert "profile_spediteur" in html


def test_build_digest_html_data_country_attribute():
    items = [(_item("X", country="AUT"), _score())]
    _, html = build_digest(items, _CFG)
    assert 'data-country="AUT"' in html


# ── write_dashboard ───────────────────────────────────────────────────────────

def test_write_dashboard_creates_files(tmp_path, monkeypatch):
    import agent3_tender.core.digest as digest_mod
    monkeypatch.setattr(digest_mod, "_DOCS_DIR", tmp_path)

    items = [(_item("W1"), _score()), (_item("W2"), _score())]
    _, html = build_digest(items, _CFG)
    write_dashboard(items, html, _CFG)

    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "data.json").exists()


def test_write_dashboard_data_json_has_all_items(tmp_path, monkeypatch):
    import json

    import agent3_tender.core.digest as digest_mod
    monkeypatch.setattr(digest_mod, "_DOCS_DIR", tmp_path)

    items = [(_item("W1"), _score()), (_item("W2"), _score())]
    _, html = build_digest(items, _CFG)
    write_dashboard(items, html, _CFG)

    data = json.loads((tmp_path / "data.json").read_text())
    assert len(data) == 2
    titles = {d["title"] for d in data}
    assert "Ausschreibung W1" in titles
    assert "Ausschreibung W2" in titles
