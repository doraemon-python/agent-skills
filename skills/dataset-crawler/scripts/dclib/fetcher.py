"""非同期の HTTP 取得・robots.txt・ホストごとの待ち時間・生データ（raw/）の保存。

並行に取得するのは「違うホスト」だけ。同じホストへのリクエストは常に 1 本ずつ、delay_sec 以上の間隔を空ける。
この約束を守るため、ホストの待ち時間は record_response() でだけ更新する。
"""
import asyncio
import gzip
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import ConfigError, CrawlPolicy
from .state import State
from .util import atomic_write_bytes, sha256_hex

EXT_BY_TYPE = {
    "text/html": "html", "application/xhtml+xml": "html", "application/json": "json",
    "application/pdf": "pdf", "text/xml": "xml", "application/xml": "xml", "text/csv": "csv",
    "text/plain": "txt", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
    "image/gif": "gif", "image/svg+xml": "svg",
}
RATE_LIMIT_STATUSES = (429, 503)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int  # ネットワークエラーは 0
    content_type: str
    body: bytes
    error: str | None = None
    retry_after: float | None = None


def ext_for(content_type: str, url: str = "") -> str:
    ext = EXT_BY_TYPE.get(content_type.split(";")[0].strip().lower())
    if ext:
        return ext
    suffix = Path(urlsplit(url).path).suffix.lstrip(".").lower()
    return suffix if suffix.isalnum() and len(suffix) <= 5 else "bin"


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class AsyncFetcher:
    """`async with AsyncFetcher(policy) as f:` で使う。1 つのインスタンスを全タスクで共有する。"""

    def __init__(self, policy: CrawlPolicy):
        self.policy = policy
        self.client = httpx.AsyncClient(
            headers={"User-Agent": policy.user_agent, "Accept-Language": policy.accept_language},
            timeout=policy.timeout_sec,
            follow_redirects=True,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._browser = None
        self._browser_lock = asyncio.Lock()

    async def __aenter__(self) -> "AsyncFetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()
        if self._browser:
            await self._browser.close()
            await self._playwright.stop()

    # --- robots.txt ---
    async def robots_allows(self, url: str) -> bool:
        if not self.policy.respect_robots:
            return True
        rp = await self._robots_for(_origin(url))
        return True if rp is None else rp.can_fetch(self.policy.user_agent, url)

    def robots_crawl_delay(self, url: str) -> float | None:
        """robots_allows() を先に呼んだオリジンについてだけ答える。"""
        rp = self._robots.get(_origin(url))
        if rp is None:
            return None
        delay = rp.crawl_delay(self.policy.user_agent)
        return float(delay) if delay is not None else None

    async def _robots_for(self, origin: str) -> RobotFileParser | None:
        # 同じオリジンの robots.txt を複数のタスクが同時に取りにいかないようにする
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._robots:
                self._robots[origin] = await self._load_robots(origin)
        return self._robots[origin]

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        rp = RobotFileParser()
        try:
            r = await self.client.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            return None  # 取れないときは制限なしとして扱う（一般的なクローラの挙動）
        if r.status_code in (401, 403):
            rp.disallow_all = True
            return rp
        if r.status_code >= 400:
            return None
        rp.parse(r.text.splitlines())
        return rp

    # --- 取得 ---
    async def fetch(self, url: str, render_js: bool = False) -> FetchResult:
        if render_js:
            return await self._fetch_js(url)
        try:
            async with self.client.stream("GET", url) as r:
                chunks, size = [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > self.policy.max_bytes:
                        return FetchResult(url, str(r.url), r.status_code, "", b"",
                                           error=f"max_bytes（{self.policy.max_bytes}）を超えました")
                    chunks.append(chunk)
                return FetchResult(
                    url=url,
                    final_url=str(r.url),
                    status=r.status_code,
                    content_type=r.headers.get("content-type", ""),
                    body=b"".join(chunks),
                    retry_after=_retry_after(r.headers.get("retry-after")),
                )
        except httpx.HTTPError as e:
            return FetchResult(url, url, 0, "", b"", error=f"{type(e).__name__}: {e}")

    async def _fetch_js(self, url: str) -> FetchResult:
        """JavaScript で描画されるページ用。Playwright が必要（references/setup.md）。"""
        async with self._browser_lock:
            if self._browser is None:
                require_playwright()
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch()
        context = await self._browser.new_context(
            user_agent=self.policy.user_agent,
            extra_http_headers={"Accept-Language": self.policy.accept_language},
        )
        # 画像・動画・フォントは本文の抽出に不要。読み込まないことで相手サーバーへのリクエストも減らす
        await context.route("**/*", _block_heavy_resources)
        page = await context.new_page()
        timeout_ms = self.policy.timeout_sec * 1000
        try:
            resp = await page.goto(url, wait_until="load", timeout=timeout_ms)
            try:
                # 描画用の通信が落ち着くまで少し待つ。解析タグなどで通信が続くサイトでは待ち切らずに進む
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
            except Exception:
                pass
            html = (await page.content()).encode("utf-8")
            if resp is None:
                return FetchResult(url, page.url, 0, "", b"", error="レスポンスがありません")
            headers = await resp.all_headers()
            return FetchResult(
                url=url,
                final_url=page.url,
                status=resp.status,
                content_type="text/html; charset=utf-8",  # 描画後の DOM を HTML として保存する
                body=html,
                retry_after=_retry_after(headers.get("retry-after")),
            )
        except Exception as e:  # Playwright の例外型はオプション依存なので広く拾う
            return FetchResult(url, url, 0, "", b"", error=f"{type(e).__name__}: {e}")
        finally:
            await context.close()


async def _block_heavy_resources(route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        await route.abort()
    else:
        await route.continue_()


def require_playwright() -> None:
    """render_js を使う前に呼ぶ。Playwright が無ければ、取得を始める前にわかるようにする。"""
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        raise ConfigError(
            "render_js には Playwright が必要です。`uv run --with playwright ...` で実行してください"
            "（references/setup.md の「JavaScript 描画が必要なサイト」）"
        ) from None


def record_response(state: State, policy: CrawlPolicy, host: str, res: FetchResult, delay: float) -> float | None:
    """応答を見てホストの次回リクエスト可能時刻を更新する。レートリミットなら待ち秒数を返す。"""
    now = time.time()
    if res.status in RATE_LIMIT_STATUSES:
        prev = state.host(host)
        backoff = prev["backoff_sec"] if prev else 0
        if res.retry_after is not None:
            # サーバーの指定に従う（max_backoff_sec より長くても短縮しない）
            backoff = max(res.retry_after, delay)
        else:
            # 指定が無ければ delay の 10 倍から倍々に伸ばす
            backoff = min(backoff * 2 if backoff else delay * 10, policy.max_backoff_sec)
        state.set_host(host, next_allowed_at=now + backoff, backoff_sec=backoff,
                       last_status=res.status, note="rate limited")
        return backoff
    state.set_host(host, next_allowed_at=now + delay, backoff_sec=0, last_status=res.status)
    return None


def host_wait(state: State, host: str) -> float:
    """そのホストに次にリクエストしてよいまでの秒数。"""
    h = state.host(host)
    return max(0.0, h["next_allowed_at"] - time.time()) if h else 0.0


def save_raw(raw_dir: Path, body: bytes, ext: str) -> tuple[str, str]:
    """内容のハッシュで raw/ に gzip 保存する。同じ内容は 1 回しか保存しない。(hash, 相対パス) を返す。"""
    digest = sha256_hex(body)
    rel = Path("raw") / digest[:2] / f"{digest}.{ext}.gz"
    path = raw_dir.parent / rel
    if not path.exists():
        atomic_write_bytes(path, gzip.compress(body, compresslevel=6))
    return digest, rel.as_posix()


def load_raw(root: Path, rel: str) -> bytes:
    return gzip.decompress((root / rel).read_bytes())
