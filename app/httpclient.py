"""共用 HTTP client：用作業系統憑證庫驗證 TLS。

政府網站常用 TWCA 等台灣憑證機構，不在 Python 的 certifi 憑證庫內，
會導致 httpx CERTIFICATE_VERIFY_FAILED。truststore 改用 OS 憑證庫
（Windows 憑證存放區已內含 TWCA 根憑證），維持 TLS 驗證、不必關掉 verify。
"""
import ssl

import httpx

from .config import settings


def _ssl_context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        return True  # 退回 certifi 預設驗證


def make_client(referer: str = None, accept: str = "application/json") -> httpx.Client:
    headers = {
        "User-Agent": settings.HTTP_USER_AGENT,
        "Accept": accept,
        "Accept-Language": "zh-TW,zh;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    return httpx.Client(
        headers=headers, timeout=30.0, follow_redirects=True, verify=_ssl_context()
    )
