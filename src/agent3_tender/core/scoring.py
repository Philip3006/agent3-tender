"""LLM-Scoring via Anthropic Haiku 4.5 — striktes JSON, kein Raten bei Parse-Fehler."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import anthropic
from pydantic import ValidationError

from .models import Item, Score

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Du bist ein Ausschreibungs-Analyst für Logistik im DACH-Raum.
Bewertet werden Ausschreibungen anhand zweier Zielprofile:
- profile_spediteur: Klassischer mittelständischer Spediteur DE
  (Sammelladung, Stückgut, Charterverkehr, Subunternehmer, Regelverkehr)
- profile_kep: KEP / Last-Mile-Anbieter
  (Kurier, Express, Paket, Same-Day, Stadtlogistik, B2C)

ANTWORTE AUSSCHLIESSLICH MIT VALIDEM JSON — kein Text davor, kein Text danach.
Verwende exakt dieses Schema:
{
  "relevance": <float 0.0..1.0>,
  "profile_spediteur": <float 0.0..1.0>,
  "profile_kep": <float 0.0..1.0>,
  "reasoning": "<max 2 prägnante Sätze auf Deutsch>",
  "tags": ["<Schlagwort1>", "<Schlagwort2>"]
}

Bewertungsregeln:
- relevance: Wie relevant ist die Ausschreibung für die Logistikbranche generell?
- profile_spediteur: Passt die Ausschreibung zu einem klassischen Spediteur?
- profile_kep: Passt die Ausschreibung zu einem KEP/Last-Mile-Anbieter?
- Erfinde KEINE URLs, Preise, Fristen oder Auftraggebernamen
- Bei fehlenden Informationen: 0.0 setzen, nicht schätzen
- Tags-Beispiele: "Regelverkehr", "KEP", "Frist <30d", "Lager", "Re-Tender",
  "Schwerlast", "DACH", "Sammelgut", "Subunternehmer", "Öffentliche Hand"
- Kein Markdown, kein Fließtext, nur JSON\
"""

_JSON_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _extract_json(text: str) -> str:
    """Find JSON object in text that might be wrapped in prose."""
    text = text.strip()
    if text.startswith("{"):
        return text
    match = _JSON_RE.search(text)
    return match.group(0) if match else text


def _build_user_msg(item: Item) -> str:
    raw_excerpt = json.dumps(item.raw, ensure_ascii=False)[:4000]
    deadline_str = item.deadline.strftime("%Y-%m-%d") if item.deadline else "unbekannt"
    cpv_str = ", ".join(item.cpv) if item.cpv else "keine"
    typ = item.notice_type or item.signal_kind or "unbekannt"
    return (
        f"Titel: {item.title}\n"
        f"Auftraggeber: {item.buyer or 'unbekannt'}\n"
        f"Land: {item.country}\n"
        f"CPV: {cpv_str}\n"
        f"Typ: {typ}\n"
        f"Frist: {deadline_str}\n"
        f"\nRohdaten (Auszug):\n{raw_excerpt}"
    )


def _parse_response(content: list) -> str:
    """Collect all text blocks from an Anthropic response content list."""
    parts = []
    for block in content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)


async def _score_one(
    item: Item,
    client: anthropic.AsyncAnthropic,
    model: str,
    use_web_search: bool,
) -> tuple[Item, Score] | None:
    kwargs: dict = dict(
        model=model,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_msg(item)}],
    )
    if use_web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    try:
        response = await client.messages.create(**kwargs)
    except anthropic.APIError as exc:
        logger.error(
            "Anthropic API Fehler für %s/%s: %s", item.source, item.external_id, exc
        )
        return None

    raw_text = _parse_response(response.content)

    # Log token usage for cost monitoring
    if hasattr(response, "usage"):
        usage = response.usage
        logger.debug(
            "Tokens %s/%s: in=%d out=%d",
            item.source,
            item.external_id,
            getattr(usage, "input_tokens", 0),
            getattr(usage, "output_tokens", 0),
        )

    json_str = _extract_json(raw_text)
    try:
        score = Score.model_validate_json(json_str)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        logger.warning(
            "Score-Parse-Fehler für %s/%s: %s | raw=%r",
            item.source,
            item.external_id,
            exc,
            raw_text[:200],
        )
        return None

    return item, score


async def score_batch(
    items: list[Item],
    cfg: dict,
) -> list[tuple[Item, Score]]:
    if not items:
        return []

    model: str = cfg.get("scoring", {}).get("model", "claude-haiku-4-5-20251001")
    concurrency: int = cfg.get("scoring", {}).get("concurrency", 4)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY nicht gesetzt — Scoring übersprungen")
        return []

    client = anthropic.AsyncAnthropic(api_key=api_key)
    semaphore = asyncio.Semaphore(concurrency)

    async def _guarded(item: Item) -> tuple[Item, Score] | None:
        async with semaphore:
            return await _score_one(item, client, model, use_web_search=item.signal_kind == "web_news")

    results = await asyncio.gather(*[_guarded(i) for i in items])
    scored = [r for r in results if r is not None]
    logger.info("Scoring: %d/%d Items erfolgreich gescored", len(scored), len(items))
    return scored
