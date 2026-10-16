"""Uygulama ayarlari (kural parametreleri)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth import require_session
from app.db import get_db
from app.models import DEFAULT_SETTINGS, AppSetting, Channel
from app.schemas import SettingsUpdate

router = APIRouter(
    prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_session)]
)


def _load_row(db) -> AppSetting:
    row = db.get(AppSetting, "app")
    if row is None:
        row = AppSetting(key="app", value={})
        db.add(row)
    return row


@router.get("")
def get_settings(db=Depends(get_db)):
    row = _load_row(db)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(row.value or {})
    return {"settings": merged}


@router.put("")
def update_settings(body: SettingsUpdate, db=Depends(get_db)):
    row = _load_row(db)
    value = dict(row.value or {})

    updates = body.model_dump(exclude_none=True)
    for field, new_value in updates.items():
        if field in DEFAULT_SETTINGS and new_value is not None:
            value[field] = new_value

    row.value = value
    row.updated_at = datetime.now(timezone.utc)
    db.commit()

    merged = dict(DEFAULT_SETTINGS)
    merged.update(value)
    return {"settings": merged}


@router.get("/channels-overview")
def channels_overview(db=Depends(get_db)):
    """Ayarlar sayfasindaki kanal bazli RPM/limit ozeti icin yardimci veri."""
    channels = db.execute(select(Channel)).scalars().all()
    return {
        "channels": [
            {
                "yt_channel_id": c.yt_channel_id,
                "title": c.title,
                "rpm": c.rpm,
                "daily_upload_limit": c.daily_upload_limit,
                "publish_window_start": c.publish_window_start,
                "publish_window_end": c.publish_window_end,
            }
            for c in channels
        ]
    }
