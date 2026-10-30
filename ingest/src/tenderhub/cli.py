"""Command line: tenderhub sources | sync | refresh | daemon | search | stats | show."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta

from . import registry
from .runner import Runner
from .store import Store


def parse_since(v: str) -> datetime:
    """'3d', '12h', '90m' relative to now, or an ISO date/datetime."""
    if m := re.fullmatch(r"(\d+)([dhm])", v):
        n, unit = int(m.group(1)), m.group(2)
        unit_name = {"d": "days", "h": "hours", "m": "minutes"}[unit]
        return datetime.now(UTC) - timedelta(**{unit_name: n})
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _names(args: argparse.Namespace) -> list[str]:
    names = args.sources or list(registry.all_connectors())
    for n in names:
        registry.get(n)  # fail fast on typos
    return names


async def _with_runner(args: argparse.Namespace, fn):
    store = Store(args.db)
    runner = Runner(store, concurrency=args.concurrency)
    try:
        return await fn(runner)
    finally:
        await runner.aclose()
        store.close()


def cmd_sources(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    for name, cls in registry.all_connectors().items():
        missing = cls.missing_env(env)
        print(f"{name:28} {','.join(cls.countries):12} {cls.kind:15} every {str(cls.poll_interval):>9}  "
              f"{'refresh ' if cls.supports_refresh else ''}{'NEEDS ' + ','.join(missing) if missing else ''}  {cls.title}")
    for mod, err in registry.import_errors.items():
        print(f"{mod:28} IMPORT ERROR: {err}", file=sys.stderr)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    since = parse_since(args.since) if args.since else None

    async def go(r: Runner):
        sem = asyncio.Semaphore(args.concurrency)

        async def one(n: str):
            async with sem:
                res = await r.sync(n, since=since, max_notices=args.limit, full=args.full)
                print(res.line(), flush=True)
                for p in res.problems[:5]:
                    print(f"    invalid: {p}")
                return res
        return await asyncio.gather(*(one(n) for n in _names(args)))

    results = asyncio.run(_with_runner(args, go))
    return 1 if any(r.status == "error" for r in results) else 0


def cmd_refresh(args: argparse.Namespace) -> int:
    async def go(r: Runner):
        return [await r.refresh(n, limit=args.limit) for n in _names(args)]
    results = asyncio.run(_with_runner(args, go))
    for res in results:
        print(res.line())
    return 1 if any(r.status == "error" for r in results) else 0


def cmd_daemon(args: argparse.Namespace) -> int:
    asyncio.run(_with_runner(args, lambda r: r.daemon(args.sources or None, tick=args.tick)))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = Store(args.db)
    rows = store.search(args.query, country=args.country, defence=True if args.defence else None,
                        open_only=args.open, source=args.source, cpv_prefix=args.cpv, limit=args.limit)
    for r in rows:
        if args.json:
            d = dict(r); d.pop("raw"); print(json.dumps(d, ensure_ascii=False))
        else:
            dl = (r["deadline_at"] or "")[:10]
            print(f"{(r['published_at'] or '')[:10]} {r['buyer_country'] or '--'} {r['status']:8} {dl:10} "
                  f"{'D ' if r['is_defence'] else '  '}{r['source']:18} {r['title'][:90]}\n{'':40}{r['url']}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    store = Store(args.db)
    print(f"{'source':28} {'notices':>8} {'open':>6} {'def':>6}  latest      last ok              error")
    for r in store.stats():
        print(f"{r['source']:28} {r['notices']:>8} {r['open'] or 0:>6} {r['defence'] or 0:>6}  "
              f"{(r['latest'] or '')[:10]:10}  {(r['last_success_at'] or '')[:19]:19}  {(r['last_error'] or '')[:60]}")
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    from .enrich import enrich
    store = Store(args.db)
    row = store.db.execute("SELECT id FROM notices WHERE source=? AND source_id=?", (args.source, args.source_id)).fetchone()
    if not row:
        print("not found", file=sys.stderr)
        return 1
    res = enrich(store, row["id"])
    for p in res.pages:
        print(f"page  {len(p['markdown']):>7} chars  {p['url']}")
    for a in res.attachments:
        where = f"{a['folder']}/" if a["folder"] else ""
        status = f"ERROR {a['error']}" if a["error"] else f"{a['size']:>9} B  {a['chars']:>7} chars text"
        print(f"file  {status}  {where}{a['filename']}")
    return 0


def cmd_dedupe(args: argparse.Namespace) -> int:
    notices, clusters = Store(args.db).relink_all()
    print(f"{notices} notices -> {clusters} unique tenders ({notices - clusters} duplicates merged)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web import serve
    serve(args.db, args.host, args.port)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = Store(args.db)
    row = store.db.execute("SELECT * FROM notices WHERE source=? AND source_id=?", (args.source, args.source_id)).fetchone()
    if not row:
        print("not found", file=sys.stderr)
        return 1
    d = dict(row)
    d["raw"] = json.loads(d["raw"])
    d["versions"] = [dict(v) for v in store.db.execute(
        "SELECT version, captured_at, content_hash FROM notice_versions WHERE notice_id=? ORDER BY version", (row["id"],))]
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tenderhub")
    p.add_argument("--db", default=os.environ.get("TENDERHUB_DB", "tenderhub.db"))
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--concurrency", type=int, default=8, help="sources synced at the same time")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources", help="list connectors").set_defaults(fn=cmd_sources)

    s = sub.add_parser("sync", help="incremental sync (all sources if none given)")
    s.add_argument("sources", nargs="*")
    s.add_argument("--since", help="backfill window: 3d, 12h, or ISO date")
    s.add_argument("--limit", type=int, help="stop after N notices (smoke test; high-water not advanced)")
    s.add_argument("--full", action="store_true", help="ignore saved cursor and high-water mark")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("refresh", help="re-fetch open notices to pick up changes")
    s.add_argument("sources", nargs="*")
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(fn=cmd_refresh)

    s = sub.add_parser("daemon", help="keep everything up to date forever")
    s.add_argument("sources", nargs="*")
    s.add_argument("--tick", type=float, default=60.0)
    s.set_defaults(fn=cmd_daemon)

    s = sub.add_parser("search", help="query stored notices")
    s.add_argument("query", nargs="?", help="full-text query (SQLite FTS5 syntax)")
    s.add_argument("--country"); s.add_argument("--source"); s.add_argument("--cpv", help="CPV prefix, e.g. 35")
    s.add_argument("--defence", action="store_true"); s.add_argument("--open", action="store_true")
    s.add_argument("--limit", type=int, default=30); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    sub.add_parser("stats", help="per-source counts and sync health").set_defaults(fn=cmd_stats)

    s = sub.add_parser("enrich", help="capture the pages and attachments of one tender (and its copies on other sources)")
    s.add_argument("source"); s.add_argument("source_id")
    s.set_defaults(fn=cmd_enrich)

    sub.add_parser("dedupe", help="rebuild cross-source duplicate clusters for all stored notices").set_defaults(fn=cmd_dedupe)

    s = sub.add_parser("serve", help="browse stored notices in a local web viewer")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("show", help="print one stored notice with its version history")
    s.add_argument("source"); s.add_argument("source_id")
    s.set_defaults(fn=cmd_show)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
