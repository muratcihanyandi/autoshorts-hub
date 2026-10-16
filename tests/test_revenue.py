"""
Gelir tahmini testleri.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from conftest import make_channel

from app.models import Video, VideoStats
from app.services.revenue import estimate_revenue, revenue_series
from app.timeutil import ensure_utc

TZ = ZoneInfo("Europe/Istanbul")


def test_estimate_revenue_math():
    assert estimate_revenue(1000, 2.0) == 2.0
    assert estimate_revenue(1500, 2.0) == 3.0
    assert estimate_revenue(0, 2.0) == 0.0
    assert estimate_revenue(-100, 2.0) == 0.0, "negatif artis gelir uretmemeli"


def test_revenue_series_sums_view_deltas(db):
    """Ardisik olcumler arasindaki izlenme artisi RPM ile gelire cevrilmeli."""
    ch = make_channel(db, "UCtestAAAAAAAAAAAAAAAA", rpm=2.0)
    now = datetime.now(timezone.utc)

    video = Video(
        video_id="vid_0001",
        channel_id=ch.yt_channel_id,
        title="Test videosu",
        published_at=now - timedelta(days=5),
    )
    db.add(video)
    # Ayni gun iki olcum: 100 -> 150 izlenme.
    db.add(VideoStats(video_id="vid_0001", views=100, captured_at=now - timedelta(hours=6)))
    db.add(VideoStats(video_id="vid_0001", views=150, captured_at=now - timedelta(hours=2)))
    db.commit()

    series = revenue_series(db, days=7)

    # 50 izlenme artisi x 2.0 RPM / 1000 = 0.1 USD
    assert series["total_views"] == 50
    assert abs(series["total_revenue"] - 0.1) < 1e-9
    assert series["total_revenue"] == sum(series["revenue"])


def test_revenue_series_empty(db):
    series = revenue_series(db, days=7)
    assert series["total_revenue"] == 0.0
    assert series["total_views"] == 0
    assert len(series["days"]) == 7


def test_prune_keeps_daily_last_snapshot(db):
    """Budama sonrasi her video icin her gunden tek olcum kalmali."""
    from app.services.collector import prune_old_stats

    ch = make_channel(db, "UCtestAAAAAAAAAAAAAAAA")
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)

    db.add(Video(video_id="vid_0001", channel_id=ch.yt_channel_id, title="eski"))
    # 100 gun onceki gunde 3 olcum, bugun 1 olcum.
    db.add(VideoStats(video_id="vid_0001", views=10, captured_at=old))
    db.add(VideoStats(video_id="vid_0001", views=20, captured_at=old + timedelta(hours=1)))
    db.add(VideoStats(video_id="vid_0001", views=30, captured_at=old + timedelta(hours=2)))
    db.add(VideoStats(video_id="vid_0001", views=40, captured_at=now))
    db.commit()

    result = prune_old_stats(db, keep_days=90)
    db.commit()

    remaining = (
        db.query(VideoStats).filter(VideoStats.video_id == "vid_0001").all()
    )
    old_day_rows = [
        s for s in remaining if ensure_utc(s.captured_at) < now - timedelta(days=1)
    ]
    assert result["pruned"] >= 2
    assert len(old_day_rows) == 1, "eski gunden yalnizca son olcum kalmali"
    assert old_day_rows[0].views == 30, "kalan olcum gunun en son degeri olmali"
