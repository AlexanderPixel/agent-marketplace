"""SQLite storage: notices with version history, sync state per source, run log, full-text search."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .dedupe import match_keys
from .models import Notice

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS notices (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    notice_type     TEXT NOT NULL,
    status          TEXT NOT NULL,
    description     TEXT,
    buyer_name      TEXT,
    buyer_id        TEXT,
    buyer_country   TEXT,
    buyer_city      TEXT,
    procedure_type  TEXT,
    legal_basis     TEXT,
    published_at    TEXT,
    deadline_at     TEXT,
    value_amount    REAL,
    value_currency  TEXT,
    platform_url    TEXT,
    language        TEXT,
    is_defence      INTEGER NOT NULL DEFAULT 0,
    defence_reasons TEXT,
    refs            TEXT NOT NULL DEFAULT '{}',
    raw             TEXT NOT NULL DEFAULT '{}',
    content_hash    TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    last_changed_at TEXT NOT NULL,
    last_refreshed_at TEXT,
    cluster_id      INTEGER,            -- same tender across sources/notices (see dedupe.py)
    is_primary      INTEGER NOT NULL DEFAULT 0,  -- the one notice shown per cluster
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS notices_published ON notices (published_at);
CREATE INDEX IF NOT EXISTS notices_deadline  ON notices (deadline_at);
CREATE INDEX IF NOT EXISTS notices_country   ON notices (buyer_country, published_at);
CREATE INDEX IF NOT EXISTS notices_defence   ON notices (is_defence, published_at);
CREATE INDEX IF NOT EXISTS notices_changed   ON notices (last_changed_at);

CREATE TABLE IF NOT EXISTS notice_keys (
    key       TEXT NOT NULL,
    notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    PRIMARY KEY (key, notice_id)
);
CREATE INDEX IF NOT EXISTS notice_keys_notice ON notice_keys (notice_id);

CREATE TABLE IF NOT EXISTS notice_versions (
    notice_id    INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    data         TEXT NOT NULL,        -- normalized notice JSON (without raw)
    captured_at  TEXT NOT NULL,
    PRIMARY KEY (notice_id, version)
);

CREATE TABLE IF NOT EXISTS notice_cpv (
    notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    cpv       TEXT NOT NULL,
    PRIMARY KEY (notice_id, cpv)
);
CREATE INDEX IF NOT EXISTS notice_cpv_code ON notice_cpv (cpv);

CREATE TABLE IF NOT EXISTS documents (
    notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    url       TEXT NOT NULL,
    title     TEXT,
    kind      TEXT,
    PRIMARY KEY (notice_id, url)
);

-- deep capture of a tender (enrich.py): its pages as readable markdown, and its attachments
CREATE TABLE IF NOT EXISTS notice_pages (
    notice_id  INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    url        TEXT NOT NULL,
    title      TEXT,
    markdown   TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (notice_id, url)
);

CREATE TABLE IF NOT EXISTS attachments (
    id           INTEGER PRIMARY KEY,
    notice_id    INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    url          TEXT NOT NULL,
    filename     TEXT NOT NULL,
    folder       TEXT,                -- e.g. "Vergabeunterlagen/Version 2" for amendment rounds
    listed_size  TEXT,
    listed_date  TEXT,
    size         INTEGER,
    content_type TEXT,
    sha256       TEXT,
    path         TEXT,                -- local copy
    text         TEXT,                -- extracted text
    pages        INTEGER,
    error        TEXT,
    fetched_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attachments_notice ON attachments (notice_id);

CREATE TABLE IF NOT EXISTS sync_state (
    source          TEXT PRIMARY KEY,
    state           TEXT NOT NULL DEFAULT '{}',
    high_water      TEXT,               -- everything published before this has been synced
    last_run_at     TEXT,
    last_success_at TEXT,
    last_error      TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_run_at     TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- sync | refresh
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- running | ok | error
    since       TEXT,
    seen        INTEGER NOT NULL DEFAULT 0,
    inserted    INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    invalid     INTEGER NOT NULL DEFAULT 0,
    requests    INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS notices_fts USING fts5(
    title, description, buyer_name, content='notices', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS notices_ai AFTER INSERT ON notices BEGIN
    INSERT INTO notices_fts(rowid, title, description, buyer_name) VALUES (new.id, new.title, new.description, new.buyer_name);
END;
CREATE TRIGGER IF NOT EXISTS notices_ad AFTER DELETE ON notices BEGIN
    INSERT INTO notices_fts(notices_fts, rowid, title, description, buyer_name) VALUES ('delete', old.id, old.title, old.description, old.buyer_name);
END;
CREATE TRIGGER IF NOT EXISTS notices_au AFTER UPDATE OF title, description, buyer_name ON notices BEGIN
    INSERT INTO notices_fts(notices_fts, rowid, title, description, buyer_name) VALUES ('delete', old.id, old.title, old.description, old.buyer_name);
    INSERT INTO notices_fts(rowid, title, description, buyer_name) VALUES (new.id, new.title, new.description, new.buyer_name);
END;
"""

