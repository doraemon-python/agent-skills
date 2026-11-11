# dataset.yaml の仕様

1 つのデータセット = 1 つのディレクトリ = 1 つの `dataset.yaml`。例は `assets/dataset.example.yaml`。
すべてのサブコマンドが実行のたびにこのファイルを検証し、不正ならエラーで止まる。

## トップレベル

| キー | 必須 | 内容 |
| --- | --- | --- |
| `name` | ✓ | データセット名 |
| `purpose` | ✓ | 何のためのデータか。抽出時の判断に迷ったときの拠り所になるので具体的に書く |
| `schema_version` | ✓ | 整数。`tables` を変えたら 1 上げて `migrate` する |
| `crawl` | ✓ | 取得のポリシー（下記） |
| `sources` | ✓ | 取得元のリスト（下記） |
| `tables` | ✓ | 出力する CSV の定義（下記） |
| `conventions` | | 判断基準のリスト。レビューで決まったことを追記していく。変えても schema_version は上げない |
| `output.csv_encoding` | | `utf-8`（既定）か `utf-8-sig`（Excel でそのまま開く場合） |

## crawl

| キー | 既定値 | 内容 |
| --- | --- | --- |
| `user_agent` | （必須） | 自分を名乗る UA。連絡先（メールか URL）を入れる |
| `concurrency` | `4` | 同時に取得する**ホスト**の数。同じホストへは設定に関係なく常に 1 本ずつ。`crawl --concurrency` で一時的に変えられる |
| `respect_robots` | `true` | robots.txt に従う。`false` にするのは自分のサイトなど許可がある場合だけ |
| `delay_sec` | `3` | 同じホストへのリクエスト間隔（秒）。1 未満は不可。robots.txt の Crawl-delay の方が長ければそちらを使う |
| `timeout_sec` | `30` | 1 リクエストのタイムアウト |
| `max_retries` | `3` | 5xx・通信エラーの再試行回数。超えたら URL を `failed` にする |
| `max_bytes` | `20000000` | 1 レスポンスの上限サイズ |
| `max_backoff_sec` | `3600` | 429 / 503 を受けたときの待ち時間の上限 |
| `max_consecutive_errors` | `5` | 同じホストでこの回数連続して失敗したら、その実行ではそのホストを取得しない（ブロックされている可能性）。他のホストは続ける |
| `accept_language` | `ja,en;q=0.8` | Accept-Language ヘッダ |
| `strip_params` | `["utm_*", "fbclid", "gclid"]` | URL から取り除くクエリパラメータ（glob）。並び順やセッション ID など、同じページが別 URL になる原因を入れる |

## sources

```yaml
sources:
  - id: example_rent          # 小文字英数字と _。ログ・進捗 DB・extractors/<id>.py で使う
    seeds: [https://...]      # 最初に取得する URL
    allow: ['^https://example\.com/rent/(list|detail)/']   # キューに入れてよい URL（正規表現、search）
    deny: ['/print/', '\?.*sort=']                         # allow に一致しても除外する URL
    delay_sec: 5              # このソースだけ間隔を変える（任意）
    render_js: false          # true なら Playwright で描画して取得（references/setup.md）
    pages:                    # URL ごとの扱い。上から順に最初に一致したものを使う
      - match: '/rent/list'   # 正規表現（search）
        type: listing         # 任意の名前。pending --type で絞れる
        extract: false        # false: 抽出対象にしない（リンクをたどるだけ）
        follow: true          # false: このページのリンクをキューに入れない
        revisit_hours: 24     # 取得後、この時間が経ったら再取得する（新着・更新を追う）
        priority: 10          # 大きいほど先に取得する
```

- `allow` を省略すると、シードと同じホストのすべての URL が対象になる。広すぎるので基本は書く
- どの `pages` にも一致しない URL は `type: page, extract: true, follow: true` として扱う
- 取得したページのリンクは、**どれかの source の allow に一致すれば**その source の URL としてキューに入る
- `revisit_hours` を付けたページは、再取得時に内容（見た目のテキスト）が変わっていなければ「変化なし」となり、抽出し直さない。
  そのページから作られたレコードの `_last_seen_at` だけ更新される
- `allow` / `deny` / `pages` を変えてもスキーマ変更ではない。既存のキューにも次回の `crawl` から適用される

## tables

```yaml
tables:
  listings:                       # テーブル名 = CSV のファイル名（data/listings.csv）
    description: 物件 1 件 = 1 行
    primary_key: [site_listing_id] # 同じレコードかどうかの判定キー。複数列も可（[ticker, date] など）
    keep_history: true             # 値が変わったら旧行を listings.history.csv に追記する
    columns:
      - name: rent_jpy             # 小文字英字で始まる英数字と _
        type: integer
        required: true             # 新規レコードでこの列が無い・空ならエラー
        judgment: true             # 判断が分かれる変換。値を入れるときは decisions が必須
        description: 月額賃料（円）。管理費は含めない   # 抽出の指示そのもの。単位・含む/含まないを書く
        example: 85000             # 提案・確認用（検証には使わない）
```

### 列の型

| type | CSV での表現 | 抽出時に渡す値 |
| --- | --- | --- |
| `string` | そのまま | 文字列 |
| `text` | `max_inline_chars`（既定 1000）以下ならそのまま、超えたら `files/` に保存してそのパス | 文字列 |
| `integer` | `85000` | 数値か数字の文字列（`"85,000"` 可）。単位の換算は抽出側で済ませる |
| `number` | `41.5` | 同上 |
| `boolean` | `true` / `false` | 真偽値 |
| `date` | `2026-11-11` | `YYYY-MM-DD` |
| `datetime` | ISO 8601（`2026-11-11T09:00:00+09:00`） | 同左 |
| `enum` | `values` のどれか | `values` にない値はエラー（スキーマ変更を提案する） |
| `list` | JSON 配列（`["ペット可","角部屋"]`） | 文字列・数値の配列 |
| `url` | そのまま | `http(s)://` で始まる文字列 |
| `file` | `files/` 内のパス | URL 文字列、または `{"url"}` / `{"path"}` / `{"text", "ext"}` / `{"page_id"}` |
| `image` | 同上 | 同上 |

- `file` / `image` に `multiple: true` を付けると複数持てる。CSV にはパスの JSON 配列が入る
- `primary_key` に `text` / `list` / `file` / `image` は使えない。primary_key の列は自動で `required` になる
- 空の値（`null`・`""`・`[]`）は CSV では空欄になる
