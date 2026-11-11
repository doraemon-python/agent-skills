"""時刻・ハッシュ・ファイル書き込みなどの小さな共通処理。"""
import fnmatch
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def now_iso() -> str:
    """UTC の ISO 8601（秒まで）。文字列のまま大小比較できる形にそろえる。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_after(seconds: float) -> str:
    ts = datetime.now(timezone.utc).timestamp() + seconds
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_id(key_values: list) -> str:
    """主キーの値から、ファイル名にも使える安定した _id を作る。"""
    joined = "\x1f".join("" if v is None else str(v) for v in key_values)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def normalize_url(url: str, strip_params: list[str]) -> str:
    """フラグメントを落とし、追跡用パラメータ（utm_* など）を取り除く。"""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not any(fnmatch.fnmatch(k, pat) for pat in strip_params)
    ]
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """一時ファイルに書いてから置き換える。途中で落ちても壊れたファイルを残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
