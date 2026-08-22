"""一次性回填 grants.tags 的 AI / 影片 / 文化標籤。

只比對 title、summary 與 raw_json.description，不比對 URL 或整段 raw_json。
"""
import json
from collections import defaultdict

import _bootstrap  # noqa: F401

from app import db
from app.crawlers.grants import (
    append_ai_tag_if_matched,
    append_culture_tag_if_matched,
    append_video_tag_if_matched,
)


CATEGORY_APPENDERS = (
    ("AI", append_ai_tag_if_matched),
    ("影片", append_video_tag_if_matched),
    ("文化", append_culture_tag_if_matched),
)


def _json_loads(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _summary_text(value) -> str:
    data = _json_loads(value, "")
    if isinstance(data, dict):
        return " ".join(str(v) for v in data.values() if v is not None)
    if isinstance(data, list):
        return " ".join(str(v) for v in data if v is not None)
    return str(data or "")


def _description_text(value) -> str:
    data = _json_loads(value, {})
    if isinstance(data, dict):
        return str(data.get("description") or "")
    return ""


def main() -> int:
    added = defaultdict(list)
    changed = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, tags, summary, raw_json FROM grants ORDER BY id"
        ).fetchall()
        for row in rows:
            tags = _json_loads(row["tags"], [])
            if not isinstance(tags, list):
                tags = []
            summary = _summary_text(row["summary"])
            description = _description_text(row["raw_json"])
            next_tags = tags
            for tag, appender in CATEGORY_APPENDERS:
                had_tag = tag in next_tags
                next_tags = appender(
                    next_tags,
                    title=row["title"] or "",
                    summary=summary,
                    description=description,
                )
                if not had_tag and tag in next_tags:
                    added[tag].append(row["title"] or f"id={row['id']}")
            if next_tags != tags:
                db.set_tags(conn, "grants", row["id"], next_tags)
                changed.append(row["title"] or f"id={row['id']}")

        totals = {}
        for tag, _ in CATEGORY_APPENDERS:
            total = 0
            for row in conn.execute("SELECT tags FROM grants").fetchall():
                tags = _json_loads(row["tags"], [])
                if isinstance(tags, list) and tag in tags:
                    total += 1
            totals[tag] = total

    for tag, _ in CATEGORY_APPENDERS:
        print(f"{tag} tag newly added grants: {len(added[tag])}")
        print(f"{tag} tag total grants: {totals[tag]}")
        for title in added[tag]:
            print(f"- {title}")
    print(f"Changed grants: {len(changed)}")
    return len(changed)


if __name__ == "__main__":
    main()
