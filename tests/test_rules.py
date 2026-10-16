"""
Yayin kural motoru testleri.

Kapsanan kurallar:
- ayni icerik farkli kanallarda ayni anda yayinlanamaz (>= 7 gun bosluk)
- ayni kanalda tekrar icin uzun bekleme (varsayilan 90 gun)
- gunluk yukleme siniri dolunca sonraki gune atlanir
- yayin penceresi disindaki istek pencereye tasınır
- otomatik planlama N kanali kademeli dagitir
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from conftest import make_channel, make_content, make_publication
from app.services.rules import (
    content_fingerprint,
    evaluate_publication,
    suggest_plan,
)

TZ = ZoneInfo("Europe/Istanbul")


def utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def local_dt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ).astimezone(timezone.utc)


def test_fingerprint_is_stable_for_same_text():
    a = content_fingerprint(title="Merhaba Dünya", description="ilk")
    b = content_fingerprint(title="merhaba   dünya", description="ilk")
    c = content_fingerprint(title="Merhaba Dünya", description="farklı")
    d = content_fingerprint(file_sha256="a" * 64)

    assert a == b, "buyuk/kucuk harf ve bosluk farklari ayni icerik sayilmali"
    assert a != c
    assert d.startswith("file:"), "dosya yuklenen icerik dosya ozetini kullanmali"
    # Veritabani sutunu varchar(96); daha uzun deger PostgreSQL'de hata verir.
    assert len(a) < 96 and len(d) < 96, "parmak izi sutun sinirini asmamali"


def test_cross_channel_gap_pushes_second_channel_a_week(db):
    """Ayni icerik A kanalinda yayinlandiysa B kanali en az 7 gun bekler."""
    ch_a = make_channel(db, "UCtestAAAAAAAAAAAAAAAA")
    ch_b = make_channel(db, "UCtestBBBBBBBBBBBBBBBB")
    content = make_content(db)

    make_publication(
        db, content, ch_a, published_at=utc(2026, 10, 1), scheduled_at=utc(2026, 10, 1)
    )

    outcome = evaluate_publication(db, content, ch_b, utc(2026, 10, 2))

    assert not outcome.blocked
    expected = utc(2026, 10, 8)  # 1 Ekim + 7 gun
    assert outcome.scheduled_at >= expected, (
        f"kanallar arasi bosluk uygulanmali: {outcome.scheduled_at} >= {expected}"
    )
    assert any("boşluk" in r for r in outcome.reasons)


def test_same_content_cannot_publish_to_two_channels_same_day(db):
    """1 Ekim'de A'da yayinlanan icerik 2 Ekim'de B'de yayinlanamaz (ertelenir)."""
    ch_a = make_channel(db, "UCtestAAAAAAAAAAAAAAAA")
    ch_b = make_channel(db, "UCtestBBBBBBBBBBBBBBBB")
    content = make_content(db)

    make_publication(
        db, content, ch_a, published_at=utc(2026, 10, 1), scheduled_at=utc(2026, 10, 1)
    )
    outcome = evaluate_publication(db, content, ch_b, utc(2026, 10, 1, hour=23))

    same_day = outcome.scheduled_at.date() == utc(2026, 10, 1).date()
    assert not same_day, "ayni gun ikinci kanala yayin kabul edilmemeli"


def test_same_channel_repeat_requires_long_wait(db):
    ch = make_channel(db, "UCtestAAAAAAAAAAAAAAAA")
    content = make_content(db)
    make_publication(
        db, content, ch, published_at=utc(2026, 10, 1), scheduled_at=utc(2026, 10, 1)
    )

    outcome = evaluate_publication(db, content, ch, utc(2026, 10, 10))
    assert outcome.scheduled_at >= utc(2026, 12, 30), "90 gun tekrar kurali uygulanmali"


def test_daily_limit_moves_to_next_day(db):
    ch = make_channel(db, "UCtestAAAAAAAAAAAAAAAA", daily_upload_limit=1)
    other = make_content(db, title="Başka içerik", description="başka")
    content = make_content(db)

    # Bugun icin limit dolu: baska bir icerik zaten planli.
    make_publication(
        db,
        other,
        ch,
        status="pending",
        scheduled_at=local_dt(2026, 10, 2, 18),
    )

    outcome = evaluate_publication(db, content, ch, local_dt(2026, 10, 2, 19))
    scheduled_local = outcome.scheduled_at.astimezone(TZ)

    assert scheduled_local.date() > datetime(2026, 10, 2).date(), (
        "gunluk limit doluysa sonraki gune atlanmali"
    )


def test_publish_window_snaps_into_range(db):
    ch = make_channel(
        db,
        "UCtestAAAAAAAAAAAAAAAA",
        publish_window_start=17 * 60,
        publish_window_end=21 * 60,
    )
    content = make_content(db)

    # Yerel saatle 03:00'de istenen yayin ayni gun 17:00'ye tasinmali.
    outcome = evaluate_publication(db, content, ch, local_dt(2026, 10, 2, 3))
    scheduled_local = outcome.scheduled_at.astimezone(TZ)

    assert (scheduled_local.hour, scheduled_local.minute) == (17, 0)
    assert scheduled_local.date() == datetime(2026, 10, 2).date()


def test_suggest_plan_distributes_channels_with_gap(db):
    """Otomatik planlama: uc kanal arasi her adim >= 7 gun olmali."""
    channels = [
        make_channel(db, "UCtestAAAAAAAAAAAAAAAA"),
        make_channel(db, "UCtestBBBBBBBBBBBBBBBB"),
        make_channel(db, "UCtestCCCCCCCCCCCCCCCC"),
    ]
    content = make_content(db)

    plan = suggest_plan(db, content, channels, utc(2026, 10, 1))

    times = sorted(outcome.scheduled_at for _, outcome in plan)
    for earlier, later in zip(times, times[1:]):
        gap = later - earlier
        assert gap >= timedelta(days=7), (
            f"kanallar arasi bosluk en az 7 gun olmali, bulundu: {gap}"
        )


def test_inactive_channel_is_blocked(db):
    ch = make_channel(db, "UCtestAAAAAAAAAAAAAAAA", is_active=False)
    content = make_content(db)

    outcome = evaluate_publication(db, content, ch, utc(2026, 10, 1))
    assert outcome.blocked
    assert not outcome.ok


def test_no_conflict_when_fingerprints_differ(db):
    """Farkli icerikler ayni gunde farkli kanallara yayinlanabilir."""
    ch_a = make_channel(db, "UCtestAAAAAAAAAAAAAAAA")
    ch_b = make_channel(db, "UCtestBBBBBBBBBBBBBBBB")
    content_a = make_content(db, title="Birinci", description="x")
    content_b = make_content(db, title="İkinci", description="y")

    make_publication(
        db, content_a, ch_a, published_at=utc(2026, 10, 1), scheduled_at=utc(2026, 10, 1)
    )
    outcome = evaluate_publication(db, content_b, ch_b, local_dt(2026, 10, 1, 18))
    assert outcome.ok, "farkli icerikler kuraldan etkilenmemeli"
