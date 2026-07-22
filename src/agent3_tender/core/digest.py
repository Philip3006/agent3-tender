"""Digest-Generierung: Markdown, SMTP-Mail, statisches HTML-Dashboard. Implementiert in Sprint 4."""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from .models import Item, Score

logger = logging.getLogger(__name__)

_DOCS_DIR = Path(__file__).parent.parent.parent.parent / "docs"


def build_digest(
    scored_items: list[tuple[Item, Score]],
    cfg: dict,
    alarm: list[str] | None = None,
) -> tuple[str, str]:
    """Return (markdown, html) for the digest. Items must already be sorted by deadline ASC."""
    if not scored_items:
        return _empty_markdown(), _empty_html(cfg)

    title = cfg.get("digest", {}).get("dashboard_title", "Agent 3 — Ausschreibungs-Digest")
    lines = [
        f"# {title}",
        f"_Stand: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}_ — {len(scored_items)} Treffer\n",
    ]
    if alarm:
        for msg in alarm:
            lines.append(f"> {msg}\n")
    for item, score in scored_items:
        deadline_str = item.deadline.strftime("%d.%m.%Y") if item.deadline else "–"
        tags = ", ".join(score.tags) if score.tags else "–"
        lines += [
            f"## {item.title}",
            f"**Auftraggeber:** {item.buyer or '–'}  |  **Land:** {item.country}  |  **Frist:** {deadline_str}",
            f"**Score:** Spediteur {score.profile_spediteur:.0%} · KEP {score.profile_kep:.0%}  |  Tags: {tags}",
            f"_{score.reasoning}_",
            f"[Zur Ausschreibung]({item.url})\n",
            "---",
        ]
    markdown = "\n".join(lines)
    html = _render_html(scored_items, cfg)
    return markdown, html


def _empty_markdown() -> str:
    return f"# Digest {datetime.now(UTC).strftime('%Y-%m-%d')}\n\nKeine relevanten Ausschreibungen in diesem Lauf.\n"


def _empty_html(cfg: dict) -> str:
    title = cfg.get("digest", {}).get("dashboard_title", "Agent 3")
    return f"<html><body><h1>{title}</h1><p>Keine Treffer.</p></body></html>"


