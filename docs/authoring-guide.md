# スキルの書き方ガイド

Agent Skills の仕様は [agentskills.io](https://agentskills.io/specification) と
[Claude のドキュメント](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) を参照してください。
ここでは、このリポジトリで守る書き方をまとめます。

## スキルが読み込まれる仕組み

1. 起動時、エージェントは全スキルの `name` と `description` だけを読む
2. ユーザーの依頼が `description` に合うと判断したら `SKILL.md` 本文を読む
3. 本文から参照された `references/` や `scripts/` は、必要になったときだけ読む・実行する

つまり **`description` の書き方でスキルが使われるかどうかが決まり**、本文は短いほどコンテキストを節約できます。

## frontmatter

```yaml
---
name: release-checklist          # 必須。フォルダ名と同じ。小文字英数字とハイフン、64 文字以内
description: >-                  # 必須。1024 文字以内
  Web / モバイルアプリのリリース前チェックを行う。ユーザーが「リリース前に確認して」
  「ストア申請の準備」「バージョンを上げて」と言ったときに使う。
license: MIT                     # 任意
compatibility: git と python3 が必要   # 任意。動作に必要な環境（500 文字以内）
metadata:                        # 任意。自由なキーと値
  version: "1.0.0"
  author: doraemon-python
---
```

作成中で公開したくないスキルは `metadata` に `internal: true` を書くと、`npx skills add` の一覧に出なくなります
（`INSTALL_INTERNAL_SKILLS=1` を付けたときだけインストール可能）。

Claude Code では `allowed-tools`（許可なしで使えるツール）など追加のフィールドも使えます。
他のエージェントでは無視されるので、使う場合は Claude Code 向けであることを本文にも書いておきます。

### description のコツ

- **何をするか** と **いつ使うか** の両方を書く
- ユーザーが実際に口にしそうな言葉（「PR 書いて」「リリース準備」など）を入れる
- 三人称・言い切りで書く（「〜します」より「〜する」）
- 似たスキルと区別できるように、対象外のケースを書くのも有効（「コミットメッセージには使わない」など）

## 本文

- 500 行以内。超えそうなら `references/` に分割し、本文から「〇〇のときは `references/xxx.md` を読む」と書く
- エージェントがすでに知っている一般知識は書かない。**このプロジェクト・自分の流儀ならではの手順**を書く
- 手順は番号付きリストで、判断が必要な箇所は条件を明示する
- 出力の形式（テンプレート）が決まっているなら例を載せる
- 参照ファイルは本文から 1 階層で辿れるようにする（参照先からさらに参照させない）

## scripts/

- 毎回同じになる処理・正確さが必要な処理はスクリプトにする（エージェントに毎回コードを書かせない）
- 外部ライブラリへの依存は最小限に。必要なら `compatibility` に書く
- 本文には「何のために・どう実行するか」を書く: `python3 scripts/detect_versions.py <project-dir>`
- 失敗時は原因がわかるメッセージを標準エラーに出す

## 公開前チェック

- [ ] API キー、トークン、社内 URL、個人情報が含まれていない
- [ ] `python3 scripts/validate_skills.py` が通る
- [ ] `npx skills add . --skill <name> -a claude-code -g -y` で入れ、実際に依頼してみて、意図どおりにスキルが使われた
- [ ] README.md の「収録スキル」に追加した
