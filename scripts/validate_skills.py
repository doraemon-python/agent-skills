#!/usr/bin/env python3
"""skills/ 以下の全スキルを検証する。

使い方: python3 scripts/validate_skills.py
エラーがあれば終了コード 1。警告だけなら 0。
"""
import re
import sys

from skilllib import ROOT, FrontmatterError, skill_dirs, split_frontmatter

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED_WORDS = ("anthropic", "claude")
# agentskills.io の仕様 + Claude Code が解釈するフィールド
KNOWN_FIELDS = {
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
    "argument-hint", "disable-model-invocation", "user-invocable", "model", "context", "agent", "hooks",
}
MAX_BODY_LINES = 500
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "API キーらしき文字列"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub トークンらしき文字列"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS アクセスキーらしき文字列"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "秘密鍵"),
]


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str):
        self.warnings.append(f"{where}: {msg}")


def validate_skill(skill_dir, report: Report) -> None:
    where = str(skill_dir.relative_to(ROOT))
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        report.error(where, "SKILL.md がありません")
        return

    try:
        meta, body = split_frontmatter(skill_md.read_text(encoding="utf-8"))
    except FrontmatterError as e:
        report.error(f"{where}/SKILL.md", str(e))
        return

    name = meta.get("name")
    if not isinstance(name, str) or not name:
        report.error(where, "name がありません")
    else:
        if not NAME_RE.match(name):
            report.error(where, f"name '{name}' は小文字英数字とハイフンのみ使えます（先頭・末尾・連続ハイフン不可）")
        if len(name) > 64:
            report.error(where, f"name が 64 文字を超えています（{len(name)} 文字）")
        if name != skill_dir.name:
            report.error(where, f"name '{name}' がフォルダ名 '{skill_dir.name}' と一致しません")
        for word in RESERVED_WORDS:
            if word in name:
                report.error(where, f"name に予約語 '{word}' は使えません")

    desc = meta.get("description")
    if not isinstance(desc, str) or not desc.strip():
        report.error(where, "description がありません")
    else:
        if len(desc) > 1024:
            report.error(where, f"description が 1024 文字を超えています（{len(desc)} 文字）")
        if re.search(r"<[^>]+>", desc):
            report.error(where, "description に XML タグは使えません")
        if "TODO" in desc:
            report.error(where, "description に TODO が残っています")

    compat = meta.get("compatibility")
    if compat is not None and len(str(compat)) > 500:
        report.error(where, "compatibility が 500 文字を超えています")

    if "metadata" in meta and not isinstance(meta["metadata"], dict):
        report.error(where, "metadata は key: value の map にしてください")

    for key in meta:
        if key not in KNOWN_FIELDS:
            report.warn(where, f"未知の frontmatter フィールド '{key}'（環境によっては無視されます）")

    body_lines = body.count("\n") + 1
    if body_lines > MAX_BODY_LINES:
        report.warn(where, f"SKILL.md 本文が {body_lines} 行あります。{MAX_BODY_LINES} 行以内を推奨（references/ に分割）")
    if re.search(r"(?m)^\s*(?:[-*]|\d+\.)?\s*TODO\b", body):
        report.warn(where, "SKILL.md 本文に TODO が残っています")

    # 本文からの相対リンク切れ
    for target in re.findall(r"\]\(([^)#\s]+)\)", body):
        if "://" in target or target.startswith("mailto:"):
            continue
        if not (skill_dir / target).exists():
            report.error(where, f"リンク先が存在しません: {target}")

    # 秘密情報らしき文字列
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                report.error(str(path.relative_to(ROOT)), f"{label}が含まれています")


def main() -> int:
    report = Report()
    dirs = skill_dirs()
    if not dirs:
        report.warn("skills", "スキルが 1 つもありません")
    for d in dirs:
        validate_skill(d, report)

    for w in report.warnings:
        print(f"⚠️  {w}")
    for e in report.errors:
        print(f"❌ {e}")
    if report.errors:
        print(f"\n{len(report.errors)} 件のエラー、{len(report.warnings)} 件の警告")
        return 1
    print(f"✅ {len(dirs)} 件のスキルを検証しました（警告 {len(report.warnings)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
