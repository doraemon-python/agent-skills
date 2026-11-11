"""判断ログ（decisions.jsonl）の確認と、人による修正（corrections.jsonl）。"""
from .config import FILE_TYPES, Dataset
from .store import StoreError, TableFile, coerce, load_corrections
from .util import append_jsonl, now_iso, read_jsonl


def list_decisions(ds: Dataset, *, needs_review: bool, table: str | None, field: str | None,
                   record_id: str | None, limit: int) -> list[dict]:
    corrected = {c.get("decision_id") for c in read_jsonl(ds.logs_dir / "corrections.jsonl")}
    reviewed = {r["decision_id"] for r in read_jsonl(ds.logs_dir / "reviews.jsonl")}
    out = []
    for d in reversed(read_jsonl(ds.logs_dir / "decisions.jsonl")):
        if needs_review and (not d.get("needs_review") or d["id"] in corrected or d["id"] in reviewed):
            continue
        if table and d["table"] != table:
            continue
        if field and d["field"] != field:
            continue
        if record_id and d["record_id"] != record_id:
            continue
        d = dict(d, corrected=d["id"] in corrected, reviewed=d["id"] in reviewed)
        out.append(d)
        if len(out) >= limit:
            break
    return out


def find_decision(ds: Dataset, decision_id: str) -> dict:
    for d in read_jsonl(ds.logs_dir / "decisions.jsonl"):
        if d["id"] == decision_id:
            return d
    raise StoreError(f"判断ログに {decision_id} がありません")


def approve(ds: Dataset, decision_ids: list[str], note: str | None) -> None:
    """確認した結果、抽出時の判断で正しかった。レビュー待ちから外す。"""
    for did in decision_ids:
        find_decision(ds, did)
        append_jsonl(ds.logs_dir / "reviews.jsonl", {"ts": now_iso(), "decision_id": did, "note": note})


def fix(ds: Dataset, *, table: str, record_id: str, field: str, value, note: str,
        decision_id: str | None = None, unpin: bool = False) -> tuple[str, str]:
    """CSV の 1 セルを直し、corrections.jsonl に記録する。以後の再抽出でもこの値が優先される。"""
    t = ds.table(table)
    col = t.column(field)
    if col is None:
        raise StoreError(f"{table} に列 {field} はありません")
    if col.type in FILE_TYPES:
        raise StoreError("file / image 列は fix で直せません。抽出結果を作り直して store してください")
    if field in t.primary_key:
        raise StoreError("primary_key の列は fix で直せません（_id が変わるため）。抽出結果を作り直してください")
    tf = TableFile(ds, t)
    entry = {"ts": now_iso(), "decision_id": decision_id, "table": table, "record_id": record_id,
             "field": field, "note": note}
    if unpin:
        if (table, record_id, field) not in load_corrections(ds):
            raise StoreError("その値は固定されていません")
        append_jsonl(ds.logs_dir / "corrections.jsonl", {**entry, "unpin": True})
        return "", ""
    new = coerce(col, value)
    old = tf.set_cell(record_id, field, new)
    tf.save()
    append_jsonl(ds.logs_dir / "corrections.jsonl", {**entry, "old": old, "new": new})
    return old, new
