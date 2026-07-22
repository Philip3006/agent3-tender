# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# agent3-tender

Agent 3: Transport-/Logistik-Ausschreibungen, Lauf alle 2 Tage.
Pipeline: `sammeln -> normalisieren -> deduplizieren -> LLM-Scoring -> speichern -> Digest`

## Quellen
- `sources/ted.py`                 — TED Search API v3, oeffentliche Hand EU-weit
- `sources/oeffentlichevergabe.py` — Datenservice Oeffentlicher Einkauf (OCDS), auch unterschwellig
- `sources/signals.py`             — Bedarfsindikatoren fuer private Nachfrage (kein Register,
                                      daher Signal-basiert: Lagerbau, Stellenanzeigen, auslaufende
                                      TED-Vergaben als Re-Tender-Prognose)

## Regeln
- Ein neuer Quell-Adapter = eine Datei in `sources/`, die `fetch(cfg) -> list[Item]` zurueckgibt.
  Kein HTTP-Code ausserhalb von `core/http.py`.
- **URLs werden nie vom LLM erzeugt**, immer aus dem Rohdatensatz durchgereicht.
- Scoring gibt striktes JSON zurueck. Bei Parse-Fehler: Item verwerfen, loggen, nicht raten.
- Dedupe-Key: `sha256(source + external_id)`. Cross-Source-Duplikate (TED vs. oeffentlichevergabe.de)
  per Fuzzy-Match auf (Auftraggeber, Titel, Frist).
- Sortierung im Digest: `deadline ASC`, nicht Veroeffentlichungsdatum.
- Pro Quelle ein Health-Check: erwarteter Mindest-Output. Drei Laeufe unter Schwelle -> Alarm,
  nicht stiller Digest (Silent-Failure-Schutz).

## Befehle
    uv sync
    uv run ruff check --fix . && uv run ruff format .
    uv run python -m agent3_tender.run --dry-run
    uv run python -m agent3_tender.run --dry-run --source ted
    uv run python -m agent3_tender.run --dry-run --skip-scoring
    uv run pytest                              # alle Tests
    uv run pytest tests/test_dedupe.py         # einzelner Test-Datei
    uv run pytest tests/test_dedupe.py::test_name  # einzelner Test
    PYTHONPATH=src .venv/bin/pytest           # Python 3.13: pth-Dateien mit _ werden geskippt

## CI / GitHub Actions
GH Actions Workflow (`.github/workflows/agent3-tender.yml`):
- Job `test`: ruff + pytest bei jedem Push/PR.
- Job `run`: voller Pipeline-Lauf (alle 2 Tage via Cron), danach GH Pages Deploy nach `docs/`.

Benoetigte Repository Secrets (Settings → Secrets → Actions):
    ANTHROPIC_API_KEY   — fuer LLM-Scoring (Pflicht)
    SMTP_URL            — smtp://user:pass@host:port (optional, fuer Mail)
    DIGEST_RECIPIENT    — E-Mail-Adresse des Empfaengers (optional)
    BA_JOBS_API_KEY     — Bundesagentur Jobboerse API Key (optional)
    AMS_JOBS_API_KEY    — AMS Oesterreich API Key (optional)

GitHub Pages: Repository Settings → Pages → Source = "Deploy from branch gh-pages" (wird via Action gesetzt).

## Architektur-Notizen

### Datenfluss
`run.py` orchestriert den gesamten Lauf async:
1. `sources/*.fetch(cfg)` → `list[Item]` (parallel via `asyncio.gather`)
2. `core/dedupe.filter_new()` → filtert bekannte Hashes + Cross-Source-Fuzzy-Duplikate
3. `core/scoring.score_batch()` → `list[(Item, Score)]` (Haiku 4.5, concurrency=4)
4. `core/store.upsert_item()` → SQLite
5. `core/digest.build_digest()` → Markdown + HTML; `send_smtp()` + `write_dashboard()`

### Kernmodule
- `core/http.py`: Einziger HTTP-Client (httpx). Automatische Retries bei 5xx/Timeouts (tenacity).
  Alle Adapter nutzen `get_json` / `post_json` — nie direkt httpx importieren.
- `core/store.py`: SQLite in `data/agent3.db`. Schema wird bei Start via `_migrate()` idempotent angelegt.
  `all_scored_for_digest()` liest **alle** je gespeicherten Items mit Score (nicht nur letzter Lauf) — Digest akkumuliert über Zeit.
- `core/dedupe.py`: Zwei Stufen — exakter Hash-Vergleich (`sha256(source::external_id)`), dann
  Fuzzy-Match (rapidfuzz) über (Titel[:80], Auftraggeber) bei gleicher Frist. Schwellen: title ≥ 90, buyer ≥ 85.
- `core/scoring.py`: `Score.best = max(profile_spediteur, profile_kep)` ist der entscheidende Wert
  für `min_score`-Filter (config default 0.55). Web-Search (`web_search_20250305`) wird **nur** bei
  `item.signal_kind == "web_news"` aktiviert.
