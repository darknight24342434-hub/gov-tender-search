"""抓教育線訓練/研習標案（g0v PCC API，通用爬蟲、無媒體過濾器）。

搜尋詞內建（見 EDU_TERMS），寫入 DB 時掛「教育」標籤。
教育「影片/教材」案由 crawl_media_tenders.py 收，此檔專收「研習/課程/增能」等訓練案。

範例：python scripts/crawl_edu_tenders.py
"""
import _bootstrap  # noqa: F401

from app.crawlers import pcc_g0v

EDU_TERMS = [
    "教師研習", "數位增能", "師資培訓", "教師增能", "數位學習",
    "AI素養", "智慧教育", "資訊融入教學", "生成式AI", "人工智慧教育",
]


def main():
    print(f"開始抓教育標案：{EDU_TERMS}")
    stats = pcc_g0v.ingest(
        queries=EDU_TERMS,
        pages=1,
        with_deadline=True,
        max_detail=80,
        extra_tag="教育",
        progress=print,
    )
    print("─" * 40)
    print(f"完成：新增 {stats['inserted']}、更新 {stats['updated']}、"
          f"掃描 {stats['scanned']}、detail {stats['detail_calls']}、錯誤 {stats['errors']}")


if __name__ == "__main__":
    main()
