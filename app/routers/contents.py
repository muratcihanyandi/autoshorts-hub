"""
Icerik kutuphanesi.

Icerikler iki sekilde eklenir:
- Yuklenen video dosyasi (fingerprint = dosyanin sha256 ozeti)
- Harici referans (fingerprint = normallestirilmis baslik+aciklama ozeti)

Ayni parmak izine sahip icerik ikinci kez eklenemez; kural motoru "ayni icerik"
tespitini bu parmak izi uzerinden yapar.
"""

from __future__ import annotations

import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select

from app.auth import require_session
from app.config import settings
from app.db import get_db
from app.models import Content
from app.schemas import ContentCreate, content_dict
from app.services.rules import content_fingerprint

router = APIRouter(
    prefix="/api/contents", tags=["contents"], dependencies=[Depends(require_session)]
)

_UPLOAD_CHUNK = 1024 * 1024
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


def _find_by_fingerprint(db, fingerprint: str):
    return db.execute(
        select(Content).where(Content.fingerprint == fingerprint)
    ).scalars().first()


@router.get("")
def list_contents(db=Depends(get_db)):
    contents = db.execute(select(Content)).scalars().all()
    return {"contents": [content_dict(c) for c in contents]}


@router.post("", status_code=201)
def create_content(body: ContentCreate, db=Depends(get_db)):
    if not (body.title or "").strip():
        raise HTTPException(status_code=400, detail="Başlık zorunlu.")

    fingerprint = content_fingerprint(
        title=body.title, description=body.description
    )
    existing = _find_by_fingerprint(db, fingerprint)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Aynı içerik zaten kayıtlı: {existing.title} "
            f"(#{existing.id}). Kural motoru ikisini aynı içerik sayar.",
        )

    content = Content(
        fingerprint=fingerprint,
        group_key=(body.group_key or "").strip(),
        title=body.title.strip(),
        description=body.description or "",
        tags=list(body.tags or []),
        notes=body.notes or "",
        source_kind="reference",
    )
    db.add(content)
    db.commit()
    return {"content": content_dict(content)}


@router.post("/upload", status_code=201)
async def upload_content(
    file: UploadFile,
    title: str = "",
    description: str = "",
    group_key: str = "",
    notes: str = "",
    db=Depends(get_db),
):
    """
    Video dosyasini yukler ve icerik kaydi olusturur.

    Dosya akis seklinde okunur; sha256 ozeti okuma sirasinda hesaplanir, bu
    yuzden Raspberry Pi'de buyuk dosyalar ekstra bellek kullanmaz.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Dosya seçilmedi.")

    os.makedirs(settings.uploads_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{os.path.basename(file.filename)}"
    target_path = os.path.join(settings.uploads_dir, safe_name)

    digest = hashlib.sha256()
    total = 0
    try:
        with open(target_path, "wb") as out:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413, detail="Dosya boyutu 2 GB sınırını aşıyor."
                    )
                digest.update(chunk)
                out.write(chunk)
    except HTTPException:
        if os.path.exists(target_path):
            os.remove(target_path)
        raise
    except OSError as exc:
        if os.path.exists(target_path):
            os.remove(target_path)
        raise HTTPException(status_code=500, detail=f"Dosya yazılamadı: {exc}")

    file_sha256 = digest.hexdigest()
    fingerprint = content_fingerprint(file_sha256=file_sha256)
    existing = _find_by_fingerprint(db, fingerprint)
    if existing is not None:
        os.remove(target_path)
        raise HTTPException(
            status_code=409,
            detail=f"Birebir aynı dosya zaten kayıtlı: {existing.title} "
            f"(#{existing.id}).",
        )

    final_title = (title or "").strip() or os.path.splitext(file.filename)[0]
    content = Content(
        fingerprint=fingerprint,
        group_key=(group_key or "").strip(),
        title=final_title,
        description=description or "",
        notes=notes or "",
        source_kind="upload",
        file_path=target_path,
        file_sha256=file_sha256,
        file_size=total,
    )
    db.add(content)
    db.commit()
    return {"content": content_dict(content)}


@router.delete("/{content_id}")
def delete_content(content_id: int, db=Depends(get_db)):
    content = db.get(Content, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="İçerik bulunamadı.")

    # Dosya sadece kendi yukleme klasorumuzdeyse silinir; harici referanslara
    # (source_kind=reference) dokunulmaz.
    removed_file = False
    if content.file_path and content.file_path.startswith(
        os.path.abspath(settings.uploads_dir)
    ):
        if os.path.exists(content.file_path):
            os.remove(content.file_path)
            removed_file = True

    db.delete(content)
    db.commit()
    return {"ok": True, "file_removed": removed_file}
