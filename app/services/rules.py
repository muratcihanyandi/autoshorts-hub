"""
Yayin kural motoru.

Kullanici gereksinimi: ayni icerik birden fazla kanala AYNI ANDA gitmemeli;
ayni icerik birden fazla kanala gidecekse bile kanallar arasinda en az
`cross_channel_gap_days` (varsayilan 7 gun) bulunmali.

Motor, bir (icerik, kanal, zaman) uclemesi icin KURALLARA UYAN EN ERKEN ZAMANI
hesaplar; istenen zaman kurallara aykiriysa zaman otomatik ileri alinir ve bu
durum kullaniciya Turkce bir "kural notu" ile aciklanir. Boylece yanlislikla
ayni icerigin iki kanalda ayni gun yayinlanmasi mumkun olmaz.

Kurallar (sirayla uygulanir, en gec olan kazanir):
1. Kanal aktif mi? (degilse engellenir)
2. Ayni kanal tekrari: ayni icerik ayni kanalda `same_channel_repeat_days` gun
   icinde tekrar yayinlanamaz.
3. Kanallar arasi bosluk: ayni parmak izine (fingerprint) sahip icerik baska bir
   kanalda yayinlandiysa (ya da yayinlanmak uzere siradaysa), bu kanalda en az
   `cross_channel_gap_days` gun sonra yayinlanabilir.
4. Gunluk yukleme siniri: kanalin gunluk limiti doluysa bir sonraki gune atlanir.
5. Yayin penceresi: kanal icin tanimli saat araliginin disindaysa pencerenin
   baslangicina tasinir.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models import (
    Channel,
    Content,
    Publication,
    get_settings_value,
)
from app.timeutil import ensure_utc

_ACTIVE_STATUSES = ("pending", "published")
_MAX_DAY_SKIP = 60  # gunluk limit doluysa en fazla 60 gun ileri atlanir (guvenlik)


def _as_utc(value: datetime) -> datetime:
    return ensure_utc(value)


def _local(value: datetime, tz: ZoneInfo) -> datetime:
    return _as_utc(value).astimezone(tz)


def _normalize_text(value: str) -> str:
    """Kucuk harf + boslik normalizasyonu; fingerprint tespitinde kullanilir."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def content_fingerprint(
    *,
    title: str = "",
    description: str = "",
    file_sha256: str = "",
) -> str:
    """
    Icerigin benzersiz ozetini uretir.

    Dosya yuklendiyse dosya ozeti esas alinir (birebir ayni video = ayni parmak
    izi). Dosya yoksa normallestirilmis baslik + aciklama kullanilir; boylece
    ayni metinle acilan kayitlar da ayni icerik sayilir.
    """
    if file_sha256:
        return f"file:{file_sha256}"
    payload = _normalize_text(title) + "\n" + _normalize_text(description)
    return "meta:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def effective_publication_time(pub: Publication) -> datetime:
    """Yayinin etkili zamani: yayinlandiysa published_at, siradaysa scheduled_at."""
    if pub.status == "published" and pub.published_at is not None:
        return _as_utc(pub.published_at)
    return _as_utc(pub.scheduled_at)


@dataclass
class RuleOutcome:
    requested_at: datetime
    scheduled_at: datetime
    ok: bool = True                 # istenen zaman degistirilmeden kabul mu?
    blocked: bool = False           # kural geregi hic mi yayinlanamaz?
    reasons: list[str] = field(default_factory=list)


def _snap_into_window(at_local: datetime, channel: Channel) -> datetime:
    """
    Zamani kanalin yayin penceresine tasir.

    Pencere gunun [start, end] dakikalari arasidir. Pencere baslangicindan once
    ayni gunun baslangicina, pencere sonrasinda ertesi gunun baslangicina
    tasinir. Pencere tanimsizsa (start >= end) degisiklik yapilmaz.
    """
    start = int(channel.publish_window_start or 0)
    end = int(channel.publish_window_end or 0)
    if start >= end:
        return at_local

    minute_of_day = at_local.hour * 60 + at_local.minute
    if minute_of_day < start:
        return at_local.replace(
            hour=start // 60, minute=start % 60, second=0, microsecond=0
        )
    if minute_of_day > end:
        nxt = at_local + timedelta(days=1)
        return nxt.replace(
            hour=start // 60, minute=start % 60, second=0, microsecond=0
        )
    return at_local


def _daily_count(db: Session, channel: Channel, day_local, tz: ZoneInfo) -> int:
    """Kanalin belirli bir gunde (yerel tarih) planli + yayinlanmis sayisi."""
    pubs = db.execute(
        select(Publication).where(
            Publication.channel_id == channel.yt_channel_id,
            Publication.status.in_(_ACTIVE_STATUSES),
        )
    ).scalars().all()
    return sum(
        1
        for p in pubs
        if _local(effective_publication_time(p), tz).date() == day_local
    )


