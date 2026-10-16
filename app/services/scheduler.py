"""
Arka plan zamanlayici.

Uygulama 7/24 acik kalir; APScheduler iki donguyu yurutur:
- collect_all_channels : kanal/video istatistiklerini toplar (varsayilan 30 dk)
- process_due          : zamani gelen yayinlari isler (varsayilan 5 dk)

Gorevler kacirma toleransli (misfire_grace_time) ve tek ornekli
(max_instances=1) calisir; uzun suren bir toplama dongusu bir sonrakini
bloklamaz, ayni gorevin ust uste binmesini engeller.
"""

from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db import session_scope
from app.models import JobRun
from app.services.collector import collect_all_channels, prune_old_stats
from app.services.publisher import process_due_publications
from app.services.youtube import YouTubeAPI, YouTubeError

_scheduler: BackgroundScheduler | None = None


def _run_job(job_name: str, callable_) -> dict:
    with session_scope() as db:
        run = JobRun(job_name=job_name, status="running")
        db.add(run)
        db.commit()

        started = datetime.now(timezone.utc)
        try:
            result = callable_(db) or {}
        except Exception as exc:  # beklenmeyen hata uygulamayi durdurmamali
            db.rollback()
            run.status = "failed"
            run.detail = {"error": str(exc)[:2000]}
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"error": str(exc)}

        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        run.detail = _jsonable(result)
        db.commit()
        return result


def _jsonable(value):
    """Sonucu JSON kolonuna yazilabilir hale getirir."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def job_collect_all(db) -> dict:
    """Tum aktif kanallarin istatistiklerini toplar."""
    try:
        api = YouTubeAPI()
    except YouTubeError as exc:
        return {"error": str(exc)}
    return collect_all_channels(db, api=api)


def job_process_due(db) -> dict:
    """Zamani gelmis yayinlari isler."""
    return process_due_publications(db)


def collect_job() -> dict:
    return _run_job("collect_all_channels", job_collect_all)


def publish_job() -> dict:
    return _run_job("process_due_publications", job_process_due)


def job_prune_stats(db) -> dict:
    """Eski anlik istatistikleri gune bir olacak sekilde budar."""
    return prune_old_stats(db)


def prune_job() -> dict:
    return _run_job("prune_old_stats", job_prune_stats)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=settings.timezone)

    _scheduler.add_job(
        collect_job,
        trigger=IntervalTrigger(minutes=max(5, settings.poll_interval_minutes)),
        id="collect_all_channels",
        name="Kanal istatistik toplama",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        next_run_time=datetime.now(timezone.utc),
    )

    _scheduler.add_job(
        publish_job,
        trigger=IntervalTrigger(minutes=max(1, settings.publish_check_interval_minutes)),
        id="process_due_publications",
        name="Yayin kuyrugu isleme",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    # Veritabani buyumesini kontrol altinda tutan gunluk bakim (gece 04:10).
    _scheduler.add_job(
        prune_job,
        trigger=CronTrigger(hour=4, minute=10, timezone=settings.timezone),
        id="prune_old_stats",
        name="Eski istatistik budama",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
