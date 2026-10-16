"""
Yayin kuyrugu isleyicisi.

Zamani gelmis pending yayinlari sirayla isler:
1. Kural motoru tekrar calistirilir (aradaki zamanda baska bir yayin kurali
   bozmus olabilir); zaman ileri alinmissa yayin otomatik ertelenir.
2. Kanalda OAuth baglantisi yoksa yayin 6 saat ileri alinir ve kural notunda
   gerekli adim aciklanir.
3. Yukleme basarisizsa en fazla 3 deneme yapilir; sonrasinda kalici hata olur.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Publication
from app.services.rules import evaluate_publication
from app.services.youtube import YouTubeError, YouTubeUploader

MAX_ATTEMPTS = 3
OAUTH_RETRY_DELAY = timedelta(hours=6)
TRANSIENT_RETRY_DELAY = timedelta(minutes=15)


def _file_path_for(publication) -> str:
    content = publication.content
    if content and content.file_path:
        return content.file_path
    return ""


def process_due_publications(db: Session, *, uploader_factory=None) -> dict:
    """
    Zamani gelmis yayinlari isler ve ozet dondurur.

    `uploader_factory(refresh_token)` sadece testlerde sahte yukleyici vermek
    icin kullanilir; uretimde gercek YouTubeUploader olusturulur.
    """
    now = datetime.now(timezone.utc)
    due = db.execute(
        select(Publication).where(
            Publication.status == "pending",
            Publication.scheduled_at <= now,
        )
    ).scalars().all()

    summary = {"processed": 0, "published": 0, "rescheduled": 0, "failed": 0}

    for publication in due:
        summary["processed"] += 1
        content = publication.content
        channel = publication.channel
        if content is None or channel is None:
            publication.status = "failed"
            publication.last_error = "Icerik veya kanal kaydi bulunamadi."
            continue

        # 1) Kurallar tekrar degerlendirilir.
        outcome = evaluate_publication(
            db,
            content,
            channel,
            publication.scheduled_at,
            exclude_publication_id=publication.id,
        )
        if outcome.blocked:
            publication.status = "failed"
            publication.last_error = " ".join(outcome.reasons)
            summary["failed"] += 1
            continue
        if outcome.scheduled_at > now:
            publication.scheduled_at = outcome.scheduled_at
            publication.rule_note = " ".join(outcome.reasons)
            summary["rescheduled"] += 1
            continue

        # 2) OAuth kontrolu.
        if not channel.refresh_token:
            publication.scheduled_at = now + OAUTH_RETRY_DELAY
            publication.rule_note = (
                "Kanal icin YouTube baglantisi yok; OAuth adimini tamamlayin. "
                "Yayin 6 saat sonra tekrar denenecek."
            )
            summary["rescheduled"] += 1
            continue

        file_path = _file_path_for(publication)
        if not file_path or not os.path.isfile(file_path):
            publication.status = "failed"
            publication.last_error = (
                f"Icerik dosyasi bulunamadi: {file_path or '(bos)'}. "
                "Icerigi yeniden yukleyin."
            )
            summary["failed"] += 1
            continue

        # 3) Yukleme.
        try:
            if uploader_factory is None:
                uploader_factory = YouTubeUploader
            uploader = uploader_factory(channel.refresh_token)
            video_id = uploader.upload(
                file_path,
                title=content.title,
                description=content.description,
                tags=list(content.tags or []),
                privacy_status=str(
                    (channel.settings or {}).get("privacy_status", "private")
                ),
            )
            publication.status = "published"
            publication.published_at = datetime.now(timezone.utc)
            publication.youtube_video_id = video_id
            publication.last_error = ""
            publication.rule_note = ""
            summary["published"] += 1
        except YouTubeError as exc:
            publication.attempts += 1
            publication.last_error = str(exc)
            if publication.attempts >= MAX_ATTEMPTS:
                publication.status = "failed"
                summary["failed"] += 1
            else:
                publication.scheduled_at = now + TRANSIENT_RETRY_DELAY
                summary["rescheduled"] += 1

    db.commit()
    return summary
