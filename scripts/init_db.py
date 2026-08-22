"""建立資料表與種子標籤。用法：python scripts/init_db.py"""
import _bootstrap  # noqa: F401

from app import db
from app.config import settings


def main():
    db.init_db()
    print(f"✓ 資料庫已就緒：{settings.DB_PATH}")
    with db.get_conn() as conn:
        tags = db.list_tags(conn)
    print(f"✓ 種子標籤 {len(tags)} 個：{'、'.join(tags)}")


if __name__ == "__main__":
    main()
