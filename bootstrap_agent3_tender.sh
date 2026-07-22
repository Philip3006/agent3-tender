#!/usr/bin/env bash
# ---------------------------------------------------------------
# agent3-tender — Agent 3: Transport-/Logistik-Ausschreibungen
# Laeuft alle 2 Tage. Oeffentliche Hand (TED, oeffentlichevergabe.de)
# + Signal-Erkennung fuer private Nachfrage.
#
# Scaffoldet DIREKT in das aktuelle Verzeichnis (kein Unterordner).
# Nutzung:  bash bootstrap.sh          # ins aktuelle Verzeichnis
#           bash bootstrap.sh <pfad>   # optional: anderer Zielordner
# ---------------------------------------------------------------
set -euo pipefail

TARGET="${1:-.}"
mkdir -p "$TARGET"
cd "$TARGET"

echo "==> Scaffolde agent3-tender in: $(pwd)"

if [ -e "src/agent3_tender" ] || [ -e "pyproject.toml" ]; then
  echo "Abbruch: Hier existiert bereits ein Projekt (pyproject.toml oder src/)." >&2
  echo "Leg das Skript in einen leeren Ordner oder raeum vorher auf." >&2
  exit 1
fi

# --- Verzeichnisse --------------------------------------------------------
mkdir -p .github/workflows data tests
mkdir -p src/agent3_tender/core
mkdir -p src/agent3_tender/sources

find src/agent3_tender -type d -exec touch {}/__init__.py \;
touch tests/__init__.py
touch data/.gitkeep

# --- Platzhalter-Module ---------------------------------------------------
: > src/agent3_tender/core/models.py      # Pydantic: Item, Score, Digest
: > src/agent3_tender/core/store.py       # SQLite: seen(hash), items
: > src/agent3_tender/core/dedupe.py      # sha256(quelle+id) + fuzzy match
: > src/agent3_tender/core/scoring.py     # Claude API, striktes JSON-Schema
: > src/agent3_tender/core/digest.py      # Markdown-Report + Versand
: > src/agent3_tender/core/http.py        # httpx-Client, Retry/Backoff

: > src/agent3_tender/run.py
: > src/agent3_tender/sources/ted.py                   # api.ted.europa.eu/v3
: > src/agent3_tender/sources/oeffentlichevergabe.py   # OCDS
: > src/agent3_tender/sources/signals.py               # Bedarfsindikatoren (private Nachfrage)

cat > src/agent3_tender/config.yaml <<'YAML'
schedule: "0 6 */2 * *"        # alle 2 Tage, 06:00 UTC
lookback_days: 4              # Ueberlappung -> Dedupe faengt Wochenenden ab
countries: [DEU]
cpv:
  - "60000000"   # Transportdienstleistungen
  - "63000000"   # unterstuetzende Transportdienstleistungen
  - "64120000"   # Kurierdienste
notice_types: [pin, cn, can]  # can = Basis fuer Re-Tender-Prognose
min_score: 0.55
YAML

cat > pyproject.toml <<'TOML'
[project]
name = "agent3-tender"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "anthropic>=0.40",
]

[dependency-groups]
dev = ["ruff>=0.6", "pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.ruff.format]
quote-style = "double"
TOML

cat > .env.example <<'ENV'
ANTHROPIC_API_KEY=
SMTP_URL=
DIGEST_RECIPIENT=
ENV

cat > .gitignore <<'GIT'
.venv/
__pycache__/
*.pyc
.env
data/*.db
.ruff_cache/
.pytest_cache/
GIT

cat > CLAUDE.md <<'MD'
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
    uv run pytest
MD

cat > README.md <<'MD'
# agent3-tender
Agent 3 — Transport-/Logistik-Ausschreibungen, oeffentliche Hand + Signale, alle 2 Tage.
Setup: `uv sync && cp .env.example .env`
MD

cat > .github/workflows/agent3-tender.yml <<'YML'
name: agent3-tender
on:
  schedule:
    - cron: "0 6 */2 * *"
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run python -m agent3_tender.run
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
YML

git init -q 2>/dev/null || true

if command -v uv >/dev/null 2>&1; then
  echo "==> uv gefunden, synce Umgebung"
  uv sync
else
  echo "==> uv nicht installiert. Danach ausfuehren:"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync"
fi

echo
echo "==> Fertig. agent3-tender liegt in: $(pwd)"
echo "    cp .env.example .env"
echo "    claude"
