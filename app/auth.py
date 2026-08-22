"""密碼閘：單一共用密碼 + 簽章 session cookie。

設計刻意最小化：環境變數 APP_PASSWORD 設好密碼，登入頁輸入即進入。
不做帳號系統——外網存取的「留個密碼接口」需求由此滿足，
再疊一層 Cloudflare Access 作縱深防禦（見 deploy/cloudflare-tunnel.md）。
"""
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="gov-tender-auth")


def verify_password(pw: str) -> bool:
    expected = settings.APP_PASSWORD or ""
    if not expected:
        # 未設密碼 = 鎖死，誰都進不來（避免裸奔）
        return False
    return secrets.compare_digest(pw or "", expected)


def make_token() -> str:
    return _serializer.dumps({"ok": True})


def valid_token(token: str) -> bool:
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=settings.SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
