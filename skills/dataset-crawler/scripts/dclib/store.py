"""抽出結果を検証して CSV・files/ に保存し、判断ログを残す。入力形式は references/dataset-layout.md。"""
import asyncio
import csv
import io
import json
import math
import shutil
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .config import FILE_TYPES, SYSTEM_COLUMNS, Column, Dataset, Table
from .fetcher import AsyncFetcher, ext_for, host_wait, load_raw, record_response, save_raw
from .state import State
from .util import append_jsonl, atomic_write_bytes, host_of, now_iso, read_jsonl, record_id, sha256_hex

ENVELOPE_STATUSES = {"done", "skipped", "error"}
CONFIDENCES = {"high", "medium", "low"}


class StoreError(Exception):
    pass


class TransientFileError(Exception):
    """一時的な理由でファイルを用意できなかった。既存のファイルを残し、ページは抽出待ちのままにする。"""

# 時間をおけば取れる可能性がある失敗。これ以外（404 / 410 / robots.txt など）はそのファイルを諦める
TRANSIENT_STATUSES = {0, 403, 408, 429} | set(range(500, 600))


# ---------------------------------------------------------------- CSV テーブル

class TableFile:
    """1 テーブル = data/<name>.csv。読み込んで _id で上書きし、まとめて書き戻す。"""

    def __init__(self, ds: Dataset, table: Table):
        self.ds, self.table = ds, table
        self.path = ds.data_dir / f"{table.name}.csv"
        self.history_path = ds.data_dir / f"{table.name}.history.csv"
        self.rows: dict[str, dict] = {}
        self.history: list[dict] = []
        self.dirty = False
        if self.path.exists():
            with self.path.open(encoding=ds.csv_encoding, newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != table.header:
                    raise StoreError(
                        f"{self.path.name} の列が dataset.yaml と一致しません。"
                        "スキーマを変えたなら `crawler.py migrate` を実行してください"
                    )
                self.rows = {r["_id"]: r for r in reader}

    def upsert(self, rid: str, values: dict[str, str], meta: dict[str, str]) -> str:
        """values に含まれる列だけ上書きする（含まれない列は既存の値を残す）。"""
        now = now_iso()
        old = self.rows.get(rid)
        if old is None:
            row = {h: "" for h in self.table.header}
            row.update(values, _id=rid, _first_seen_at=now)
            result = "inserted"
        else:
            row = dict(old)
            row.update(values)
            changed = any(old.get(k, "") != row[k] for k in values)
            if changed and self.table.keep_history:
                self.history.append({**old, "_replaced_at": now})
            result = "updated" if changed else "unchanged"
        row.update(meta, _last_seen_at=now)
        self.rows[rid] = row
        self.dirty = True
        return result

    def set_cell(self, rid: str, field: str, value: str) -> str:
        row = self.rows.get(rid)
        if row is None:
            raise StoreError(f"{self.table.name} に _id={rid} の行がありません")
        old = row[field]
        if old != value and self.table.keep_history:
            self.history.append({**row, "_replaced_at": now_iso()})
        row[field] = value
        self.dirty = True
        return old

    def save(self) -> None:
        if not self.dirty:
            return
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=self.table.header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(self.rows.values())
        atomic_write_bytes(self.path, buf.getvalue().encode(self.ds.csv_encoding))
        if self.history:
            header = self.table.header + ["_replaced_at"]
            new_file = not self.history_path.exists()
            with self.history_path.open("a", encoding=self.ds.csv_encoding, newline="") as f:
                writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
                if new_file:
                    writer.writeheader()
                writer.writerows(self.history)
            self.history = []
        self.dirty = False


class Tables:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self._files: dict[str, TableFile] = {}

    def __getitem__(self, name: str) -> TableFile:
        if name not in self._files:
            self._files[name] = TableFile(self.ds, self.ds.table(name))
        return self._files[name]

    def save_all(self) -> None:
        for f in self._files.values():
            f.save()


# ---------------------------------------------------------------- 値の変換

def coerce(col: Column, value) -> str:
    """1 セルの値を CSV の文字列に変換する。不正なら StoreError。"""
    if value is None or value == "" or value == []:
        return ""
    t = col.type
    if t in ("string", "text"):
        if isinstance(value, (dict, list)):
            raise StoreError(f"{col.name}: 文字列を入れてください")
        return str(value).strip()
    if t == "url":
        s = str(value).strip()
        if not s.startswith(("http://", "https://")):
            raise StoreError(f"{col.name}: URL は http(s):// で始めてください: {s!r}")
        return s
    if t == "integer":
        if isinstance(value, bool):
            raise StoreError(f"{col.name}: 整数を入れてください: {value!r}")
        if isinstance(value, float):
            if not value.is_integer():
                raise StoreError(f"{col.name}: 整数ではありません: {value!r}")
            return str(int(value))
        s = str(value).replace(",", "").strip()
        try:
            return str(int(s))
        except ValueError:
            raise StoreError(f"{col.name}: 整数に変換できません: {value!r}（単位の換算は抽出側で済ませる）") from None
    if t == "number":
        if isinstance(value, bool):
            raise StoreError(f"{col.name}: 数値を入れてください: {value!r}")
        try:
            x = float(str(value).replace(",", "").strip())
        except ValueError:
            raise StoreError(f"{col.name}: 数値に変換できません: {value!r}") from None
        if not math.isfinite(x):
            raise StoreError(f"{col.name}: 有限の数値を入れてください")
        return str(int(x)) if x.is_integer() and abs(x) < 1e15 else repr(x)
    if t == "boolean":
        if isinstance(value, bool):
            return "true" if value else "false"
        s = str(value).strip().lower()
        if s in ("true", "false"):
            return s
        raise StoreError(f"{col.name}: true / false を入れてください: {value!r}")
    if t == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            raise StoreError(f"{col.name}: 日付は YYYY-MM-DD で入れてください: {value!r}") from None
    if t == "datetime":
        s = str(value).strip()
        try:
            datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            raise StoreError(f"{col.name}: 日時は ISO 8601（例 2026-11-11T09:00:00+09:00）で入れてください: {value!r}") from None
        return s
    if t == "enum":
        s = str(value).strip()
        if s not in col.values:
            raise StoreError(f"{col.name}: {s!r} は values {col.values} にありません（新しい値が必要ならスキーマ変更を提案する）")
        return s
    if t == "list":
        if not isinstance(value, list) or any(isinstance(v, (dict, list)) for v in value):
            raise StoreError(f"{col.name}: list 型には文字列・数値の配列を入れてください")
        return json.dumps(value, ensure_ascii=False)
    raise StoreError(f"{col.name}: {t} 型はここでは変換できません")


def load_corrections(ds: Dataset) -> dict[tuple[str, str, str], str]:
    """人が直した値。再抽出しても上書きされないよう、保存時に毎回かぶせる。"""
    pinned = {}
    for c in read_jsonl(ds.logs_dir / "corrections.jsonl"):
        key = (c["table"], c["record_id"], c["field"])
        if c.get("unpin"):
            pinned.pop(key, None)
        else:
            pinned[key] = c["new"]
    return pinned


# ---------------------------------------------------------------- ファイル列

class FileSaver:
    """file / image 列と、長い text 列を files/ に保存する。

    URL で渡されたファイルは、書き込みの前に prefetch() でまとめて非同期にダウンロードしておく
    （違うホストは並行、同じホストは delay_sec を空けて 1 本ずつ）。ダウンロードしたものは raw/ に保存され、
    assets テーブルで URL と対応づけるので、再抽出や migrate のときは再ダウンロードしない。
    """

    MAX_HOST_WAIT_SEC = 120  # これ以上待たされるホストのファイルは今回は諦める

    def __init__(self, ds: Dataset, state: State):
        self.ds, self.state = ds, state
        self.warnings: list[str] = []
        self.failed: dict[str, tuple[str, bool]] = {}  # URL → (失敗理由, 一時的か)

    def _dir(self, table: str, rid: str) -> Path:
        return self.ds.files_dir / table / rid

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.ds.root).as_posix()

    # --- 検証（書き込み前）
    @staticmethod
    def items_of(col: Column, value) -> list:
        items = value if isinstance(value, list) else [value]
        if not col.multiple and len(items) > 1:
            raise StoreError(f"{col.name}: 複数のファイルを入れるには列に multiple: true が必要です")
        return [{"url": it} if isinstance(it, str) else it for it in items if it not in (None, "")]

    def check_items(self, col: Column, value) -> list[str]:
        """値の形を検証し、ダウンロードが必要な URL を返す。"""
        urls = []
        for item in self.items_of(col, value):
            if not isinstance(item, dict):
                raise StoreError(f"{col.name}: file / image には URL 文字列か {{url|path|text|page_id}} を入れてください")
            if "url" in item:
                if not str(item["url"]).startswith(("http://", "https://")):
                    raise StoreError(f"{col.name}: URL は http(s):// で始めてください: {item['url']!r}")
                urls.append(item["url"])
            elif "path" in item:
                if not Path(item["path"]).expanduser().is_file():
                    raise StoreError(f"{col.name}: ファイルがありません: {item['path']}")
            elif "page_id" in item:
                if self.state.page(int(item["page_id"])) is None:
                    raise StoreError(f"{col.name}: page_id {item['page_id']} がありません")
            elif "text" not in item:
                raise StoreError(f"{col.name}: url / path / text / page_id のどれかを指定してください")
        return urls

    # --- 非同期の事前ダウンロード
    def prefetch(self, urls: list[str]) -> None:
        todo = sorted({u for u in urls if self.state.asset(u) is None})
        if todo:
            asyncio.run(self._prefetch(todo))

    async def _prefetch(self, urls: list[str]) -> None:
        by_host: dict[str, list[str]] = defaultdict(list)
        for u in urls:
            by_host[host_of(u)].append(u)
        slots = asyncio.Semaphore(self.ds.crawl.concurrency)
        async with AsyncFetcher(self.ds.crawl) as fetcher:
            async def one_host(host: str, host_urls: list[str]) -> None:
                for url in host_urls:  # 同じホストは 1 本ずつ順番に
                    await self._download(fetcher, slots, host, url)
            await asyncio.gather(*(one_host(h, us) for h, us in by_host.items()))

    async def _download(self, fetcher: AsyncFetcher, slots: asyncio.Semaphore, host: str, url: str) -> None:
        if not await fetcher.robots_allows(url):
            self.failed[url] = ("robots.txt で禁止", False)
            return
        source = self.ds.source_for_url(url)
        delay = max(self.ds.delay_for(source), fetcher.robots_crawl_delay(url) or 0)
        for _ in range(2):  # 短いレートリミットなら 1 回だけ待って再試行する
            wait = host_wait(self.state, host)
            if wait > self.MAX_HOST_WAIT_SEC:
                self.failed[url] = (f"{host} は待機中（あと {int(wait)} 秒）", True)
                return
            await asyncio.sleep(wait)  # 待っている間は枠を使わない
            async with slots:
                res = await fetcher.fetch(url)
            backoff = record_response(self.state, self.ds.crawl, host, res, delay)
            append_jsonl(self.ds.logs_dir / "crawl.jsonl", {
                "ts": now_iso(), "kind": "asset", "url": url, "status": res.status,
                "bytes": len(res.body), "error": res.error, "backoff_sec": backoff,
            })
            if backoff is None:
                break
        if res.error or res.status >= 400:
            transient = res.status in TRANSIENT_STATUSES
            self.failed[url] = (f"取得に失敗（{res.error or res.status}）", transient)
            return
        ext = ext_for(res.content_type, url)
        _, raw_path = await asyncio.to_thread(save_raw, self.ds.raw_dir, res.body, ext)
        self.state.add_asset(url, raw_path, res.content_type)

    # --- 書き込み
    def save_text(self, table: str, rid: str, col: str, text: str) -> str:
        path = self._dir(table, rid) / f"{col}.md"
        atomic_write_bytes(path, text.encode("utf-8"))
        return self._rel(path)

    def save_files(self, table: str, rid: str, col: Column, value) -> str:
        items = self.items_of(col, value)
        transient = [it["url"] for it in items
                     if "url" in it and self.state.asset(it["url"]) is None and self.failed.get(it["url"], ("", True))[1]]
        if transient:
            # 一部だけで置き換えると、保存済みのファイルが一時的な失敗で消えてしまう
            raise TransientFileError(", ".join(f"{self.failed.get(u, ('ダウンロードされていません',))[0]}: {u}"
                                               for u in transient))
        fetched = []
        for item in items:
            got = self._obtain(item, col.name)
            if got is not None:
                fetched.append(got)
        folder = self._dir(table, rid)
        # 古いファイルは、新しいファイルを全部用意できてから入れ替える
        for old in list(folder.glob(f"{col.name}.*")) + list(folder.glob(f"{col.name}_*.*")):
            old.unlink()
        paths = []
        for i, (body, ext) in enumerate(fetched):
            name = f"{col.name}_{i + 1}.{ext}" if col.multiple else f"{col.name}.{ext}"
            atomic_write_bytes(folder / name, body)
            paths.append(self._rel(folder / name))
        if col.multiple:
            return json.dumps(paths, ensure_ascii=False) if paths else ""
        return paths[0] if paths else ""

    def _obtain(self, item: dict, col_name: str) -> tuple[bytes, str] | None:
        if "url" in item:
            cached = self.state.asset(item["url"])
            if cached is None:
                why = self.failed[item["url"]][0]
                self.warnings.append(f"{col_name}: {why}: {item['url']}")
                return None
            return load_raw(self.ds.root, cached["raw_path"]), cached["raw_path"].split(".")[-2]
        if "path" in item:
            p = Path(item["path"]).expanduser()
            return p.read_bytes(), p.suffix.lstrip(".").lower() or "bin"
        if "text" in item:
            return str(item["text"]).encode("utf-8"), str(item.get("ext", "md"))
        row = self.state.page(int(item["page_id"]))
        return load_raw(self.ds.root, row["raw_path"]), row["raw_path"].split(".")[-2]


