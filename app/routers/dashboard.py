"""
Panel (dashboard) veri uc noktalari ve saglik kontrolleri.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app import __version__
from app.auth import require_session
from app.config import settings
from app.db import get_db
from app.models import Channel, ChannelStats, JobRun, Publication, Video, VideoStats
from app.schemas import video_dict
from app.services.revenue import revenue_series
from app.services.scheduler import get_scheduler

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/health")
def health(db=Depends(get_db)):
    """CasaOS / izleme icin herkese acik saglik kontrolu."""
    db_ok = True
    try:
        db.execute(select(func.now()))
    except Exception:
        db_ok = False

    scheduler = get_scheduler()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": "AutoShorts Hub",
        "version": __version__,
        "database": db_ok,
        "scheduler_running": scheduler is not None and scheduler.running,
        "youtube_api_key": bool(settings.youtube_api_key),
        "youtube_oauth": bool(
            settings.youtube_client_id and settings.youtube_client_secret
        ),
    }


@router.get("/overview", dependencies=[Depends(require_session)])
def overview(db=Depends(get_db)):
    """Panelin ust ozet kartlari ve grafik verisi."""
    channels = db.execute(select(Channel)).scalars().all()

    channel_items = []
    total_subscribers = 0
    total_views = 0
    total_videos = 0
    for channel in channels:
        latest = (
            db.execute(
                select(ChannelStats)
                .where(ChannelStats.channel_id == channel.yt_channel_id)
                .order_by(ChannelStats.captured_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        subscribers = latest.subscribers if latest else 0
        views = latest.total_views if latest else 0
        videos = latest.video_count if latest else 0
        total_subscribers += subscribers
        total_views += views
        total_videos += videos
        channel_items.append(
            {
                "yt_channel_id": channel.yt_channel_id,
                "title": channel.title,
                "handle": channel.handle,
                "thumbnail_url": channel.thumbnail_url,
                "is_active": channel.is_active,
                "has_oauth": bool(channel.refresh_token),
                "subscribers": subscribers,
                "total_views": views,
                "video_count": videos,
                "last_polled_at": (
                    channel.last_polled_at.isoformat()
                    if channel.last_polled_at
                    else None
                ),
                "last_error": channel.last_error,
            }
        )

    revenue_30 = revenue_series(db, days=30)
    revenue_7 = revenue_series(db, days=7)

    status_counts = dict(
        db.execute(
            select(Publication.status, func.count(Publication.id)).group_by(
                Publication.status
            )
        ).all()
    )

    # Son gorev kosulari (7/24 calisma kaniti).
    job_rows = db.execute(
        select(JobRun).order_by(JobRun.started_at.desc()).limit(50)
    ).scalars().all()
    last_jobs: dict[str, dict] = {}
    for run in job_rows:
        if run.job_name not in last_jobs:
            last_jobs[run.job_name] = {
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
                "detail": run.detail,
            }

    tracked_videos = db.scalar(select(func.count(Video.video_id))) or 0

    return {
        "channels": channel_items,
        "totals": {
            "channels": len(channels),
            "subscribers": total_subscribers,
            "views": total_views,
            "videos": total_videos,
            "tracked_videos": tracked_videos,
        },
        "revenue": {
            "days": revenue_30["days"],
            "series": revenue_30["revenue"],
            "views_series": revenue_30["views"],
            "total_30d": revenue_30["total_revenue"],
            "total_7d": revenue_7["total_revenue"],
            "total_views": revenue_30["total_views"],
            "views_7d": revenue_7["total_views"],
            "per_channel": revenue_30["per_channel"],
        },
        "publications": status_counts,
        "jobs": last_jobs,
    }


@router.get("/videos", dependencies=[Depends(require_session)])
def list_videos(
    channel_id: str = "",
    days: int = 7,
    limit: int = 50,
    db=Depends(get_db),
):
    """
    Videolari son istatistikleriyle listeler.

    `views_gained`: secilen pencere icindeki izlenme artisi (pencere icindeki
    ilk ve son olcum arasindaki fark).
    """
    limit = max(1, min(limit, 200))
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))

    query = select(Video).order_by(Video.published_at.desc()).limit(limit)
    if channel_id:
        query = query.where(Video.channel_id == channel_id)
    videos = db.execute(query).scalars().all()

    video_ids = [v.video_id for v in videos]
    stats_map: dict[str, list] = defaultdict(list)
    if video_ids:
        stat_rows = db.execute(
            select(VideoStats.video_id, VideoStats.captured_at, VideoStats.views)
            .where(
                VideoStats.video_id.in_(video_ids),
                VideoStats.captured_at >= since,
            )
            .order_by(VideoStats.video_id, VideoStats.captured_at)
        ).all()
        for video_id, captured_at, views in stat_rows:
            stats_map[video_id].append((captured_at, views))

    latest_all = {
        row[0]: row[1]
        for row in db.execute(
            select(VideoStats.video_id, VideoStats.views)
            .where(VideoStats.video_id.in_(video_ids))
            .order_by(VideoStats.video_id, VideoStats.captured_at)
        ).all()
    }

    result = []
    for video in videos:
        series = stats_map.get(video.video_id, [])
        views_gained = 0
        if len(series) >= 2:
            views_gained = max(0, series[-1][1] - series[0][1])
        item = video_dict(video, latest_stats=None)
        item["views"] = latest_all.get(video.video_id, series[-1][1] if series else 0)
        item["views_gained"] = views_gained
        result.append(item)

    result.sort(key=lambda v: v["views_gained"], reverse=True)
    return {"videos": result}


@router.get("/revenue", dependencies=[Depends(require_session)])
def revenue(days: int = 30, db=Depends(get_db)):
    """Gunluk tahmini gelir serisi (RPM yaklasimi)."""
    return revenue_series(db, days=max(1, min(days, 365)))


@router.get("/jobs", dependencies=[Depends(require_session)])
def recent_jobs(limit: int = 20, db=Depends(get_db)):
    """Arka plan gorevlerinin son calisma kayitlari."""
    rows = (
        db.execute(
            select(JobRun).order_by(JobRun.started_at.desc()).limit(
                max(1, min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return {
        "jobs": [
            {
                "id": run.id,
                "job_name": run.job_name,
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
                "detail": run.detail,
            }
            for run in rows
        ]
    }
