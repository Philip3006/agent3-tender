"""TED Search API v3 — Transport/Logistik-Ausschreibungen DACH."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from agent3_tender.core.http import post_json
from agent3_tender.core.models import Item

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
_NOTICE_URL = "https://ted.europa.eu/en/notice/{pub}"

# TED v3 notice-type → canonical notice_type
_TYPE_MAP: dict[str, str] = {
    "cn-standard": "cn",
    "cn-social": "cn",
    "pin-only": "pin",
    "pin-cfc-standard": "pin",
    "pin-cfc-social": "pin",
    "pin-buyer": "pin",
    "can-standard": "can",
    "can-social": "can",
    "can-desg": "can",
    "can-modif": "can",
    "veat": "can",
}

# Simplified config types → TED v3 type values (as of 2026-07)
# Removed: cn-utilities, cn-defence, can-utilities (no longer accepted by TED v3 search API)
_CFG_TO_TED: dict[str, list[str]] = {
    "cn": ["cn-standard", "cn-social"],
    "can": ["can-standard", "can-social", "can-desg", "can-modif"],
    "pin": ["pin-only", "pin-cfc-standard", "pin-buyer"],
}

_RESPONSE_FIELDS = [
    "publication-number",
    "BT-21-Procedure",
    "organisation-name-buyer",
    "deadline-receipt-tender-date-lot",
    "publication-date",
    "notice-type",
    "classification-cpv",
    "organisation-country-buyer",
]

_SCOPE = 2  # TED v3: valid range 0..2; 2 = all notices (active + archived)


def _build_query(cfg: dict) -> str:
    countries = cfg.get("countries", ["DEU"])
    cpv_codes = cfg.get("cpv", ["60000000"])
    lookback = cfg.get("lookback_days", 4)
    since = (datetime.now(UTC) - timedelta(days=lookback)).strftime("%Y%m%d")

    cfg_types: list[str] = cfg.get("notice_types", ["cn", "can", "pin"])
    ted_types: list[str] = []
    for t in cfg_types:
        ted_types.extend(_CFG_TO_TED.get(t, []))
    type_filter = " OR ".join(f'NOTICE-TYPE = "{t}"' for t in ted_types)

    country_vals = " OR ".join(f'BUYER-COUNTRY = "{c}"' for c in countries)
    cpv_vals = " OR ".join(f'classification-cpv = {c}' for c in cpv_codes)

    return (
        f"PUBLICATION-DATE >= {since} "
        f"AND ({type_filter}) "
        f"AND ({country_vals}) "
        f"AND ({cpv_vals})"
    )


def _title(raw: Any) -> str:
    if isinstance(raw, dict):
        for lang in ("deu", "ger", "eng", "fra"):
            if lang in raw:
                val = raw[lang]
                return str(val[0]) if isinstance(val, list) else str(val)
        vals = list(raw.values())
        if vals:
            v = vals[0]
            return str(v[0]) if isinstance(v, list) else str(v)
        return ""
    return str(raw) if raw else ""


def _buyer(raw: Any) -> str | None:
    # New format: {"deu": ["Buyer Name"]} or list of names
    if isinstance(raw, dict):
        for lang in ("deu", "ger", "eng", "fra"):
            if lang in raw:
                val = raw[lang]
                if isinstance(val, list) and val:
                    return str(val[0])
                return str(val) if val else None
        vals = list(raw.values())
        if vals:
            v = vals[0]
            return str(v[0]) if isinstance(v, list) and v else str(v) if v else None
        return None
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("content")
                if name:
                    return str(name)
            elif isinstance(entry, str) and entry:
                return entry
        return None
    return str(raw) if raw else None


def _date(raw: Any) -> datetime | None:
    if not raw:
        return None
    # New API returns lists for lot-level fields
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return None
    s = str(raw)
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        # Handle "2026-08-25+02:00" format (date with offset, no time)
        if "+" in s[10:] or s.endswith("Z"):
            return datetime.fromisoformat(s[:10]).replace(tzinfo=UTC)
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _cpv(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    codes: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, dict):
            code = entry.get("code") or entry.get("cpvCode") or entry.get("id")
            if code and str(code) not in seen:
                codes.append(str(code))
                seen.add(str(code))
        elif isinstance(entry, str) and entry not in seen:
            codes.append(entry)
            seen.add(entry)
    return codes


def _country(raw: Any) -> str:
    if isinstance(raw, list):
        return str(raw[0]) if raw else "DEU"
    return str(raw) if raw else "DEU"


def parse_notice(notice: dict) -> Item | None:
    """Parse a single TED v3 notice dict into an Item. Returns None on missing required fields."""
    pub = notice.get("publication-number", "")
    if not pub:
        return None

    title = _title(notice.get("BT-21-Procedure") or notice.get("notice-title") or notice.get("title"))
    if not title:
        return None

    country_raw = notice.get("organisation-country-buyer") or notice.get("country-code", "DEU")
    country = _country(country_raw)

    notice_type_raw = str(notice.get("notice-type", "")).lower()
    notice_type = _TYPE_MAP.get(notice_type_raw, "cn")

    deadline = _date(notice.get("deadline-receipt-tender-date-lot") or notice.get("deadline-date") or notice.get("deadline"))
    published_at = _date(notice.get("publication-date") or notice.get("published"))
    cpv_list = _cpv(notice.get("classification-cpv") or notice.get("cpv-list") or notice.get("cpv") or [])
    buyer = _buyer(notice.get("organisation-name-buyer") or notice.get("buyer-name") or notice.get("buyer"))
    url = _NOTICE_URL.format(pub=pub)

    # CAN with no deadline → likely contract award; mark as potential re-tender signal
    signal_kind = "re_tender" if (notice_type == "can" and deadline is None) else None

    return Item(
        source="ted",
        external_id=pub,
        title=title,
        buyer=buyer,
        country=country,
        cpv=cpv_list,
        notice_type=notice_type,  # type: ignore[arg-type]
        published_at=published_at,
        deadline=deadline,
        url=url,
        raw=notice,
        signal_kind=signal_kind,
    )


async def fetch(cfg: dict) -> list[Item]:
    query = _build_query(cfg)
    items: list[Item] = []
    page = 1
    limit = 100

    while True:
        body = {
            "query": query,
            "scope": _SCOPE,
            "fields": _RESPONSE_FIELDS,
            "limit": limit,
            "page": page,
        }
        try:
            data = await post_json(_SEARCH_URL, json=body)
        except Exception as exc:
            logger.error("TED API Fehler (Seite %d): %s", page, exc)
            break

        notices = data.get("notices", [])
        if not notices:
            break

        for raw in notices:
            try:
                item = parse_notice(raw)
                if item is not None:
                    items.append(item)
            except Exception as exc:
                logger.warning("TED Parse-Fehler bei %s: %s", raw.get("publication-number"), exc)

        total = data.get("totalNoticeCount", 0)
        fetched_so_far = (page - 1) * limit + len(notices)
        logger.info("TED: %d/%d Notices", fetched_so_far, total)

        if fetched_so_far >= total or len(notices) < limit:
            break
        page += 1

    logger.info("TED: %d Items gesamt", len(items))
    return items
