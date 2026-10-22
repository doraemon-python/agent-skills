# このリポジトリについて

アプリ開発用の自作 Agent Skills を管理・公開するリポジトリ。`skills/` 以下の各フォルダが 1 つのスキル。

## スキルを追加・編集するときのルール

- 新規スキルは `python3 scripts/new_skill.py <name>` で作る。`references/` `scripts/` `assets/` は必要なときだけ `-r` `-s` `-a` で付ける。`skills/<name>/SKILL.md` の形に置けば `npx skills` が自動で検出する（配布は `npx skills` のみ）
- スキルの削除は `python3 scripts/remove_skill.py <name>` で行い、README.md の「収録スキル」の行も消す
- `name` は小文字英数字とハイフンのみ・64 文字以内で、フォルダ名と一致させる。`claude` / `anthropic` は含めない
- `description` には「何をするか」と「いつ使うか（ユーザーがどう言ったら使うか）」を三人称で書く。1024 文字以内
- `SKILL.md` 本文は 500 行以内に収め、長い資料は `references/` に分けて本文からリンクする
- 決まった処理はスクリプトにして `scripts/` に置き、本文から実行方法を指示する
- APIキー・社内 URL・個人情報など、公開できない情報は書かない（このリポジトリは公開前提）
- 書き方の詳細は `docs/authoring-guide.md` を参照

## 変更後に必ず実行する

```bash
python3 scripts/validate_skills.py
```

スキルを追加・削除したら README.md の「収録スキル」の表も更新する。
