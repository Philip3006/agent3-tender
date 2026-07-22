from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import Item, Score

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).parent.parent.parent.parent / "data" / "agent3.db"


def _fmt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path | str = _DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                hash TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                buyer TEXT,
                country TEXT,
                cpv_json TEXT,
                notice_type TEXT,
                signal_kind TEXT,
                published_at TEXT,
                deadline TEXT,
                url TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                score_json TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_items_deadline ON items(deadline);
            CREATE INDEX IF NOT EXISTS idx_items_country ON items(country);
            CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                dry_run INTEGER NOT NULL,
                items_total INTEGER,
                items_new INTEGER,
                items_scored INTEGER,
                items_digest INTEGER,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS health (
                source TEXT NOT NULL,
                run_id INTEGER NOT NULL,
                count INTEGER NOT NULL,
                below_threshold INTEGER NOT NULL,
                PRIMARY KEY (source, run_id)
            );
        """)
        self._conn.commit()

    def upsert_item(self, item: Item, score: Score | None = None) -> None:
        now = _now()
        score_json = score.model_dump_json() if score else None
        self._conn.execute(
            """
            INSERT INTO items
              (hash, source, external_id, title, buyer, country, cpv_json,
               notice_type, signal_kind, published_at, deadline, url,
               raw_json, score_json, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hash) DO UPDATE SET
              last_seen_at = excluded.last_seen_at,
              score_json = COALESCE(excluded.score_json, score_json)
            """,
            (
                item.hash,
                item.source,
                item.external_id,
                item.title,
                item.buyer,
                item.country,
                json.dumps(item.cpv),
                item.notice_type,
                item.signal_kind,
                _fmt(item.published_at),
                _fmt(item.deadline),
                str(item.url),
                json.dumps(item.raw),
                score_json,
                now,
                now,
            ),
        )
        self._conn.commit()

    def known_hashes(self) -> set[str]:
        cur = self._conn.execute("SELECT hash FROM items")
        return {row[0] for row in cur.fetchall()}

    def all_for_fuzzy(self) -> list[dict]:
        cur = self._conn.execute("SELECT hash, source, buyer, title, deadline FROM items")
        return [dict(row) for row in cur.fetchall()]

    def all_scored_for_digest(self, min_score: float) -> list[tuple[Item, Score]]:
        cur = self._conn.execute(
            "SELECT * FROM items WHERE score_json IS NOT NULL ORDER BY deadline ASC NULLS LAST"
        )
        results = []
        for row in cur.fetchall():
            score = Score.model_validate_json(row["score_json"])
            if score.best < min_score:
                continue
            item = Item(
                source=row["source"],
                external_id=row["external_id"],
                title=row["title"],
                buyer=row["buyer"],
                country=row["country"] or "DEU",
                cpv=json.loads(row["cpv_json"] or "[]"),
                notice_type=row["notice_type"],
                signal_kind=row["signal_kind"],
                published_at=(
                    datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
                ),
                deadline=(
                    datetime.fromisoformat(row["deadline"]) if row["deadline"] else None
                ),
                url=row["url"],
                raw=json.loads(row["raw_json"]),
            )
            results.append((item, score))
        return results

    def record_run(
        self,
        *,
        started_at: datetime,
        dry_run: bool,
        finished_at: datetime | None = None,
        items_total: int | None = None,
        items_new: int | None = None,
        items_scored: int | None = None,
        items_digest: int | None = None,
        error: str | None = None,
        run_id: int | None = None,
    ) -> int:
        if run_id is None:
            cur = self._conn.execute(
                "INSERT INTO runs (started_at, dry_run) VALUES (?, ?)",
                (_fmt(started_at), int(dry_run)),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]
        self._conn.execute(
            """UPDATE runs SET finished_at=?, items_total=?, items_new=?,
               items_scored=?, items_digest=?, error=? WHERE id=?""",
            (_fmt(finished_at), items_total, items_new, items_scored, items_digest, error, run_id),
        )
        self._conn.commit()
        return run_id

    def record_health(self, *, source: str, run_id: int, count: int, threshold: int) -> None:
        below = int(count < threshold)
        self._conn.execute(
            "INSERT OR REPLACE INTO health"
            " (source, run_id, count, below_threshold) VALUES (?, ?, ?, ?)",
            (source, run_id, count, below),
        )
        self._conn.commit()

    def consecutive_below_threshold(self, source: str, n: int = 3) -> int:
        cur = self._conn.execute(
            "SELECT below_threshold FROM health WHERE source=? ORDER BY run_id DESC LIMIT ?",
            (source, n),
        )
        rows = cur.fetchall()
        if len(rows) < n:
            return 0
        return sum(r[0] for r in rows)

    def close(self) -> None:
        self._conn.close()
