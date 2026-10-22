"""scripts/ 内のツールで共有する、スキルの読み込み処理。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


class FrontmatterError(ValueError):
    pass


def split_frontmatter(text: str) -> tuple[dict, str]:
    """SKILL.md を (frontmatter の dict, 本文) に分ける。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise FrontmatterError("先頭に --- で囲まれた YAML frontmatter がありません")
    raw, body = m.groups()
    try:
        import yaml  # PyYAML があれば正式なパーサーを使う（CI ではインストールされる）
    except ImportError:
        data = _parse_simple_yaml(raw)
    else:
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise FrontmatterError(f"YAML の構文エラー: {e}") from e
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter が key: value の形式になっていません")
    return data, body


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _parse_simple_yaml(raw: str) -> dict:
    """PyYAML がない環境用。SKILL.md でよく使う書き方（文字列・ブロックスカラー・1 階層の map）だけ扱う。"""
    data: dict = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            raise FrontmatterError(f"解釈できない行です: {line!r}")
        key, value = m.groups()
        value = re.sub(r"\s+#.*$", "", value) if not value.startswith(("'", '"')) else value

        block = []
        while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
            block.append(lines[i])
            i += 1
        block_text = [b.strip() for b in block if b.strip()]

        if value in (">", ">-", "|", "|-"):
            data[key] = (" " if value.startswith(">") else "\n").join(block_text)
        elif value == "" and block_text:
            sub = {}
            for b in block_text:
                sm = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", b)
                if not sm:
                    raise FrontmatterError(f"解釈できない行です: {b!r}")
                sub[sm.group(1)] = _unquote(sm.group(2))
            data[key] = sub
        else:
            data[key] = _unquote(" ".join([value, *block_text]))
    return data


def skill_dirs() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))

