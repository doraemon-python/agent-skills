"""URL キューを非同期に取得するクロールのループ。AI を使わないので cron からも実行できる。

- 違うホストは最大 concurrency 本まで同時に取得する
- 同じホストは同時に 1 本だけ。応答後 delay_sec（429 / 503 ならバックオフ）経つまで次を出さない
- 進捗 DB（SQLite）への書き込みはイベントループのスレッドだけで行い、await をまたがない。
  HTML の解析や raw/ への保存などの重い処理はスレッドに逃がす
"""
import asyncio
import json
import signal
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

from .config import ConfigError, Dataset, PageRule
from .fetcher import AsyncFetcher, FetchResult, ext_for, record_response, require_playwright, save_raw
from .pages import decode, extract_links, kind_of, text_hash
from .state import State
from .store import StoreError, touch_last_seen
from .util import append_jsonl, normalize_url, now_iso

EXIT_OK = 0
EXIT_STOPPED = 3  # レートリミット・連続エラーで止まったホストがある。時間をおいて再実行する
EXIT_INTERRUPTED = 130


def seed(ds: Dataset, state: State) -> int:
    added = 0
    with state.db:
        for src in ds.sources:
            for url in src.seeds:
                url = normalize_url(url, ds.crawl.strip_params)
                rule = src.rule_for(url)
                added += state.enqueue(url, src.id, rule.type, priority=rule.priority)
    return added


def run_crawl(ds: Dataset, state: State, *, max_requests: int, max_minutes: float, max_wait_sec: float,
              concurrency: int | None = None, log=print) -> tuple[str, Counter, int]:
    if any(src.render_js for src in ds.sources):
        require_playwright()
    crawler = Crawler(ds, state, concurrency=concurrency or ds.crawl.concurrency, log=log)
    return asyncio.run(crawler.run(max_requests=max_requests, max_minutes=max_minutes, max_wait_sec=max_wait_sec))


@dataclass
class Analysis:
    """スレッドで計算する、取得結果の解析結果。"""
    kind: str
    content_hash: str
    raw_path: str
    text_hash: str | None
    links: list[str]


def _analyze(ds: Dataset, rule: PageRule, res: FetchResult) -> Analysis:
    ext = ext_for(res.content_type, res.final_url)
    kind = kind_of(res.content_type, f".{ext}.gz")
    content_hash, raw_path = save_raw(ds.raw_dir, res.body, ext)
    th = text_hash(res.body, res.content_type, kind) if rule.extract else None
    links = []
    if rule.follow and kind == "html":
        links = [normalize_url(u, ds.crawl.strip_params)
                 for u in extract_links(decode(res.body, res.content_type), res.final_url)]
    return Analysis(kind, content_hash, raw_path, th, links)


