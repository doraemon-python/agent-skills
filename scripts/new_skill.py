#!/usr/bin/env python3
"""template/ から新しいスキルを作る。

使い方: python3 scripts/new_skill.py <skill-name> [-r] [-s] [-a]

デフォルトでは SKILL.md だけを作る。必要なフォルダはオプションで追加する。
  -r, --references  references/ を作る（必要なときだけ読ませる詳細資料）
  -s, --scripts     scripts/ を作る（スキルから実行するスクリプト）
  -a, --assets      assets/ を作る（テンプレートや画像などの素材）
"""
import argparse
import re
import shutil
import sys

from skilllib import ROOT, SKILLS_DIR
from validate_skills import NAME_RE, RESERVED_WORDS

TEMPLATE_DIR = ROOT / "template"
OPTIONAL_DIRS = {
    "references": "必要なときだけ読ませる詳細資料",
    "scripts": "スキルから実行するスクリプト",
    "assets": "テンプレートや画像などの素材",
}


def render(template: str, name: str, enabled: set[str]) -> str:
    title = " ".join(w.capitalize() for w in name.split("-"))
    text = template.replace("SKILL_NAME", name).replace("SKILL_TITLE", title)
    # {{#dir}} ... {{/dir}} の区間は、そのフォルダを作るときだけ残す
    for d in OPTIONAL_DIRS:
        block = re.compile(r"\{\{#" + d + r"\}\}\n(.*?)\{\{/" + d + r"\}\}\n?", re.DOTALL)
        text = block.sub(lambda m: m.group(1) if d in enabled else "", text)
    return text.rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="template/ から新しいスキルを作る。デフォルトでは SKILL.md だけを作る。"
    )
    parser.add_argument("name", help="スキル名（小文字英数字とハイフン）")
    for d, help_text in OPTIONAL_DIRS.items():
        parser.add_argument(f"-{d[0]}", f"--{d}", action="store_true", help=f"{d}/ を作る（{help_text}）")
    args = parser.parse_args()

    name = args.name
    if not NAME_RE.match(name) or len(name) > 64:
        print(f"スキル名 '{name}' は小文字英数字とハイフンのみ・64 文字以内にしてください", file=sys.stderr)
        return 1
    if any(word in name for word in RESERVED_WORDS):
        print(f"スキル名に {' / '.join(RESERVED_WORDS)} は使えません", file=sys.stderr)
        return 1

    dest = SKILLS_DIR / name
    if dest.exists():
        print(f"すでに存在します: {dest.relative_to(ROOT)}", file=sys.stderr)
        return 1

    enabled = {d for d in OPTIONAL_DIRS if getattr(args, d)}

    # ひな形は SKILL.md.tmpl という名前にしてある（npx skills の再帰検索で template/ がスキル扱いされないように）
    dest.mkdir(parents=True)
    template = (TEMPLATE_DIR / "SKILL.md.tmpl").read_text(encoding="utf-8")
    (dest / "SKILL.md").write_text(render(template, name, enabled), encoding="utf-8")
    for d in sorted(enabled):
        if (TEMPLATE_DIR / d).is_dir():
            shutil.copytree(TEMPLATE_DIR / d, dest / d)
        else:
            (dest / d).mkdir()

    print(f"作成しました: skills/{name}/SKILL.md")
    for d in sorted(enabled):
        print(f"作成しました: skills/{name}/{d}/")
    print("次: SKILL.md の TODO を埋めてください")
    return 0


if __name__ == "__main__":
    sys.exit(main())
