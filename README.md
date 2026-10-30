# Agent Marketplace

An EU layer for defence and public-procurement suppliers: find EU tenders in any language, check eligibility,
build cross-border consortia through agent-to-agent deals, and prepare the paperwork.

The requirements spec is in [.claude/CLAUDE.md](.claude/CLAUDE.md).

## Run it

You need the .NET 10 SDK, Node 22 and Docker (or any Postgres 16+).

```bash
docker compose up -d                       # Postgres on localhost:5432 (marketplace / marketplace)

cd server/Api
# Optional: an OpenRouter key turns the AI features on. Without one, every feature uses its rule-based fallback.
cat > appsettings.Local.json <<'JSON'
{ "AI": { "ApiKey": "sk-or-...", "Model": "openai/gpt-4.1-mini" } }
JSON
dotnet run                                 # http://localhost:5080

cd ../../web
npm install
npm run dev                                # http://localhost:5173 (proxies /api to :5080)
```

`npm run build` puts the UI into `server/Api/wwwroot`, so `dotnet run` alone serves the whole app on :5080.

The database is kept between starts, so bid runs and won/lost results stay. Demo data is seeded only into an empty database. There are no migrations: after a schema change, start once with `Db__ResetOnStart=true` to recreate it (this deletes all data).

## How it is built

- `server/Api`: one .NET 10 minimal-API host. EF Core on Postgres, OpenRouter (any OpenAI-compatible endpoint)
  through Microsoft.Extensions.AI.
- `web`: Vue 3 + PrimeVue 4 + vue-i18n (Polish, English, German).
- `ingest`: Python tender ingestion pipeline. Pulls notices from TED, e-Zamówienia and 24 more EU sources into
  one database and keeps them up to date; see [ingest/README.md](ingest/README.md).
- **Demo sign-in**: the "Acting as" switcher picks the company; the API scopes every private query to it
  (EF Core query filters in `AppDb`, so isolation lives in the data layer).
- **Agent activity log**: every agent action is written through `ActivityLog` with agent, action and reason, and
  streamed live to the UI (server-sent events).

## Demo data

`demo-data/` holds the cached real notices the API loads on start, so the demo never depends on a live call:
TED eForms notices (including the demo tender 723644-2026, BAAINBw, 20 lots), 208 Polish defence TED notices,
and 567 e-Zamówienia BZP notices. `tools/prepare_demo_data.py` rebuilds it from the raw pull; see `demo-data/SOURCES.md`.
`POST /api/tenders/import/ted/{publicationNumber}` pulls a further notice live from the TED Search API.
`GET /api/tenders/{id}/ocds` returns any tender as an OCDS 1.1 release package.
