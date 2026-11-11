# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx>=0.27",
#   "beautifulsoup4>=4.12",
#   "lxml>=5.0",
#   "markdownify>=0.13",
#   "pyyaml>=6.0",
#   "pypdf>=4.0",
# ]
# ///
"""dataset-crawler の CLI。サブコマンドの一覧は `crawler.py -h`、使い方は SKILL.md。

実行例:
  uv run scripts/crawler.py probe https://example.com/list
  uv run scripts/crawler.py -d ./my-dataset init
  uv run scripts/crawler.py -d ./my-dataset crawl --max-requests 100
  uv run scripts/crawler.py -d ./my-dataset pending
  uv run scripts/crawler.py -d ./my-dataset show 12
  uv run scripts/crawler.py -d ./my-dataset store extracted.jsonl
  uv run scripts/crawler.py -d ./my-dataset status
"""
import argparse
import asyncio
import importlib.util
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dclib.config import CONFIG_FILE, ConfigError, Dataset, load_dataset  # noqa: E402
from dclib.crawl import EXIT_STOPPED, run_crawl, seed  # noqa: E402
from dclib.fetcher import AsyncFetcher, require_playwright  # noqa: E402
from dclib.pages import Page, html_to_markdown, decode, kind_of, pdf_to_text  # noqa: E402
from dclib.review import approve, find_decision, fix, list_decisions  # noqa: E402
from dclib.state import State  # noqa: E402
from dclib.store import StoreError, TableFile, archive_tables, parse_input, store_envelopes  # noqa: E402
from dclib.util import normalize_url, read_jsonl  # noqa: E402

PROBE_UA = "dataset-crawler-probe/0.1"


def err(msg: str) -> None:
    print(f"エラー: {msg}", file=sys.stderr)


def open_dataset(args, *, check_schema: bool = True) -> tuple[Dataset, State]:
    ds = load_dataset(Path(args.dataset))
    if not ds.db_path.exists():
        raise ConfigError(f"{ds.root} はまだ初期化されていません。`crawler.py -d {args.dataset} init` を実行してください")
    state = State(ds.db_path)
    if check_schema:
        current = state.get_meta("schema_version")
        if current is not None and int(current) != ds.schema_version:
            state.close()
            raise ConfigError(
                f"dataset.yaml の schema_version（{ds.schema_version}）が保存済みデータ（{current}）と違います。"
                "`crawler.py migrate` を実行してください")
    return ds, state


def print_chunk(text: str, offset: int, max_chars: int) -> None:
    chunk = text[offset:offset + max_chars]
    print(chunk)
    end = offset + len(chunk)
    if end < len(text):
        print(f"\n--- 全 {len(text)} 文字中 {offset}〜{end} を表示。続きは --offset {end} ---")


# ---------------------------------------------------------------- サブコマンド

def cmd_probe(args) -> int:
    """データセットを作る前に、1 ページをクローラと同じ方法で取得して中身を見る。"""
    from dclib.config import CrawlPolicy

    if args.render_js:
        require_playwright()

    async def fetch_one():
        async with AsyncFetcher(CrawlPolicy(user_agent=args.user_agent)) as fetcher:
            if not await fetcher.robots_allows(args.url):
                return None
            return await fetcher.fetch(args.url, render_js=args.render_js)

    res = asyncio.run(fetch_one())
    if res is None:
        err("robots.txt でこの URL の取得が禁止されています")
        return 1
    if res.error:
        err(res.error)
        return 1
    print(f"HTTP {res.status}  {res.final_url}  {res.content_type}  {len(res.body)} bytes\n")
    kind = kind_of(res.content_type, res.final_url)
    if kind == "html":
        text = html_to_markdown(decode(res.body, res.content_type), res.final_url, args.selector)
    elif kind == "pdf":
        text = pdf_to_text(res.body)
    else:
        text = decode(res.body, res.content_type)
    print_chunk(text, args.offset, args.max_chars)
    return 0


