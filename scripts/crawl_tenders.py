"""抓取標案（g0v PCC API）。

範例：
  python scripts/crawl_tenders.py --query 資安 --query 軟體 --pages 2
  python scripts/crawl_tenders.py --queries 資安,工程,顧問 --pages 3 --max-detail 150
  python scripts/crawl_tenders.py --query 雲端 --no-deadline   # 只抓清單、不打 detail（快）
"""
import argparse

import _bootstrap  # noqa: F401

from app.crawlers import pcc_g0v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", action="append", default=[], help="關鍵字（可重複）")
    ap.add_argument("--queries", default="", help="逗號分隔的多個關鍵字")
    ap.add_argument("--pages", type=int, default=1, help="每個關鍵字抓幾頁（每頁約 100 筆）")
    ap.add_argument("--no-deadline", action="store_true", help="不打 detail 補截止日（較快）")
    ap.add_argument("--max-detail", type=int, default=120, help="detail 呼叫上限")
    args = ap.parse_args()

    queries = list(args.query)
    if args.queries:
        queries += [q.strip() for q in args.queries.split(",") if q.strip()]
    if not queries:
        ap.error("請至少給一個 --query 或 --queries")

    print(f"開始抓取：{queries}（pages={args.pages}, 補截止日={not args.no_deadline}）")
    stats = pcc_g0v.ingest(
        queries=queries,
        pages=args.pages,
        with_deadline=not args.no_deadline,
        max_detail=args.max_detail,
        progress=print,
    )
    print("─" * 40)
    print(f"完成：新增 {stats['inserted']}、更新 {stats['updated']}、"
          f"掃描 {stats['scanned']}、detail 呼叫 {stats['detail_calls']}、錯誤 {stats['errors']}")


if __name__ == "__main__":
    main()
