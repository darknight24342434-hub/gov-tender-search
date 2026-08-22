"""De-duplicate grants by normalized title and agency.

Rules:
- Normalize fullwidth text to halfwidth with NFKC.
- Remove leading bracketed prefixes, for example "【公告】".
- Remove trailing parenthesized suffixes, for example "(申請至115年7月24日)".
- Collapse whitespace.
- Keep the most complete row: has apply_end, longer raw_json, then has summary.
"""
import argparse
import re
import sqlite3
import unicodedata
from collections import defaultdict

import _bootstrap  # noqa: F401

from app.config import settings


LEADING_BRACKET_RE = re.compile(r"^\s*(?:[【\[\(（〔][^】\]\)）〕]{0,40}[】\]\)）〕]\s*)+")
TRAILING_PAREN_RE = re.compile(r"\s*(?:[\(（][^()（）]{0,80}[\)）]\s*)+$")
SPACE_RE = re.compile(r"\s+")


def normalize_key_part(value: str | None, *, strip_title_noise: bool = False) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.strip()
    if strip_title_noise:
        previous = None
        while previous != text:
            previous = text
            text = LEADING_BRACKET_RE.sub("", text).strip()
            text = TRAILING_PAREN_RE.sub("", text).strip()
    text = SPACE_RE.sub("", text)
    return text.casefold()


def completeness_score(row: sqlite3.Row) -> tuple:
    raw_len = len(row["raw_json"] or "")
    return (
        1 if row["apply_end"] else 0,
        raw_len,
        1 if row["summary"] else 0,
        row["updated_at"] or "",
        row["id"],
    )


def dedup(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    conn.row_factory = sqlite3.Row
    before = conn.execute("SELECT COUNT(*) AS c FROM grants").fetchone()["c"]
    rows = conn.execute(
        """
        SELECT id, agency, title, apply_end, summary, raw_json, updated_at
        FROM grants
        ORDER BY id
        """
    ).fetchall()

    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        title_key = normalize_key_part(row["title"], strip_title_noise=True)
        agency_key = normalize_key_part(row["agency"])
        if not title_key or not agency_key:
            continue
        groups[(title_key, agency_key)].append(row)

    deletions: list[int] = []
    duplicate_groups = 0
    kept: list[dict] = []
    for key, grouped in groups.items():
        if len(grouped) < 2:
            continue
        duplicate_groups += 1
        keep = max(grouped, key=completeness_score)
        delete_ids = [row["id"] for row in grouped if row["id"] != keep["id"]]
        deletions.extend(delete_ids)
        kept.append({
            "key": key,
            "keep_id": keep["id"],
            "delete_ids": delete_ids,
            "title": keep["title"],
            "agency": keep["agency"],
        })

    if deletions and not dry_run:
        conn.executemany("DELETE FROM grants WHERE id=?", [(row_id,) for row_id in deletions])
        conn.commit()

    after = conn.execute("SELECT COUNT(*) AS c FROM grants").fetchone()["c"] if not dry_run else before
    return {
        "before": before,
        "after": after,
        "deleted": len(deletions),
        "duplicate_groups": duplicate_groups,
        "dry_run": dry_run,
        "kept": kept,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report duplicates without deleting rows.")
    args = parser.parse_args()

    with sqlite3.connect(settings.DB_PATH) as conn:
        result = dedup(conn, dry_run=args.dry_run)

    print(
        f"grants before={result['before']} after={result['after']} "
        f"deleted={result['deleted']} duplicate_groups={result['duplicate_groups']} "
        f"dry_run={result['dry_run']}"
    )
    for item in result["kept"]:
        deleted = ",".join(str(row_id) for row_id in item["delete_ids"])
        print(f"keep id={item['keep_id']} delete=[{deleted}] agency={item['agency']} title={item['title']}")


if __name__ == "__main__":
    main()
