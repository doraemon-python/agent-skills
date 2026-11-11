---
name: dataset-crawler
description: Web サイトを自律的・定期的にクロールし、物件情報・求人・株価・商品など量が多く構造の定まらない情報を、承認済みスキーマの CSV データベースとして手元に蓄積し続ける。目的に合わせたデータ構造の提案と承認、レートリミットで中断しても続きから再開できる進捗管理、人によって判断が分かれる変換の記録とレビュー・修正、生データの保存による再クロールなしの作り直しまで扱う。ユーザーが「〇〇の情報を集めてデータベースにしたい」「定期的にクロールして CSV にまとめて」「スクレイピングして構造化して」「前回の続きからクロールして」「集めたデータの判断を見直したい」「収集データのスキーマを変えたい」と言ったときに使う。1 ページの内容を読んで答えるだけの依頼には使わない。
license: MIT
compatibility: Python 3.10 以上と uv（推奨）が必要。JavaScript で描画されるサイトには Playwright を追加で使う
metadata:
  version: "0.1.0"
---

# Dataset Crawler

決められた対象を繰り返しクロールし、ページから必要な情報を抜き出して **CSV + ファイル** のデータベースを育てていく。
全部集め切ることは前提にしない。少しずつ進め、止まっても続きから再開し、後から判断を見直せることを優先する。

役割分担:

- **スクリプト（`scripts/crawler.py`）**: 取得・robots.txt・待ち時間・進捗・生データ保存・CSV 書き込み・検証・ログ。毎回同じ処理はすべてこちらでやる。
  取得は非同期で、**違うホストは同時に（既定 4 ホストまで）、同じホストは 1 本ずつ `delay_sec` を空けて**行う。ファイル列のダウンロードも同じ
- **エージェント（あなた）**: スキーマの提案、ページを読んで値を取り出すこと、判断が要る変換とその理由の記録、スキーマ変更の提案

## 準備

セットアップは [references/setup.md](references/setup.md) に従う（`uv` があれば追加の作業はほぼない）。
コマンドはすべて次の形で実行する。以下では `crawler` と略す。

```bash
uv run <このスキルのディレクトリ>/scripts/crawler.py -d <データセットのディレクトリ> <サブコマンド> ...
```

まず、データセットがすでにあるかを確かめる。ユーザーが指定した場所（指定がなければカレント）に `dataset.yaml` があれば
`crawler -d <dir> status` を実行して状況を把握し、「3. クロールと抽出」から再開する。なければ 1 から始める。

## 1. 目的を聞き、データ構造を提案して承認をもらう

承認をもらう前に `dataset.yaml` を書いたり `init` したりしない。

1. 次をユーザーに確認する（会話からわかるものは聞き直さない）
   - 何のためのデータか（比較・分析・監視など。列の取捨選択がここで決まる）
   - 対象サイト・範囲（地域・職種・銘柄など）と、だいたいの件数の見込み
   - 更新を追いたいか（値下げ・掲載終了・日々の株価など）と、その頻度
2. 対象サイトの利用規約・robots.txt・公式 API の有無を確認する。API やダウンロード可能なデータがあればそちらを提案する。
   ログインが必要なページ・有料コンテンツ・個人情報が中心のページは、ユーザーに権利と目的を確認してから扱う
3. ページの種類（一覧・詳細・PDF など）ごとに 2〜3 件を `crawler probe <URL>` で取得し、実際の表記を見る。
   中身が空なら JavaScript 描画なので `--render-js` を試す
4. [references/schema-design.md](references/schema-design.md) の指針に沿ってスキーマを作り、同ファイルの「提案テンプレート」の形で提示する。
   特に **人によって判断が分かれる列（judgment）** と、そのときの判断基準の案を明示する
5. ユーザーの修正を反映し、明示的な承認をもらう

## 2. 初期化と試運転

1. [assets/dataset.example.yaml](assets/dataset.example.yaml) を参考に、承認内容どおりの `<dir>/dataset.yaml` を書く（仕様は [references/config.md](references/config.md)）。
   `crawl.user_agent` には連絡先を入れる（ユーザーに確認する）
2. `crawler -d <dir> init` でディレクトリと進捗 DB を作り、シード URL を登録する
3. `crawler -d <dir> crawl --max-requests 10` で少しだけ取得し、`status` と `pending` で確かめる
   - 目的のページが抽出待ちに入っているか、関係ないページを拾っていないか
   - ずれていれば `allow` / `deny` / `pages` を直す（スキーマではないので schema_version は上げない）
4. 数ページを抽出・保存してみて（次の節の手順）、CSV を見せてユーザーに確認してもらう

## 3. クロールと抽出（ここを繰り返す）

ユーザーが指定した量・時間（指定がなければ 1 回あたり 100 リクエスト程度）まで、次を繰り返す。

1. **取得**: `crawler -d <dir> crawl --max-requests 100`
   - 制限されたり失敗が続いたりしたホストは、そのホストだけ待機・中断し、他のホストの取得は続く
   - 終了コード 3（レートリミット・連続エラーのホストがあった）なら、再実行して制限を突破しようとしない。抽出だけ進めて、どのホストが何時以降に再開できるかを報告する
   - 待ち時間を短くしたり、User-Agent を偽ったりして制限を回避しない
