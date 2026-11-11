"""dataset.yaml の読み込みと検証。仕様は references/config.md。"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .util import host_of

CONFIG_FILE = "dataset.yaml"
COLUMN_TYPES = {
    "string", "text", "integer", "number", "boolean", "date", "datetime",
    "enum", "list", "url", "file", "image",
}
FILE_TYPES = {"file", "image"}
# CSV の末尾に必ず付く管理用の列（_id だけは先頭）
SYSTEM_COLUMNS = ["_source_url", "_page_id", "_schema_version", "_first_seen_at", "_last_seen_at"]
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ConfigError(Exception):
    pass


@dataclass
class Column:
    name: str
    type: str
    required: bool = False
    description: str = ""
    values: list[str] = field(default_factory=list)  # enum の選択肢
    judgment: bool = False  # 人により判断が分かれる変換。決定ログが必須
    multiple: bool = False  # file / image を複数持つ
    max_inline_chars: int = 1000  # text がこれを超えたら files/ に退避


@dataclass
class Table:
    name: str
    primary_key: list[str]
    columns: list[Column]
    description: str = ""
    keep_history: bool = False

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def header(self) -> list[str]:
        return ["_id"] + [c.name for c in self.columns] + SYSTEM_COLUMNS


@dataclass
class PageRule:
    match: re.Pattern
    type: str
    extract: bool = True
    follow: bool = True
    revisit_hours: float | None = None
    priority: int = 0


DEFAULT_RULE = PageRule(match=re.compile(""), type="page")


@dataclass
class Source:
    id: str
    seeds: list[str]
    allow: list[re.Pattern]
    deny: list[re.Pattern]
    pages: list[PageRule]
    delay_sec: float | None = None
    render_js: bool = False

    def accepts(self, url: str) -> bool:
        if any(p.search(url) for p in self.deny):
            return False
        if self.allow:
            return any(p.search(url) for p in self.allow)
        # allow を省略したらシードと同じホストだけ
        return host_of(url) in {host_of(s) for s in self.seeds}

    def rule_for(self, url: str) -> PageRule:
        return next((r for r in self.pages if r.match.search(url)), DEFAULT_RULE)


@dataclass
class CrawlPolicy:
    user_agent: str
    concurrency: int = 4  # 同時に取得するホストの数。同じホストへは常に 1 本ずつ
    respect_robots: bool = True
    delay_sec: float = 3.0
    timeout_sec: float = 30.0
    max_retries: int = 3
    max_bytes: int = 20_000_000
    max_backoff_sec: float = 3600.0
    max_consecutive_errors: int = 5
    accept_language: str = "ja,en;q=0.8"
    strip_params: list[str] = field(default_factory=lambda: ["utm_*", "fbclid", "gclid"])


@dataclass
class Dataset:
    root: Path
    name: str
    purpose: str
    schema_version: int
    crawl: CrawlPolicy
    sources: list[Source]
    tables: list[Table]
    conventions: list[str]
    csv_encoding: str = "utf-8"
    raw_config: dict = field(default_factory=dict)

    def source(self, source_id: str) -> Source:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise ConfigError(f"source '{source_id}' は dataset.yaml にありません")

    def source_for_url(self, url: str) -> Source | None:
        return next((s for s in self.sources if s.accepts(url)), None)

    def table(self, name: str) -> Table:
        for t in self.tables:
            if t.name == name:
                return t
        raise ConfigError(f"table '{name}' は dataset.yaml にありません（あるのは {[t.name for t in self.tables]}）")

    def delay_for(self, source: Source | None) -> float:
        if source and source.delay_sec is not None:
            return source.delay_sec
        return self.crawl.delay_sec

    # よく使うパス
    @property
    def db_path(self) -> Path:
        return self.root / "state" / "crawl.sqlite"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def files_dir(self) -> Path:
        return self.root / "files"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def _regexes(values, where: str) -> list[re.Pattern]:
    out = []
    for v in values or []:
        try:
            out.append(re.compile(v))
        except re.error as e:
            raise ConfigError(f"{where}: 正規表現 '{v}' が不正です: {e}") from e
    return out


def _parse_column(raw: dict, where: str) -> Column:
    if not isinstance(raw, dict) or "name" not in raw or "type" not in raw:
        raise ConfigError(f"{where}: 列には name と type が必要です")
    name, ctype = raw["name"], raw["type"]
    if not NAME_RE.match(str(name)):
        raise ConfigError(f"{where}: 列名 '{name}' は小文字英字で始まる英数字とアンダースコアにしてください")
    if ctype not in COLUMN_TYPES:
        raise ConfigError(f"{where}.{name}: type '{ctype}' は使えません（{sorted(COLUMN_TYPES)}）")
    if ctype == "enum" and not raw.get("values"):
        raise ConfigError(f"{where}.{name}: enum には values が必要です")
    if raw.get("multiple") and ctype not in FILE_TYPES:
        raise ConfigError(f"{where}.{name}: multiple は file / image だけに使えます（他は list 型を使う）")
    known = {"name", "type", "required", "description", "values", "judgment", "multiple", "max_inline_chars", "example"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"{where}.{name}: 未知のキー {sorted(unknown)}")
    return Column(
        name=name,
        type=ctype,
        required=bool(raw.get("required", False)),
        description=str(raw.get("description", "")),
        values=[str(v) for v in raw.get("values") or []],
        judgment=bool(raw.get("judgment", False)),
        multiple=bool(raw.get("multiple", False)),
        max_inline_chars=int(raw.get("max_inline_chars", 1000)),
    )


def _parse_table(name: str, raw: dict) -> Table:
    where = f"tables.{name}"
    if not NAME_RE.match(name):
        raise ConfigError(f"{where}: テーブル名は小文字英字で始まる英数字とアンダースコアにしてください")
    columns = [_parse_column(c, where) for c in raw.get("columns") or []]
    if not columns:
        raise ConfigError(f"{where}: columns が空です")
    names = [c.name for c in columns]
    if len(set(names)) != len(names):
        raise ConfigError(f"{where}: 列名が重複しています")
    pk = raw.get("primary_key")
    if isinstance(pk, str):
        pk = [pk]
    if not pk:
        raise ConfigError(f"{where}: primary_key が必要です（同じものを 2 回保存したとき上書きするためのキー）")
    for k in pk:
        col = next((c for c in columns if c.name == k), None)
        if col is None:
            raise ConfigError(f"{where}: primary_key の '{k}' が columns にありません")
        if col.type in FILE_TYPES | {"text", "list"}:
            raise ConfigError(f"{where}: {col.type} 型の列は primary_key にできません")
        col.required = True
    return Table(
        name=name,
        primary_key=list(pk),
        columns=columns,
        description=str(raw.get("description", "")),
        keep_history=bool(raw.get("keep_history", False)),
    )


def _parse_source(raw: dict) -> Source:
    sid = raw.get("id")
    if not sid or not NAME_RE.match(str(sid)):
        raise ConfigError("sources: id（小文字英数字とアンダースコア）が必要です")
    where = f"sources.{sid}"
    seeds = raw.get("seeds") or []
    if not seeds:
        raise ConfigError(f"{where}: seeds（最初に取得する URL）が必要です")
    pages = []
    for i, p in enumerate(raw.get("pages") or []):
        if "match" not in p or "type" not in p:
            raise ConfigError(f"{where}.pages[{i}]: match と type が必要です")
        pages.append(PageRule(
            match=_regexes([p["match"]], f"{where}.pages[{i}]")[0],
            type=str(p["type"]),
            extract=bool(p.get("extract", True)),
            follow=bool(p.get("follow", True)),
            revisit_hours=p.get("revisit_hours"),
            priority=int(p.get("priority", 0)),
        ))
    return Source(
        id=sid,
        seeds=[str(s) for s in seeds],
        allow=_regexes(raw.get("allow"), f"{where}.allow"),
        deny=_regexes(raw.get("deny"), f"{where}.deny"),
        pages=pages,
        delay_sec=raw.get("delay_sec"),
        render_js=bool(raw.get("render_js", False)),
    )


def load_dataset(root: Path) -> Dataset:
    root = root.resolve()
    path = root / CONFIG_FILE
    if not path.is_file():
        raise ConfigError(f"{path} がありません。先に dataset.yaml を作ってください（assets/dataset.example.yaml を参照）")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"dataset.yaml の構文エラー: {e}") from e

    for key in ("name", "purpose", "schema_version", "crawl", "sources", "tables"):
        if key not in raw:
            raise ConfigError(f"dataset.yaml に {key} がありません")

    c = raw["crawl"] or {}
    if not c.get("user_agent"):
        raise ConfigError("crawl.user_agent が必要です（連絡先を含めた、自分を名乗る UA にする）")
    crawl = CrawlPolicy(user_agent=c["user_agent"])
    for key in ("concurrency", "respect_robots", "delay_sec", "timeout_sec", "max_retries", "max_bytes",
                "max_backoff_sec", "max_consecutive_errors", "accept_language", "strip_params"):
        if key in c:
            setattr(crawl, key, c[key])
    if not isinstance(crawl.concurrency, int) or crawl.concurrency < 1:
        raise ConfigError("crawl.concurrency は 1 以上の整数にしてください")
    if crawl.delay_sec < 1:
        raise ConfigError("crawl.delay_sec は 1 秒以上にしてください（相手サーバーに負荷をかけない）")

    sources = [_parse_source(s) for s in raw["sources"] or []]
    if len({s.id for s in sources}) != len(sources):
        raise ConfigError("sources の id が重複しています")
    tables_raw = raw["tables"] or {}
    if not isinstance(tables_raw, dict) or not tables_raw:
        raise ConfigError("tables は「テーブル名: 定義」の map で 1 つ以上書いてください")
    tables = [_parse_table(name, t or {}) for name, t in tables_raw.items()]

    output = raw.get("output") or {}
    encoding = output.get("csv_encoding", "utf-8")
    if encoding not in ("utf-8", "utf-8-sig"):
        raise ConfigError("output.csv_encoding は utf-8 か utf-8-sig（Excel で開く場合）にしてください")

    return Dataset(
        root=root,
        name=str(raw["name"]),
        purpose=str(raw["purpose"]),
        schema_version=int(raw["schema_version"]),
        crawl=crawl,
        sources=sources,
        tables=tables,
        conventions=[str(x) for x in raw.get("conventions") or []],
        csv_encoding=encoding,
        raw_config=raw,
    )