def cmd_init(args) -> int:
    ds = load_dataset(Path(args.dataset))
    for d in (ds.data_dir, ds.files_dir, ds.raw_dir, ds.logs_dir, ds.root / "schema", ds.root / "extractors"):
        d.mkdir(parents=True, exist_ok=True)
    state = State(ds.db_path)
    try:
        current = state.get_meta("schema_version")
        if current is not None and int(current) != ds.schema_version:
            err(f"schema_version が {current} → {ds.schema_version} に変わっています。init ではなく migrate を実行してください")
            return 1
        snapshot = ds.root / "schema" / f"v{ds.schema_version}.yaml"
        shutil.copyfile(ds.root / CONFIG_FILE, snapshot)
        state.set_meta("schema_version", str(ds.schema_version))
        added = seed(ds, state)
    finally:
        state.close()
    gitignore = ds.root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# 再取得できる・大きいもの\nraw/\nstate/\n", encoding="utf-8")
    print(f"初期化しました: {ds.root}")
    print(f"  テーブル: {', '.join(t.name for t in ds.tables)}  /  ソース: {', '.join(s.id for s in ds.sources)}")
    print(f"  シード URL を {added} 件キューに追加しました")
    print(f"  スキーマの控え: schema/{snapshot.name}")
    return 0


def cmd_crawl(args) -> int:
    ds, state = open_dataset(args)
    try:
        concurrency = args.concurrency or ds.crawl.concurrency
        print(f"クロール開始（最大 {args.max_requests} リクエスト / {args.max_minutes} 分 / 同時に {concurrency} ホスト）")
        reason, stats, code = run_crawl(ds, state, max_requests=args.max_requests, max_minutes=args.max_minutes,
                                        max_wait_sec=args.max_wait, concurrency=concurrency)
        pending = state.db.execute("SELECT COUNT(*) FROM pages WHERE extract_status='pending'").fetchone()[0]
    finally:
        state.close()
    print(f"\n停止理由: {reason}")
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())) if stats else "  （取得なし）")
    print(f"  抽出待ちページ: {pending} 件")
    if code == EXIT_STOPPED:
        print("  → 制限された・失敗が続いたホストがあります。時間をおいて同じコマンドを再実行すれば続きから再開します。")
    return code


def cmd_pending(args) -> int:
    ds, state = open_dataset(args)
    try:
        rows = state.pending_pages(args.limit, args.source, args.type)
        total = state.db.execute("SELECT COUNT(*) FROM pages WHERE extract_status='pending'").fetchone()[0]
    finally:
        state.close()
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0
    print(f"抽出待ち {total} 件（先頭 {len(rows)} 件）")
    for r in rows:
        print(f"  page {r['id']:>6}  {r['source_id']}/{r['page_type']}  {r['bytes']:>8} bytes  {r['final_url']}")
    return 0


def cmd_show(args) -> int:
    ds, state = open_dataset(args, check_schema=False)
    try:
        row = state.page(args.page_id)
        if row is None:
            err(f"page {args.page_id} はありません")
            return 1
        page = Page.load(ds.root, row)
    finally:
        state.close()
    print(f"page {page.id}  {page.source_id}/{page.page_type}  fetched {page.fetched_at}  status {row['extract_status']}")
    print(f"url: {page.final_url}\n")
    text = page.text if args.raw else page.markdown(args.selector)
    print_chunk(text, args.offset, args.max_chars)
    return 0


def cmd_store(args) -> int:
    ds, state = open_dataset(args)
    try:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        envelopes = parse_input(text)
        summary = store_envelopes(ds, state, envelopes, by=args.by)
    finally:
        state.close()
    return report_store(summary)


def report_store(summary: dict) -> int:
    print(f"保存: ページ {summary['pages']} 件 / 追加 {summary['inserted']} 行・更新 {summary['updated']} 行・"
          f"変化なし {summary['unchanged']} 行 / 判断ログ {summary['decisions']} 件")
    if summary["pinned_applied"]:
        print(f"  人が修正済みの値を {summary['pinned_applied']} セルに適用しました（corrections.jsonl）")
    for w in summary["warnings"]:
        print(f"⚠️  {w}")
    for e in summary["errors"]:
        print(f"❌ {e}", file=sys.stderr)
    if summary["errors"]:
        print(f"\n{len(summary['errors'])} 件のページは保存していません。直して該当ページだけ再度 store してください。",
              file=sys.stderr)
        return 1
    return 0


