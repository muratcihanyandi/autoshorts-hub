"""Giris / cikis ve oturum durumu."""

from fastapi import APIRouter, HTTPException, Request, Response

from app import __version__
from app.auth import (
    COOKIE_NAME,
    issue_token,
    login_required,
    revoke_token,
    token_from_request,
    verify_password,
)
from app.config import settings
from app.schemas import LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/state")
def auth_state():
    """Arayuzun acilista sordugu temel durum bilgisi."""
    return {
        "login_required": login_required(),
        "app_name": "AutoShorts Hub",
        "version": __version__,
        "youtube_api_key": bool(settings.youtube_api_key),
        "youtube_oauth": bool(
            settings.youtube_client_id and settings.youtube_client_secret
        ),
    }


@router.post("/login")
def login(body: LoginRequest, response: Response):
    if not login_required():
        return {"ok": True}
    if not verify_password(body.password):
        raise HTTPException(status_code=401, detail="Şifre hatalı.")
    token = issue_token()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    revoke_token(token_from_request(request))
    response.delete_cookie(key=COOKIE_NAME)
    return {"ok": True}
