"""
Veritabani modelleri (PostgreSQL).

Tasarim notlari:
- Tum zaman damgalari UTC olarak saklanir; gosterim katmani yerel saate cevirir.
- Zengin ayarlar (yayin penceresi, RPM gibi) kanal basina JSON yerine ayri kolonlar
  olarak tutulur ki kural motoru bunlara dogrudan SQL ile erisebilsin.
- Istatistikler ekle-only (append-only) tablolarda zaman serisi olarak birikir;
  gecmis asla uzerine yazilmaz, boylece gelir tahmini geriye donuk hesaplanabilir.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Channel(Base):
    """Takip edilen YouTube kanali."""

    __tablename__ = "channels"

    yt_channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    handle: Mapped[str] = mapped_column(String(128), default="")
    custom_url: Mapped[str] = mapped_column(String(255), default="")
    thumbnail_url: Mapped[str] = mapped_column(Text, default="")
    uploads_playlist_id: Mapped[str] = mapped_column(String(64), default="")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Gelir tahmini ve yayin kurallari kanal bazinda ayarlanabilir.
    content_type: Mapped[str] = mapped_column(String(16), default="mixed")
    rpm: Mapped[float] = mapped_column(Float, default=2.0)
    daily_upload_limit: Mapped[int] = mapped_column(Integer, default=3)

    # Yayin penceresi: gunun kacinci dakikasi (0-1439). Ayni aralik her gun gecerlidir.
    publish_window_start: Mapped[int] = mapped_column(Integer, default=17 * 60)
    publish_window_end: Mapped[int] = mapped_column(Integer, default=21 * 60)

    # Video yuklemek icin kanal sahibinin OAuth yenileme anahtari.
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="")

    stats: Mapped[list["ChannelStats"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )

    @property
    def has_oauth(self) -> bool:
        return bool(self.refresh_token)


class ChannelStats(Base):
    """Kanal ozeti anlik goruntusu (abone, toplam izlenme, video sayisi)."""

    __tablename__ = "channel_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.yt_channel_id", ondelete="CASCADE"), index=True
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    subscribers: Mapped[int] = mapped_column(Integer, default=0)
    total_views: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)

    channel: Mapped[Channel] = relationship(back_populates="stats")


class Video(Base):
    """Kanalin bir videosu (kesfedildigi anda kaydedilir)."""

    __tablename__ = "videos"

    video_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.yt_channel_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_url: Mapped[str] = mapped_column(Text, default="")
    # Shorts tespiti sure bazli yapilir (65 sn ve alti).
    is_short: Mapped[bool] = mapped_column(Boolean, default=False)

    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    stats: Mapped[list["VideoStats"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )


class VideoStats(Base):
    """Video istatistik anlik goruntusu (izlenme, begeni, yorum)."""

    __tablename__ = "video_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.video_id", ondelete="CASCADE"), index=True
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)

    video: Mapped[Video] = relationship(back_populates="stats")


class Content(Base):
    """
    Yayinlanabilir icerik kaydi.

    fingerprint, icerigi benzersiz tanimlayan ozettir (sha256). Kural motoru
    "ayni icerik" kontrolunu bu deger uzerinden yapar. group_key ile ayni ailenin
    varyantlari (orn. ayni konunun farkli montaji) tek grupta toplanabilir.
    """

    __tablename__ = "contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "meta:"/"file:" oneki + 64 hex karakter = 70+ bayt; SQLite uzunlugu
    # zorlamadigi icin bu tur hatalar sadece PostgreSQL'de ortaya cikar.
    fingerprint: Mapped[str] = mapped_column(String(96), index=True)
    group_key: Mapped[str] = mapped_column(String(128), default="", index=True)

    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)

    source_kind: Mapped[str] = mapped_column(String(16), default="reference")
    file_path: Mapped[str] = mapped_column(Text, default="")
    file_sha256: Mapped[str] = mapped_column(String(64), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)

    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    publications: Mapped[list["Publication"]] = relationship(
        back_populates="content", cascade="all, delete-orphan"
    )


class Publication(Base):
    """
    Bir icerigin bir kanala yayinlanma plani.

    Durumlar:
    - pending   : zamanlanmis, sirada
    - published : YouTube'a yuklenmis
    - failed    : kalici hata (oturum/OAuth/limit)
    - cancelled : kullanicinin iptali
    """

    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("content_id", "channel_id", name="uq_publication_content_channel"),
        Index(
            "ix_publications_due",
            "status",
            "scheduled_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.yt_channel_id", ondelete="CASCADE"), index=True
    )

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    youtube_video_id: Mapped[str] = mapped_column(String(32), default="")

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    # Kural motorunun son kararini kaydet: kullanici arayuzde neden ertelendigini gorur.
    rule_note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    content: Mapped[Content] = relationship(back_populates="publications")
    channel: Mapped[Channel] = relationship()


class AppSetting(Base):
    """Uygulama geneli ayarlar (kural parametreleri, API durumu vb.)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobRun(Base):
    """Arka plan gorev calisma kaydi (7/24 saglik takibi icin)."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="running")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Varsayilan uygulama ayarlari
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict = {
    # Ayni icerik FARKLI kanallara en fazla bu kadar siklikla gider.
    "cross_channel_gap_days": 7,
    # Ayni kanala ayni icerigin tekrari icin gereken minimum gun.
    "same_channel_repeat_days": 90,
    # Kanal bazinda gunluk yukleme siniri (kanal uzerinden asilabilir).
    "default_daily_upload_limit": 3,
    # Gelir tahmini icin varsayilan RPM (1000 izlenme basina USD).
    "default_rpm": 2.0,
    "default_shorts_rpm": 0.15,
}


def get_settings_value(db, key: str, default=None):
    """Ayarlar tablosundan deger okur; yoksa kod icindeki varsayilani dondurur."""
    row = db.get(AppSetting, "app")
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row.value.get(key, DEFAULT_SETTINGS.get(key, default))
