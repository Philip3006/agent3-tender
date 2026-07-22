from __future__ import annotations

import unicodedata

from rapidfuzz import fuzz

from .models import Item
from .store import Store


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    s = s.replace("ß", "ss").replace("ẞ", "SS")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def filter_new(items: list[Item], store: Store) -> list[Item]:
    known = store.known_hashes()
    fuzzy_rows = store.all_for_fuzzy()
    result: list[Item] = []
    seen_in_batch: set[str] = set()

    for item in items:
        h = item.hash
        if h in known or h in seen_in_batch:
            continue
        if _is_cross_source_duplicate(item, fuzzy_rows):
            continue
        seen_in_batch.add(h)
        result.append(item)
    return result


def _is_cross_source_duplicate(item: Item, rows: list[dict]) -> bool:
    norm_title = _normalize(item.title)[:80]
    norm_buyer = _normalize(item.buyer)
    deadline_date = item.deadline.date().isoformat() if item.deadline else None

    for row in rows:
        if row["source"] == item.source:
            continue
        row_title = _normalize(row["title"])[:80]
        row_buyer = _normalize(row["buyer"])
        row_deadline = row["deadline"][:10] if row["deadline"] else None

        if deadline_date and row_deadline and deadline_date != row_deadline:
            continue

        title_score = fuzz.ratio(norm_title, row_title)
        buyer_score = fuzz.ratio(norm_buyer, row_buyer) if norm_buyer and row_buyer else 0

        if title_score >= 90 and (not norm_buyer or buyer_score >= 85):
            return True
    return False
