"""
Scoring-Contract-Tests — kein echter API-Aufruf, Anthropic-Client vollständig gemockt.

Geprüft wird das Verhalten bei:
- validem JSON-Output des Modells
- malformtem JSON (Item wird verworfen, nicht geraten)
- JSON mit Prose-Umhüllung (soll trotzdem geparst werden)
- Score-Werten außerhalb 0..1 (Item wird verworfen)
- Anthropic API-Fehler (Item wird verworfen)
- fehlender ANTHROPIC_API_KEY (leere Liste)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent3_tender.core.models import Item
from agent3_tender.core.scoring import (
    _build_user_msg,
    _extract_json,
    _parse_response,
    _score_one,
    score_batch,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_item(
    external_id: str = "TEST-001",
    signal_kind=None,
    deadline=None,
) -> Item:
    return Item(
        source="ted",
        external_id=external_id,
        title="Transportdienstleistungen Bundesamt",
        buyer="Bundesamt für Güterverkehr",
        country="DEU",
        cpv=["60000000"],
        notice_type="cn",
        deadline=deadline,
        url=f"https://ted.europa.eu/en/notice/{external_id}",
        signal_kind=signal_kind,
    )


def _valid_score_json(**overrides) -> str:
    data = {
        "relevance": 0.85,
        "profile_spediteur": 0.9,
        "profile_kep": 0.3,
        "reasoning": "Klassischer Sammelladungsverkehr, gut für Spediteure.",
        "tags": ["Regelverkehr", "DACH"],
    }
    data.update(overrides)
    return json.dumps(data)


def _mock_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage = MagicMock(input_tokens=100, output_tokens=50)
    return resp


def _mock_client(text: str):
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=_mock_response(text))
    return client


# ── _extract_json ─────────────────────────────────────────────────────────────

def test_extract_json_clean():
    d = {"relevance": 0.8, "profile_spediteur": 0.7, "profile_kep": 0.2,
         "reasoning": "x", "tags": []}
    s = json.dumps(d)
    assert _extract_json(s) == s


def test_extract_json_with_prose():
    d = {"relevance": 0.8, "profile_spediteur": 0.5, "profile_kep": 0.2,
         "reasoning": "y", "tags": []}
    inner = json.dumps(d)
    s = f"Hier ist meine Bewertung:\n{inner}\nFertig."
    extracted = _extract_json(s)
    assert extracted.startswith("{")
    parsed = json.loads(extracted)
    assert parsed["relevance"] == 0.8


def test_extract_json_no_json_returns_text():
    s = "Kein JSON hier"
    assert _extract_json(s) == s


# ── _parse_response ───────────────────────────────────────────────────────────

def test_parse_response_single_text_block():
    block = MagicMock()
    block.text = "hello"
    assert _parse_response([block]) == "hello"


def test_parse_response_skips_non_text_blocks():
    text_block = MagicMock()
    text_block.text = "result"
    tool_block = MagicMock(spec=[])  # no .text attribute
    assert _parse_response([tool_block, text_block]) == "result"


def test_parse_response_empty():
    assert _parse_response([]) == ""


# ── _score_one ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_one_valid_json():
    item = _make_item()
    client = _mock_client(_valid_score_json())
    result = await _score_one(item, client, "claude-haiku-4-5-20251001", False)
    assert result is not None
    scored_item, score = result
    assert scored_item.external_id == "TEST-001"
    assert score.profile_spediteur == pytest.approx(0.9)
    assert score.best == pytest.approx(0.9)
    assert "Regelverkehr" in score.tags


@pytest.mark.asyncio
async def test_score_one_json_with_prose_wrapper():
    wrapped = f"Hier ist die Bewertung:\n{_valid_score_json()}\nDas war meine Einschätzung."
    item = _make_item()
    client = _mock_client(wrapped)
    result = await _score_one(item, client, "claude-haiku-4-5-20251001", False)
    assert result is not None
    _, score = result
    assert score.relevance == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_score_one_malformed_json_returns_none():
    item = _make_item()
    client = _mock_client("Das ist kein JSON {kaputt")
    result = await _score_one(item, client, "claude-haiku-4-5-20251001", False)
    assert result is None


@pytest.mark.asyncio
async def test_score_one_empty_response_returns_none():
    item = _make_item()
    client = _mock_client("")
    result = await _score_one(item, client, "claude-haiku-4-5-20251001", False)
    assert result is None


@pytest.mark.asyncio
async def test_score_one_score_above_1_returns_none():
    bad_json = _valid_score_json(profile_spediteur=1.5)
    item = _make_item()
    client = _mock_client(bad_json)
    result = await _score_one(item, client, "claude-haiku-4-5-20251001", False)
    assert result is None


@pytest.mark.asyncio
async def test_score_one_score_negative_returns_none():
    bad_json = _valid_score_json(relevance=-0.1)
    item = _make_item()
    client = _mock_client(bad_json)
    result = await _score_one(item, client, "claude-haiku-4-5-20251001", False)
    assert result is None


@pytest.mark.asyncio
async def test_score_one_api_error_returns_none():
    import anthropic as _anthropic

    item = _make_item()
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=_anthropic.APIStatusError(
            "rate limit",
            response=MagicMock(status_code=429),
            body={},
        )
    )
    result = await _score_one(item, client, "claude-haiku-4-5-20251001", False)
    assert result is None


@pytest.mark.asyncio
async def test_score_one_web_search_enabled_for_web_news():
    item = _make_item(signal_kind="web_news")
    client = _mock_client(_valid_score_json())
    await _score_one(item, client, "claude-haiku-4-5-20251001", use_web_search=True)
    call_kwargs = client.messages.create.call_args.kwargs
    assert "tools" in call_kwargs
    assert any(t.get("type") == "web_search_20250305" for t in call_kwargs["tools"])


@pytest.mark.asyncio
async def test_score_one_no_web_search_for_regular_items():
    item = _make_item()
    client = _mock_client(_valid_score_json())
    await _score_one(item, client, "claude-haiku-4-5-20251001", use_web_search=False)
    call_kwargs = client.messages.create.call_args.kwargs
    assert "tools" not in call_kwargs


# ── score_batch ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_batch_empty_returns_empty():
    result = await score_batch([], {"scoring": {"model": "x", "concurrency": 2}})
    assert result == []


@pytest.mark.asyncio
async def test_score_batch_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    items = [_make_item()]
    result = await score_batch(items, {})
    assert result == []


@pytest.mark.asyncio
async def test_score_batch_filters_failed_items(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    items = [_make_item("A"), _make_item("B"), _make_item("C")]

    responses = [
        _mock_response(_valid_score_json()),      # A → success
        _mock_response("kaputt {"),               # B → parse fail → discarded
        _mock_response(_valid_score_json()),      # C → success
    ]
    call_count = 0

    async def fake_create(**kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    mock_client_instance = MagicMock()
    mock_client_instance.messages = MagicMock()
    mock_client_instance.messages.create = fake_create

    target = "agent3_tender.core.scoring.anthropic.AsyncAnthropic"
    with patch(target, return_value=mock_client_instance):
        results = await score_batch(items, {"scoring": {"model": "x", "concurrency": 1}})

    assert len(results) == 2
    ids = {item.external_id for item, _ in results}
    assert "A" in ids
    assert "C" in ids
    assert "B" not in ids


# ── _build_user_msg ───────────────────────────────────────────────────────────

def test_build_user_msg_contains_title():
    item = _make_item()
    msg = _build_user_msg(item)
    assert "Transportdienstleistungen Bundesamt" in msg


def test_build_user_msg_contains_cpv():
    item = _make_item()
    msg = _build_user_msg(item)
    assert "60000000" in msg


def test_build_user_msg_unknown_deadline():
    item = _make_item(deadline=None)
    msg = _build_user_msg(item)
    assert "unbekannt" in msg


def test_build_user_msg_raw_truncated():
    item = _make_item()
    item.raw = {"data": "x" * 10000}
    msg = _build_user_msg(item)
    assert len(msg) < 10000
