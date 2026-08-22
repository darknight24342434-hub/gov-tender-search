"""智慧標籤分類：把標案/補助案歸到固定詞彙的 1~5 個標籤。"""
from typing import List

from . import codex_client
from ..db import DEFAULT_TAGS

_VOCAB = set(DEFAULT_TAGS)
_PROMPT = """你是政府標案/補助案的分類器。請從下列固定標籤中，挑出最貼切的 1 到 5 個：
{vocab}

規則：
- 只能使用上面清單裡的標籤，不可自創。
- 依「標的內容」分類，不要被機關名稱誤導。
- 只輸出 JSON，格式為 {{"tags": ["標籤1","標籤2"]}}，不要任何其他文字。

待分類資料：
標題：{title}
機關：{agency}
摘要/內容：{body}
"""


def build_prompt(title: str, agency: str = "", body: str = "") -> str:
    """Build the tagging prompt without invoking Codex."""
    return _PROMPT.format(
        vocab="、".join(DEFAULT_TAGS),
        title=title or "",
        agency=agency or "",
        body=(body or "")[:1500],
    )


def normalize_result(result: dict | None) -> List[str]:
    """Normalize Codex JSON into a controlled tag list."""
    if not result:
        return []
    raw = result.get("tags") or []
    if not isinstance(raw, list):
        return []
    # 過濾到受控詞彙、去重、最多 5 個
    seen, out = set(), []
    for t in raw:
        t = str(t).strip()
        if t in _VOCAB and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 5:
            break
    return out


def classify(title: str, agency: str = "", body: str = "") -> List[str]:
    """回傳標籤清單；AI 不可用或失敗時回傳 []。"""
    result = codex_client.run_json(build_prompt(title, agency, body))
    return normalize_result(result)