2. **抽出待ちの確認**: `crawler -d <dir> pending --limit 10`
3. **読む**: `crawler -d <dir> show <page_id>`。長いページは `--offset` で続きを読むか、`--selector` で必要な部分だけ表示する
4. **書く**: 抽出結果を JSONL にまとめる（1 行 = 1 ページ。形式の詳細は [references/dataset-layout.md](references/dataset-layout.md) の「store の入力形式」）

   ```json
   {"page_id": 12, "status": "done", "records": [{"table": "listings", "values": {"site_listing_id": "123456", "name": "サンプルハイツ", "rent_jpy": 85000, "rent_raw": "8.5万円", "layout": "2LDK", "floor_plan": "https://example.com/img/fp.png"}, "decisions": [{"field": "rent_jpy", "input": "8.5万円", "reason": "万円表記を円に換算", "confidence": "high"}, {"field": "layout", "input": "2SLDK", "reason": "規約: S は数えない", "confidence": "medium"}]}]}
   {"page_id": 13, "status": "skipped", "note": "掲載終了ページ"}
   ```

5. **保存**: `crawler -d <dir> store <file.jsonl>`。エラーが出たページだけ直して再実行する（保存済みのページは再送しなくてよい）
6. 抽出待ちが無くなったら 1 に戻る

抽出のルール:

- 毎回 `dataset.yaml` の列の `description` と `conventions` に従う。ページに書かれていない値は推測で埋めず、空（`null`）にする
- `judgment: true` の列に値を入れるときは、必ず `decisions` に `input`（元の表記）・`reason`・`confidence` を書く（無いと store が拒否する）。
  それ以外の列でも、解釈を挟んだときは `decisions` に残す
- 迷ったら `confidence: "low"` にする。レビュー待ちとして人に回る。勝手に規則を作らない
- スキーマに合わない情報（新しい間取りの種類、列が無い重要な情報など）は、無理に既存の列に押し込まず、`note` 系の列に残すかメモしておき、まとめて「5. スキーマ変更」として提案する
- 一覧ページから得られる値と詳細ページの値で、同じレコード（同じ primary_key）を部分的に更新してよい。`values` に含めた列だけが上書きされる
- 1 回の store は 5〜10 ページ程度にまとめる

**量が多いとき**: 同じ種類のページを 20〜30 件ほど手で抽出し、表記の規則が固まったら、[assets/extractor_template.py](assets/extractor_template.py) をもとに
`<dir>/extractors/<source_id>.py` を書くことをユーザーに提案する。`crawler -d <dir> auto-extract --source <id> --dry-run` の結果を自分の抽出結果と突き合わせ、
一致してから dry-run なしで実行する。extractor が判断できないページ（`None` を返したもの）は抽出待ちに残るので、引き続きエージェントが抽出する。

## 4. 報告

区切りごとに（ユーザーに返すとき、止まったとき）`crawler -d <dir> status` を実行し、次の形で報告する。

```markdown
**今回の進捗**: 取得 120 ページ / 追加 85 行・更新 12 行（listings: 計 1,240 行）
**残り**: 取得待ち 430 URL / 抽出待ち 0 ページ / 再訪問待ち 35 URL
**止まった理由**: example.com からレートリミット（429）。2026-11-11 15:30 以降に再開できます
**確認してほしいこと**: レビュー待ちの判断 4 件（`review` で一覧）。「管理費込み」の扱いが 3 件
**スキーマの提案**: なし
```

## レビューと修正

判断ログは `logs/decisions.jsonl` に残る。ユーザーが見直したいと言ったとき、またはレビュー待ちがたまったら:

1. `crawler -d <dir> review` でレビュー待ち（`confidence: low` など）を一覧する。列やレコードで絞るなら `--table` `--field` `--id`、全件は `--all`
2. ユーザーの判断に従って:
   - 値を直す: `crawler -d <dir> fix <decision_id> --value <正しい値> --note "<理由>"`。直した値は固定され、再抽出しても上書きされない
   - 抽出時の判断で正しかった: `crawler -d <dir> fix --approve <decision_id> ...`
3. 同じ判断が今後も出そうなら、その基準を `dataset.yaml` の `conventions` に追記することを提案する（スキーマ変更ではないので schema_version は上げない）。
   既存データにも適用したいなら `crawler -d <dir> reextract --source <id>`（再クロールせず raw/ から抽出し直す）

## 5. スキーマ変更

列の追加・削除・型の変更、テーブルの追加などは、必ず変更点（差分と理由、既存データへの影響）を示して承認をもらってから行う。

1. `dataset.yaml` を直し、`schema_version` を 1 上げる
2. `crawler -d <dir> migrate`: 旧 CSV と files/ を `data/_archive/v<旧>/` に退避し、全ページを抽出待ちに戻す（再クロールはしない）
3. 「3. クロールと抽出」の手順で抽出し直す。人が修正した値（corrections）は、列が残っていれば自動で適用される

## 定期実行と運用

定期的に回す方法（cron・エージェントのスケジュール機能）、レートリミット時の挙動、ディスク容量、トラブル対応は
[references/operations.md](references/operations.md) を読む。取得（`crawl`）は AI を使わないので、cron だけで回せる。

## してはいけないこと

- 承認前にスキーマを確定させる・`schema_version` を上げずに列を変える
- robots.txt で禁止されたページ、ログインや有料の壁の向こうを、ユーザーの確認なしに取得する
- `delay_sec` を 1 秒未満にする、同じホストへ並列に取得する（`crawl` を同時に複数走らせる・ホスト名を変えて同じサーバーを並列に叩くなど）、制限された後にすぐ再試行する
- 値を推測で埋める・判断の理由を書かずに judgment 列を埋める
- `data/` の CSV や `logs/` を手で書き換える（`store` と `fix` を使う。ログの整合性が崩れる）
- `raw/` や `state/` を消す（再クロールなしで作り直せなくなる・進捗が失われる）
