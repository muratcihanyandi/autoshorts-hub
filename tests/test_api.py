"""
API uçtan uca (smoke) testleri.

YouTube'a giden cagrilar mock'lanir; kural motoru ve veri akisi gercek calisir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import make_channel, make_content, make_publication
from app.models import Channel, Publication
from app.timeutil import ensure_utc


def test_health_is_public(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["database"] is True


def test_web_ui_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "AutoShorts Hub" in res.text
    assert "chart.umd.js" in res.text


def test_login_wall(client, password_protected):
    # Parola aktifken korumali uc nokta 401 dondurur.
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/overview").status_code == 401

    # Yanlis parola reddedilir.
    res = client.post("/api/auth/login", json={"password": "yanlis"})
    assert res.status_code == 401

    # Dogru parola oturum acar ve korumali uc nokta erisilebilir olur.
    res = client.post("/api/auth/login", json={"password": "gizli-parola"})
    assert res.status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_channel_add_requires_api_key(client):
    res = client.post("/api/channels", json={"ref": "@kanal"})
    assert res.status_code == 400
    assert "API_KEY" in res.json()["detail"]


def test_channel_settings_validation(client, db):
    ch = make_channel(db)
    res = client.patch(
        "/api/channels/" + ch.yt_channel_id,
        json={"publish_window_start": 22 * 60, "publish_window_end": 20 * 60},
    )
    assert res.status_code == 400
    assert "pencere" in res.json()["detail"]

    res = client.patch(
        "/api/channels/" + ch.yt_channel_id,
        json={"rpm": 3.5, "daily_upload_limit": 5},
    )
    assert res.status_code == 200
    assert res.json()["channel"]["rpm"] == 3.5


def test_content_lifecycle(client, db):
    res = client.post(
        "/api/contents",
        json={"title": "Deneme", "description": "açıklama", "tags": ["oyun"]},
    )
    assert res.status_code == 201
    content_id = res.json()["content"]["id"]

    # Ayni baslik + aciklama ikinci kez eklenemez (ayni parmak izi).
    res = client.post(
        "/api/contents", json={"title": "Deneme", "description": "açıklama"}
    )
    assert res.status_code == 409

    # Liste ve silme.
    assert len(client.get("/api/contents").json()["contents"]) == 1
    assert client.delete("/api/contents/" + str(content_id)).status_code == 200
    assert len(client.get("/api/contents").json()["contents"]) == 0


def test_publication_auto_plan_spreads_channels(client, db):
    """API uzerinden otomatik plan: kanallar arasi >= 7 gun."""
    ch_a = make_channel(db, "UCtestAAAAAAAAAAAAAAAA")
    ch_b = make_channel(db, "UCtestBBBBBBBBBBBBBBBB")
    content = make_content(db)

    res = client.post(
        "/api/publications/auto",
        json={"content_id": content.id, "channel_ids": [ch_a.yt_channel_id, ch_b.yt_channel_id]},
    )
    assert res.status_code == 201
    created = res.json()["created"]
    assert len(created) == 2

    times = sorted(
        datetime.fromisoformat(c["publication"]["scheduled_at"]) for c in created
    )
    gap = times[1] - times[0]
    assert gap >= timedelta(days=7)

    # Kuyrukta gorunmeli.
    pubs = client.get("/api/publications").json()["publications"]
    assert len(pubs) == 2

    # Ayni icerik ayni kanala ikinci plan eklenemez.
    res = client.post(
        "/api/publications",
        json={"content_id": content.id, "channel_ids": [ch_a.yt_channel_id]},
    )
    assert res.status_code == 201
    assert len(res.json()["skipped"]) == 1

    # Iptal: pending kayit silinir.
    pub_id = pubs[0]["id"]
    assert client.delete("/api/publications/" + str(pub_id)).status_code == 200


def test_publisher_reschedules_when_rules_conflict(db):
    """Kural cakismasi yukleme aninda tekrar kontrol edilir ve ertelenir."""
    from app.services.publisher import process_due_publications

    ch_a = make_channel(db, "UCtestAAAAAAAAAAAAAAAA")
    ch_b = make_channel(db, "UCtestBBBBBBBBBBBBBBBB")
    content = make_content(db)

    # A kanalinda 1 saat once yayinlandi; B kanalindaki pending yayin su an.
    make_publication(
        db, content, ch_a,
        status="published",
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    pending = make_publication(
        db, content, ch_b,
        status="pending",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    summary = process_due_publications(db, uploader_factory=lambda token: (_ for _ in ()).throw(
        AssertionError("kural cakismasi varken yukleme denenmemeli")
    ))

    db.refresh(pending)
    assert pending.status == "pending", "cakisma varsa yayin denenmemeli"
    assert ensure_utc(pending.scheduled_at) > datetime.now(timezone.utc)
    assert summary["rescheduled"] >= 1
    assert "boşluk" in pending.rule_note


def test_settings_roundtrip(client):
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json()["settings"]["cross_channel_gap_days"] == 7

    res = client.put("/api/settings", json={"cross_channel_gap_days": 10})
    assert res.status_code == 200
    assert res.json()["settings"]["cross_channel_gap_days"] == 10

    # Kural motoru yeni degeri kullanmali.
    res = client.get("/api/settings")
    assert res.json()["settings"]["cross_channel_gap_days"] == 10


def test_overview_and_videos_flow(client, db):
    ch = make_channel(db, "UCtestAAAAAAAAAAAAAAAA", rpm=2.0)
    from app.models import Video, VideoStats

    now = datetime.now(timezone.utc)
    db.add(Video(video_id="vid_1", channel_id=ch.yt_channel_id, title="Video", published_at=now))
    db.add(VideoStats(video_id="vid_1", views=100, captured_at=now - timedelta(hours=4)))
    db.add(VideoStats(video_id="vid_1", views=180, captured_at=now - timedelta(hours=1)))
    db.add(Publication(
        content_id=make_content(db).id,
        channel_id=ch.yt_channel_id,
        status="pending",
        scheduled_at=now + timedelta(days=1),
    ))
    db.commit()

    overview = client.get("/api/overview").json()
    assert overview["totals"]["channels"] == 1
    assert overview["totals"]["tracked_videos"] == 1
    assert overview["publications"]["pending"] == 1
    assert overview["revenue"]["total_views"] == 80

    videos = client.get("/api/videos?days=7").json()["videos"]
    assert videos[0]["views"] == 180
    assert videos[0]["views_gained"] == 80
