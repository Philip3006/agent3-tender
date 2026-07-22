"""Entry point: uv run python -m agent3_tender.run [--dry-run] [--source NAME] [--skip-scoring]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "agent3.db"


def _load_cfg() -> dict:
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


async def _run(args: argparse.Namespace) -> None:
    from agent3_tender.core.store import Store

    cfg = _load_cfg()
    store = Store(path=_DB_PATH)
    started_at = datetime.now(UTC)
    run_id = store.record_run(started_at=started_at, dry_run=args.dry_run)

    try:
        from agent3_tender.sources import oeffentlichevergabe, signals, ted

        source_map = {
            "ted": ted.fetch,
            "oeffentlichevergabe": oeffentlichevergabe.fetch,
            "signals": signals.fetch,
        }
        active_sources = (
            {args.source: source_map[args.source]}
            if args.source
            else source_map
        )
        if args.source and args.source not in source_map:
            logger.error("Unbekannte Quelle: %s", args.source)
            sys.exit(1)

        # Sammeln (parallel, Einzel-Fehler werden abgefangen)
        tasks = [fn(cfg) for fn in active_sources.values()]
        per_source = await asyncio.gather(*tasks, return_exceptions=True)

        all_items = []
        min_counts: dict = cfg.get("health_min_count", {})
        for name, result in zip(active_sources.keys(), per_source, strict=False):
            if isinstance(result, Exception):
                logger.error("Quelle %s fehlgeschlagen: %s", name, result, exc_info=result)
                count = 0
            else:
                count = len(result)
                all_items.extend(result)
                logger.info("Quelle %-22s → %d Items", name, count)
            store.record_health(
                source=name,
                run_id=run_id,
                count=count,
                threshold=min_counts.get(name, 1),
            )

        logger.info("Gesamt gesammelt: %d Items", len(all_items))

        # Deduplizieren
        from agent3_tender.core.dedupe import filter_new

        new_items = filter_new(all_items, store)
        logger.info("Neu nach Dedupe: %d", len(new_items))

        # Scoring
        scored_count = 0
        if not args.skip_scoring and new_items:
            from agent3_tender.core.scoring import score_batch

            scored_pairs = await score_batch(new_items, cfg)
            for item, score in scored_pairs:
                store.upsert_item(item, score)
                scored_count += 1
            # Items ohne Score trotzdem speichern (für spätere Läufe)
            scored_ids = {i.hash for i, _ in scored_pairs}
            for item in new_items:
                if item.hash not in scored_ids:
                    store.upsert_item(item)
        else:
            for item in new_items:
                store.upsert_item(item)

        # Digest
        min_score: float = cfg.get("min_score", 0.55)
        digest_items = store.all_scored_for_digest(min_score)

        # Health-Alarm-Check
        from agent3_tender.core import health as health_mod

        alarm = health_mod.check_alarms(store, cfg)

        from agent3_tender.core.digest import build_digest

        markdown, html = build_digest(digest_items, cfg, alarm=alarm)
        logger.info("Digest: %d Items ≥ %.0f%%", len(digest_items), min_score * 100)

        if args.dry_run:
            print("\n" + "=" * 60)
            print(markdown)
            print("=" * 60)
        else:
            from agent3_tender.core.digest import send_smtp, write_dashboard

            await send_smtp(markdown, cfg)
            write_dashboard(digest_items, html, cfg)

        store.record_run(
            run_id=run_id,
            started_at=started_at,
            dry_run=args.dry_run,
            finished_at=datetime.now(UTC),
            items_total=len(all_items),
            items_new=len(new_items),
            items_scored=scored_count,
            items_digest=len(digest_items),
        )

    except Exception as exc:
        logger.exception("Fataler Fehler im Run")
        store.record_run(
            run_id=run_id,
            started_at=started_at,
            dry_run=args.dry_run,
            finished_at=datetime.now(UTC),
            error=str(exc),
        )
        sys.exit(1)
    finally:
        from agent3_tender.core import http

        await http.close()
        store.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="agent3-tender Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Kein SMTP, kein Dashboard-Deploy")
    parser.add_argument(
        "--source",
        choices=["ted", "oeffentlichevergabe", "signals"],
        help="Nur eine Quelle laufen lassen",
    )
    parser.add_argument("--skip-scoring", action="store_true", help="Nur sammeln und dedupen")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
