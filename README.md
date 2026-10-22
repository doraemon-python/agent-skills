# Agent Skills for App Development

アプリ開発のために作成した自作 [Agent Skills](https://agentskills.io) を管理・公開するリポジトリです。
[skills CLI](https://github.com/vercel-labs/skills)（`npx skills`）でインストールできます。

## インストール

Node.js 22.20 以上が必要です。Claude Code・Codex・Cursor など、skills CLI が対応するエージェントに入れられます。

```bash
# 収録スキルの一覧を見る
npx skills add doraemon-python/agent-skills --list

# 選んでインストール（対話形式でスキルとエージェントを選ぶ）
npx skills add doraemon-python/agent-skills

# スキルを指定して Claude Code の全プロジェクト共通（~/.claude/skills）に入れる
npx skills add doraemon-python/agent-skills --skill <skill-name> -a claude-code -g

# 更新・削除
npx skills update
npx skills remove <skill-name>
```

`-g` を付けないと、カレントのプロジェクト（`.claude/skills/` など）にインストールされます。

## リポジトリ構成

```text
.
├── skills/                           # 公開するスキル（1 フォルダ = 1 スキル）
│   └── <skill-name>/
│       ├── SKILL.md                  # 必須: frontmatter + 手順
│       ├── references/               # 任意: 必要時だけ読ませる詳細資料
│       ├── scripts/                  # 任意: スキルから実行するスクリプト
│       └── assets/                   # 任意: テンプレートや画像などの素材
├── template/                         # 新規スキルのひな形（SKILL.md.tmpl。スキルとして検出されない名前）
├── scripts/
│   ├── new_skill.py                  # ひな形からスキルを作成
│   ├── remove_skill.py               # スキルを削除
│   └── validate_skills.py            # frontmatter・リンク切れ・秘密情報のチェック
├── docs/authoring-guide.md           # スキルの書き方ガイド
└── .github/                          # CI（検証）、Issue / PR テンプレート
```

`npx skills` は `skills/<name>/SKILL.md` を自動で見つけるので、スキルを置いて push すればそのまま公開されます。

## スキルの開発フロー

```bash
# 1. ひな形から作成（デフォルトでは skills/<name>/SKILL.md だけが作られる）
python3 scripts/new_skill.py my-new-skill

#    必要なフォルダはオプションで追加する（組み合わせ可）
#    -r / --references  references/  必要なときだけ読ませる詳細資料
#    -s / --scripts     scripts/     スキルから実行するスクリプト
#    -a / --assets      assets/      テンプレートや画像などの素材
python3 scripts/new_skill.py my-new-skill -r -s

# 2. SKILL.md を編集（書き方は docs/authoring-guide.md）

# 3. 検証
python3 scripts/validate_skills.py

# 4. push 前に、ローカルのリポジトリから Claude Code に入れて試す
#    （インストール時点の内容がコピーされるので、編集したら再実行する）
npx skills add . --skill my-new-skill -a claude-code -g -y
```

スキルを削除するときは次のコマンドを使います（削除するファイルを表示して確認を取ります。`-y` で確認を省略）。

```bash
python3 scripts/remove_skill.py my-new-skill
```

main への push と PR では GitHub Actions が `validate_skills.py` を実行します。

## ライセンス

[MIT](LICENSE)