def cmd_auto_extract(args) -> int:
    """extractors/<source>.py の extract(page) で、決まった形のページを機械的に抽出する。"""
    ds, state = open_dataset(args)
    try:
        path = ds.root / "extractors" / f"{args.source}.py"
        if not path.is_file():
            err(f"{path} がありません（assets/extractor_template.py を参照）")
            return 1
        spec = importlib.util.spec_from_file_location(f"extractor_{args.source}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        envelopes, left = [], 0
        for row in state.pending_pages(args.limit, args.source, args.type):
            result = module.extract(Page.load(ds.root, row))
            if result is None:  # extractor が判断できないページはエージェントに残す
                left += 1
                continue
            envelopes.append({"page_id": row["id"], **result})
        if args.dry_run:
            for env in envelopes:
                print(json.dumps(env, ensure_ascii=False))
            print(f"\n（dry-run）{len(envelopes)} ページ分を表示。保存していません。extractor が対象外としたページ: {left}",
                  file=sys.stderr)
            return 0
        summary = store_envelopes(ds, state, envelopes, by=f"extractors/{path.name}")
    finally:
        state.close()
    print(f"extractor が対象外としたページ: {left} 件（エージェントが抽出する）")
    return report_store(summary)


def cmd_add_urls(args) -> int:
    ds, state = open_dataset(args, check_schema=False)
    added = requeued = skipped = 0
    try:
        with state.db:
            for url in args.urls:
                url = normalize_url(url, ds.crawl.strip_params)
                src = ds.source(args.source) if args.source else ds.source_for_url(url)
                if src is None or not src.accepts(url):
                    print(f"  対象外（どの source の allow にも一致しない）: {url}")
                    skipped += 1
                    continue
                rule = src.rule_for(url)
                if state.enqueue(url, src.id, rule.type, priority=rule.priority + args.priority):
                    added += 1
                elif args.requeue and state.requeue(url):
                    requeued += 1
    finally:
        state.close()
    print(f"追加 {added} 件 / 再キュー {requeued} 件 / 対象外 {skipped} 件")
    return 0


def cmd_status(args) -> int:
    ds, state = open_dataset(args, check_schema=False)
    db = state.db
    try:
        urls = db.execute(
            "SELECT source_id, status, (next_fetch_at IS NOT NULL AND next_fetch_at > strftime('%Y-%m-%dT%H:%M:%SZ','now')) AS later,"
            " COUNT(*) AS n FROM urls GROUP BY 1, 2, 3").fetchall()
        pages = dict(db.execute("SELECT extract_status, COUNT(*) FROM pages GROUP BY 1").fetchall())
        hosts = db.execute("SELECT * FROM hosts WHERE next_allowed_at > ? AND backoff_sec > 0", (time.time(),)).fetchall()
        last_run = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        schema_saved = state.get_meta("schema_version")
    finally:
        state.close()

    by_source: dict[str, Counter] = {}
    for r in urls:
        key = "queued_later" if r["status"] == "queued" and r["later"] else r["status"]
        by_source.setdefault(r["source_id"], Counter())[key] += r["n"]
    tables = {}
    for t in ds.tables:
        try:
            tables[t.name] = len(TableFile(ds, t).rows)
        except StoreError as e:
            tables[t.name] = f"読めません: {e}"
    decisions = read_jsonl(ds.logs_dir / "decisions.jsonl")
    needs_review = len(list_decisions(ds, needs_review=True, table=None, field=None, record_id=None, limit=10**9))

    info = {
        "dataset": ds.name, "schema_version": ds.schema_version, "schema_version_saved": schema_saved,
        "urls": {k: dict(v) for k, v in by_source.items()}, "pages": pages, "tables": tables,
        "decisions": len(decisions), "needs_review": needs_review,
        "hosts_backing_off": [dict(h) for h in hosts],
        "last_run": dict(last_run) if last_run else None,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print(f"データセット: {ds.name}（schema v{ds.schema_version}）  {ds.root}")
    if schema_saved and int(schema_saved) != ds.schema_version:
        print(f"⚠️  保存済みデータは schema v{schema_saved}。`migrate` が必要です")
    print("\nURL キュー（source ごと）")
    url_labels = [("queued", "取得待ち"), ("queued_later", "再訪問・再試行待ち"), ("done", "取得済"),
                  ("failed", "失敗"), ("gone", "消滅(404/410)"), ("blocked", "除外")]
    for sid, c in sorted(by_source.items()):
        print(f"  {sid}: " + " / ".join(f"{label} {c[k]}" for k, label in url_labels))
    print("\n抽出（取得したページ）")
    labels = [("pending", "抽出待ち"), ("done", "抽出済"), ("skipped", "対象外と判断"), ("error", "抽出失敗"),
              ("unchanged", "変化なし"), ("not_target", "一覧など"), ("superseded", "古い版")]
    print("  " + " / ".join(f"{label} {pages.get(k, 0)}" for k, label in labels))
    print("\nテーブル")
    for name, n in tables.items():
        print(f"  {name}: {n} 行" if isinstance(n, int) else f"  {name}: {n}")
    print(f"\n判断ログ {len(decisions)} 件（レビュー待ち {needs_review} 件）")
    for h in hosts:
        print(f"⏸  {h['host']} は待機中（HTTP {h['last_status']}, 待ち {int(h['backoff_sec'])} 秒）")
    if last_run:
        print(f"\n直近の実行: #{last_run['id']} {last_run['kind']} {last_run['started_at']}〜{last_run['finished_at'] or '（実行中または異常終了）'}"
              f" 停止理由: {last_run['stop_reason']}")
    return 0


def cmd_review(args) -> int:
    ds = load_dataset(Path(args.dataset))
    items = list_decisions(ds, needs_review=not args.all, table=args.table, field=args.field,
                           record_id=args.id, limit=args.limit)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("該当する判断ログはありません")
        return 0
    for d in items:
        flags = "".join([" [修正済]" if d["corrected"] else "", " [確認済]" if d["reviewed"] else "",
                         " [要確認]" if d.get("needs_review") else ""])
        print(f"{d['id']}  {d['table']}.{d['field']}  _id={d['record_id']}  {d['key']}{flags}")
        print(f"    入力: {d.get('input')!r}")
        pinned = "  ※人が修正した値で上書き済み" if d.get("overridden_by_correction") else ""
        print(f"    出力: {d.get('output')!r}  （確信度 {d.get('confidence')}, by {d.get('by')}）{pinned}")
        print(f"    理由: {d['reason']}")
        print(f"    出典: page {d['page_id']} {d['source_url']}")
    return 0


def cmd_fix(args) -> int:
    ds = load_dataset(Path(args.dataset))
    if args.approve:
        approve(ds, args.approve, args.note)
        print(f"{len(args.approve)} 件を確認済みにしました")
        return 0
    if args.decision_id:
        d = find_decision(ds, args.decision_id)
        table, record_id, field = d["table"], d["record_id"], d["field"]
    else:
        if not (args.table and args.id and args.field):
            err("decision_id か、--table --id --field の組を指定してください")
            return 1
        table, record_id, field = args.table, args.id, args.field
    if not args.unpin and args.value is None:
        err("--value で正しい値を指定してください（固定を外すなら --unpin）")
        return 1
    value = json.loads(args.value) if args.json_value else args.value
    old, new = fix(ds, table=table, record_id=record_id, field=field, value=value, note=args.note,
                   decision_id=args.decision_id, unpin=args.unpin)
    if args.unpin:
        print(f"{table}.{field} (_id={record_id}) の固定を外しました。次の抽出結果で上書きされます")
    else:
        print(f"{table}.{field} (_id={record_id}): {old!r} → {new!r}（corrections.jsonl に記録。再抽出しても維持されます）")
    return 0


def cmd_reextract(args) -> int:
    ds, state = open_dataset(args)
    try:
        if not (args.all or args.source or args.page):
            err("--all / --source / --page のどれかを指定してください")
            return 1
        n = state.reset_extract(source_id=args.source, page_ids=args.page)
    finally:
        state.close()
    print(f"{n} ページを抽出待ちに戻しました（再取得はしません）。`pending` から抽出し直してください")
    return 0


def cmd_migrate(args) -> int:
    ds = load_dataset(Path(args.dataset))
    state = State(ds.db_path)
    try:
        old = int(state.get_meta("schema_version") or ds.schema_version)
        if old == ds.schema_version:
            print(f"schema_version は {old} のままです。dataset.yaml の schema_version を上げてから実行してください")
            return 1
        if ds.schema_version < old:
            err(f"schema_version を下げることはできません（{old} → {ds.schema_version}）")
            return 1
        moved = archive_tables(ds, old)
        shutil.copyfile(ds.root / CONFIG_FILE, ds.root / "schema" / f"v{ds.schema_version}.yaml")
        n = state.reset_extract()
        state.set_meta("schema_version", str(ds.schema_version))
        added = seed(ds, state)
    finally:
        state.close()
    print(f"schema v{old} → v{ds.schema_version} に移行しました")
    print(f"  旧データ: data/_archive/v{old}/ に退避（{', '.join(moved) or 'なし'}）")
    print(f"  {n} ページを抽出待ちに戻しました（raw/ から作り直すので再クロールは不要）")
    if added:
        print(f"  新しいシード URL を {added} 件追加しました")
    return 0


# ---------------------------------------------------------------- 引数

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="自律クロールで構造化データ（CSV）を作るツール。詳しくは SKILL.md")
    p.add_argument("-d", "--dataset", default=".", help="データセットのディレクトリ（dataset.yaml がある場所）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("probe", help="データセットなしで 1 ページ取得して中身を見る（スキーマ設計用）")
    s.add_argument("url")
    s.add_argument("--selector", help="CSS セレクタで一部だけ表示")
    s.add_argument("--max-chars", type=int, default=20000)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--render-js", action="store_true", help="Playwright で描画してから取得")
    s.add_argument("--user-agent", default=PROBE_UA)
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("init", help="dataset.yaml を検証し、ディレクトリと進捗 DB を作ってシードを登録")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("crawl", help="キューの URL を順に取得する（中断しても続きから再開）")
    s.add_argument("--max-requests", type=int, default=100)
    s.add_argument("--max-minutes", type=float, default=30)
    s.add_argument("--max-wait", type=float, default=60, help="次の URL が取得可能になるまでこれ以上待つなら終了（秒）")
    s.add_argument("--concurrency", type=int, help="同時に取得するホスト数（既定は crawl.concurrency）。同じホストへは常に 1 本ずつ")
    s.set_defaults(func=cmd_crawl)

    s = sub.add_parser("pending", help="抽出待ちのページ一覧")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--source")
    s.add_argument("--type", help="page_type で絞る")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_pending)

    s = sub.add_parser("show", help="取得済みページを Markdown にして表示")
    s.add_argument("page_id", type=int)
    s.add_argument("--selector", help="CSS セレクタで一部だけ表示")
    s.add_argument("--max-chars", type=int, default=20000)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--raw", action="store_true", help="変換せず元の HTML / テキストを表示")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("store", help="抽出結果（JSONL）を検証して CSV に保存")
    s.add_argument("file", help="JSONL ファイル。- なら標準入力")
    s.add_argument("--by", default="agent", help="判断ログに記録する抽出者")
    s.set_defaults(func=cmd_store)

    s = sub.add_parser("auto-extract", help="extractors/<source>.py で機械的に抽出して保存")
    s.add_argument("--source", required=True)
    s.add_argument("--type", help="page_type で絞る")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--dry-run", action="store_true", help="保存せずに結果を表示（エージェントの抽出と突き合わせる）")
    s.set_defaults(func=cmd_auto_extract)

    s = sub.add_parser("add-urls", help="URL を手動でキューに追加")
    s.add_argument("urls", nargs="+")
    s.add_argument("--source", help="source を明示（省略時は allow で判定）")
    s.add_argument("--priority", type=int, default=0, help="ページルールの priority に加算")
    s.add_argument("--requeue", action="store_true", help="取得済みでもキューに戻す")
    s.set_defaults(func=cmd_add_urls)

    s = sub.add_parser("status", help="進捗を表示")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("review", help="判断ログを表示（デフォルトはレビュー待ちだけ）")
    s.add_argument("--all", action="store_true", help="レビュー待ち以外も表示")
    s.add_argument("--table")
    s.add_argument("--field")
    s.add_argument("--id", help="レコードの _id")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("fix", help="判断の結果を人が修正・承認する")
    s.add_argument("decision_id", nargs="?")
    s.add_argument("--table")
    s.add_argument("--id", help="レコードの _id")
    s.add_argument("--field")
    s.add_argument("--value", help="正しい値")
    s.add_argument("--json-value", action="store_true", help="--value を JSON として解釈（list 型など）")
    s.add_argument("--note", default="", help="修正の理由")
    s.add_argument("--unpin", action="store_true", help="修正した値の固定を外す")
    s.add_argument("--approve", nargs="+", metavar="DECISION_ID", help="修正不要として確認済みにする")
    s.set_defaults(func=cmd_fix)

    s = sub.add_parser("reextract", help="取得済みページを抽出待ちに戻す（判断基準を変えたとき）")
    s.add_argument("--all", action="store_true")
    s.add_argument("--source")
    s.add_argument("--page", type=int, nargs="+")
    s.set_defaults(func=cmd_reextract)

    s = sub.add_parser("migrate", help="schema_version を上げた後、旧 CSV を退避して raw/ から作り直す準備をする")
    s.set_defaults(func=cmd_migrate)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (ConfigError, StoreError) as e:
        err(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
