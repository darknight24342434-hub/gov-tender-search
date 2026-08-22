"""呼叫本機 `codex exec` 的薄包裝（ChatGPT 訂閱額度，不需 OpenAI API key）。

依模型圓桌會議的慣例：codex 須帶 --ignore-user-config 並用 -c model=... 指定模型，
以避開 service_tier bug 與 agentic 漂移（見 .env 的 CODEX_EXTRA_ARGS / CODEX_MODEL）。

設計重點：
- 失敗一律不丟例外給呼叫端，回傳 None，讓爬蟲/批次不會中斷。
- 輸出可能夾雜其他文字，extract_json 會盡量抓出 JSON 物件。
"""
import json
import re
import subprocess
from typing import Optional

from ..config import settings


class CodexError(Exception):
    pass


def _build_cmd(prompt: str) -> list:
    cmd = [settings.CODEX_BIN, "exec", *settings.CODEX_EXTRA_ARGS,
           "-c", f"model={settings.CODEX_MODEL}", prompt]
    return cmd


def run(prompt: str) -> Optional[str]:
    """執行 codex exec，回傳 stdout 文字；失敗回 None。"""
    if settings.CODEX_DISABLED:
        return None
    try:
        proc = subprocess.run(
            _build_cmd(prompt),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.CODEX_TIMEOUT,
        )
    except FileNotFoundError:
        # 找不到 codex 執行檔
        return None
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def extract_json(text: Optional[str]) -> Optional[dict]:
    """從 codex 輸出中抓出第一個合法 JSON 物件。"""
    if not text:
        return None
    # 先試整段
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    # 去掉 ```json ... ``` 圍欄
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except (ValueError, TypeError):
            pass
    # 掃描第一個 { 到最後一個 } 之間，逐步收斂
    start = text.find("{")
    end = text.rfind("}")
    while start != -1 and end > start:
        chunk = text[start:end + 1]
        try:
            return json.loads(chunk)
        except (ValueError, TypeError):
            end = text.rfind("}", start, end)
    return None


def run_json(prompt: str) -> Optional[dict]:
    """執行並解析 JSON；任何環節失敗回 None。"""
    return extract_json(run(prompt))


def available() -> bool:
    """快速檢查 codex 是否可呼叫（給健康檢查 / 啟動提示用）。"""
    if settings.CODEX_DISABLED:
        return False
    try:
        proc = subprocess.run(
            [settings.CODEX_BIN, "--version"],
            capture_output=True, text=True, timeout=15,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
