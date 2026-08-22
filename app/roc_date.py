"""民國年 / 西元年日期轉換工具。

g0v 搜尋結果的 `date` 是西元 YYYYMMDD 整數（例：20260617）；
但 detail 端點的「截止投標」「公告日」是民國年字串（例："115/05/22 17:00"）。
全系統一律把日期正規化成 ISO `YYYY-MM-DD` 存放，方便 `deadline >= date('now')` 過濾。
"""
from datetime import date
from typing import Optional, Tuple


def roc_to_iso(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """'115/05/22 17:00' -> ('2026-05-22', '17:00')；只給日期 -> ('2026-05-22', None)。

    無法解析時回傳 (None, None)。
    """
    if not value:
        return None, None
    s = str(value).strip()
    if not s:
        return None, None
    parts = s.split()
    date_part = parts[0].replace("-", "/")
    time_part = parts[1] if len(parts) > 1 else None
    try:
        y, m, d = (int(x) for x in date_part.split("/"))
        # 民國年通常 < 1000；若已是西元年就不再加 1911
        gy = y + 1911 if y < 1000 else y
        date(gy, m, d)  # 驗證合法
        return f"{gy:04d}-{m:02d}-{d:02d}", time_part
    except (ValueError, TypeError):
        return None, None


def yyyymmdd_to_iso(value) -> Optional[str]:
    """20260617 或 '20260617' -> '2026-06-17'。"""
    if value is None:
        return None
    s = str(value).strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def normalize_iso(value: Optional[str]) -> Optional[str]:
    """盡量把使用者/seed 給的日期轉成 ISO；支援 ISO、民國年、YYYYMMDD。"""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # 已是 ISO
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
            return s
        except ValueError:
            return None
    if s.isdigit() and len(s) == 8:
        return yyyymmdd_to_iso(s)
    iso, _ = roc_to_iso(s)
    return iso
