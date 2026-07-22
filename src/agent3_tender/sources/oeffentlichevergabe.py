"""Bekanntmachungsservice (oeffentlichevergabe.de) — Transport/Logistik DACH.

API changed from GET /api/1/tender (OCDS, defunct 2026) to POST /bkmk/searches (BKMSql).
Notice URL: https://oeffentlichevergabe.de/ui/public/notices/{noticeIdentifier}
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from agent3_tender.core.http import post_json
from agent3_tender.core.models import Item

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://oeffentlichevergabe.de/bkmk/searches"
_NOTICE_URL = "https://oeffentlichevergabe.de/ui/public/notices/{nid}"

# notice-type → canonical
_TYPE_MAP: dict[str, str] = {
    "cn-standard": "cn",
    "cn-social": "cn",
    "can-standard": "can",
    "can-social": "can",
    "can-desg": "can",
    "can-modif": "can",
    "pin-only": "pin",
    "pin-cfc-standard": "pin",
    "pin-buyer": "pin",
    "veat": "can",
}

# NUTS-code prefixes for DACH countries
_COUNTRY_NUTS: dict[str, str] = {"DEU": "DE", "AUT": "AT", "CHE": "CH"}


def _date(raw: Any) -> datetime | None:
    if not raw:
        return None
    s = str(raw)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _first_text(entries: list[dict] | None) -> str | None:
    if not entries:
        return None
    for lang in ("DEU", "GER", "ENG", "FRA"):
        for e in entries:
            if e.get("languageId") == lang and e.get("value"):
                return str(e["value"])
    if entries and entries[0].get("value"):
        return str(entries[0]["value"])
    return None


def _country_from_places(places: list[dict]) -> str:
    if not places:
        return "DEU"
    return str(places[0].get("country", "DEU"))


def parse_element(el: dict) -> Item | None:
    """Parse a single bkmk/searches result element into an Item."""
    nid = el.get("noticeIdentifier", "")
    if not nid:
        return None

    lot_id = el.get("lotIdentifier", "")
    external_id = f"{nid}/{lot_id}" if lot_id else nid

    title = (
        _first_text(el.get("noticeTitle"))
        or _first_text(el.get("lotTitle"))
    )
    if not title:
        return None

    buyers: list[dict] = el.get("buyers") or []
    buyer = buyers[0].get("name") if buyers else None

    country = _country_from_places(el.get("placesOfPerformance") or [])

    cpv_main = el.get("mainCpvCode")
    cpv = [str(cpv_main)] if cpv_main else []

    notice_type_raw = str(el.get("noticeType", "")).lower()
    notice_type = _TYPE_MAP.get(notice_type_raw, "cn")

    published_at = _date(el.get("publicationDate"))
    deadline = _date(el.get("firstDeadline") or el.get("deadlineReceiptTenders"))

    url = _NOTICE_URL.format(nid=nid)

    return Item(
        source="oeffentlichevergabe",
        external_id=external_id,
        title=title,
        buyer=buyer,
        country=country,
        cpv=cpv,
        notice_type=notice_type,  # type: ignore[arg-type]
        published_at=published_at,
        deadline=deadline,
        url=url,
        raw=el,
    )


def _cpv_prefixes(cpv_codes: list[str]) -> list[str]:
    """Reduce full CPV codes to minimal unique 2-digit prefixes for STARTS_WITH."""
    prefixes = sorted({c[:2] for c in cpv_codes if len(c) >= 2})
    return prefixes


async def fetch(cfg: dict) -> list[Item]:
    lookback = cfg.get("lookback_days", 4)
    since = (datetime.now(UTC) - timedelta(days=lookback)).strftime("%Y-%m-%d")
    cpv_codes: list[str] = cfg.get("cpv", ["60000000"])
    countries: list[str] = cfg.get("countries", ["DEU"])

    nuts_prefixes = [_COUNTRY_NUTS.get(c, c[:2]) for c in countries]
    cpv_prefixes = _cpv_prefixes(cpv_codes)

    items: list[Item] = []
    page = 0
    page_size = 50

    while True:
        body: dict = {
            "SELECT": "ALL",
            "WHERE": [
                {"fields": ["allCpvCodes"], "operator": "STARTS_WITH", "operands": cpv_prefixes},
                {"fields": ["publicationDate"], "operator": ">=", "operands": [since]},
                {"fields": ["allPlacesOfPerformanceNutsCodes"], "operator": "STARTS_WITH", "operands": nuts_prefixes},
            ],
            "FROM": "lots",
            "PAGE": {"number": page, "size": page_size},
            "ORDER": {"field": "publicationDate", "direction": "DESC"},
        }

        try:
            data = await post_json(_SEARCH_URL, json=body)
        except Exception as exc:
            logger.error("oeffentlichevergabe API Fehler (Seite %d): %s", page, exc)
            break

        elements: list[dict] = data.get("elements") or []
        if not elements:
            break

        for el in elements:
            try:
                item = parse_element(el)
                if item is not None:
                    items.append(item)
            except Exception as exc:
                logger.warning("OEV Parse-Fehler bei %s: %s", el.get("noticeIdentifier"), exc)

        total = data.get("totalElements", 0)
        fetched = page * page_size + len(elements)
        logger.debug("oeffentlichevergabe: %d/%d", fetched, total)

        if len(elements) < page_size or fetched >= total:
            break
        page += 1

    logger.info("oeffentlichevergabe: %d Items gesamt", len(items))
    return items
