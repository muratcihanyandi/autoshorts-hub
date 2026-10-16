"""
Uygulama yapilandirmasi.

Tum ayarlar ortam degiskenlerinden okunur; boylece ayni imaj farkli kurulumlarda
(Pi 5 / CasaOS / yerel gelistirme) degistirilmeden calisir. Gizli degerler (API
anahtarlari, veritabani sifresi) kod icinde tutulmaz, .env uzerinden verilir.
"""

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class Settings:
    # PostgreSQL baglantisi. Uretimde mutlaka dolu olmali; bos birakilirsa
    # uygulama acilista net bir hata verir.
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "").strip()
    )

    # Web arayuzu sifresi. Bos birakilirsa giris iste girmez (sadece guvenli
    # yerel ag icin onerilir); deger verilirse tum /api istemleri oturum ister.
    app_password: str = field(
        default_factory=lambda: os.environ.get("APP_PASSWORD", "")
    )

    # YouTube Data API v3 anahtari (kanal/video istatistik okumasi icin).
    youtube_api_key: str = field(
        default_factory=lambda: os.environ.get("YOUTUBE_API_KEY", "").strip()
    )

    # YouTube'a video yuklemek icin OAuth istemcisi (Google Cloud Console).
    youtube_client_id: str = field(
        default_factory=lambda: os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    )
    youtube_client_secret: str = field(
        default_factory=lambda: os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    )

    # Yuklenen icerik dosyalari ve calisma verileri bu klasore yazilir.
    data_dir: str = field(
        default_factory=lambda: os.environ.get("DATA_DIR", "/data").strip() or "/data"
    )

    # Zamanlama ve is pencereleri bu zaman diliminde hesaplanir.
    timezone: str = field(
        default_factory=lambda: os.environ.get("TZ", "Europe/Istanbul").strip()
        or "Europe/Istanbul"
    )

    # Istatistik toplama dongusu (dakika). YouTube kotasinin buyuk kismi burada
    # harcanir: kanal basina dongu yaklasik 3-4 birim kullanir.
    poll_interval_minutes: int = field(
        default_factory=lambda: _env_int("POLL_INTERVAL_MINUTES", 30)
    )

    # Yayin kuyrugu isleme sikligi (dakika).
    publish_check_interval_minutes: int = field(
        default_factory=lambda: _env_int("PUBLISH_CHECK_INTERVAL_MINUTES", 5)
    )

    # Kanal basina istatistikleri takip edilen en yeni video sayisi. API cagri
    # sayisini ve veritabani boyutunu sinirlar.
    max_tracked_videos: int = field(
        default_factory=lambda: _env_int("MAX_TRACKED_VIDEOS", 200)
    )

    # OAuth yonlendirme adresi. Bos birakilirsa istegin geldigi adresten turetilir;
    # ters vekil arkasinda zorunlu olabilir (ornek: https://hub.example.com).
    oauth_redirect_url: str = field(
        default_factory=lambda: os.environ.get("OAUTH_REDIRECT_URL", "").strip()
    )

    # Oturum cerezini imzalamak icin kullanilir; sabit deger verilmezse her
    # acilista rastgele uretilir (restart sonrasi oturumlar duser, sorun degil).
    session_secret: str = field(
        default_factory=lambda: os.environ.get("SESSION_SECRET", "").strip()
    )

    debug: bool = field(default_factory=lambda: _env_bool("DEBUG", False))

    @property
    def uploads_dir(self) -> str:
        return os.path.join(self.data_dir, "uploads")

    def require_database(self) -> None:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL ortam degiskeni tanimli degil. "
                "Ornek: postgresql://kullanici:sifre@sunucu:5432/autoshorts_hub"
            )


settings = Settings()
