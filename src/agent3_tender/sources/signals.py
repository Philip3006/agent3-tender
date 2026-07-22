"""Bedarfsindikatoren — Re-Tender aus TED-CANs, Stellenanzeigen, Web-News-Signale."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from agent3_tender.core.http import get_json, post_json
from agent3_tender.core.models import Item

logger = logging.getLogger(__name__)

# ── TED-basierte Re-Tender ────────────────────────────────────────────────────

_TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
_NOTICE_URL = "https://ted.europa.eu/en/notice/{pub}"

_CAN_TYPES = ["can-standard", "can-social", "can-desg", "can-modif"]


async def _fetch_retender(cfg: dict) -> list[Item]:
    """Query TED for CANs from ~retender_years ago and emit re-tender prediction items."""
    years: int = cfg.get("retender_years", 4)
    countries: list[str] = cfg.get("countries", ["DEU"])
    cpv_codes: list[str] = cfg.get("cpv", ["60000000"])
    window = 30  # ±days around the retender window

    mid = datetime.now(UTC) - timedelta(days=365 * years)
    date_from = (mid - timedelta(days=window)).strftime("%Y%m%d")
    date_to = (mid + timedelta(days=window)).strftime("%Y%m%d")

    type_filter = " OR ".join(f'NOTICE-TYPE = "{t}"' for t in _CAN_TYPES)
    country_filter = " OR ".join(f'BUYER-COUNTRY = "{c}"' for c in countries)
    cpv_filter = " OR ".join(f'classification-cpv = {c}' for c in cpv_codes)

    query = (
        f"PUBLICATION-DATE >= {date_from} AND PUBLICATION-DATE <= {date_to} "
        f"AND ({type_filter}) "
        f"AND ({country_filter}) "
        f"AND ({cpv_filter})"
    )
    body = {
        "query": query,
        "scope": 2,
        "fields": [
            "publication-number", "BT-21-Procedure", "organisation-name-buyer",
            "publication-date", "notice-type", "classification-cpv", "organisation-country-buyer",
        ],
        "limit": 100,
        "page": 1,
    }

    try:
        data = await post_json(_TED_SEARCH_URL, json=body)
    except Exception as exc:
        logger.error("Re-Tender TED-Abfrage fehlgeschlagen: %s", exc)
        return []

    items: list[Item] = []
    for raw in data.get("notices", []):
        pub = raw.get("publication-number", "")
        if not pub:
            continue
        title = _title_str(raw.get("BT-21-Procedure") or raw.get("notice-title") or raw.get("title"))
        if not title:
            continue
        pub_date = _parse_date(raw.get("publication-date"))
        predicted_deadline = pub_date + timedelta(days=365 * years) if pub_date else None
        country_raw = raw.get("organisation-country-buyer") or raw.get("country-code", "DEU")
        country = str(country_raw[0]) if isinstance(country_raw, list) and country_raw else str(country_raw)
        try:
            item = Item(
                source="signals",
                external_id=f"retender-{pub}",
                title=f"Re-Tender-Prognose: {title}",
                buyer=_buyer_str(raw.get("organisation-name-buyer") or raw.get("buyer-name")),
                country=country,
                cpv=_cpv_list(raw.get("classification-cpv") or raw.get("cpv-list") or []),
                notice_type="signal",
                published_at=pub_date,
                deadline=predicted_deadline,
                url=_NOTICE_URL.format(pub=pub),
                raw=raw,
                signal_kind="re_tender",
            )
            items.append(item)
        except Exception as exc:
            logger.warning("Re-Tender Item ungültig (%s): %s", pub, exc)

    logger.info("Re-Tender: %d Signale", len(items))
    return items


# ── Bundesagentur Jobbörse (DE) ───────────────────────────────────────────────

_BA_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
_BA_OAUTH_URL = "https://rest.arbeitsagentur.de/oauth/gettoken_cc"
_JOB_DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"
_JOB_TERMS = ["Logistikleiter", "Speditionsleiter", "Fuhrparkleiter"]


async def _ba_token() -> str | None:
    api_key = os.environ.get("BA_JOBS_API_KEY")
    if not api_key:
        return None
    try:
        resp = await get_json(
            _BA_OAUTH_URL,
            params={"grant_type": "client_credentials"},
            headers={"X-API-Key": api_key},
        )
        return resp.get("access_token")
    except Exception as exc:
        logger.warning("BA OAuth-Token fehlgeschlagen: %s", exc)
        return None


async def _fetch_ba_jobs(cfg: dict) -> list[Item]:
    token = await _ba_token()
    if not token:
        logger.info("BA_JOBS_API_KEY nicht gesetzt — Bundesagentur-Jobs übersprungen")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    items: list[Item] = []

    for term in _JOB_TERMS:
        try:
            data = await get_json(
                _BA_URL,
                params={"was": term, "umkreis": 200, "size": 25},
                headers=headers,
            )
        except Exception as exc:
            logger.warning("BA Jobs '%s' Fehler: %s", term, exc)
            continue

        for job in data.get("stellenangebote", []) or []:
            ref = job.get("refnr") or job.get("hashId")
            if not ref:
                continue
            title = job.get("titel") or job.get("beruf") or term
            pub_date = _parse_date(
                job.get("aktuelleVeroeffentlichungsdatum") or job.get("eintrittsdatum")
            )
            deadline = pub_date + timedelta(days=60) if pub_date else None
            try:
                item = Item(
                    source="signals",
                    external_id=f"ba-job-{ref}",
                    title=f"Stellenanzeige: {title}",
                    buyer=job.get("arbeitgeber"),
                    country="DEU",
                    cpv=[],
                    notice_type="signal",
                    published_at=pub_date,
                    deadline=deadline,
                    url=_JOB_DETAIL_URL.format(ref=ref),
                    raw=job,
                    signal_kind="job",
                )
                items.append(item)
            except Exception as exc:
                logger.warning("BA Job Item ungültig (%s): %s", ref, exc)

    logger.info("BA Jobs: %d Signale", len(items))
    return items


# ── AMS Österreich ────────────────────────────────────────────────────────────

_AMS_URL = "https://jobs.ams.at/public/emps/api/search"
_AMS_JOB_URL = "https://jobs.ams.at/public/emps/joboffer/{job_id}"
_AMS_TERMS = ["Logistikleiter", "Speditionsleiter"]


async def _fetch_ams_jobs(cfg: dict) -> list[Item]:
    api_key = os.environ.get("AMS_JOBS_API_KEY")
    if not api_key:
        logger.info("AMS_JOBS_API_KEY nicht gesetzt — AMS-Jobs übersprungen")
        return []
    headers = {"Authorization": f"Bearer {api_key}"}
    items: list[Item] = []

    for term in _AMS_TERMS:
        try:
            data = await get_json(
                _AMS_URL,
                params={"query": term, "page": 0, "size": 25},
                headers=headers,
            )
        except Exception as exc:
            logger.warning("AMS Jobs '%s' Fehler: %s", term, exc)
            continue

        for job in data.get("content", []) or data.get("jobs", []) or []:
            job_id = str(job.get("id") or job.get("jobId") or "")
            if not job_id:
                continue
            title = job.get("title") or job.get("jobTitle") or term
            pub_date = _parse_date(job.get("publishedAt") or job.get("createdAt"))
            deadline = pub_date + timedelta(days=60) if pub_date else None
            url = job.get("url") or _AMS_JOB_URL.format(job_id=job_id)
            try:
                item = Item(
                    source="signals",
                    external_id=f"ams-job-{job_id}",
                    title=f"Stellenanzeige (AT): {title}",
                    buyer=job.get("company") or job.get("employer"),
                    country="AUT",
                    cpv=[],
                    notice_type="signal",
                    published_at=pub_date,
                    deadline=deadline,
                    url=url,
                    raw=job,
                    signal_kind="job",
                )
                items.append(item)
            except Exception as exc:
                logger.warning("AMS Job Item ungültig (%s): %s", job_id, exc)

    logger.info("AMS Jobs: %d Signale", len(items))
    return items


# ── Web-News-Signale (synthetisch — LLM sucht im Scoring via web_search) ──────

_WEB_NEWS_QUERY_TEMPLATES: list[tuple[str, str]] = [
    ("Lagerhalle Neubau Schweiz {year}", "CHE"),
    ("Logistikzentrum Bau Zürich {year}", "CHE"),
    ("Spedition Ausschreibung Schweiz {year}", "CHE"),
    ("Lagerbau Österreich {year}", "AUT"),
    ("Logistikimmobilie Neubau Deutschland {year}", "DEU"),
]

_SEARCH_BASE = "https://www.google.com/search?q="


def _web_news_items() -> list[Item]:
    now = datetime.now(UTC)
    year = now.year
    items: list[Item] = []
    for template, country in _WEB_NEWS_QUERY_TEMPLATES:
        query = template.format(year=year)
        slug = query[:40].lower().replace(" ", "-")
        try:
            item = Item(
                source="signals",
                external_id=f"web-news-{slug}",
                title=query,
                buyer=None,
                country=country,
                cpv=[],
                notice_type="signal",
                published_at=now,
                deadline=now + timedelta(days=90),
                url=_SEARCH_BASE + query.replace(" ", "+"),
                raw={"query": query, "generated_at": now.isoformat()},
                signal_kind="web_news",
            )
            items.append(item)
        except Exception as exc:
            logger.warning("Web-News Item ungültig ('%s'): %s", query, exc)
    return items


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _parse_date(raw: Any) -> datetime | None:
    if not raw:
        return None
    s = str(raw)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _title_str(raw: Any) -> str:
    if isinstance(raw, dict):
        for lang in ("deu", "ger", "eng", "fra"):
            if lang in raw:
                return str(raw[lang])
        vals = list(raw.values())
        return str(vals[0]) if vals else ""
    return str(raw) if raw else ""


def _buyer_str(raw: Any) -> str | None:
    # New TED v3 format: {"deu": ["Buyer Name"]}
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
            elif isinstance(entry, str):
                return entry
        return None
    if isinstance(raw, str):
        return raw
    return None


def _cpv_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    codes = []
    for entry in raw:
        if isinstance(entry, dict):
            code = entry.get("code") or entry.get("cpvCode") or entry.get("id")
            if code:
                codes.append(str(code))
        elif isinstance(entry, str):
            codes.append(entry)
    return codes


# ── öffentliche API ───────────────────────────────────────────────────────────

async def fetch(cfg: dict) -> list[Item]:
    """Collect all demand signals: re-tenders, job postings, web-news."""
    retender, ba_jobs, ams_jobs = await asyncio.gather(
        _fetch_retender(cfg),
        _fetch_ba_jobs(cfg),
        _fetch_ams_jobs(cfg),
    )
    web_news = _web_news_items()
    all_items = retender + ba_jobs + ams_jobs + web_news
    logger.info(
        "Signals gesamt: %d re-tender + %d BA-jobs + %d AMS-jobs + %d web-news = %d",
        len(retender), len(ba_jobs), len(ams_jobs), len(web_news), len(all_items),
    )
    return all_items