# ---------------------------------------------------------------- 保存の本体

def parse_input(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise StoreError(f"{n} 行目が JSON ではありません: {e}") from e
    return out


def _check_record(ds: Dataset, rec: dict) -> tuple[Table, dict, list[dict]]:
    """レコードを検証し、(table, 変換前の値, 判断ログ) を返す。"""
    if not isinstance(rec, dict) or "table" not in rec or "values" not in rec:
        raise StoreError("records の各要素には table と values が必要です")
    table = ds.table(rec["table"])
    values = rec["values"]
    if not isinstance(values, dict):
        raise StoreError("values はオブジェクトにしてください")
    unknown = set(values) - {c.name for c in table.columns}
    if unknown:
        raise StoreError(f"{table.name}: 未知の列 {sorted(unknown)}（列を増やすならスキーマ変更を提案する）")
    decisions = rec.get("decisions") or []
    for d in decisions:
        if not isinstance(d, dict) or not d.get("field") or not d.get("reason"):
            raise StoreError(f"{table.name}: decisions の各要素には field と reason が必要です")
        if table.column(d["field"]) is None:
            raise StoreError(f"{table.name}: decisions の field '{d['field']}' は列にありません")
        if d.get("confidence", "high") not in CONFIDENCES:
            raise StoreError(f"{table.name}: confidence は {sorted(CONFIDENCES)} のどれかにしてください")
    return table, values, decisions


def store_envelopes(ds: Dataset, state: State, envelopes: list[dict], by: str = "agent") -> dict:
    tables = Tables(ds)
    files = FileSaver(ds, state)
    pinned = load_corrections(ds)
    schema_version = ds.schema_version
    summary = {"pages": 0, "inserted": 0, "updated": 0, "unchanged": 0, "decisions": 0,
               "errors": [], "warnings": files.warnings, "pinned_applied": 0}

    # 1) 全ページを検証する（エラーのあったページは丸ごと保存しない）
    valid: list[tuple[dict, str, str | None, list]] = []
    urls: list[str] = []
    new_ids: set[tuple[str, str]] = set()  # このバッチで新しく作るレコード
    for i, env in enumerate(envelopes):
        where = f"#{i + 1}"
        try:
            if not isinstance(env, dict) or "page_id" not in env:
                raise StoreError("各行に page_id が必要です")
            where = f"page_id={env['page_id']}"
            page = state.page(int(env["page_id"]))
            if page is None:
                raise StoreError("その page_id はありません")
            status = env.get("status", "done")
            if status not in ENVELOPE_STATUSES:
                raise StoreError(f"status は {sorted(ENVELOPE_STATUSES)} のどれかにしてください")
            records = env.get("records") or []
            if status != "done" and records:
                raise StoreError("status が done 以外のときは records を空にしてください")
            if status != "done" and not env.get("note"):
                raise StoreError("skipped / error には理由を note に書いてください")
            prepared, page_urls, page_new = [], [], set()
            for rec in records:
                table, values, decisions = _check_record(ds, rec)
                cells, deferred = {}, {}
                for name, v in values.items():
                    col = table.column(name)
                    if col.type in FILE_TYPES:
                        page_urls += files.check_items(col, v)
                        deferred[name] = v
                    else:
                        cells[name] = coerce(col, v)
                pk = [cells.get(k, "") for k in table.primary_key]
                if any(v == "" for v in pk):
                    raise StoreError(f"{table.name}: primary_key {table.primary_key} の値がありません")
                rid = record_id(pk)
                exists = rid in tables[table.name].rows or (table.name, rid) in new_ids | page_new
                for col in table.columns:
                    if col.required and not exists and col.name not in values:
                        raise StoreError(f"{table.name}: 必須列 {col.name} がありません")
                    if col.required and col.name in values and values[col.name] in (None, "", []):
                        raise StoreError(f"{table.name}: 必須列 {col.name} が空です")
                decided = {d["field"] for d in decisions}
                for col in table.columns:
                    if col.judgment and col.name in values and values[col.name] not in (None, "", []) \
                            and col.name not in decided:
                        raise StoreError(
                            f"{table.name}.{col.name} は judgment 列です。decisions に field・input・reason を書いてください")
                page_new.add((table.name, rid))
                prepared.append((table, rid, cells, deferred, decisions))
            valid.append((page, status, env.get("note"), prepared))
            urls += page_urls
            new_ids |= page_new
        except (StoreError, ValueError, KeyError, TypeError) as e:
            summary["errors"].append(f"{where}: {e}")

    # 2) file / image の URL をまとめて非同期にダウンロードする
    files.prefetch(urls)

    # 3) files/ と CSV に書き、判断ログを残す
    decision_log: list[dict] = []
    incomplete: set[int] = set()  # 一時的にファイルを取れなかったページ
    for page, _status, _note, prepared in valid:
        for table, rid, cells, deferred, decisions in prepared:
            for name, v in deferred.items():
                try:
                    cells[name] = files.save_files(table.name, rid, table.column(name), v)
                except TransientFileError as e:
                    files.warnings.append(f"page {page['id']} {table.name}.{name}: 一時的に取得できないため、"
                                          f"この列は前の値とファイルを残しました（{e}）")
                    incomplete.add(page["id"])
            for col in table.columns:
                if col.type == "text" and len(cells.get(col.name, "")) > col.max_inline_chars:
                    cells[col.name] = files.save_text(table.name, rid, col.name, cells[col.name])
            extracted = dict(cells)  # 判断ログには、人の修正をかぶせる前の抽出結果を残す
            overridden = set()
            for name in list(cells):
                key = (table.name, rid, name)
                if key in pinned and cells[name] != pinned[key]:
                    cells[name] = pinned[key]
                    overridden.add(name)
                    summary["pinned_applied"] += 1
            meta = {"_source_url": page["final_url"], "_page_id": str(page["id"]),
                    "_schema_version": str(schema_version)}
            summary[tables[table.name].upsert(rid, cells, meta)] += 1
            key_values = {k: cells[k] for k in table.primary_key}
            for d in decisions:
                confidence = d.get("confidence", "high")
                decision_log.append({
                    "id": "d_" + sha256_hex(f"{time.time_ns()}{rid}{d['field']}{len(decision_log)}".encode())[:12],
                    "ts": now_iso(), "table": table.name, "record_id": rid, "key": key_values,
                    "field": d["field"], "input": d.get("input"),
                    "output": d.get("output", extracted.get(d["field"])),
                    "overridden_by_correction": d["field"] in overridden,
                    "reason": d["reason"], "confidence": confidence,
                    "needs_review": bool(d.get("needs_review")) or confidence == "low",
                    "page_id": page["id"], "source_url": page["final_url"],
                    "schema_version": schema_version, "by": by,
                })
        summary["pages"] += 1

    # 4) CSV → 判断ログ → 抽出済みフラグ の順に書く（途中で落ちても再実行すれば揃う）
    tables.save_all()
    for d in decision_log:
        append_jsonl(ds.logs_dir / "decisions.jsonl", d)
    summary["decisions"] = len(decision_log)
    for page, status, note, prepared in valid:
        if page["id"] in incomplete:
            state.mark_incomplete(page["id"], "ファイルを一時的に取得できなかった。同じ内容を再度 store する")
        else:
            state.finish_extract(page["id"], status, schema_version, by, note, len(prepared))
    if incomplete:
        files.warnings.append(f"page {sorted(incomplete)} は抽出待ちのままです。時間をおいて同じ内容をもう一度 store してください")
    summary["incomplete_pages"] = sorted(incomplete)
    return summary


def touch_last_seen(ds: Dataset, urls: set[str]) -> int:
    """内容が変わっていなかったページのレコードについて、_last_seen_at だけ更新する。"""
    if not urls:
        return 0
    n = 0
    now = now_iso()
    for table in ds.tables:
        tf = TableFile(ds, table)
        for row in tf.rows.values():
            if row["_source_url"] in urls:
                row["_last_seen_at"] = now
                tf.dirty = True
                n += 1
        tf.save()
    return n


def archive_tables(ds: Dataset, old_version: int) -> list[str]:
    """スキーマ変更時、今の CSV と files/ を data/_archive/v<old>/ に退避する。"""
    dest = ds.data_dir / "_archive" / f"v{old_version}"
    moved = []
    for path in sorted(ds.data_dir.glob("*.csv")):
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), dest / path.name)
        moved.append(path.name)
    if ds.files_dir.exists() and any(ds.files_dir.iterdir()):
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ds.files_dir), dest / "files")
        moved.append("files/")
    return moved


__all__ = ["StoreError", "Tables", "TableFile", "coerce", "store_envelopes", "touch_last_seen",
           "archive_tables", "parse_input", "load_corrections", "SYSTEM_COLUMNS"]
