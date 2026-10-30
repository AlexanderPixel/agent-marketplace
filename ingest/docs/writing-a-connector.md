# Writing a connector

A connector teaches tenderhub to read one procurement source. The core (scheduling, storage, dedup,
version history, retries, rate limiting, defence classification, CLI) is done; a connector only maps
the source into `Notice` objects.

## Files you own

| File | Purpose |
|---|---|
| `src/tenderhub/connectors/<name>.py` | the connector (auto-discovered, no registry to edit) |
| `tests/connectors/test_<name>.py` | offline test replaying recorded responses with `respx` |
| `tests/fixtures/<name>/...` | small recorded responses (trim to a few records; no huge files) |

Do **not** edit anything else (core modules, other connectors, `pyproject.toml`). If the core is missing
something you need, work around it inside your module and say so in your report.

## Read first

- `src/tenderhub/connector.py` — the contract (`Connector`, `Checkpoint`, `StoredRef`, class attributes)
- `src/tenderhub/models.py` — `Notice`, `Document`, `NoticeType`, `Status`, `normalize_cpv`, `absolute_url`, `validate_notice`
- `src/tenderhub/connectors/ted.py` + `tests/connectors/test_ted.py` — the reference implementation and test
- `src/tenderhub/data/__init__.py` — `load_tenants(platform)` for multi-tenant platforms
- `tests/conftest.py` — `run_sync`, `load_json`, `load_text` helpers

## Rules

1. `sync(since, state)` is an **async generator**: yield every notice published *or changed* at/after `since`,
   and yield `Checkpoint(state)` whenever it is safe to resume. Page until the source is exhausted.
   If the source has no date filter, walk newest-first and stop once items are older than `since`.
2. Use `self.http` (rate-limited, retrying) for every request — never create your own client.
   `get_json`, `post_json`, `get_text`, `get`, `post` take the usual httpx kwargs (`params`, `json`, `data`, `headers`).
3. Map to `Notice` faithfully. Required: `source=self.name`, `source_id` (stable, unique within the source),
   absolute `url` to the human-readable notice, `title`. Fill everything else you reliably can:
   notice_type, buyer name/id/country (ISO alpha-2)/city, CPV (8 digits via `normalize_cpv`), deadline,
   published_at (**tz-aware**; use the source's local zone if it gives naive times, e.g. `ZoneInfo("Europe/Warsaw")`),
   value + currency, procedure type, `platform_url`, `documents`, `refs` (ocid / TED number / BZP number, for cross-source linking),
   `language`. Put a trimmed copy of the source record in `raw`.
4. `validate_notice(n)` must return `[]` for everything you yield; invalid notices are dropped by the runner.
5. Set `poll_interval`, `requests_per_second` (≤ 5, be polite; 0.5–2 is typical for scraped sites),
   `first_sync_lookback`, `kind` (`portal` | `platform` | `defence_agency` | `aggregator`), `countries`.
6. If notices change after publication (deadline extensions, Q&A, new documents) and the source lets you
   fetch one notice by id, set `supports_refresh = True` and implement `refresh(ref)`.
7. If the source needs an API key, declare it in `required_env` and read it from `self.ctx.env`. Never
   create accounts or invent keys; the connector is then skipped until someone provides the key.
8. Prefer official APIs, open-data feeds, RSS/Atom and the JSON endpoints a site's own frontend calls, over HTML scraping.
   Parse HTML with `selectolax` (`from selectolax.parser import HTMLParser`), XML with `lxml`.
   Available libraries: httpx, selectolax, lxml, python-dateutil, stdlib. Do not add dependencies.
9. A site that blocks plain HTTP clients (Cloudflare challenge, 403 for everything) — note it in your report;
   do not try to bypass bot protection or CAPTCHAs.

## Done means

```bash
uv run pytest -q tests/connectors/test_<name>.py tests/test_contract.py   # offline: passes
uv run pytest -q -m live -k <name>                                       # live: seen > 0, invalid == 0
uv run tenderhub --db /tmp/<name>.db sync <name> --since 3d               # real incremental sync works
uv run tenderhub --db /tmp/<name>.db sync <name>                          # second run: mostly unchanged
```
