"""
Kanal istatistiklerini toplayan arka plan gorevi.

Her dongude kanalin ozet istatistigi (abone, toplam izlenme, video sayisi),
yeni videolar kesfedilir ve takip edilen videolarin anlik izlenme/begeni/yorum
degerleri zaman serisi olarak kaydedilir. Veriler ekle-only yazilir; gecmis
asla degistirilmez, boylece gelir tahmini geriye donuk hesaplanabilir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Channel, ChannelStats, JobRun, Video, VideoStats
from app.services.youtube import YouTubeAPI, YouTubeError, parse_iso_duration
from app.timeutil import ensure_utc

# Yayin tarihi bu kadar yeni olan videolar her dongude olculur; eskiler gunde
# bir olculur. 7/24 calisan uygulamada veritabaninin kontrolsuz buyumesini
# onler (200 video x 48 olcum/gun = gunluk ~10.000 satir olurdu).
RECENT_SNAPSHOT_DAYS = 14
STALE_SNAPSHOT_HOURS = 20

# Anlik istatistik detaylari bu kadar gun sonrasinda gune bir adet kalacak
# sekilde budanir; gunluk ozetler boylece korunur.
STATS_RETENTION_DAYS = 90


def _parse_published_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # YouTube ISO-8601 (2024-05-04T10:00:00Z) degerini UTC datetime'a cevir.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def collect_channel(db: Session, api: YouTubeAPI, channel: Channel) -> dict:
    """
    Tek kanalin tum istatistiklerini toplar ve ozet dondurur.

    Hata olusursa kanalin kaydina yazilir; diger kanallarin toplanmasi
    durdurulmaz (cagiran taraf kanal kanal cagirir).
    """
    summary = {
        "channel": channel.title or channel.yt_channel_id,
        "new_videos": 0,
        "tracked_videos": 0,
        "subscribers": 0,
    }

    stats_map = api.channel_stats([channel.yt_channel_id])
    info = stats_map.get(channel.yt_channel_id)
    if info is None:
        raise YouTubeError(
            f"Kanal yanit vermedi: {channel.title or channel.yt_channel_id}"
        )

    # Kanal ust verileri degismisse guncelle.
    channel.title = info["title"] or channel.title
    channel.handle = info["handle"]
    channel.custom_url = info["custom_url"]
    channel.thumbnail_url = info["thumbnail_url"]
    channel.uploads_playlist_id = info["uploads_playlist_id"] or channel.uploads_playlist_id
    summary["subscribers"] = info["subscribers"]

    db.add(
        ChannelStats(
            channel_id=channel.yt_channel_id,
            subscribers=info["subscribers"],
            total_views=info["total_views"],
            video_count=info["video_count"],
        )
    )

    # --- Yeni videolari kesfet (yukleme oynatma listesi uzerinden) --------
    known_ids: set[str] = set(
        db.execute(
            select(Video.video_id).where(Video.channel_id == channel.yt_channel_id)
        ).scalars()
    )

    playlist_id = channel.uploads_playlist_id
    if playlist_id:
        discovered: list[dict] = []
        page_token = None
        while len(discovered) < settings.max_tracked_videos:
            items, page_token = api.uploads_page(playlist_id, page_token)
            if not items:
                break
            discovered.extend(items)
            if not page_token:
                break

        for item in discovered[: settings.max_tracked_videos]:
            video_id = item.get("video_id", "")
            if not video_id:
                continue
            existing = db.get(Video, video_id)
            if existing is None:
                db.add(
                    Video(
                        video_id=video_id,
                        channel_id=channel.yt_channel_id,
                        title=item.get("title", ""),
                        published_at=_parse_published_at(item.get("published_at", "")),
                        thumbnail_url=item.get("thumbnail_url", ""),
                    )
                )
                known_ids.add(video_id)
                summary["new_videos"] += 1
            else:
                existing.last_seen_at = datetime.now(timezone.utc)

    # --- Takip edilen videolarin istatistikleri ---------------------------
    tracked_ids = list(known_ids)[: settings.max_tracked_videos]
    if tracked_ids:
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=RECENT_SNAPSHOT_DAYS)
        stale_after = now - timedelta(hours=STALE_SNAPSHOT_HOURS)

        videos_by_id = {
            v.video_id: v
            for v in db.execute(
                select(Video).where(Video.video_id.in_(tracked_ids))
            ).scalars()
        }
        last_snapshot = dict(
            db.execute(
                select(VideoStats.video_id, func.max(VideoStats.captured_at))
                .where(VideoStats.video_id.in_(tracked_ids))
                .group_by(VideoStats.video_id)
            ).all()
        )

        # Yeni videolar her dongude, eskiler gunde bir olculur.
        to_measure = []
        for video_id in tracked_ids:
            video = videos_by_id.get(video_id)
            if video is None:
                continue
            published = ensure_utc(video.published_at)
            last = ensure_utc(last_snapshot.get(video_id))
            if published is not None and published >= recent_cutoff:
                to_measure.append(video_id)
            elif last is None or last <= stale_after:
                to_measure.append(video_id)

        if to_measure:
            details = api.video_details(to_measure)
            for detail in details:
                video = videos_by_id.get(detail["video_id"])
                if video is None:
                    continue
                video.title = detail["title"] or video.title
                video.duration_sec = detail["duration_sec"]
                video.is_short = detail["is_short"]
                db.add(
                    VideoStats(
                        video_id=video.video_id,
                        views=detail["views"],
                        likes=detail["likes"],
                        comments=detail["comments"],
                    )
                )
                video.last_seen_at = now
            summary["tracked_videos"] = len(to_measure)

    channel.last_polled_at = datetime.now(timezone.utc)
    channel.last_error = ""
    return summary


def collect_all_channels(db: Session, *, api: YouTubeAPI | None = None) -> dict:
    """Tum aktif kanallari sirayla toplar; hata tek kanali etkiler."""
    if api is None:
        api = YouTubeAPI()

    channels = db.execute(
        select(Channel).where(Channel.is_active.is_(True))
    ).scalars().all()

    results = []
    for channel in channels:
        try:
            results.append(collect_channel(db, api, channel))
            db.commit()
        except YouTubeError as exc:
            db.rollback()
            channel.last_error = str(exc)
            channel.last_polled_at = datetime.now(timezone.utc)
            db.commit()
            results.append(
                {"channel": channel.title or channel.yt_channel_id, "error": str(exc)}
            )

    return {"channels": results, "collected_at": datetime.now(timezone.utc).isoformat()}


def prune_old_stats(db: Session, keep_days: int = STATS_RETENTION_DAYS) -> dict:
    """
    Eski anlik istatistikleri budar; her video icin her gunden yalnizca son
    olcum kalir. Gunluk ozetler (dolayisiyla gelir tahmini) korunur.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(30, keep_days))

    day_expr = func.date(VideoStats.captured_at)
    keep_ids = select(func.max(VideoStats.id)).group_by(
        VideoStats.video_id, day_expr
    )

    result = db.execute(
        delete(VideoStats).where(
            VideoStats.captured_at < cutoff,
            VideoStats.id.not_in(keep_ids),
        )
    )
    return {"pruned": getattr(result, "rowcount", 0) or 0}
