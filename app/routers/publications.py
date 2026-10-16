"""
Yayin plani (kuyruk) yonetimi.

Iki planlama modu vardir:
- Tek tek: her kanal icin istenen zaman kural motorundan gecer; kurallar zamanı
  ileri tasirsa yayin otomatik ertelenir ve kural notu kayda yazilir.
- Otomatik dagitim: ayni icerik secilen kanallara kademeli dagitilir; kanallar
  arasi bosluk en az `cross_channel_gap_days` (varsayilan 7 gun) olur.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.auth import require_session
from app.db import get_db
from app.models import Channel, Content, Publication
from app.schemas import (
    PublicationAutoPlan,
    PublicationCreate,
    publication_dict,
)
from app.services.rules import evaluate_publication, suggest_plan

router = APIRouter(
    prefix="/api/publications",
    tags=["publications"],
    dependencies=[Depends(require_session)],
)


def _get_content(db, content_id: int) -> Content:
    content = db.get(Content, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="İçerik bulunamadı.")
    return content


def _get_channels(db, channel_ids: list[str]) -> list[Channel]:
    if not channel_ids:
        raise HTTPException(status_code=400, detail="En az bir kanal seçin.")
    channels = (
        db.execute(select(Channel).where(Channel.yt_channel_id.in_(channel_ids)))
        .scalars()
        .all()
    )
    found = {c.yt_channel_id for c in channels}
    missing = [cid for cid in channel_ids if cid not in found]
    if missing:
        raise HTTPException(
            status_code=404, detail=f"Kanal bulunamadı: {', '.join(missing)}"
        )
    return channels


def _existing_publication(db, content_id: int, channel_id: str):
    return (
        db.execute(
            select(Publication).where(
                Publication.content_id == content_id,
                Publication.channel_id == channel_id,
            )
        )
        .scalars()
        .first()
    )


@router.get("")
def list_publications(status: str = "", db=Depends(get_db)):
    query = select(Publication).order_by(Publication.scheduled_at.desc()).limit(500)
    if status:
        query = query.where(Publication.status == status)
    pubs = db.execute(query).scalars().all()
    return {"publications": [publication_dict(p) for p in pubs]}


@router.post("", status_code=201)
def create_publications(body: PublicationCreate, db=Depends(get_db)):
    content = _get_content(db, body.content_id)
    channels = _get_channels(db, body.channel_ids)
    base_at = body.at or datetime.now(timezone.utc)

    created = []
    skipped = []
    for channel in channels:
        existing = _existing_publication(db, content.id, channel.yt_channel_id)
        if existing is not None:
            skipped.append(
                {
                    "channel_id": channel.yt_channel_id,
                    "channel_title": channel.title,
                    "reason": "Bu içerik bu kanal için zaten planlanmış.",
                    "existing": publication_dict(existing),
                }
            )
            continue

        outcome = evaluate_publication(db, content, channel, base_at)
        if outcome.blocked:
            skipped.append(
                {
                    "channel_id": channel.yt_channel_id,
                    "channel_title": channel.title,
                    "reason": " ".join(outcome.reasons),
                }
            )
            continue

        publication = Publication(
            content_id=content.id,
            channel_id=channel.yt_channel_id,
            status="pending",
            scheduled_at=outcome.scheduled_at,
            rule_note=" ".join(outcome.reasons),
        )
        db.add(publication)
        db.flush()
        created.append(
            {
                "publication": publication_dict(publication),
                "adjusted": not outcome.ok,
                "reasons": outcome.reasons,
            }
        )

    db.commit()
    return {"created": created, "skipped": skipped}


@router.post("/auto", status_code=201)
def auto_plan(body: PublicationAutoPlan, db=Depends(get_db)):
    """
    Ayni icerigi secilen kanallara kural motoruna uygun kademeli dagitir.

    Kanallar arasi bosluk en az cross_channel_gap_days olur; boylece ayni icerik
    iki kanalda ayni gun kesinlikle yayinlanmaz.
    """
    content = _get_content(db, body.content_id)
    channels = _get_channels(db, body.channel_ids)
    start_at = body.start_at or datetime.now(timezone.utc)

    created = []
    skipped = []
    for channel, outcome in suggest_plan(db, content, channels, start_at):
        existing = _existing_publication(db, content.id, channel.yt_channel_id)
        if existing is not None:
            skipped.append(
                {
                    "channel_id": channel.yt_channel_id,
                    "channel_title": channel.title,
                    "reason": "Bu içerik bu kanal için zaten planlanmış.",
                }
            )
            continue
        if outcome.blocked:
            skipped.append(
                {
                    "channel_id": channel.yt_channel_id,
                    "channel_title": channel.title,
                    "reason": " ".join(outcome.reasons),
                }
            )
            continue

        publication = Publication(
            content_id=content.id,
            channel_id=channel.yt_channel_id,
            status="pending",
            scheduled_at=outcome.scheduled_at,
            rule_note=" ".join(outcome.reasons),
        )
        db.add(publication)
        db.flush()
        created.append(
            {
                "publication": publication_dict(publication),
                "adjusted": not outcome.ok,
                "reasons": outcome.reasons,
            }
        )

    db.commit()
    return {"created": created, "skipped": skipped}


@router.delete("/{publication_id}")
def cancel_publication(publication_id: int, db=Depends(get_db)):
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Yayın planı bulunamadı.")

    if publication.status == "published":
        raise HTTPException(
            status_code=400,
            detail="Yayınlanmış kayıt iptal edilemez; YouTube üzerinde yönetin.",
        )
    db.delete(publication)
    db.commit()
    return {"ok": True}
