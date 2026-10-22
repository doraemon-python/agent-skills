#!/usr/bin/env python3
"""skills/ からスキルを削除する。

使い方: python3 scripts/remove_skill.py <skill-name> [-y]

削除するファイルを一覧表示し、確認してから skills/<skill-name>/ を削除する。
README.md の「収録スキル」に残っている行は自動では消さないので、表示された行を手で削除する。
"""
import argparse
import shutil
import sys

from skilllib import ROOT, SKILLS_DIR, skill_dirs
from validate_skills import NAME_RE

README = ROOT / "README.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="skills/ からスキルを削除する。")
    parser.add_argument("name", help="削除するスキル名")
    parser.add_argument("-y", "--yes", action="store_true", help="確認せずに削除する")
    args = parser.parse_args()

    name = args.name
    target = SKILLS_DIR / name
    # NAME_RE で確認してから使うので、../ などで skills/ の外を指すことはない
    if not NAME_RE.match(name) or not target.is_dir():
        print(f"スキルが見つかりません: skills/{name}", file=sys.stderr)
        existing = [d.name for d in skill_dirs()]
        if existing:
            print(f"存在するスキル: {', '.join(existing)}", file=sys.stderr)
        return 1

    files = sorted(p.relative_to(ROOT) for p in target.rglob("*") if p.is_file())
    print(f"次のスキルを削除します: skills/{name}/")
    for f in files:
        print(f"  {f}")

    if not args.yes:
        try:
            answer = input("削除しますか？ [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("中止しました")
            return 1

    shutil.rmtree(target)
    print(f"削除しました: skills/{name}/")

    if README.is_file():
        leftovers = [
            (i, line) for i, line in enumerate(README.read_text(encoding="utf-8").splitlines(), 1)
            if f"skills/{name}/" in line
        ]
        if leftovers:
            print("README.md にまだこのスキルへの記述があります。削除してください:")
            for i, line in leftovers:
                print(f"  README.md:{i}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
