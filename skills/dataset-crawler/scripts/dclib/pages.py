"""取得したページを、エージェントが読みやすいテキストに変換する・リンクを抜き出す。"""
import io
import json
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify

from .fetcher import load_raw
from .util import sha256_hex

DROP_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "canvas", "form"]


def kind_of(content_type: str, raw_path: str) -> str:
    ct = (content_type or "").lower()
    if "html" in ct or raw_path.endswith(".html.gz"):
        return "html"
    if "json" in ct or raw_path.endswith(".json.gz"):
        return "json"
    if "pdf" in ct or raw_path.endswith(".pdf.gz"):
        return "pdf"
    if ct.startswith("text/") or "xml" in ct:
        return "text"
    return "binary"


def decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    candidates = [m.group(1)] if m else []
    head = body[:2048].decode("ascii", "ignore")
    m = re.search(r"<meta[^>]+charset=[\"']?([\w-]+)", head, re.I)
    if m:
        candidates.append(m.group(1))
    for enc in candidates + ["utf-8", "cp932", "euc-jp"]:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def html_to_markdown(html: str, base_url: str, selector: str | None = None) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(DROP_TAGS):
        tag.decompose()
    # 相対リンク・画像を絶対 URL にしておく（画像列の URL をそのまま使えるように）
    for tag in soup.find_all(href=True):
        tag["href"] = urljoin(base_url, tag["href"])
    for tag in soup.find_all(src=True):
        tag["src"] = urljoin(base_url, tag["src"])
    root = soup
    if selector:
        found = soup.select(selector)
        if not found:
            return f"(セレクタ '{selector}' に一致する要素はありません)"
        root = BeautifulSoup("".join(str(x) for x in found), "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    md = markdownify(str(root.body or root), heading_style="ATX", strip=["head"])
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return f"# {title}\n\n{md}" if title else md


def pdf_to_text(body: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "(pypdf が無いため PDF をテキスト化できません。references/setup.md を参照)"
    reader = PdfReader(io.BytesIO(body))
    return "\n\n".join(f"--- p.{i + 1} ---\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages))


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = [urljoin(base_url, a["href"]) for a in soup.find_all("a", href=True)]
    links += [urljoin(base_url, l["href"]) for l in soup.find_all("link", rel="next", href=True)]
    return [u for u in links if u.startswith(("http://", "https://"))]


@dataclass
class Page:
    """抽出時に使うページ。extractors/*.py にもこの形で渡す。"""
    id: int
    url: str
    final_url: str
    source_id: str
    page_type: str
    fetched_at: str
    content_type: str
    body: bytes
    kind: str

    @classmethod
    def load(cls, root: Path, row) -> "Page":
        return cls(
            id=row["id"], url=row["url"], final_url=row["final_url"], source_id=row["source_id"],
            page_type=row["page_type"], fetched_at=row["fetched_at"],
            content_type=row["content_type"] or "", body=load_raw(root, row["raw_path"]),
            kind=kind_of(row["content_type"], row["raw_path"]),
        )

    @cached_property
    def text(self) -> str:
        return decode(self.body, self.content_type)

    @cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.text, "lxml")

    def json(self):
        return json.loads(self.text)

    def markdown(self, selector: str | None = None) -> str:
        if self.kind == "html":
            return html_to_markdown(self.text, self.final_url, selector)
        if self.kind == "json":
            return json.dumps(self.json(), ensure_ascii=False, indent=2)
        if self.kind == "pdf":
            return pdf_to_text(self.body)
        if self.kind == "text":
            return self.text
        return f"(バイナリ {len(self.body)} bytes, {self.content_type})"


def text_hash(body: bytes, content_type: str, kind: str) -> str:
    """変更検知用のハッシュ。HTML は見た目のテキストで比べる（CSRF トークンや広告の差で「変更あり」にしない）。"""
    if kind == "html":
        soup = BeautifulSoup(decode(body, content_type), "lxml")
        for tag in soup(DROP_TAGS):
            tag.decompose()
        return sha256_hex(" ".join(soup.get_text(" ").split()).encode("utf-8"))
    return sha256_hex(body)
