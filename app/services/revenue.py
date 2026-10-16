"""
Gelir tahmini.

YouTube Analytics API yerine RPM (1000 izlenme basina gelir) yaklasimi kullanilir:
video_stats zaman serisindeki ardisik olcumler arasindaki izlenme artisi, kanalin
RPM degeriyle carpilarak tahmini gelire cevrilir. Gercek Analytics API entegrasyonu
ileri surumde eklenebilir; bu yuzden tum degerler acikca "tahmin" olarak isimlendirilir.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Channel, Video, VideoStats
from app.timeutil import ensure_utc


def estimate_revenue(views_delta: int, rpm: float) -> float:
    """Izlenme artisini RPM ile tahmini gelire cevirir (USD)."""
    if views_delta <= 0 or rpm <= 0:
        return 0.0
    return views_delta * rpm / 1000.0


def revenue_series(db: Session, days: int = 30) -> dict:
    """
    Gun bazinda tahmini gelir ve izlenme serisi uretir.

    Her video icin olcum zamanlari kucukten buyuge siralanir; ardisik olcumler
    arasindaki izlenme artisi, IKINCI olcumun yerel tarihine yazilir (artisin
    gerceklesme gunu olarak en makul yaklasim budur).
    """
    tz = ZoneInfo(settings.timezone)
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))

    rows = db.execute(
        select(VideoStats, Video, Channel)
        .join(Video, VideoStats.video_id == Video.video_id)
        .join(Channel, Video.channel_id == Channel.yt_channel_id)
        .where(VideoStats.captured_at >= since)
        .order_by(VideoStats.video_id, VideoStats.captured_at)
    ).all()

    per_day_revenue: dict[str, float] = defaultdict(float)
    per_day_views: dict[str, int] = defaultdict(int)
    per_channel_revenue: dict[str, float] = defaultdict(float)

    previous: dict[str, VideoStats] = {}
    for stats, video, channel in rows:
        prev = previous.get(video.video_id)
        previous[video.video_id] = stats
        if prev is None:
            continue

        delta = max(0, stats.views - prev.views)
        if delta == 0:
            continue

        captured = ensure_utc(stats.captured_at)
        day = captured.astimezone(tz).date().isoformat()
        rpm = float(channel.rpm or 0)
        revenue = estimate_revenue(delta, rpm)

        per_day_views[day] += delta
        per_day_revenue[day] += revenue
        per_channel_revenue[channel.title or channel.yt_channel_id] += revenue

    all_days = [
        (datetime.now(tz).date() - timedelta(days=offset)).isoformat()
        for offset in range(max(1, days) - 1, -1, -1)
    ]

    return {
        "days": all_days,
        "revenue": [round(per_day_revenue.get(d, 0.0), 4) for d in all_days],
        "views": [per_day_views.get(d, 0) for d in all_days],
        "total_revenue": round(sum(per_day_revenue.values()), 4),
        "total_views": sum(per_day_views.values()),
        "per_channel": {
            name: round(value, 4) for name, value in per_channel_revenue.items()
        },
    }
