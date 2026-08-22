"""抓取 g0v PCC 決標行情。"""
import argparse

import _bootstrap  # noqa: F401

from app.crawlers.pcc_awards import crawl_awards


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", action="append", default=[], help="搜尋關鍵字，可重複")
    parser.add_argument("--queries", default="", help="逗號分隔的搜尋關鍵字")
    parser.add_argument("--pages", type=int, default=1, help="每個關鍵字抓取頁數")
    parser.add_argument("--max-detail", type=int, default=200, help="detail 呼叫上限")
    args = parser.parse_args()

    queries = list(args.query)
    if args.queries:
        queries.extend(q.strip() for q in args.queries.split(",") if q.strip())
    if not queries:
        parser.error("請提供 --query 或 --queries")

    print(f"開始抓取決標行情：queries={queries}, pages={args.pages}, max_detail={args.max_detail}")
    stats = crawl_awards(
        queries=queries,
        pages=args.pages,
        max_detail=args.max_detail,
        progress=print,
    )
    print("-" * 40)
    print(
        "完成："
        f"新增 {stats['inserted']}、更新 {stats['updated']}、"
        f"掃描 {stats['scanned']}、決標 {stats['awards']}、"
        f"detail {stats['detail_calls']}、detail_error {stats['detail_errors']}、"
        f"error {stats['errors']}"
    )


if __name__ == "__main__":
    main()
