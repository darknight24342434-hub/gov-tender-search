"""批次用 Codex 產生標籤與摘要（只跑缺漏的，省額度）。

排程建議：每日爬完後跑一次，把新標案/補助案的標籤與摘要補齊存進 DB，
這樣使用者搜尋與看詳情頁時都不必即時呼叫 AI。

範例：
  python scripts/enrich_ai.py --kind tenders --do both --limit 50
  python scripts/enrich_ai.py --kind grants  --do tags --limit 100
"""
import argparse
import time

import _bootstrap  # noqa: F401

from app import db, search
from app.ai import codex_client, summarize, tagging
from app.config import settings


def _bodies(kind, row):
    if kind == "tenders":
        title, agency, type_ = row.get("title", ""), row.get("agency", ""), row.get("type", "")
        return (title, agency, type_), summarize.tender_body(row), "標案"
    return (row.get("title", ""), row.get("agency", ""), row.get("target", "")), \
        summarize.grant_body(row), "補助案"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["tenders", "grants"], required=True)
    ap.add_argument("--do", choices=["tags", "summary", "both"], default="both")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    if settings.CODEX_DISABLED:
        print("CODEX_DISABLED=true，已停用 AI，無事可做。")
        return
    if not codex_client.available():
        print("⚠ 偵測不到可用的 codex 執行檔（CODEX_BIN）。請確認本機已安裝並登入 Codex CLI。")
        return

    need_tags = args.do in ("tags", "both")
    need_sum = args.do in ("summary", "both")
    conds = []
    if need_tags:
        conds.append("(tags IS NULL OR tags = '' OR tags = '[]')")
    if need_sum:
        conds.append("(summary IS NULL OR summary = '')")
    where = " OR ".join(conds)

    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM {args.kind} WHERE {where} ORDER BY id DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
    ids = [r["id"] for r in rows]
    print(f"待處理 {len(ids)} 筆 {args.kind}（do={args.do}）")

    done = 0
    for rid in ids:
        with db.get_conn() as conn:
            row = search.get_one(conn, args.kind, rid)
        if not row:
            continue
        (title, agency, extra), body, kind_label = _bodies(args.kind, row)
        if need_tags and not row.get("tags"):
            tags = tagging.classify(title, agency, body)
            if tags:
                with db.get_conn() as conn:
                    db.set_tags(conn, args.kind, rid, tags)
                print(f"  #{rid} 標籤：{tags}")
        if need_sum and not row.get("summary"):
            result = summarize.summarize(body, kind=kind_label)
            if result:
                with db.get_conn() as conn:
                    db.set_summary(conn, args.kind, rid, result)
                print(f"  #{rid} 摘要完成")
        done += 1
        time.sleep(0.3)
    print(f"✓ 完成 {done} 筆")


if __name__ == "__main__":
    main()
