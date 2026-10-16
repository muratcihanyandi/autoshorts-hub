"""
YouTube Data API v3 sarmalayicisi.

Iki ayri yetki modeli vardir:
- Okuma (kanal/video istatistigi): API anahtari ile `YouTubeAPI`.
- Yazma (video yukleme): kanal sahibi OAuth anahtari ile `YouTubeUploader`.

Kota notu: okuma cagrilari 1 birim, video yuklemesi 1600 birim maliyetlidir.
Gunluk varsayilan kota 10000 birimdir; 30 dakikalik toplama dongusu ve gunluk
3 yukleme bu butceye rahat sigar.
"""

from __future__ import annotations

import re
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.config import settings

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class YouTubeError(RuntimeError):
    """Kullaniciya gosterilebilir YouTube hatasi."""


def parse_iso_duration(value: str) -> int:
    """ISO 8601 sureyi (PT4M13S) saniyeye cevirir."""
    match = _ISO_DURATION.match(value or "")
    if not match:
        return 0
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    return (
        parts.get("days", 0) * 86400
        + parts.get("hours", 0) * 3600
        + parts.get("minutes", 0) * 60
        + parts.get("seconds", 0)
    )


def _http_error_message(exc: HttpError) -> str:
    status = getattr(exc, "resp", None)
    code = getattr(status, "status", 0) if status else 0
    if code == 403:
        return (
            "YouTube API kotasi doldu (403). Toplama dongusu gun icinde "
            "devam edecek; kotayi azaltmak icin POLL_INTERVAL_MINUTES degerini "
            "buyutebilirsiniz."
        )
    if code == 400:
        return f"YouTube API gecersiz istek (400): {exc}"
    return f"YouTube API hatasi ({code}): {exc}"


class YouTubeAPI:
    """API anahtari ile genel (herkese acik) veri okuma."""

    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key or settings.youtube_api_key).strip()
        if not self.api_key:
            raise YouTubeError(
                "YOUTUBE_API_KEY tanimli degil. YouTube Data API v3 anahtarini "
                "ortam degiskeni olarak verin."
            )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = build(
                "youtube", "v3", developerKey=self.api_key, cache_discovery=False
            )
        return self._client

    def resolve_channel(self, ref: str) -> dict[str, Any]:
        """
        Kanal referansini (ID / @handle / kullanici adi) kanal kaydina cevirir.

        search.list kullanimindan bilincli olarak kacinilir (100 birim kota);
        bunun yerine dusuk maliyetli channels.list denemeleri yapilir.
        """
        ref = (ref or "").strip()
        if not ref:
            raise YouTubeError("Kanal referansi bos.")

        # Tam URL girildiyse icinden handle/ID cek.
        if "/" in ref:
            tail = ref.rstrip("/").rsplit("/", 1)[-1]
            if tail:
                ref = tail

        attempts: list[tuple[str, str]] = []
        if ref.startswith("UC") and len(ref) >= 20:
            attempts.append(("id", ref))
        if ref.startswith("@"):
            attempts.append(("forHandle", ref[1:]))
        attempts.append(("forHandle", ref.lstrip("@")))
        if not attempts:
            attempts.append(("forUsername", ref))

        for param, value in attempts:
            try:
                resp = (
                    self.client.channels()
                    .list(
                        part="snippet,contentDetails,statistics",
                        **{param: value},
                    )
                    .execute()
                )
            except HttpError as exc:
                if exc.resp is not None and exc.resp.status in (400, 403):
                    continue
                raise YouTubeError(_http_error_message(exc)) from exc
            items = resp.get("items", [])
            if items:
                return self._channel_from_item(items[0])

        raise YouTubeError(
            "Kanal bulunamadi. Kanal ID'si (UC... baslangicli) veya @handle "
            "girdiginizden emin olun."
        )

    @staticmethod
    def _channel_from_item(item: dict) -> dict[str, Any]:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        thumbnails = snippet.get("thumbnails", {})
        thumb = ""
        for key in ("high", "medium", "default"):
            if key in thumbnails:
                thumb = thumbnails[key].get("url", "")
                break
        return {
            "yt_channel_id": item.get("id", ""),
            "title": snippet.get("title", ""),
            "handle": (snippet.get("customUrl", "") or "").lstrip("@"),
            "custom_url": snippet.get("customUrl", ""),
            "thumbnail_url": thumb,
            "uploads_playlist_id": content.get("relatedPlaylists", {}).get(
                "uploads", ""
            ),
            "subscribers": int(stats.get("subscriberCount", 0) or 0),
            "total_views": int(stats.get("viewCount", 0) or 0),
            "video_count": int(stats.get("videoCount", 0) or 0),
        }

    def channel_stats(self, channel_ids: list[str]) -> dict[str, dict]:
        """Birden fazla kanalin anlik istatistiklerini tek cagride getirir."""
        if not channel_ids:
            return {}
        try:
            resp = (
                self.client.channels()
                .list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(channel_ids),
                )
                .execute()
            )
        except HttpError as exc:
            raise YouTubeError(_http_error_message(exc)) from exc

        result = {}
        for item in resp.get("items", []):
            data = self._channel_from_item(item)
            result[data["yt_channel_id"]] = data
        return result

    def uploads_page(
        self, playlist_id: str, page_token: str | None = None
    ) -> tuple[list[dict], str | None]:
        """Yukleme oynatma listesinin bir sayfasini dondurur (en yeni once)."""
        try:
            resp = (
                self.client.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            raise YouTubeError(_http_error_message(exc)) from exc

        items = []
        for entry in resp.get("items", []):
            snippet = entry.get("snippet", {})
            details = entry.get("contentDetails", {})
            thumb = ""
            for key in ("high", "medium", "default"):
                if key in snippet.get("thumbnails", {}):
                    thumb = snippet["thumbnails"][key].get("url", "")
                    break
            items.append(
                {
                    "video_id": details.get("videoId", ""),
                    "title": snippet.get("title", ""),
                    "published_at": snippet.get("publishedAt", ""),
                    "thumbnail_url": thumb,
                }
            )
        return items, resp.get("nextPageToken")

    def video_details(self, video_ids: list[str]) -> list[dict]:
        """Video surelerini ve anlik istatistiklerini getirir (50'li gruplar)."""
        result = []
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start : start + 50]
            if not batch:
                continue
            try:
                resp = (
                    self.client.videos()
                    .list(
                        part="snippet,contentDetails,statistics",
                        id=",".join(batch),
                    )
                    .execute()
                )
            except HttpError as exc:
                raise YouTubeError(_http_error_message(exc)) from exc
            for item in resp.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                details = item.get("contentDetails", {})
                duration = parse_iso_duration(details.get("duration", ""))
                result.append(
                    {
                        "video_id": item.get("id", ""),
                        "channel_id": snippet.get("channelId", ""),
                        "title": snippet.get("title", ""),
                        "published_at": snippet.get("publishedAt", ""),
                        "duration_sec": duration,
                        "views": int(stats.get("viewCount", 0) or 0),
                        "likes": int(stats.get("likeCount", 0) or 0),
                        "comments": int(stats.get("commentCount", 0) or 0),
                        # Shorts tespiti: 65 sn ve alti (1 dakikalik Shorts payi).
                        "is_short": 0 < duration <= 65,
                    }
                )
        return result