def evaluate_publication(
    db: Session,
    content: Content,
    channel: Channel,
    at: datetime,
    *,
    extra_times: Iterable[tuple[str, datetime]] = (),
    exclude_publication_id: int | None = None,
) -> RuleOutcome:
    """
    (icerik, kanal, zaman) icin kurallara uyan en erken zamani hesaplar.

    `extra_times`, cok kanalli otomatik planlama sirasinda bu cagridan once
    sanal olarak planlanan (kanal, zaman) ciftlerini icerir; boylece ayni
    planlama oturumunda iki kanal arasindaki bosluk da garanti edilir.

    `exclude_publication_id`, kuyruktaki mevcut bir yayini yeniden
    degerlendirirken o yayinin kendisini ayni-kanal kontrolunden cikarir;
    aksi halde her pending kayit kendisiyle cakisir ve surekli ertelenirdi.
    """
    tz = ZoneInfo(app_settings.timezone)
    requested = _as_utc(at)
    reasons: list[str] = []

    if not channel.is_active:
        return RuleOutcome(
            requested_at=requested,
            scheduled_at=requested,
            ok=False,
            blocked=True,
            reasons=["Kanal pasif durumda; önce kanalı aktif hale getirin."],
        )

    repeat_days = int(get_settings_value(db, "same_channel_repeat_days", 90))
    gap_days = int(get_settings_value(db, "cross_channel_gap_days", 7))
    daily_limit = int(
        channel.daily_upload_limit
        or get_settings_value(db, "default_daily_upload_limit", 3)
    )

    earliest = requested

    # --- Kural 2: ayni kanal tekrari -------------------------------------
    same_channel_query = select(Publication).where(
        Publication.channel_id == channel.yt_channel_id,
        Publication.content_id == content.id,
        Publication.status.in_(_ACTIVE_STATUSES),
    )
    if exclude_publication_id is not None:
        same_channel_query = same_channel_query.where(
            Publication.id != exclude_publication_id
        )
    same_channel = db.execute(same_channel_query).scalars().all()
    for pub in same_channel:
        candidate = effective_publication_time(pub) + timedelta(days=repeat_days)
        if candidate > earliest:
            earliest = candidate
            reasons.append(
                f"Bu içerik bu kanalda zaten yayınlanmış; aynı kanalda tekrar "
                f"için {repeat_days} gün beklenmeli."
            )

    # --- Kural 3: baska kanallarda ayni parmak izi ------------------------
    same_fingerprint_contents = db.execute(
        select(Content.id).where(Content.fingerprint == content.fingerprint)
    ).scalars().all()

    others = db.execute(
        select(Publication).where(
            Publication.channel_id != channel.yt_channel_id,
            Publication.content_id.in_(same_fingerprint_contents),
            Publication.status.in_(_ACTIVE_STATUSES),
        )
    ).scalars().all()

    other_times = [(p.channel_id, effective_publication_time(p)) for p in others]
    other_times.extend(
        (ch_id, _as_utc(t)) for ch_id, t in extra_times if ch_id != channel.yt_channel_id
    )

    for _, other_at in other_times:
        candidate = other_at + timedelta(days=gap_days)
        if candidate > earliest:
            earliest = candidate
            reasons.append(
                f"Aynı içerik başka bir kanalda yayında; kanallar arası minimum "
                f"{gap_days} gün boşluk uygulanıyor."
            )

    # --- Kural 4 + 5: gunluk limit ve yayin penceresi ---------------------
    # pencereye oturt, sonra gun doluysa ertesi gune tasi ve tekrar dene.
    for _ in range(_MAX_DAY_SKIP):
        local = _local(earliest, tz)
        snapped = _snap_into_window(local, channel)
        if snapped != local:
            earliest = snapped.astimezone(dt_timezone.utc)
            reasons.append(
                f"Yayın penceresine taşındı: {channel.publish_window_start // 60:02d}:"
                f"{channel.publish_window_start % 60:02d}-"
                f"{channel.publish_window_end // 60:02d}:"
                f"{channel.publish_window_end % 60:02d} araligi."
            )
            continue

        if _daily_count(db, channel, local.date(), tz) < daily_limit:
            break

        next_day = (local + timedelta(days=1)).replace(
            hour=channel.publish_window_start // 60 or 8,
            minute=0,
            second=0,
            microsecond=0,
        )
        earliest = _snap_into_window(next_day, channel).astimezone(dt_timezone.utc)
        reasons.append(
            f"Kanalın günlük yükleme sınırı ({daily_limit}) dolu; sonraki gün alındı."
        )

    ok = abs((earliest - requested).total_seconds()) < 60
    return RuleOutcome(
        requested_at=requested,
        scheduled_at=earliest,
        ok=ok,
        blocked=False,
        reasons=reasons,
    )


def suggest_plan(
    db: Session,
    content: Content,
    channels: list[Channel],
    start_at: datetime,
) -> list[tuple[Channel, RuleOutcome]]:
    """
    Ayni icerigi birden fazla kanala, kurallara uyarak dagitir.

    Her kanal icin onceki kanallarin planlanan zamanlari da bosluk hesabina
    katilir; boylece N kanal icin zamanlar arasi bosluk en az gap_days olur
    (kanal1: bugun, kanal2: +7 gun, kanal3: +14 gun ...).
    """
    tz = ZoneInfo(app_settings.timezone)
    planned: list[tuple[str, datetime]] = []
    result: list[tuple[Channel, RuleOutcome]] = []

    base = _as_utc(start_at)
    for channel in channels:
        outcome = evaluate_publication(
            db, content, channel, base, extra_times=planned
        )
        result.append((channel, outcome))
        planned.append((channel.yt_channel_id, outcome.scheduled_at))

    return result
