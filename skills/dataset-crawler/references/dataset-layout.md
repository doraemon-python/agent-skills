# データセットの構造

```text
<dataset>/
├── dataset.yaml                # 設定とスキーマ（人とエージェントが編集する唯一のファイル）
├── schema/v1.yaml, v2.yaml     # init / migrate 時の dataset.yaml の控え
├── data/                       # ★ 成果物
│   ├── <table>.csv             # 1 テーブル 1 ファイル
│   ├── <table>.history.csv     # keep_history: true のとき。上書き前の行
│   └── _archive/v1/            # migrate で退避した旧スキーマの CSV と files/
├── files/                      # ★ CSV に入らないもの（長文・書類・画像）
│   └── <table>/<_id>/<column>.<ext>        # multiple のときは <column>_1.<ext>, <column>_2.<ext> ...
├── logs/
│   ├── crawl.jsonl             # 取得のたびに 1 行
│   ├── decisions.jsonl         # 判断を伴う変換のたびに 1 行（追記のみ）
│   ├── corrections.jsonl       # 人による修正（追記のみ）
│   └── reviews.jsonl           # 「修正不要」と確認した判断
├── extractors/<source_id>.py   # 任意。決まった形のページ用の抽出コード
├── raw/                        # 一時データ: 取得した生データ（gzip、内容ハッシュ名）
│   └── ab/abcdef….html.gz
└── state/crawl.sqlite          # 進捗: URL キュー・取得履歴・ホストの待ち時間
```

`data/` と `files/` は `raw/` と `logs/corrections.jsonl` から再クロールせずに作り直せる（`migrate` / `reextract`）。逆に `raw/` と `state/` は作り直せないので消さない。

## CSV（data/<table>.csv）

- UTF-8（`output.csv_encoding: utf-8-sig` なら BOM 付き）、改行 LF、ヘッダ行あり、RFC 4180 のクォート
- 列の順番: `_id`、`dataset.yaml` の `columns` の順、管理列
- 1 行 = primary_key で決まる 1 レコード。同じキーで再度保存すると上書き（渡した列だけ）
- 値の表現は config.md の「列の型」のとおり。空欄は「値なし」

管理列:

| 列 | 内容 |
| --- | --- |
| `_id` | primary_key の値から作る 16 桁のハッシュ。`files/<table>/<_id>/` のフォルダ名、`fix --id` に使う |
| `_source_url` | 最後にこの行を更新したページの URL（リダイレクト後） |
| `_page_id` | 同じくページの ID。`show <page_id>` で元のページを見られる |
| `_schema_version` | 保存したときの schema_version |
| `_first_seen_at` | 初めて保存した日時（UTC） |
| `_last_seen_at` | 最後に保存した・再取得で存在を確認した日時（UTC）。古いままなら掲載終了の可能性 |

`<table>.history.csv` は上の列に `_replaced_at`（上書きされた日時）を足したもの。値の推移（値下げなど）を追うのに使う。

## files/

- パスは常に**データセットのルートからの相対パス**で CSV に入る（例 `files/listings/356a192b7913b04c/floor_plan.png`）。
  `_archive/v1/` 内の CSV のパスは `_archive/v1/` からの相対になる
- `text` 列は `max_inline_chars` を超えると `<column>.md` に保存される。値が `files/` で始まり `.md` で終わるセルはファイル参照
- `file` / `image` 列で URL を渡すと、`crawl` と同じ待ち時間・robots.txt のルールでダウンロードする。ダウンロードしたものは `raw/` にも保存され、
  再抽出・migrate のときは再ダウンロードしない
- 1 レコードのファイルはすべて `files/<table>/<_id>/` にまとまる。レコードの値を更新すると、その列のファイルは置き換わる
- ダウンロードに失敗したとき:
  - 404 / 410 / robots.txt で禁止 など恒久的な失敗: そのファイルだけ除いて保存する（警告が出る）
  - 通信エラー・5xx・429・403・ホストの待機中 など一時的な失敗: その列は前の値とファイルをそのまま残し、ページを抽出待ちに戻す。
    時間をおいて同じ内容をもう一度 `store` すれば埋まる（一時的な失敗で保存済みのファイルが消えないようにするため）