class Crawler:
    def __init__(self, ds: Dataset, state: State, *, concurrency: int, log=print):
        self.ds, self.state, self.log = ds, state, log
        self.concurrency = max(1, concurrency)
        self.stats: Counter = Counter()
        self.errors_in_row: dict[str, int] = defaultdict(int)
        self.parked: dict[str, str] = {}  # この実行ではもう取得しないホスト → 理由
        self.unchanged_urls: set[str] = set()
        self.fetcher: AsyncFetcher | None = None
        self.run_id = 0
        self.dispatched = 0  # 割り当てた URL 数（--max-requests の判定に使う）
        self._sigint_installed = False

    def event(self, **kw) -> None:
        append_jsonl(self.ds.logs_dir / "crawl.jsonl", {"ts": now_iso(), "run": self.run_id, "kind": "page", **kw})

    async def run(self, *, max_requests: int, max_minutes: float, max_wait_sec: float) -> tuple[str, Counter, int]:
        self.run_id = self.state.start_run("crawl")
        deadline = time.time() + max_minutes * 60
        in_flight: dict[asyncio.Task, str] = {}  # タスク → ホスト
        stop = asyncio.Event()
        self._install_sigint(stop, in_flight)
        reason, code = "", EXIT_OK

        try:
            async with AsyncFetcher(self.ds.crawl) as self.fetcher:
                while True:
                    if not reason:
                        if stop.is_set():
                            reason, code = "interrupted", EXIT_INTERRUPTED
                        elif self.dispatched >= max_requests:
                            reason = "max_requests"
                        elif time.time() >= deadline:
                            reason = "max_minutes"

                    # 空いている枠に、取得中でも待機中でもないホストの URL を割り当てる
                    busy = set(in_flight.values()) | set(self.parked)
                    while not reason and len(in_flight) < self.concurrency and self.dispatched < max_requests:
                        row = self.state.next_url(busy)
                        if row is None:
                            break
                        task = asyncio.create_task(self._process(row))
                        in_flight[task] = row["host"]
                        busy.add(row["host"])
                        self.dispatched += 1

                    if not in_flight:
                        if reason:
                            break
                        wait = self.state.seconds_until_next(set(self.parked))
                        if wait is None:
                            reason = "host_errors" if self.parked else "queue_empty"
                            break
                        if wait > max_wait_sec or time.time() + wait > deadline:
                            reason, code = self._waiting_reason(wait)
                            break
                        await self._sleep_or_stop(stop, wait + 0.05)
                        continue

                    # どれかの取得が終わるか、別のホストが取得可能になるか、止める指示が来るまで待つ
                    timeout = None
                    if not reason:
                        nxt = self.state.seconds_until_next(busy)
                        if nxt is not None:
                            timeout = min(max(nxt, 0.05), max(deadline - time.time(), 0.05))
                    waiters = list(in_flight)
                    stop_waiter = None
                    if not stop.is_set():
                        stop_waiter = asyncio.create_task(stop.wait())
                        waiters.append(stop_waiter)
                    done, _ = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                    if stop_waiter:
                        stop_waiter.cancel()
                    for task in done:
                        if task is stop_waiter:
                            continue
                        in_flight.pop(task)
                        if not task.cancelled() and task.exception():
                            raise task.exception()
        except asyncio.CancelledError:  # SIGINT をシグナルハンドラで拾えない環境（Windows）
            reason, code = "interrupted", EXIT_INTERRUPTED
            for task in in_flight:
                task.cancel()
        finally:
            self._remove_sigint()
            try:
                self.stats["touched"] = touch_last_seen(self.ds, self.unchanged_urls)
            except StoreError as e:
                self.log(f"⚠️  _last_seen_at を更新できませんでした: {e}")
            if self.parked:
                reason += "; " + "; ".join(f"{h} を中断: {why}" for h, why in self.parked.items())
                if code == EXIT_OK:
                    code = EXIT_STOPPED
            self.state.finish_run(self.run_id, reason, json.dumps(self.stats, ensure_ascii=False))
        return reason, self.stats, code

    def _waiting_reason(self, wait: float) -> tuple[str, int]:
        backing_off = self.state.db.execute(
            "SELECT COUNT(*) FROM hosts WHERE backoff_sec > 0 AND next_allowed_at > ?", (time.time(),)).fetchone()[0]
        wait_text = f"{wait / 3600:.1f} 時間" if wait >= 3600 else f"{int(wait)} 秒"
        if backing_off:
            return f"rate_limited (あと {wait_text})", EXIT_STOPPED
        return f"waiting (今すぐ取得できる URL なし。次は {wait_text} 後)", EXIT_OK

    @staticmethod
    async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # --- Ctrl-C: 1 回目は新しい取得をやめて取得中のものを待つ。2 回目は取得中のものも打ち切る
    def _install_sigint(self, stop: asyncio.Event, in_flight: dict) -> None:
        def on_sigint():
            if not stop.is_set():
                stop.set()
                self.log("中断します。取得中のリクエストが終わるのを待っています（もう一度 Ctrl-C で即中断）")
            else:
                for task in in_flight:
                    task.cancel()
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGINT, on_sigint)
            self._sigint_installed = True
        except (NotImplementedError, RuntimeError):
            self._sigint_installed = False

    def _remove_sigint(self) -> None:
        if self._sigint_installed:
            asyncio.get_running_loop().remove_signal_handler(signal.SIGINT)

    # --- 1 URL の処理
    async def _process(self, row) -> None:
        ds, state = self.ds, self.state
        url, host = row["url"], row["host"]
        try:
            source = ds.source(row["source_id"])
        except ConfigError:
            state.mark_url(row["id"], "blocked", error="source が dataset.yaml から消えています")
            return
        if not source.accepts(url):
            state.mark_url(row["id"], "blocked", error="allow / deny の対象外になりました")
            return
        if not await self.fetcher.robots_allows(url):
            state.mark_url(row["id"], "blocked", error="robots.txt で禁止")
            self.stats["blocked"] += 1
            self.event(url=url, result="robots_blocked")
            return

        rule = source.rule_for(url)
        delay = max(ds.delay_for(source), self.fetcher.robots_crawl_delay(url) or 0)
        self.stats["requests"] += 1
        res = await self.fetcher.fetch(url, render_js=source.render_js)

        # --- レートリミット: URL はキューに残したまま、そのホストだけ待たせる
        backoff = record_response(state, ds.crawl, host, res, delay)
        if backoff is not None:
            self.stats["rate_limited"] += 1
            self.event(url=url, status=res.status, result="rate_limited", backoff_sec=backoff)
            self.log(f"  {res.status} {url} → {host} を {int(backoff)} 秒待たせます（他のホストは続行）")
            return

        if res.error or res.status == 0 or res.status >= 500:
            attempts = row["attempts"] + 1
            failed = attempts >= ds.crawl.max_retries
            state.mark_url(row["id"], "failed" if failed else "queued", error=res.error or f"HTTP {res.status}",
                           next_fetch_in=None if failed else 60 * 2 ** attempts, count_attempt=True)
            self.stats["errors"] += 1
            self.event(url=url, status=res.status, result="failed" if failed else "retry", error=res.error)
            self._count_error(host)
            return
        if res.status in (404, 410):
            state.mark_url(row["id"], "gone", error=f"HTTP {res.status}")
            self.stats["gone"] += 1
            self.event(url=url, status=res.status, result="gone")
            return
        if res.status >= 400:
            state.mark_url(row["id"], "failed", error=f"HTTP {res.status}", count_attempt=True)
            self.stats["errors"] += 1
            self.event(url=url, status=res.status, result="failed")
            self._count_error(host)
            return

        self.errors_in_row[host] = 0
        analysis = await asyncio.to_thread(_analyze, ds, rule, res)
        # ここから先は await しない（DB の更新を他のタスクと混ぜない）
        page_id, status, new_links = self._record_page(row, rule, res, analysis)
        self.stats["fetched"] += 1
        self.stats["new_urls"] += new_links
        self.stats[f"extract_{status}"] += 1
        if status == "unchanged":
            self.unchanged_urls.add(res.final_url)
        self.event(url=url, status=res.status, result="fetched", page_id=page_id,
                   bytes=len(res.body), extract_status=status, new_urls=new_links)
        self.log(f"  {res.status} {url} → page {page_id}（{status}, 新規 URL {new_links}）")

    def _count_error(self, host: str) -> None:
        self.errors_in_row[host] += 1
        n = self.errors_in_row[host]
        if n >= self.ds.crawl.max_consecutive_errors and host not in self.parked:
            self.parked[host] = f"{n} 回連続で失敗。ブロックされている可能性"
            self.log(f"⏸  {host} で {n} 回連続失敗。この実行ではこのホストを取得しません（他のホストは続行）")

    def _record_page(self, row, rule: PageRule, res: FetchResult, a: Analysis) -> tuple[int, str, int]:
        ds, state = self.ds, self.state
        last = state.last_page(row["id"])
        if not rule.extract:
            status = "not_target"
        elif last is not None and last["text_hash"] == a.text_hash:
            status = "unchanged"
        else:
            status = "pending"
            if last is not None and last["extract_status"] == "pending":
                # 抽出前に内容が更新された。古い方は抽出しない
                state.db.execute("UPDATE pages SET extract_status='superseded' WHERE id=?", (last["id"],))

        page_id = state.add_page(
            url_id=row["id"], url=row["url"], final_url=res.final_url, source_id=row["source_id"],
            page_type=rule.type, fetched_at=now_iso(), http_status=res.status,
            content_type=res.content_type, content_hash=a.content_hash, text_hash=a.text_hash,
            raw_path=a.raw_path, bytes=len(res.body), extract_status=status,
        )
        new_links = 0
        if status != "unchanged":
            for link in a.links:
                src = ds.source_for_url(link)
                if src is None:
                    continue
                r = src.rule_for(link)
                new_links += state.enqueue(link, src.id, r.type, priority=r.priority,
                                           depth=row["depth"] + 1, discovered_from=page_id)
        if rule.revisit_hours:
            state.mark_url(row["id"], "queued", next_fetch_in=float(rule.revisit_hours) * 3600, page_id=page_id)
        else:
            state.mark_url(row["id"], "done", page_id=page_id)
        return page_id, status, new_links