def build_auth_flow(redirect_uri: str) -> Flow:
    """Kullaniciyi YouTube'a yonlendirmek icin OAuth akisi olusturur."""
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise YouTubeError(
            "YOUTUBE_CLIENT_ID ve YOUTUBE_CLIENT_SECRET tanimli degil. Google "
            "Cloud Console'dan OAuth istemcisi olusturup ortam degiskenlerini "
            "ayarlayin."
        )
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=YOUTUBE_SCOPES,
        redirect_uri=redirect_uri,
    )


class YouTubeUploader:
    """Kanalin OAuth yenileme anahtariyla video yukler."""

    def __init__(self, refresh_token: str):
        if not refresh_token:
            raise YouTubeError("Kanal icin OAuth baglantisi yok.")
        self._refresh_token = refresh_token

    def _credentials(self) -> Credentials:
        creds = Credentials(
            token=None,
            refresh_token=self._refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.youtube_client_id,
            client_secret=settings.youtube_client_secret,
            scopes=YOUTUBE_SCOPES,
        )
        creds.refresh(GoogleAuthRequest())
        return creds

    def upload(
        self,
        file_path: str,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        privacy_status: str = "private",
        publish_at: str | None = None,
    ) -> str:
        """
        Videoyu yukler ve YouTube video kimligini dondurur.

        Gorunurluk varsayilan olarak ozeldir (private); zamanlanmis yayin icin
        publish_at ISO-8601 UTC olarak verilebilir.
        """
        creds = self._credentials()
        client = build("youtube", "v3", credentials=creds, cache_discovery=False)

        body: dict[str, Any] = {
            "snippet": {
                "title": title,
                "description": description or "",
                "tags": tags or [],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": privacy_status or "private",
                "selfDeclaredMadeForKids": False,
            },
        }
        if publish_at:
            body["status"]["publishAt"] = publish_at
            body["status"]["privacyStatus"] = "private"

        media = MediaFileUpload(file_path, resumable=True, chunksize=8 * 1024 * 1024)
        try:
            request = client.videos().insert(
                part="snippet,status", body=body, media_body=media
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
            return response.get("id", "")
        except HttpError as exc:
            raise YouTubeError(_http_error_message(exc)) from exc
