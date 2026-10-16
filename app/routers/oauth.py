"""
Kanala YouTube OAuth baglantisi kurma akisi.

Google, kullaniciyi consent ekranindan uygulamanin /api/oauth/callback adresine
geri yonlendirir; burada degisim kodu yenileme anahtarina cevrilir ve kanal
kaydina saklanir. Yenileme anahtari veritabaninda tutulur ve yalnizca video
yuklerken kullanilir.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.auth import require_session
from app.config import settings
from app.db import get_db
from app.models import Channel
from app.services.youtube import YouTubeError, build_auth_flow

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

_SUCCESS_PAGE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube baglantisi tamam</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#101418;color:#e8edf2;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
 .card{{background:#1a2129;border-radius:14px;padding:32px 40px;max-width:420px;text-align:center}}
 h1{{font-size:20px;margin:0 0 8px}} p{{color:#8b98a5;margin:0}}
 a{{color:#e8b24a;text-decoration:none;font-weight:600}}
</style></head>
<body><div class="card">
 <h1>{icon} {title}</h1>
 <p>{message}</p>
 <p style="margin-top:16px"><a href="/">{back}</a></p>
</div></body></html>
"""


def _redirect_uri(request: Request) -> str:
    if settings.oauth_redirect_url:
        return settings.oauth_redirect_url
    return str(request.base_url).rstrip("/") + "/api/oauth/callback"


@router.get("/start", dependencies=[Depends(require_session)])
def oauth_start(channel_id: str, request: Request, db=Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Kanal bulunamadı.")

    try:
        flow = build_auth_flow(_redirect_uri(request))
    except YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # state: donuste hangi kanala ait oldugunu biliriz. Tek kullanicili yerel
    # kurulum oldugu icin ek imzalama gereksizdir.
    authorization_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=channel_id,
    )
    return {"authorization_url": authorization_url}


@router.get("/callback")
def oauth_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    request: Request = None,
    db=Depends(get_db),
):
    if error:
        return HTMLResponse(
            _SUCCESS_PAGE.format(
                icon="⚠️",
                title="Yetkilendirme tamamlanmadı",
                message="Google onay adımı iptal edildi veya hata verdi.",
                back="Geri dön",
            )
        )

    channel = db.get(Channel, state)
    if channel is None or not code:
        return HTMLResponse(
            _SUCCESS_PAGE.format(
                icon="⚠️",
                title="Geçersiz dönüş",
                message="Kanal bulunamadı veya yetkilendirme kodu eksik.",
                back="Geri dön",
            )
        )

    try:
        flow = build_auth_flow(_redirect_uri(request))
        flow.fetch_token(code=code)
        credentials = flow.credentials
    except YouTubeError as exc:
        return HTMLResponse(
            _SUCCESS_PAGE.format(
                icon="⚠️", title="Bağlantı kurulamadı", message=str(exc), back="Geri dön"
            )
        )
    except Exception as exc:
        return HTMLResponse(
            _SUCCESS_PAGE.format(
                icon="⚠️",
                title="Bağlantı kurulamadı",
                message=f"Google kodu değiştirilemedi: {exc}",
                back="Geri dön",
            )
        )

    if not credentials.refresh_token:
        return HTMLResponse(
            _SUCCESS_PAGE.format(
                icon="⚠️",
                title="Yenileme anahtarı alınamadı",
                message=(
                    "Google bu istemciye daha önce izin vermiş olabilir. "
                    "Google hesabı izinlerini kaldırıp tekrar deneyin."
                ),
                back="Geri dön",
            )
        )

    channel.refresh_token = credentials.refresh_token
    db.commit()

    return HTMLResponse(
        _SUCCESS_PAGE.format(
            icon="✅",
            title="YouTube bağlantısı tamam",
            message=f"{channel.title or channel.yt_channel_id} kanalı artık video yükleyebilir.",
            back="Uygulamaya dön",
        )
    )
