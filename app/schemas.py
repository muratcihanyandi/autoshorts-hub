"""
API istek/giris semalari ve model serilestiricileri.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Istek govdesi modelleri
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    password: str


class ChannelCreate(BaseModel):
    ref: str = Field(description="Kanal ID (UC...), @handle veya kanal URL'si")


class ChannelUpdate(BaseModel):
    title: str | None = None
    is_active: bool | None = None
    content_type: str | None = None
    rpm: float | None = None
    daily_upload_limit: int | None = None
    publish_window_start: int | None = None
    publish_window_end: int | None = None


class ContentCreate(BaseModel):
    title: str
    description: str = ""
    tags: list[str] = []
    group_key: str = ""
    notes: str = ""


class PublicationCreate(BaseModel):
    content_id: int
    channel_ids: list[str]
    at: datetime | None = None


class PublicationAutoPlan(BaseModel):
    content_id: int
    channel_ids: list[str]
    start_at: datetime | None = None


class SettingsUpdate(BaseModel):
    cross_channel_gap_days: int | None = None
    same_channel_repeat_days: int | None = None
    default_daily_upload_limit: int | None = None
    default_rpm: float | None = None


# ---------------------------------------------------------------------------
# Serilestiriciler (model -> JSON sozlugu)
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def channel_dict(channel, *, include_token: bool = False) -> dict:
    data = {
        "yt_channel_id": channel.yt_channel_id,
        "title": channel.title,
        "handle": channel.handle,
        "thumbnail_url": channel.thumbnail_url,
        "is_active": channel.is_active,
        "content_type": channel.content_type,
        "rpm": channel.rpm,
        "daily_upload_limit": channel.daily_upload_limit,
        "publish_window_start": channel.publish_window_start,
        "publish_window_end": channel.publish_window_end,
        "has_oauth": bool(channel.refresh_token),
        "added_at": _iso(channel.added_at),
        "last_polled_at": _iso(channel.last_polled_at),
        "last_error": channel.last_error,
    }
    if include_token:
        data["refresh_token"] = channel.refresh_token
    return data


def content_dict(content, *, include_path: bool = False) -> dict:
    data = {
        "id": content.id,
        "fingerprint": content.fingerprint,
        "group_key": content.group_key,
        "title": content.title,
        "description": content.description,
        "tags": list(content.tags or []),
        "source_kind": content.source_kind,
        "file_size": content.file_size,
        "duration_sec": content.duration_sec,
        "notes": content.notes,
        "created_at": _iso(content.created_at),
        "has_file": bool(content.file_path),
    }
    if include_path:
        data["file_path"] = content.file_path
    return data


def publication_dict(publication) -> dict:
    return {
        "id": publication.id,
        "content_id": publication.content_id,
        "channel_id": publication.channel_id,
        "status": publication.status,
        "scheduled_at": _iso(publication.scheduled_at),
        "published_at": _iso(publication.published_at),
        "youtube_video_id": publication.youtube_video_id,
        "attempts": publication.attempts,
        "last_error": publication.last_error,
        "rule_note": publication.rule_note,
        "created_at": _iso(publication.created_at),
        "content_title": publication.content.title if publication.content else "",
        "channel_title": publication.channel.title if publication.channel else "",
    }


def video_dict(video, latest_stats=None) -> dict:
    return {
        "video_id": video.video_id,
        "channel_id": video.channel_id,
        "title": video.title,
        "published_at": _iso(video.published_at),
        "duration_sec": video.duration_sec,
        "is_short": video.is_short,
        "thumbnail_url": video.thumbnail_url,
        "views": latest_stats.views if latest_stats else 0,
        "likes": latest_stats.likes if latest_stats else 0,
        "comments": latest_stats.comments if latest_stats else 0,
    }
