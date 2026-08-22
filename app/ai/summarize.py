"""四段式結構化摘要：適合誰 / 門檻 / 截止 / 下一步。"""
from typing import Optional

from . import codex_client

_KEYS = ["who", "threshold", "deadline", "next_step"]

_PROMPT = """你在幫使用者快速判斷一筆政府{kind}是否值得投。
請閱讀以下資料，用繁體中文產生四段重點，每段 1~3 句、具體可行：

- who：適合誰投／申請（產業別、規模、資格）
- threshold：門檻與限制（資格條件、押標金、預算規模、地域）
- deadline：關鍵時程（截止日、開標/審查時間）
- next_step：下一步該做什麼（去哪領標/申請、要準備什麼文件）

只輸出 JSON，格式：
{{"who":"...","threshold":"...","deadline":"...","next_step":"..."}}
不要任何其他文字。資料中若沒提到的就寫「資料未載明」。

待摘要資料：
{body}
"""


def build_prompt(body: str, kind: str = "標案") -> str:
    """Build the summarize prompt without invoking Codex."""
    return _PROMPT.format(kind=kind, body=(body or "")[:6000])


def normalize_result(result: Optional[dict]) -> Optional[dict]:
    """Normalize Codex JSON into the expected summary shape."""
    if not result:
        return None
    return {k: str(result.get(k, "")).strip() or "資料未載明" for k in _KEYS}


def summarize(body: str, kind: str = "標案") -> Optional[dict]:
    """回傳 {who, threshold, deadline, next_step}；失敗回 None。"""
    result = codex_client.run_json(build_prompt(body, kind=kind))
    return normalize_result(result)


def tender_body(t: dict) -> str:
    """把標案 row 整理成給 AI 的文字。"""
    lines = [
        f"標題：{t.get('title','')}",
        f"機關：{t.get('agency','')}",
        f"公告類型：{t.get('type','')}",
        f"預算：{t.get('budget','') or '未載明'}",
        f"公告日：{t.get('publish_date','') or '未載明'}",
        f"截止投標：{t.get('deadline','') or '未載明'} {t.get('deadline_time','') or ''}",
        f"連結：{t.get('url','')}",
    ]
    return "\n".join(lines)


def grant_body(g: dict) -> str:
    lines = [
        f"標題：{g.get('title','')}",
        f"主辦機關：{g.get('agency','')}",
        f"適用對象：{g.get('target','') or '未載明'}",
        f"申請期間：{g.get('apply_start','') or '？'} ~ {g.get('apply_end','') or '？'}",
        f"連結：{g.get('url','')}",
    ]
    raw = g.get("raw_json") or {}
    if isinstance(raw, dict) and raw.get("description"):
        lines.append(f"內容：{raw['description']}")
    return "\n".join(lines)