UpsertResult = Literal["inserted", "updated", "unchanged"]
_FIELDS = ("url", "title", "notice_type", "status", "description", "buyer_name", "buyer_id", "buyer_country",
           "buyer_city", "procedure_type", "legal_basis", "published_at", "deadline_at", "value_amount",
           "value_currency", "platform_url", "language")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _iso(v: Any) -> Any:
    return v.astimezone(UTC).isoformat(timespec="seconds") if isinstance(v, datetime) else v


@dataclass
class SourceState:
    source: str
    state: dict[str, Any]
    high_water: datetime | None
    next_run_at: datetime | None
    consecutive_failures: int


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        # long busy timeout: the daemon, `dedupe` and the viewer can all have the file open at once
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(notices)")}
        if "cluster_id" not in cols:
            self.db.execute("ALTER TABLE notices ADD COLUMN cluster_id INTEGER")
        if "is_primary" not in cols:
            self.db.execute("ALTER TABLE notices ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")
        self.db.execute("CREATE INDEX IF NOT EXISTS notices_cluster ON notices (cluster_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS notices_primary ON notices (is_primary, published_at)")

    def close(self) -> None:
        self.db.close()

    # ---------- notices ----------

    def upsert(self, n: Notice, is_defence: bool = False, defence_reasons: list[str] | None = None) -> UpsertResult:
        now = utcnow()
        h = n.content_hash()
        row = self.db.execute("SELECT id, content_hash, version FROM notices WHERE source=? AND source_id=?",
                              (n.source, n.source_id)).fetchone()
        values = {f: _iso(getattr(n, f)) for f in _FIELDS}
        values |= {"is_defence": int(is_defence), "defence_reasons": json.dumps(defence_reasons or []),
                   "refs": json.dumps(n.refs, ensure_ascii=False),
                   "raw": json.dumps(n.raw, ensure_ascii=False, default=str), "content_hash": h}
        normalized = n.to_dict()
        normalized.pop("raw")
        snapshot = json.dumps(normalized, ensure_ascii=False, default=str)

        self.db.execute("BEGIN")
        try:
            if row is None:
                cols = ["source", "source_id", *values, "version", "first_seen_at", "last_seen_at", "last_changed_at"]
                params = [n.source, n.source_id, *values.values(), 1, now, now, now]
                cur = self.db.execute(f"INSERT INTO notices ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", params)
                nid, version, result = cur.lastrowid, 1, "inserted"
            elif row["content_hash"] == h:
                self.db.execute("UPDATE notices SET last_seen_at=?, raw=? WHERE id=?", (now, values["raw"], row["id"]))
                self.db.execute("COMMIT")
                return "unchanged"
            else:
                nid, version, result = row["id"], row["version"] + 1, "updated"
                sets = ",".join(f"{k}=?" for k in values)
                self.db.execute(f"UPDATE notices SET {sets}, version=?, last_seen_at=?, last_changed_at=? WHERE id=?",
                                [*values.values(), version, now, now, nid])
                self.db.execute("DELETE FROM notice_cpv WHERE notice_id=?", (nid,))
                self.db.execute("DELETE FROM documents WHERE notice_id=?", (nid,))
            self.db.execute("INSERT INTO notice_versions VALUES (?,?,?,?,?)", (nid, version, h, snapshot, now))
            self.db.executemany("INSERT OR IGNORE INTO notice_cpv VALUES (?,?)", [(nid, c) for c in n.cpv_codes])
            self.db.executemany("INSERT OR IGNORE INTO documents VALUES (?,?,?,?)",
                                [(nid, d.url, d.title, d.kind) for d in n.documents])
            self._link(nid, match_keys(n.source, n.source_id, n.refs, n.buyer_country, n.title,
                                       values["deadline_at"]))
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # ---------- cross-source duplicates ----------

    def _link(self, nid: int, keys: set[str]) -> int:
        """Attach notice `nid` to every cluster sharing one of its match keys, merging clusters. Returns cluster id."""
        self.db.execute("DELETE FROM notice_keys WHERE notice_id=?", (nid,))
        self.db.executemany("INSERT OR IGNORE INTO notice_keys VALUES (?,?)", [(k, nid) for k in keys])
        own = self.db.execute("SELECT cluster_id FROM notices WHERE id=?", (nid,)).fetchone()[0] or nid
        clusters = {own}
        if keys:
            marks = ",".join("?" * len(keys))
            clusters |= {r[0] or r[1] for r in self.db.execute(
                f"""SELECT DISTINCT n.cluster_id, n.id FROM notice_keys k JOIN notices n ON n.id = k.notice_id
                    WHERE k.key IN ({marks}) AND k.notice_id != ?""", [*keys, nid])}
        target = min(clusters)
        others = sorted(clusters - {target})
        self.db.execute("UPDATE notices SET cluster_id=? WHERE id=?", (target, nid))
        if others:
            marks = ",".join("?" * len(others))
            self.db.execute(f"UPDATE notices SET cluster_id=? WHERE cluster_id IN ({marks}) OR id IN ({marks})",
                            [target, *others, *others])
        self._pick_primary(target)
        return target

    def _pick_primary(self, cluster_id: int) -> None:
        """Newest notice wins (an award supersedes its tender); ties go to the most complete copy."""
        self.db.execute(
            """UPDATE notices SET is_primary = (id = (
                   SELECT id FROM notices WHERE cluster_id = :c
                   ORDER BY published_at IS NULL, published_at DESC,
                            (description IS NOT NULL) + (deadline_at IS NOT NULL) + (value_amount IS NOT NULL)
                              + (buyer_name IS NOT NULL) + (buyer_city IS NOT NULL) DESC, id
                   LIMIT 1))
               WHERE cluster_id = :c""", {"c": cluster_id})

    def relink_all(self) -> tuple[int, int]:
        """Rebuild every cluster from scratch (after changing match rules, or for notices stored before dedup).
        Returns (notices, clusters). Commits in batches of 500 so a running sync is never blocked for long;
        every notice starts as its own primary, so readers see an unmerged list rather than an empty one."""
        self.db.execute("BEGIN")
        self.db.execute("DELETE FROM notice_keys")
        self.db.execute("UPDATE notices SET cluster_id = id, is_primary = 1")
        self.db.execute("COMMIT")
        rows = self.db.execute(
            "SELECT id, source, source_id, refs, buyer_country, title, deadline_at FROM notices ORDER BY id").fetchall()
        for start in range(0, len(rows), 500):
            self.db.execute("BEGIN")
            try:
                for r in rows[start:start + 500]:
                    self._link(r["id"], match_keys(r["source"], r["source_id"], json.loads(r["refs"]),
                                                   r["buyer_country"], r["title"], r["deadline_at"]))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        clusters = self.db.execute("SELECT COUNT(*) FROM notices WHERE is_primary = 1").fetchone()[0]
        return len(rows), clusters

    def mark_refreshed(self, source: str, source_id: str) -> None:
        self.db.execute("UPDATE notices SET last_refreshed_at=? WHERE source=? AND source_id=?",
                        (utcnow(), source, source_id))

    def set_status(self, source: str, source_id: str, status: str) -> None:
        self.db.execute("UPDATE notices SET status=? WHERE source=? AND source_id=?", (status, source, source_id))

    def close_expired(self) -> int:
        """Open notices whose deadline has passed become closed."""
        return self.db.execute("UPDATE notices SET status='closed' WHERE status='open' AND deadline_at < ?",
                               (utcnow(),)).rowcount

    def refresh_candidates(self, source: str, older_than: datetime, limit: int) -> list[sqlite3.Row]:
        """Still-open notices not refreshed since `older_than`, soonest deadline first."""
        return self.db.execute(
            """SELECT source_id, url, refs, raw FROM notices
               WHERE source=? AND status IN ('open','unknown') AND (deadline_at IS NULL OR deadline_at > ?)
                 AND notice_type IN ('tender','plan')
                 AND COALESCE(last_refreshed_at, first_seen_at) < ?
               ORDER BY deadline_at IS NULL, deadline_at LIMIT ?""",
            (source, utcnow(), _iso(older_than), limit)).fetchall()

    def search(self, query: str | None = None, *, country: str | None = None, defence: bool | None = None,
               open_only: bool = False, source: str | None = None, cpv_prefix: str | None = None,
               limit: int = 50) -> list[sqlite3.Row]:
        where, params = [], []
        if query:
            where.append("n.id IN (SELECT rowid FROM notices_fts WHERE notices_fts MATCH ?)")
            params.append(query)
        if country:
            where.append("n.buyer_country = ?"); params.append(country.upper())
        if defence is not None:
            where.append("n.is_defence = ?"); params.append(int(defence))
        if open_only:
            where.append("n.status = 'open'")
        if source:
            where.append("n.source = ?"); params.append(source)
        if cpv_prefix:
            where.append("n.id IN (SELECT notice_id FROM notice_cpv WHERE cpv LIKE ?)"); params.append(cpv_prefix + "%")
        sql = "SELECT n.* FROM notices n" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY n.published_at DESC LIMIT ?"
        return self.db.execute(sql, [*params, limit]).fetchall()

    # ---------- sync state ----------

    def get_state(self, source: str) -> SourceState:
        row = self.db.execute("SELECT * FROM sync_state WHERE source=?", (source,)).fetchone()
        if row is None:
            return SourceState(source, {}, None, None, 0)
        parse = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
        return SourceState(source, json.loads(row["state"]), parse(row["high_water"]), parse(row["next_run_at"]),
                           row["consecutive_failures"])

    def save_checkpoint(self, source: str, state: dict[str, Any]) -> None:
        self.db.execute("""INSERT INTO sync_state (source, state) VALUES (?, ?)
                           ON CONFLICT(source) DO UPDATE SET state=excluded.state""",
                        (source, json.dumps(state, default=str)))

    def finish_sync(self, source: str, *, ok: bool, high_water: datetime | None, next_run_at: datetime,
                    error: str | None = None) -> None:
        now = utcnow()
        self.db.execute("INSERT OR IGNORE INTO sync_state (source) VALUES (?)", (source,))
        if ok:
            self.db.execute("""UPDATE sync_state SET high_water=COALESCE(?, high_water), last_run_at=?, last_success_at=?,
                               last_error=NULL, consecutive_failures=0, next_run_at=? WHERE source=?""",
                            (_iso(high_water), now, now, _iso(next_run_at), source))
        else:
            self.db.execute("""UPDATE sync_state SET last_run_at=?, last_error=?, consecutive_failures=consecutive_failures+1,
                               next_run_at=? WHERE source=?""", (now, error, _iso(next_run_at), source))

    def reset_source(self, source: str) -> None:
        self.db.execute("DELETE FROM sync_state WHERE source=?", (source,))

    # ---------- run log ----------

    def start_run(self, source: str, kind: str, since: datetime | None) -> int:
        return self.db.execute("INSERT INTO sync_runs (source, kind, started_at, status, since) VALUES (?,?,?,?,?)",
                               (source, kind, utcnow(), "running", _iso(since))).lastrowid

    def finish_run(self, run_id: int, *, status: str, seen: int, inserted: int, updated: int, invalid: int,
                   requests: int, error: str | None = None) -> None:
        self.db.execute("""UPDATE sync_runs SET finished_at=?, status=?, seen=?, inserted=?, updated=?, invalid=?,
                           requests=?, error=? WHERE id=?""",
                        (utcnow(), status, seen, inserted, updated, invalid, requests, error, run_id))

    def stats(self) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT n.source, COUNT(*) AS notices, SUM(n.status='open') AS open, SUM(n.is_defence) AS defence,
                      MAX(n.published_at) AS latest, s.last_success_at, s.last_error, s.next_run_at
               FROM notices n LEFT JOIN sync_state s ON s.source = n.source
               GROUP BY n.source ORDER BY notices DESC""").fetchall()
