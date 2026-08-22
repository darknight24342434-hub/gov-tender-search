"""輸出 GB-R1 每日情報 Markdown 與 CSV。"""
import argparse
import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from app import db

INFO_TAG_KEYWORDS = {
    "AI": ["AI", "人工智慧", "生成式", "ChatGPT", "LLM", "大模型", "機器學習", "智慧客服"],
    "系統建置": ["系統", "平台", "網站", "資訊服務", "軟體", "建置", "維護", "資安", "雲端"],
    "影片內容": ["影片", "影音", "拍攝", "剪輯", "影像", "直播", "動畫", "多媒體", "短影音"],
    "教育訓練": ["教育", "訓練", "課程", "研習", "人才", "培訓", "工作坊"],
    "數位轉型": ["數位", "轉型", "智慧化", "資料", "數據", "自動化"],
    "補助": ["補助", "獎補助", "徵件", "申請"],
    "研究案": ["研究", "委託研究", "科研", "實驗", "分析"],
}


def _load_json(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _text_for_match(item):
    chunks = [
        item.get("title") or "",
        item.get("agency") or "",
        item.get("target_or_budget") or "",
        item.get("type") or "",
        " ".join(item.get("db_tags") or []),
    ]
    raw = item.get("raw_json")
    if isinstance(raw, dict) and raw.get("description"):
        chunks.append(str(raw["description"]))
    return "\n".join(chunks)


def _contains_keyword(text, keyword):
    if keyword.isascii():
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None
    return keyword in text


def _info_tags(item):
    text = _text_for_match(item)
    tags = []
    for tag, keywords in INFO_TAG_KEYWORDS.items():
        if any(_contains_keyword(text, keyword) for keyword in keywords):
            tags.append(tag)
    if item["kind"] == "補助" and "補助" not in tags:
        tags.append("補助")
    return tags


def _deadline_key(value):
    if not value:
        return "9999-12-31"
    return value


def _risk(item):
    risks = []
    if not item.get("deadline"):
        risks.append("截止日未載明")
    if not item.get("summary"):
        risks.append("AI摘要尚未補齊")
    if not item.get("db_tags"):
        risks.append("DB標籤尚未補齊")
    return "；".join(risks) if risks else "未見明顯資料缺口"


def _reason(item):
    tags = item.get("info_tags") or []
    if tags:
        return f"命中 {'、'.join(tags)} 情報標籤"
    return "未命中 GB-R1 情報標籤"


def _summary_value(summary, key):
    if isinstance(summary, dict):
        value = str(summary.get(key) or "").strip()
        if value:
            return value
    return "資料未載明"


def _one_line(item):
    title = item.get("title") or "未命名案件"
    deadline = item.get("deadline") or "截止未載明"
    return f"{title}，截止：{deadline}"


def fetch_items():
    today = date.today().isoformat()
    items = []
    with db.get_conn() as conn:
        tenders = conn.execute(
            """
            SELECT id, title, agency, type, budget, deadline, deadline_time, url, tags, summary, raw_json
            FROM tenders
            WHERE deadline IS NULL OR deadline >= ?
            ORDER BY (deadline IS NULL), deadline ASC, publish_date DESC, id DESC
            """,
            (today,),
        ).fetchall()
        grants = conn.execute(
            """
            SELECT id, title, agency, target, apply_end, url, tags, summary, raw_json
            FROM grants
            WHERE apply_end IS NULL OR apply_end >= ?
            ORDER BY (apply_end IS NULL), apply_end ASC, id DESC
            """,
            (today,),
        ).fetchall()

    for row in tenders:
        d = dict(row)
        items.append({
            "kind": "標案",
            "id": d["id"],
            "title": d.get("title") or "",
            "agency": d.get("agency") or "",
            "type": d.get("type") or "",
            "target_or_budget": d.get("budget") or "資料未載明",
            "deadline": " ".join(part for part in [d.get("deadline"), d.get("deadline_time")] if part),
            "url": d.get("url") or "",
            "db_tags": _load_json(d.get("tags")) or [],
            "summary": _load_json(d.get("summary")),
            "raw_json": _load_json(d.get("raw_json")),
        })
    for row in grants:
        d = dict(row)
        items.append({
            "kind": "補助",
            "id": d["id"],
            "title": d.get("title") or "",
            "agency": d.get("agency") or "",
            "type": "",
            "target_or_budget": d.get("target") or "資料未載明",
            "deadline": d.get("apply_end") or "",
            "url": d.get("url") or "",
            "db_tags": _load_json(d.get("tags")) or [],
            "summary": _load_json(d.get("summary")),
            "raw_json": _load_json(d.get("raw_json")),
        })
    for item in items:
        item["info_tags"] = _info_tags(item)
        item["risk"] = _risk(item)
        item["reason"] = _reason(item)
        item["one_line"] = _one_line(item)
        item["who"] = _summary_value(item.get("summary"), "who")
        item["next_step"] = _summary_value(item.get("summary"), "next_step")
    return sorted(
        [item for item in items if item["info_tags"]],
        key=lambda item: (_deadline_key(item.get("deadline")), -len(item["info_tags"]), item["kind"], item["id"]),
    )


def write_csv(path, items):
    fields = [
        "類型", "情報標籤", "標題", "機關", "預算或適用對象", "截止日",
        "原始連結", "一句話機會", "適合誰接", "值得看原因", "風險", "下一步",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow({
                "類型": item["kind"],
                "情報標籤": "、".join(item["info_tags"]),
                "標題": item["title"],
                "機關": item["agency"],
                "預算或適用對象": item["target_or_budget"],
                "截止日": item["deadline"] or "資料未載明",
                "原始連結": item["url"],
                "一句話機會": item["one_line"],
                "適合誰接": item["who"],
                "值得看原因": item["reason"],
                "風險": item["risk"],
                "下一步": item["next_step"],
            })


def write_markdown(path, items, generated_at):
    lines = [
        f"# GB-R1 每日情報 {generated_at[:10]}",
        "",
        "## 查詢條件",
        "- 類型：標案 / 補助案",
        "- 篩選：未截止或截止日未載明，且命中 GB-R1 情報標籤",
        "- 情報標籤：AI、系統建置、影片內容、教育訓練、數位轉型、補助、研究案",
        "",
        "## 結果摘要",
        "| 類型 | 情報標籤 | 標題 | 機關 | 預算/對象 | 截止 | 連結 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| {item['kind']} | {'、'.join(item['info_tags'])} | {item['title']} | "
            f"{item['agency']} | {item['target_or_budget']} | {item['deadline'] or '資料未載明'} | {item['url']} |"
        )

    lines += ["", "## 值得追的項目"]
    if not items:
        lines.append("目前沒有命中 GB-R1 條件的未截止案件。")
    for i, item in enumerate(items[:20], start=1):
        lines.append(
            f"{i}. {item['title']}：{item['reason']}；下一步：{item['next_step']}；風險：{item['risk']}。"
        )

    ai_missing = sum(1 for item in items if not item.get("summary"))
    tag_missing = sum(1 for item in items if not item.get("db_tags"))
    no_deadline = sum(1 for item in items if not item.get("deadline"))
    lines += [
        "",
        "## 資料品質",
        "- 來源：g0v PCC API、新創圓夢網、文化部獎補助、seed JSON（若存在）",
        f"- 命中件數：{len(items)}",
        f"- AI 未補齊：{ai_missing}",
        f"- DB 標籤未補齊：{tag_missing}",
        f"- 截止日未載明：{no_deadline}",
        f"- 產出時間：{generated_at}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60, help="輸出筆數上限")
    ap.add_argument("--out-dir", default="reports", help="輸出資料夾")
    args = ap.parse_args()

    generated_at = datetime.now().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%Y%m%d")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    items = fetch_items()[:args.limit]
    md_path = out_dir / f"GB-R1_{stamp}.md"
    csv_path = out_dir / f"GB-R1_{stamp}.csv"
    write_markdown(md_path, items, generated_at)
    write_csv(csv_path, items)
    print(f"Markdown: {md_path}")
    print(f"CSV: {csv_path}")
    print(f"Items: {len(items)}")


if __name__ == "__main__":
    main()
