"""<dataset>/extractors/<source_id>.py のひな形。

同じテンプレートのページが大量にあり、エージェントが数十件を手で抽出して規則が固まってから作る。
`crawler.py -d <dataset> auto-extract --source <source_id> --dry-run` で結果を確認してから保存する。

extract(page) は store と同じ形の dict を返す（page_id は不要）:
  {"status": "done", "records": [{"table": ..., "values": {...}, "decisions": [...]}]}
  {"status": "skipped", "note": "掲載終了ページ"}
判断できないページは None を返す。そのページは抽出待ちのまま残り、エージェントが読んで抽出する。

page で使えるもの:
  page.url / page.final_url / page.page_type / page.fetched_at
  page.soup        BeautifulSoup（HTML のとき）
  page.text        デコード済みの本文
  page.json()      JSON のとき
  page.markdown()  エージェントが `show` で見るのと同じ Markdown
"""
import re


def text_of(soup, selector: str) -> str | None:
    el = soup.select_one(selector)
    return el.get_text(" ", strip=True) if el else None


def parse_man_yen(raw: str) -> int | None:
    """「8.5万円」→ 85000。決まった形でなければ None（＝判断が要るのでエージェントに回す）。"""
    m = re.fullmatch(r"\s*([\d.]+)\s*万円\s*", raw or "")
    return round(float(m.group(1)) * 10000) if m else None


def extract(page):
    if page.page_type != "detail":
        return None
    soup = page.soup
    if soup.select_one(".closed-notice"):
        return {"status": "skipped", "note": "掲載終了"}

    listing_id = page.final_url.rstrip("/").rsplit("/", 1)[-1]
    rent_raw = text_of(soup, ".rent")
    rent = parse_man_yen(rent_raw)
    if rent is None:
        return None  # 想定外の表記。人と同じ判断が必要なのでエージェントに任せる

    return {
        "status": "done",
        "records": [{
            "table": "listings",
            "values": {
                "site_listing_id": listing_id,
                "name": text_of(soup, "h1"),
                "rent_jpy": rent,
                "rent_raw": rent_raw,
            },
            # judgment 列には決定ログが必須。機械的な変換でも「どの規則で変換したか」を残す
            "decisions": [{
                "field": "rent_jpy", "input": rent_raw, "reason": "規則: 「N万円」→ N×10000",
                "confidence": "high",
            }],
        }],
    }