- `core/health.py`: Schreibt pro Lauf je Quelle eine Zeile in `health`-Tabelle. Alarm ab 3 konsekutiven
  Läufen unter `health_min_count` (config).
- `core/digest.py`: `write_dashboard()` schreibt `docs/index.html` + `docs/data.json` (GH Pages).
  HTML enthält clientseitiges Filter-JS; Daten werden als eingebettetes JSON-Array mitgeliefert.

### Scoring-Modell
`claude-haiku-4-5-20251001`, konfigurierbar via `config.yaml → scoring.model`.
System-Prompt erzwingt ausschließlich JSON-Ausgabe. Bei Parse-Fehler wird das Item verworfen (nicht geraten).

### Signals-Quelle
`signals.py` kombiniert drei Sub-Quellen:
- Re-Tender: TED-CANs von vor `retender_years` Jahren (default 4) als Prognose
- Stellenanzeigen: Bundesagentur (DE) + AMS (AT), nur wenn API-Keys gesetzt
- Web-News: Synthetische Items (feste Suchanfragen), die beim Scoring web_search auslösen

### oeffentlichevergabe.de — API-Umbau (Stand 2026-07)
Die OCDS-API `GET /api/1/tender` ist abgeschaltet (HTTP 404). Die Plattform heißt jetzt "Bekanntmachungsservice" und nutzt eine interne BKMSql-API:
- Endpunkt: `POST https://oeffentlichevergabe.de/bkmk/searches`
- Body: `{"SELECT":"ALL","WHERE":[...],"FROM":"lots","PAGE":{"number":0,"size":50},"ORDER":{...}}`
- WHERE-Filter-Felder: `allCpvCodes` (STARTS_WITH), `publicationDate` (>=), `allPlacesOfPerformanceNutsCodes` (STARTS_WITH), `noticeType` (IN)
- DACH NUTS-Präfixe: DEU→"DE", AUT→"AT", CHE→"CH"
- Response: `{"totalElements": N, "offset": 0, "elements": [...]}`
- Element-Felder: `noticeIdentifier`, `noticeTitle`/`lotTitle` (Array mit `{value, languageId}`), `buyers` (Array mit `{name}`), `mainCpvCode`, `publicationDate`, `noticeType`, `firstDeadline`, `deadlineReceiptTenders`, `placesOfPerformance`
- Notice-URL: `https://oeffentlichevergabe.de/ui/public/notices/{noticeIdentifier}`
- `external_id` = `{noticeIdentifier}/{lotIdentifier}` (Lot-Ebene, eindeutig pro Los)
- CPV-Codes werden via `_cpv_prefixes()` auf 2-stellige Präfixe reduziert (STARTS_WITH = Präfix-Matching)
- `cn-standard` Notices haben `firstDeadline`; `can-standard` Notices (Vergabebekanntmachungen) nicht

### TED API v3 — bekannte Inkompatibilitäten (Stand 2026-07)
Die TED v3 Search API hat sich mehrfach geändert. Aktuell gültige Einschränkungen:
- `scope`: Nur Werte 0..2 erlaubt (nicht 3). Wir nutzen 2 = alle Notices.
- Suchfeld für Land: `BUYER-COUNTRY` (nicht `OA-COUNTRY`)
- Suchfeld für CPV: `classification-cpv` (nicht `CPV_CODE`)
- Response-Felder: `BT-21-Procedure` (Titel), `organisation-name-buyer` (Käufer), `deadline-receipt-tender-date-lot` (Frist), `organisation-country-buyer` (Land), `classification-cpv` (CPV-Liste)
- Ungültige Notice-Types (werden abgelehnt): `cn-utilities`, `cn-defence`, `can-utilities`
- `sortField`/`sortOrder` sind nicht mehr unterstützt
- `BT-21-Procedure` liefert `{"deu": "Titel"}` oder `{"deu": ["Titel"]}` — beide Formen werden in `_title()` behandelt
- `organisation-name-buyer` liefert `{"deu": ["Käufername"]}` — `_buyer()` iteriert über Sprachen

### Bekannte Besonderheiten
- Datei `core/health 2.py` (mit Leerzeichen im Namen) ist ein Duplikat/Artefakt — ignorieren, nicht löschen ohne Absprache.
- `PYTHONPATH=src` muss bei direktem pytest-Aufruf gesetzt werden (Python 3.13 ignoriert pth-Dateien mit `_` im Paketname). `uv run pytest` setzt es automatisch via `pyproject.toml → tool.pytest.ini_options.pythonpath`.
- Tests sind `asyncio_mode = "auto"` (pytest-asyncio) — async-Testfunktionen brauchen kein `@pytest.mark.asyncio`.
- Der `store`-Fixture in `conftest.py` nutzt `tmp_path` (In-Memory-äquivalent via tempfile) — Tests schreiben nie in `data/agent3.db`.
