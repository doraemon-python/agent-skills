"""クロールの進捗（URL キュー・取得履歴・ホストごとの待ち時間）を SQLite で管理する。

CSV と違い、ここは「どこまで進んだか」を中断・再開に強い形で持つための場所。
1 回の取得ごとにコミットするので、レートリミットや Ctrl-C で止まっても次回そのまま続きから再開できる。
"""
import calendar
import sqlite3
import time
from pathlib import Path

from .util import host_of, iso_after, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  host TEXT NOT NULL,
  source_id TEXT NOT NULL,
  page_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued / done / failed / gone / blocked
  priority INTEGER NOT NULL DEFAULT 0,
  depth INTEGER NOT NULL DEFAULT 0,
  discovered_from INTEGER,                 -- 見つけたページの pages.id
  discovered_at TEXT NOT NULL,
  next_fetch_at TEXT,                      -- NULL ならすぐ取得してよい。再訪問の時刻にも使う
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_fetched_at TEXT,
  last_page_id INTEGER
);
CREATE INDEX IF NOT EXISTS urls_queue ON urls(status, priority DESC, id);
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,                  -- = page_id。1 回の取得で 1 行
  url_id INTEGER NOT NULL REFERENCES urls(id),
  url TEXT NOT NULL,
  final_url TEXT NOT NULL,
  source_id TEXT NOT NULL,
  page_type TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  http_status INTEGER NOT NULL,
  content_type TEXT,
  content_hash TEXT NOT NULL,
  text_hash TEXT,
  raw_path TEXT NOT NULL,                  -- データセットからの相対パス
  bytes INTEGER NOT NULL,
  extract_status TEXT NOT NULL,            -- pending / done / skipped / error / unchanged / not_target / superseded
  extracted_schema_version INTEGER,
  extracted_at TEXT,
  extracted_by TEXT,
  extract_note TEXT,
  record_count INTEGER
);
CREATE INDEX IF NOT EXISTS pages_extract ON pages(extract_status, id);
CREATE INDEX IF NOT EXISTS pages_url ON pages(url_id, id);
CREATE TABLE IF NOT EXISTS hosts (
  host TEXT PRIMARY KEY,
  next_allowed_at REAL NOT NULL DEFAULT 0, -- epoch 秒。これより前にはリクエストしない
  backoff_sec REAL NOT NULL DEFAULT 0,
  last_status INTEGER,
  note TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stop_reason TEXT,
  stats TEXT
);
CREATE TABLE IF NOT EXISTS assets (       -- file / image 列のためにダウンロードしたもの
  url TEXT PRIMARY KEY,
  raw_path TEXT NOT NULL,
  content_type TEXT,
  fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # --- meta ---
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    # --- URL キュー ---
    def enqueue(self, url: str, source_id: str, page_type: str, priority: int = 0,
                depth: int = 0, discovered_from: int | None = None) -> bool:
        """未登録の URL だけ追加する。追加したら True。"""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO urls(url, host, source_id, page_type, priority, depth, discovered_from, discovered_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (url, host_of(url), source_id, page_type, priority, depth, discovered_from, now_iso()),
        )
        return cur.rowcount > 0

    def requeue(self, url: str) -> bool:
        """取得済み・失敗の URL をもう一度キューに戻す。"""
        with self.db:
            cur = self.db.execute(
                "UPDATE urls SET status='queued', next_fetch_at=NULL, attempts=0 WHERE url=?", (url,))
        return cur.rowcount > 0

    def next_url(self, exclude_hosts: set[str] = frozenset()) -> sqlite3.Row | None:
        """今すぐ取得してよい URL を 1 件返す。取得中のホスト（exclude_hosts）と待機中のホストは除く。"""
        excl = ""
        if exclude_hosts:
            excl = f" AND u.host NOT IN ({','.join('?' for _ in exclude_hosts)})"
        return self.db.execute(
            "SELECT u.* FROM urls u LEFT JOIN hosts h ON h.host = u.host"
            " WHERE u.status='queued' AND (u.next_fetch_at IS NULL OR u.next_fetch_at <= ?)"
            " AND (h.next_allowed_at IS NULL OR h.next_allowed_at <= ?)" + excl +
            " ORDER BY u.priority DESC, u.id LIMIT 1",
            (now_iso(), time.time(), *exclude_hosts),
        ).fetchone()

    def seconds_until_next(self, exclude_hosts: set[str] = frozenset()) -> float | None:
        """次に取得できる URL が出てくるまでの秒数。対象の URL が無ければ None。"""
        rows = self.db.execute(
            "SELECT u.host, MIN(COALESCE(u.next_fetch_at, '')) AS next_fetch_at, h.next_allowed_at"
            " FROM urls u LEFT JOIN hosts h ON h.host = u.host WHERE u.status='queued' GROUP BY u.host"
        ).fetchall()
        now = time.time()
        waits = []
        for r in rows:
            if r["host"] in exclude_hosts:
                continue
            ready = r["next_allowed_at"] or 0.0
            if r["next_fetch_at"]:
                ready = max(ready, calendar.timegm(time.strptime(r["next_fetch_at"], "%Y-%m-%dT%H:%M:%SZ")))
            waits.append(ready - now)
        return max(0.0, min(waits)) if waits else None

    def mark_url(self, url_id: int, status: str, *, error: str | None = None,
                 next_fetch_in: float | None = None, page_id: int | None = None,
                 count_attempt: bool = False) -> None:
        next_at = iso_after(next_fetch_in) if next_fetch_in is not None else None
        sets = ["status=?", "next_fetch_at=?", "last_error=?"]
        args: list = [status, next_at, error]
        if page_id is not None:
            sets += ["last_page_id=?", "last_fetched_at=?", "attempts=0"]
            args += [page_id, now_iso()]
        if count_attempt:
            sets.append("attempts=attempts+1")
        with self.db:
            self.db.execute(f"UPDATE urls SET {', '.join(sets)} WHERE id=?", (*args, url_id))

    # --- ホストの待ち時間（レートリミット） ---
    def host(self, host: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM hosts WHERE host=?", (host,)).fetchone()

    def set_host(self, host: str, *, next_allowed_at: float, backoff_sec: float,
                 last_status: int | None, note: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO hosts(host, next_allowed_at, backoff_sec, last_status, note) VALUES(?, ?, ?, ?, ?)"
                " ON CONFLICT(host) DO UPDATE SET next_allowed_at=excluded.next_allowed_at,"
                " backoff_sec=excluded.backoff_sec, last_status=excluded.last_status, note=excluded.note",
                (host, next_allowed_at, backoff_sec, last_status, note),
            )

    # --- 取得したページ ---
    def last_page(self, url_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM pages WHERE url_id=? ORDER BY id DESC LIMIT 1", (url_id,)).fetchone()

    def add_page(self, **cols) -> int:
        keys = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        cur = self.db.execute(f"INSERT INTO pages({keys}) VALUES({marks})", tuple(cols.values()))
        return cur.lastrowid

    def page(self, page_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()

    def pending_pages(self, limit: int, source_id: str | None = None,
                      page_type: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM pages WHERE extract_status='pending'"
        args: list = []
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        if page_type:
            sql += " AND page_type=?"
            args.append(page_type)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        return self.db.execute(sql, args).fetchall()

    def finish_extract(self, page_id: int, status: str, schema_version: int, by: str,
                       note: str | None, record_count: int) -> None:
        with self.db:
            self.db.execute(
                "UPDATE pages SET extract_status=?, extracted_schema_version=?, extracted_at=?,"
                " extracted_by=?, extract_note=?, record_count=? WHERE id=?",
                (status, schema_version, now_iso(), by, note, record_count, page_id),
            )

    def mark_incomplete(self, page_id: int, note: str) -> None:
        """保存の途中で一時的な失敗があったページを抽出待ちに戻す（すでに抽出済みだった場合も）。"""
        with self.db:
            self.db.execute("UPDATE pages SET extract_status='pending', extract_note=? WHERE id=?", (note, page_id))

    def reset_extract(self, *, source_id: str | None = None, page_ids: list[int] | None = None,
                      below_schema: int | None = None) -> int:
        """各 URL の最新の取得結果を「抽出待ち」に戻す（再クロールせずに作り直すため）。"""
        sql = (
            "UPDATE pages SET extract_status='pending' WHERE extract_status IN ('done','skipped','error')"
            " AND id IN (SELECT MAX(id) FROM pages WHERE extract_status NOT IN ('unchanged','not_target')"
            " GROUP BY url_id)"
        )
        args: list = []
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        if page_ids:
            sql += f" AND id IN ({','.join('?' for _ in page_ids)})"
            args += page_ids
        if below_schema is not None:
            sql += " AND (extracted_schema_version IS NULL OR extracted_schema_version < ?)"
            args.append(below_schema)
        with self.db:
            return self.db.execute(sql, args).rowcount

    # --- ダウンロード済みファイル ---
    def asset(self, url: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM assets WHERE url=?", (url,)).fetchone()

    def add_asset(self, url: str, raw_path: str, content_type: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO assets(url, raw_path, content_type, fetched_at) VALUES(?, ?, ?, ?)",
                            (url, raw_path, content_type, now_iso()))

    # --- 実行履歴 ---
    def start_run(self, kind: str) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO runs(kind, started_at) VALUES(?, ?)", (kind, now_iso())).lastrowid

    def finish_run(self, run_id: int, stop_reason: str, stats: str) -> None:
        with self.db:
            self.db.execute("UPDATE runs SET finished_at=?, stop_reason=?, stats=? WHERE id=?",
                            (now_iso(), stop_reason, stats, run_id))
