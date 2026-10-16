"""Kanal yonetimi."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.auth import require_session
from app.db import get_db
from app.models import Channel, ChannelStats
from app.schemas import ChannelCreate, ChannelUpdate, channel_dict
from app.services.collector import collect_channel
from app.services.youtube import YouTubeAPI, YouTubeError

router = APIRouter(
    prefix="/api/channels", tags=["channels"], dependencies=[Depends(require_session)]
)


def _latest_stats(db, channel_id: str):
    return (
        db.execute(
            select(ChannelStats)
            .where(ChannelStats.channel_id == channel_id)
            .order_by(ChannelStats.captured_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


@router.get("")
def list_channels(db=Depends(get_db)):
    channels = db.execute(select(Channel)).scalars().all()
    result = []
    for channel in channels:
        item = channel_dict(channel)
        latest = _latest_stats(db, channel.yt_channel_id)
        item["subscribers"] = latest.subscribers if latest else 0
        item["total_views"] = latest.total_views if latest else 0
        item["video_count"] = latest.video_count if latest else 0
        result.append(item)
    return {"channels": result}


@router.post("", status_code=201)
def add_channel(body: ChannelCreate, db=Depends(get_db)):
    try:
        api = YouTubeAPI()
        info = api.resolve_channel(body.ref)
    except YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if db.get(Channel, info["yt_channel_id"]) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Kanal zaten ekli: {info['title'] or info['yt_channel_id']}",
        )

    channel = Channel(
        yt_channel_id=info["yt_channel_id"],
        title=info["title"],
        handle=info["handle"],
        custom_url=info["custom_url"],
        thumbnail_url=info["thumbnail_url"],
        uploads_playlist_id=info["uploads_playlist_id"],
    )
    db.add(channel)
    db.commit()

    # Illec olcum hemen alinsin; panel bos gorunmesin.
    try:
        collect_channel(db, api, channel)
        db.commit()
    except YouTubeError:
        db.rollback()

    return {"channel": channel_dict(channel)}


@router.patch("/{channel_id}")
def update_channel(channel_id: str, body: ChannelUpdate, db=Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Kanal bulunamadı.")

    updates = body.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(channel, field, value)

    if channel.publish_window_start >= channel.publish_window_end:
        raise HTTPException(
            status_code=400,
            detail="Yayın penceresi başlangıcı bitişten önce olmalı.",
        )

    db.commit()
    return {"channel": channel_dict(channel)}


@router.delete("/{channel_id}")
def delete_channel(channel_id: str, db=Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Kanal bulunamadı.")
    db.delete(channel)
    db.commit()
    return {"ok": True}


@router.post("/{channel_id}/collect")
def trigger_collect(channel_id: str, db=Depends(get_db)):
    """Bu kanal icin istatistik toplamayi hemen tetikler."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Kanal bulunamadı.")
    try:
        api = YouTubeAPI()
        summary = collect_channel(db, api, channel)
        db.commit()
        return {"ok": True, "summary": summary}
    except YouTubeError as exc:
        db.rollback()
        channel.last_error = str(exc)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/{channel_id}/stats")
def channel_stats_series(channel_id: str, days: int = 30, db=Depends(get_db)):
    """Kanal ozet istatistigi zaman serisi (abone / toplam izlenme / video)."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Kanal bulunamadı.")

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    rows = (
        db.execute(
            select(ChannelStats)
            .where(
                ChannelStats.channel_id == channel_id,
                ChannelStats.captured_at >= since,
            )
            .order_by(ChannelStats.captured_at)
        )
        .scalars()
        .all()
    )

    return {
        "channel": channel_dict(channel),
        "points": [
            {
                "captured_at": row.captured_at.isoformat(),
                "subscribers": row.subscribers,
                "total_views": row.total_views,
                "video_count": row.video_count,
            }
            for row in rows
        ],
    }