- ファイルの種類（拡張子）は Content-Type で決め、汎用の型（`application/octet-stream` など）なら URL の拡張子を使う。PDF ページを `{"page_id": N}` で保存すると、取得した PDF がそのままコピーされる

## store の入力形式

`crawler store <file>` に渡す JSONL（1 行 = 1 ページ）。JSON 配列でもよい。

```json
{
  "page_id": 12,
  "status": "done",
  "note": "任意のメモ",
  "records": [
    {
      "table": "listings",
      "values": {"site_listing_id": "123456", "rent_jpy": 85000, "photos": ["https://.../1.jpg", "https://.../2.jpg"]},
      "decisions": [
        {"field": "rent_jpy", "input": "8.5万円（管理費込み）", "reason": "管理費 5,000 円を差し引いた", "confidence": "medium"}
      ]
    }
  ]
}
```

| キー | 内容 |
| --- | --- |
| `page_id` | `pending` / `show` に出る ID（必須） |
| `status` | `done`（既定）/ `skipped`（対象外だった）/ `error`（読めなかった）。`skipped` / `error` は `note` 必須・`records` は空 |
| `records[].table` | テーブル名 |
| `records[].values` | 列名 → 値。primary_key は必須。含めない列は既存の値のまま、`null` を入れると空にする |
| `records[].decisions[]` | 判断の記録。`field`・`reason` 必須。`input`（元の表記）は必ず書く。`confidence` は `high`（既定）/ `medium` / `low`。`low` か `needs_review: true` でレビュー待ちになる。`output` を省略すると保存した値が入る |

1 ページから複数レコード（一覧ページの全件、複数テーブル）を出してよい。0 件なら `status: skipped` にする。

検証はページ単位で、エラーのあったページだけ保存されない（他のページは保存される）。終了コード 1 とエラー内容が出るので、そのページだけ直して再送する。

## logs/

すべて JSON Lines（1 行 1 JSON、UTF-8）。

`decisions.jsonl`:

```json
{"id": "d_3f2a9c1b7e04", "ts": "2026-11-11T09:13:29Z", "table": "listings", "record_id": "356a192b7913b04c",
 "key": {"site_listing_id": "123456"}, "field": "rent_jpy", "input": "9万円（管理費込み）", "output": "85000",
 "reason": "管理費 5,000 円を差し引いた", "confidence": "medium", "needs_review": false, "overridden_by_correction": false,
 "page_id": 12, "source_url": "https://...", "schema_version": 1, "by": "agent"}
```

`by` は `agent` か `extractors/<file>`。`overridden_by_correction` が true なら、抽出結果より人の修正値が優先された。

`corrections.jsonl`:

```json
{"ts": "...", "decision_id": "d_3f2a9c1b7e04", "table": "listings", "record_id": "356a192b7913b04c", "field": "layout",
 "old": "その他", "new": "2LDK", "note": "S は数えない"}
```

`(table, record_id, field)` ごとに最新の修正が「固定値」となり、以後の store で毎回かぶせられる。`fix --unpin` で `{"unpin": true}` の行が追記され固定が外れる。

`crawl.jsonl`: `{"ts", "run", "kind": "page"|"asset", "url", "status", "result", "page_id", "bytes", "extract_status", "new_urls", "error", "backoff_sec"}`。
`result` は `fetched` / `rate_limited` / `retry` / `failed` / `gone` / `robots_blocked`。

## state/crawl.sqlite

直接書き換えない（`add-urls` / `reextract` / `migrate` を使う）。調べるときは読み取りだけ。

| テーブル | 内容 |
| --- | --- |
| `urls` | URL キュー。`status`: `queued`（`next_fetch_at` 以降に取得）/ `done` / `failed` / `gone`（404・410）/ `blocked`（robots.txt・対象外） |
| `pages` | 取得 1 回 = 1 行（= page_id）。`raw_path`、`extract_status`: `pending` / `done` / `skipped` / `error` / `unchanged` / `not_target` / `superseded` |
| `hosts` | ホストごとの次にリクエストしてよい時刻と、レートリミットの待ち時間 |
| `assets` | file / image 列のためにダウンロードした URL と raw/ の対応 |
| `runs` | `crawl` の実行履歴（停止理由と統計） |
| `meta` | 保存済みデータの schema_version |
