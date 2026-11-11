# セットアップ手順

## 必要なもの

| もの | 用途 | 必須 |
| --- | --- | --- |
| Python 3.10 以上 | スクリプトの実行 | 必須 |
| [uv](https://docs.astral.sh/uv/) | 依存ライブラリの自動インストールと実行 | 推奨（無ければ venv で代用） |
| Playwright + Chromium | JavaScript で描画されるサイトの取得 | 必要なときだけ |

使うライブラリ（`scripts/crawler.py` の先頭と `scripts/requirements.txt` に同じ内容を書いてある）:

| ライブラリ | 用途 |
| --- | --- |
| httpx | HTTP 取得（タイムアウト・リダイレクト・ストリーミングでのサイズ制限） |
| beautifulsoup4 + lxml | HTML の解析、リンク抽出 |
| markdownify | HTML をエージェントが読みやすい Markdown に変換（表を保ったまま） |
| pyyaml | `dataset.yaml` の読み込み |
| pypdf | PDF のテキスト化 |

進捗管理の SQLite、CSV、robots.txt の解釈は Python 標準ライブラリを使う。

## A. uv を使う（推奨）

```bash
# uv が無ければインストール（macOS / Linux）
curl -LsSf https://astral.sh/uv/install.sh | sh
# Homebrew なら: brew install uv
# Windows (PowerShell): powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 動作確認。初回だけ依存ライブラリが自動で入る（以後はキャッシュされる）
uv run <スキルのディレクトリ>/scripts/crawler.py -h
```

`crawler.py` は先頭に [PEP 723](https://peps.python.org/pep-0723/) 形式で依存を書いてあるので、`uv run` するだけで隔離された環境が作られる。
プロジェクトの仮想環境を汚さない。

## B. uv を使わない

```bash
python3 -m venv ~/.venvs/dataset-crawler
~/.venvs/dataset-crawler/bin/pip install -r <スキルのディレクトリ>/scripts/requirements.txt
~/.venvs/dataset-crawler/bin/python <スキルのディレクトリ>/scripts/crawler.py -h
```

以後、SKILL.md の `uv run .../crawler.py` は `~/.venvs/dataset-crawler/bin/python .../crawler.py` に読み替える。

## JavaScript 描画が必要なサイト

`probe` で本文がほぼ空（`<div id="app"></div>` だけなど）なら、JavaScript で描画されている。

```bash
# uv の場合: --with で Playwright を足して実行する
uv run --with playwright python -m playwright install chromium   # 初回だけ。ブラウザをダウンロード
uv run --with playwright <スキルのディレクトリ>/scripts/crawler.py probe <URL> --render-js

# venv の場合
~/.venvs/dataset-crawler/bin/pip install playwright
~/.venvs/dataset-crawler/bin/python -m playwright install chromium
```

`dataset.yaml` の該当 source に `render_js: true` を書き、`crawl` も `uv run --with playwright ...` で実行する。
ブラウザでの取得は重いので、先にページ内の JSON API（ブラウザの開発者ツールの Network タブで見つかることが多い）を直接取得できないか確かめる。

ブラウザでの取得の動き:

- ページの `load` の後、通信が落ち着くまで最大 5 秒待ってから描画後の HTML を保存する（解析タグなどで通信が続くページでも止まらない）
- 画像・動画・フォントは読み込まない（抽出に不要で、相手サーバーへのリクエストも減る）。画像列の URL は HTML の `img` から取れる
- 違うホストは並行して描画する（ブラウザは 1 つを共有し、ページごとに新しいコンテキストを使う）。同じホストは 1 本ずつ

## データセットの置き場所

- スキルのディレクトリの中には置かない（スキルの更新で消える）。ユーザーのプロジェクトか、専用のディレクトリに置く
- git で管理するなら、`init` が作る `.gitignore` のとおり `raw/` と `state/` は除外する（大きい・再生成できない一時データのため、別途バックアップする）。
  `dataset.yaml`・`schema/`・`data/`・`logs/`・`extractors/` は管理してよい
- 公開リポジトリに入れる場合は、収集したデータの再配布が規約上許されるか確認する

## 確認

```bash
uv run <スキルのディレクトリ>/scripts/crawler.py probe https://example.com/
```

`HTTP 200 ...` の行と、ページの Markdown が表示されれば準備完了。
