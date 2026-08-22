"""集中讀取環境變數設定（.env）。"""
import os
import secrets
import shlex
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- 密碼閘 ---
    APP_PASSWORD = os.getenv("APP_PASSWORD", "")
    # 沒設 SECRET_KEY 時不能退回固定字串——這個專案是公開的，固定字串等於任何人
    # 都能偽造 session cookie。開了密碼閘就必須自己給 SECRET_KEY；完全不開閘
    # （APP_PASSWORD 空白）時給一把隨機的，重啟就失效，本機開發用剛好。
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    if not SECRET_KEY:
        if APP_PASSWORD:
            raise RuntimeError(
                "SECRET_KEY is not set. The password gate is enabled (APP_PASSWORD is set), "
                "so a stable, secret session key is required. Generate one with "
                "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"` "
                "and put it in .env."
            )
        SECRET_KEY = secrets.token_urlsafe(48)
    SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(7 * 24 * 3600)))
    COOKIE_NAME = os.getenv("COOKIE_NAME", "gtsess")
    COOKIE_SECURE = _bool("COOKIE_SECURE")

    # --- 資料庫 ---
    _db = os.getenv("DB_PATH", str(BASE_DIR / "data" / "tenders.db"))
    DB_PATH = _db if os.path.isabs(_db) else str(BASE_DIR / _db)

    # --- Codex CLI ---
    CODEX_BIN = os.getenv("CODEX_BIN", "codex")
    CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.5")
    CODEX_EXTRA_ARGS = shlex.split(
        os.getenv("CODEX_EXTRA_ARGS", "--ignore-user-config --skip-git-repo-check")
    )
    CODEX_TIMEOUT = int(os.getenv("CODEX_TIMEOUT", "180"))
    CODEX_DISABLED = _bool("CODEX_DISABLED")

    # --- 爬蟲 ---
    PCC_API_BASE = os.getenv("PCC_API_BASE", "https://pcc-api.openfun.app").rstrip("/")
    PCC_WEB_BASE = os.getenv("PCC_WEB_BASE", "https://pcc.g0v.ronny.tw").rstrip("/")
    HTTP_USER_AGENT = os.getenv(
        "HTTP_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    )
    CRAWL_DELAY = float(os.getenv("CRAWL_DELAY", "0.8"))


settings = Settings()
