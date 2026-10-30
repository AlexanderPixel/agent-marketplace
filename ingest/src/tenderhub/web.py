"""Local read-only web viewer over the SQLite store: `tenderhub serve`.

Stdlib only. Opens the database read-only per request, so it can run while a sync/daemon is writing.
"""

from __future__ import annotations

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

PAGE_SIZE_MAX = 200
CLUSTER = "COALESCE(n.cluster_id, n.id)"  # notices stored before dedup ran count as their own cluster
SORTS = {
    "published": "n.published_at DESC",
    "deadline": "n.deadline_at IS NULL, n.deadline_at ASC",
    "value": "n.value_amount IS NULL, n.value_amount DESC",
    "seen": "n.first_seen_at DESC",
}
LIST_COLUMNS = ("id, source, source_id, url, title, notice_type, status, buyer_name, buyer_country, buyer_city, "
                "published_at, deadline_at, value_amount, value_currency, is_defence, defence_reasons, version, "
                "platform_url, first_seen_at, last_changed_at")


def fts_query(text: str) -> str:
    """User text -> safe FTS5 query: every word must match as a prefix."""
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)


class Viewer:
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).resolve())

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    def notices(self, q: dict[str, str]) -> dict[str, Any]:
        """Filters match against any copy of a tender; with merge on (default) each tender is listed once."""
        conds, params = [], []
        if text := fts_query(q.get("q", "")):
            conds.append("m.id IN (SELECT rowid FROM notices_fts WHERE notices_fts MATCH ?)")
            params.append(text)
        for key, col in (("source", "m.source"), ("country", "m.buyer_country"), ("status", "m.status"),
                         ("type", "m.notice_type")):
            if v := q.get(key):
                values = v.split(",")
                conds.append(f"{col} IN ({','.join('?' * len(values))})")
                params += values
        if q.get("defence") == "1":
            conds.append("m.is_defence = 1")
        if cpv := q.get("cpv"):
            conds.append("m.id IN (SELECT notice_id FROM notice_cpv WHERE cpv LIKE ?)")
            params.append(re.sub(r"\D", "", cpv) + "%")
        if q.get("upcoming") == "1":
            conds.append("m.deadline_at > strftime('%Y-%m-%dT%H:%M:%S', 'now')")
        match = " AND ".join(conds) or "1"
        if q.get("merge", "1") == "1":
            where = (f"(n.is_primary = 1 OR n.cluster_id IS NULL) AND {CLUSTER} IN "
                     f"(SELECT {CLUSTER.replace('n.', 'm.')} FROM notices m WHERE {match})")
        else:
            where = f"n.id IN (SELECT m.id FROM notices m WHERE {match})"
        size = min(int(q.get("size") or 50), PAGE_SIZE_MAX)
        page = max(int(q.get("page") or 1), 1)
        order = SORTS.get(q.get("sort", ""), SORTS["published"])
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM notices n WHERE {where}", params).fetchone()[0]
            rows = db.execute(
                f"""SELECT {LIST_COLUMNS},
                       (SELECT group_concat(DISTINCT c.source) FROM notices c
                         WHERE c.cluster_id = n.cluster_id AND c.source != n.source) AS also_on,
                       (SELECT COUNT(*) FROM notices c WHERE c.cluster_id = n.cluster_id) AS copies
                    FROM notices n WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?""",
                [*params, size, (page - 1) * size]).fetchall()
        return {"total": total, "page": page, "size": size, "rows": [dict(r) for r in rows]}

    def notice(self, nid: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone()
            if not row:
                return None
            d = dict(row)
            for k in ("raw", "refs", "defence_reasons"):
                d[k] = json.loads(d[k] or "null")
            d["cpv_codes"] = [r[0] for r in db.execute("SELECT cpv FROM notice_cpv WHERE notice_id=?", (nid,))]
            d["documents"] = [dict(r) for r in db.execute("SELECT url, title, kind FROM documents WHERE notice_id=?", (nid,))]
            d["versions"] = [dict(r) for r in db.execute(
                "SELECT version, captured_at FROM notice_versions WHERE notice_id=? ORDER BY version", (nid,))]
            d["copies"] = [dict(r) for r in db.execute(
                """SELECT id, source, source_id, url, title, notice_type, status, published_at, deadline_at
                   FROM notices WHERE cluster_id = ? AND id != ? ORDER BY published_at DESC""",
                (row["cluster_id"], nid))] if row["cluster_id"] else []
        return d

    def stats(self) -> dict[str, Any]:
        with self.connect() as db:
            totals = dict(db.execute(
                """SELECT COUNT(*) AS notices, SUM(is_primary = 1 OR cluster_id IS NULL) AS tenders,
                          SUM(status='open') AS open, SUM(is_defence) AS defence,
                          COUNT(DISTINCT source) AS sources, COUNT(DISTINCT buyer_country) AS countries,
                          COUNT(DISTINCT buyer_name) AS buyers FROM notices""").fetchone())
            sources = [dict(r) for r in db.execute(
                """SELECT n.source, COUNT(*) AS notices, SUM(n.status='open') AS open, SUM(n.is_defence) AS defence,
                          MAX(n.published_at) AS latest, s.last_success_at, s.last_error, s.next_run_at
                   FROM notices n LEFT JOIN sync_state s ON s.source = n.source
                   GROUP BY n.source ORDER BY notices DESC""")]
            failing = [dict(r) for r in db.execute(
                """SELECT source, last_error, last_run_at FROM sync_state
                   WHERE last_error IS NOT NULL AND source NOT IN (SELECT DISTINCT source FROM notices)""")]
            countries = [dict(r) for r in db.execute(
                """SELECT buyer_country AS country, COUNT(*) AS notices FROM notices WHERE buyer_country IS NOT NULL
                   GROUP BY buyer_country ORDER BY notices DESC""")]
            running = [dict(r) for r in db.execute(
                "SELECT source, kind, started_at FROM sync_runs WHERE status='running' ORDER BY started_at")]
            types = [dict(r) for r in db.execute(
                "SELECT notice_type AS type, COUNT(*) AS notices FROM notices GROUP BY notice_type ORDER BY notices DESC")]
        return {"totals": totals, "sources": sources, "failing": failing, "countries": countries,
                "running": running, "types": types}


def make_handler(viewer: Viewer) -> type[BaseHTTPRequestHandler]:
    page = (resources.files("tenderhub") / "static" / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # keep the terminal quiet
            pass

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, data: Any, code: int = 200) -> None:
            self.send(code, json.dumps(data, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/":
                    self.send(200, page, "text/html; charset=utf-8")
                elif url.path == "/api/notices":
                    self.json(viewer.notices(q))
                elif url.path == "/api/stats":
                    self.json(viewer.stats())
                elif url.path == "/api/notice":
                    d = viewer.notice(int(q.get("id", 0)))
                    self.json(d) if d else self.json({"error": "not found"}, 404)
                else:
                    self.json({"error": "not found"}, 404)
            except sqlite3.OperationalError as e:
                self.json({"error": str(e)}, 400)
            except (ValueError, KeyError) as e:
                self.json({"error": f"bad request: {e}"}, 400)

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    if not Path(db_path).exists():
        raise SystemExit(f"{db_path} does not exist yet — run `tenderhub sync` first")
    server = ThreadingHTTPServer((host, port), make_handler(Viewer(db_path)))
    print(f"tenderhub viewer on http://{host}:{port}  (db: {db_path})", flush=True)
    server.serve_forever()
