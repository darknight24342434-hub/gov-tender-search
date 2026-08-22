"""AI 標書解構卡。"""
import json
from typing import Optional

from . import codex_client

_KEYS = [
    "交付項目",
    "評選方式與配分",
    "廠商資格",
    "押標金",
    "履約期限",
    "預算",
    "開標時間",
    "風險提醒",
]

_PROMPT = """請根據下列政府標案資料，產出固定 8 欄 JSON 標書解構卡。
規則：
- 只能輸出 JSON object，不要 Markdown，不要額外說明。
- JSON key 必須且只能是：交付項目、評選方式與配分、廠商資格、押標金、履約期限、預算、開標時間、風險提醒。
- 每欄用繁體中文，1 到 3 句，投標決策可直接使用。
- 若資料沒有載明，填「資料未載明」。
- 不要臆測不存在的資格、金額、日期或配分。

標案資料：
{body}
"""


def _json_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _body(tender_row: dict) -> str:
    raw = tender_row.get("raw_json") or {}
    return "\n".join(
        [
            f"標案名稱：{tender_row.get('title') or ''}",
            f"機關：{tender_row.get('agency') or ''}",
            f"類型：{tender_row.get('type') or ''}",
            f"預算：{tender_row.get('budget') or ''}",
            f"公告日：{tender_row.get('publish_date') or ''}",
            f"截止投標：{tender_row.get('deadline') or ''} {tender_row.get('deadline_time') or ''}",
            f"原始資料 JSON：{_json_text(raw)}",
        ]
    )


def build_prompt(tender_row: dict) -> str:
    """Build the tender dissect prompt without invoking Codex."""
    return _PROMPT.format(body=_body(tender_row)[:12000])


def normalize_result(result: Optional[dict]) -> Optional[dict]:
    """Normalize Codex JSON into the expected dissect shape."""
    if not result:
        return None
    return {
        key: str(result.get(key) or "資料未載明").strip() or "資料未載明"
        for key in _KEYS
    }


def dissect(tender_row: dict) -> Optional[dict]:
    """回傳固定 8 欄解構 JSON；Codex 失敗時回 None。"""
    result = codex_client.run_json(build_prompt(tender_row))
    return normalize_result(result)