def _render_html(scored_items: list[tuple[Item, Score]], cfg: dict) -> str:
    title = cfg.get("digest", {}).get("dashboard_title", "Agent 3 — Ausschreibungs-Digest")
    rows = []
    for item, score in scored_items:
        deadline_str = item.deadline.strftime("%d.%m.%Y") if item.deadline else "–"
        rows.append(
            f'<tr data-country="{item.country}" data-source="{item.source}" '
            f'data-signal="{item.signal_kind or ""}" data-best="{score.best:.3f}">'
            f'<td><a href="{item.url}" target="_blank">{item.title}</a></td>'
            f"<td>{item.buyer or '–'}</td>"
            f"<td>{item.country}</td>"
            f"<td>{deadline_str}</td>"
            f'<td title="{score.reasoning}">{score.profile_spediteur:.0%} / {score.profile_kep:.0%}</td>'
            f'<td>{", ".join(score.tags)}</td>'
            f"</tr>"
        )
    rows_html = "\n".join(rows)
    data_json = json.dumps(
        [
            {
                "title": i.title,
                "buyer": i.buyer,
                "country": i.country,
                "deadline": i.deadline.isoformat() if i.deadline else None,
                "url": str(i.url),
                "source": i.source,
                "signal_kind": i.signal_kind,
                "profile_spediteur": s.profile_spediteur,
                "profile_kep": s.profile_kep,
                "reasoning": s.reasoning,
                "tags": s.tags,
            }
            for i, s in scored_items
        ],
        ensure_ascii=False,
        indent=2,
    )
    return _HTML_TEMPLATE.replace("{{TITLE}}", title).replace("{{ROWS}}", rows_html).replace("{{DATA_JSON}}", data_json).replace("{{UPDATED}}", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 1rem 2rem; background: #f8f9fa; }
  h1 { font-size: 1.4rem; color: #1a1a2e; }
  .meta { color: #666; font-size: .85rem; margin-bottom: 1rem; }
  .filters { display: flex; gap: .5rem; flex-wrap: wrap; margin-bottom: 1rem; }
  .filters input, .filters select { padding: .3rem .6rem; border: 1px solid #ccc; border-radius: 4px; font-size: .85rem; }
  table { width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  th { background: #1a1a2e; color: #fff; padding: .5rem .75rem; text-align: left; font-size: .8rem; }
  td { padding: .45rem .75rem; border-bottom: 1px solid #eee; font-size: .85rem; vertical-align: top; }
  tr:hover td { background: #f0f4ff; }
  a { color: #1a73e8; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .hidden { display: none; }
</style>
</head>
<body>
<h1>{{TITLE}}</h1>
<p class="meta">Stand: {{UPDATED}}</p>
<div class="filters">
  <input id="q" type="search" placeholder="Suche Titel / Auftraggeber…" oninput="applyFilters()">
  <select id="country" onchange="applyFilters()">
    <option value="">Alle Länder</option>
    <option>DEU</option><option>AUT</option><option>CHE</option>
  </select>
  <select id="source" onchange="applyFilters()">
    <option value="">Alle Quellen</option>
    <option>ted</option><option>oeffentlichevergabe</option><option>signals</option>
  </select>
  <select id="signal" onchange="applyFilters()">
    <option value="">Alle Typen</option>
    <option value="">Ausschreibung</option>
    <option value="re_tender">Re-Tender</option>
    <option value="job">Stellenanzeige</option>
    <option value="web_news">Web-Signal</option>
  </select>
  <label style="font-size:.85rem;display:flex;align-items:center;gap:.3rem">
    Min Score <input id="minScore" type="range" min="0" max="1" step="0.05" value="0" oninput="applyFilters();document.getElementById('scoreVal').textContent=this.value">
    <span id="scoreVal">0</span>
  </label>
</div>
<table id="tbl">
<thead><tr>
  <th>Ausschreibung</th><th>Auftraggeber</th><th>Land</th>
  <th>Frist</th><th>Score (Sped/KEP)</th><th>Tags</th>
</tr></thead>
<tbody>
{{ROWS}}
</tbody>
</table>
<script>
const data = {{DATA_JSON}};
function applyFilters() {
  const q = document.getElementById('q').value.toLowerCase();
  const country = document.getElementById('country').value;
  const source = document.getElementById('source').value;
  const signal = document.getElementById('signal').value;
  const minScore = parseFloat(document.getElementById('minScore').value);
  document.querySelectorAll('#tbl tbody tr').forEach((tr, i) => {
    const d = data[i];
    const text = (d.title + ' ' + (d.buyer||'')).toLowerCase();
    const best = Math.max(d.profile_spediteur, d.profile_kep);
    const show = text.includes(q)
      && (!country || d.country === country)
      && (!source || d.source === source)
      && (!signal || (d.signal_kind||'') === signal)
      && best >= minScore;
    tr.classList.toggle('hidden', !show);
  });
}
</script>
</body>
</html>"""


async def send_smtp(markdown: str, cfg: dict) -> None:
    smtp_url = os.environ.get("SMTP_URL", "")
    recipient = os.environ.get("DIGEST_RECIPIENT", "")
    if not smtp_url or not recipient:
        logger.warning("SMTP_URL oder DIGEST_RECIPIENT nicht gesetzt — kein E-Mail-Versand")
        return

    try:
        from email.message import EmailMessage

        import aiosmtplib

        msg = EmailMessage()
        title = cfg.get("digest", {}).get("dashboard_title", "Agent 3 Digest")
        msg["Subject"] = f"{title} {datetime.now(UTC).strftime('%Y-%m-%d')}"
        msg["From"] = cfg.get("digest", {}).get("smtp_from", "agent3@example.com")
        msg["To"] = recipient
        msg.set_content(markdown)

        # Parse smtp://user:pass@host:port
        from urllib.parse import urlparse
        parsed = urlparse(smtp_url)
        await aiosmtplib.send(
            msg,
            hostname=parsed.hostname or "localhost",
            port=parsed.port or 587,
            username=parsed.username,
            password=parsed.password,
            start_tls=True,
        )
        logger.info("Digest per E-Mail versandt an %s", recipient)
    except Exception as exc:
        logger.error("SMTP-Versand fehlgeschlagen: %s", exc)


def write_dashboard(scored_items: list[tuple[Item, Score]], html: str, cfg: dict) -> None:
    docs = _DOCS_DIR
    docs.mkdir(exist_ok=True)

    (docs / "index.html").write_text(html, encoding="utf-8")

    data = [
        {
            "title": i.title,
            "buyer": i.buyer,
            "country": i.country,
            "deadline": i.deadline.isoformat() if i.deadline else None,
            "url": str(i.url),
            "source": i.source,
            "signal_kind": i.signal_kind,
            "profile_spediteur": s.profile_spediteur,
            "profile_kep": s.profile_kep,
            "reasoning": s.reasoning,
            "tags": s.tags,
        }
        for i, s in scored_items
    ]
    (docs / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Dashboard nach %s geschrieben (%d Items)", docs, len(scored_items))
