"""
Basit oturum yonetimi.

APP_PASSWORD tanimliysa tum /api istekleri (giris ve saglik kontrolu haric)
gecerli oturum ister. Oturumlar islem belleginde tutulur; uygulama yeniden
basladiginda giriler dusup yeniden giris gerekir - tek kullanicili yerel ag
arayuzu icin bilincli ve yeterli bir secimdir.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from app.config import settings

COOKIE_NAME = "hub_session"
_active_tokens: set[str] = set()


def login_required() -> bool:
    return bool(settings.app_password)


def verify_password(password: str) -> bool:
    """Sabit zamanli karsilastirma ile sifreyi dogrular."""
    expected = settings.app_password.encode("utf-8")
    provided = (password or "").encode("utf-8")
    return secrets.compare_digest(expected, provided)


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _active_tokens.add(token)
    return token


def revoke_token(token: str | None) -> None:
    if token:
        _active_tokens.discard(token)


def token_from_request(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


def is_authenticated(request: Request) -> bool:
    if not login_required():
        return True
    token = token_from_request(request)
    return bool(token and token in _active_tokens)


def require_session(request: Request) -> None:
    """FastAPI bagimliligi: oturum yoksa 401 dondurur."""
    if not login_required():
        return
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Oturum gerekli.")
