"""
Test ortami: uygulama SQLite uzerinde, zamanlayici kapali calisir.

DATABASE_URL ortam degiskeni app modulleri import edilmeden once ayarlanmalidir;
bu yuzden bu dosyada importlar en ustte degil, ortam hazirlandiktan sonra yapilir.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Proje kokunu sys.path'e ekle (pytest kokten calistirilirsa zaten vardir).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# App paketi import edilmeden once ortam hazirlanir.
_TMP = tempfile.mkdtemp(prefix="hub-test-")
_DB_FILE = os.path.join(_TMP, "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_FILE.replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["TZ"] = "Europe/Istanbul"
# Varsayilan /data klasoru GitHub koşucusunda yazilabilir degil; testler
# ortamdan bagimsiz olmali.
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as app_settings  # noqa: E402
from app.db import Base, get_engine, init_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import (  # noqa: E402
    Channel,
    ChannelStats,
    Content,
    Publication,
    Video,
    VideoStats,
)


@pytest.fixture()
def client():
    """Her test temiz semayla baslar; zamanlayici kapalidir."""
    Base.metadata.drop_all(bind=get_engine())
    init_db()
    with TestClient(fastapi_app) as test_client:
        yield test_client
    Base.metadata.drop_all(bind=get_engine())


@pytest.fixture()
def db():
    """Dogrudan veritabani erisimi gerektiren testler icin oturum."""
    from app.db import get_session_factory

    Base.metadata.drop_all(bind=get_engine())
    init_db()
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
    Base.metadata.drop_all(bind=get_engine())


@pytest.fixture()
def password_protected():
    """Giris duvari acik/kapali test etmek icin gecici parola."""
    original = app_settings.app_password
    app_settings.app_password = "gizli-parola"
    yield app_settings
    app_settings.app_password = original


def make_channel(db, channel_id="UCtest1111111111111111", **kwargs) -> Channel:
    defaults = dict(
        yt_channel_id=channel_id,
        title="Test Kanalı",
        handle="testkanal",
        uploads_playlist_id="UU" + channel_id[2:],
    )
    defaults.update(kwargs)
    channel = Channel(**defaults)
    db.add(channel)
    db.commit()
    return channel


def make_content(db, title="Deneme içeriği", description="açıklama", **kwargs) -> Content:
    from app.services.rules import content_fingerprint

    content = Content(
        fingerprint=content_fingerprint(title=title, description=description),
        title=title,
        description=description,
        **kwargs,
    )
    db.add(content)
    db.commit()
    return content


def make_publication(db, content, channel, **kwargs) -> Publication:
    defaults = dict(
        content_id=content.id,
        channel_id=channel.yt_channel_id,
        status="published",
    )
    defaults.update(kwargs)
    publication = Publication(**defaults)
    db.add(publication)
    db.commit()
    return publication
