# ingest — tender ingestion pipeline

Pulls public-procurement notices from 26 EU sources into one SQLite database and keeps them up to date.
This is the P0 "tender aggregator" from [the spec](../.claude/CLAUDE.md): TED and e-Zamówienia, plus the
Polish e-procurement platforms where most below-threshold and military-unit tenders actually run, and the
national systems of other EU countries.

## Run it

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.12+ and the dependencies).

```bash
cd ingest
uv sync
uv run tenderhub sync --since 3d      # backfill the last 3 days from every source
uv run tenderhub daemon               # keep every source up to date (re-syncs on each source's own interval)
uv run tenderhub serve                # browse what is stored: http://127.0.0.1:8765
```

Other commands: `tenderhub sources` (list connectors), `sync <source> ...` (only some sources),
`search "amunicja" --defence --open`, `stats`, `show <source> <id>`, `refresh` (re-fetch open notices),
`dedupe` (rebuild cross-source duplicate clusters). The database defaults to `tenderhub.db`
(`--db` or `TENDERHUB_DB` to change it).

## Sources

| Area | Sources |
|---|---|
| EU-wide | TED, EU Funding & Tenders (EDF / EDIP calls, EU institution tenders) |
| Poland | BZP (e-Zamówienia), platformazakupowa.pl, ezamawiajacy.pl, Logintrade, eB2B, SmartPZP, e-ProPublico |
| Germany | oeffentlichevergabe.de (all German notices), e-Vergabe Bund (BAAINBw) |
| France | BOAMP, achats.defense.gouv.fr (Ministère des Armées) |
| Other | UK Find a Tender + Contracts Finder, NL TenderNed, ES PLACSP, NO Doffin, FI Hilma, DK udbud.dk, EE, LT, LV, CZ, RO national systems, UA Prozorro |

Agencja Uzbrojenia, NSPA and EDA are P2 in the spec and not included. `sources/` has the wider registry this
list was chosen from, mined from a year of TED and six months of BZP: every e-procurement platform in use and
every defence buyer.

## How it works

- **Connectors** (`src/tenderhub/connectors/`): one module per source, discovered automatically. Each maps its
  source into one `Notice` model (buyer, country, CPV, deadline, value, documents, cross-references).
  Adding a source: [docs/writing-a-connector.md](docs/writing-a-connector.md).
- **Incremental sync**: each source keeps a high-water mark plus a resumable cursor; every run re-reads an overlap
  window to catch late changes. Sources that support it re-fetch still-open notices to pick up deadline shifts,
  clarifications and new documents.
- **Storage**: SQLite with version history (a new version only when content really changes), CPV and document
  tables, full-text search, and a log of every sync run.
- **Duplicates**: the same tender published on several sources (TED and the national portal, BZP and the platform
  running it) becomes one cluster, matched on TED number, eForms procedure/notice id, OCDS ocid, BZP number, or
  country + deadline + title.
- **Defence flag**: directive 2009/81 legal basis, CPV 35*, or a defence buyer (military units, BAAINBw, DGA, ...).

## Tests

```bash
uv run pytest -q              # offline: core + recorded responses for every connector
uv run pytest -q -m live      # hits the real sources
```

Not yet wired into `server/Api`: the platform's API still reads `demo-data/`. A follow-up can feed this
database into the Postgres tender store.
