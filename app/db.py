"""
Veritabani baglantisi ve oturum yonetimi.

Uygulama uretimde PostgreSQL kullanir; testlerde ayni modelleri SQLite uzerinde
calistirmak icin engine olusturma tek bir noktadan gecer.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# SQLite (test) ile PostgreSQL (uretim) arasindaki farklar:
# - SQLite baglanti havuzu kullanmaz, check_same_thread kapali olmali.
# - JSON alani iki tarafta da calisir (sa.JSON tasinabilirdir).
_engine = None
_session_factory = None


class Base(DeclarativeBase):
    pass


def get_engine():
    global _engine
    if _engine is None:
        settings.require_database()
        url = settings.database_url
        if url.startswith("sqlite"):
            _engine = create_engine(
                url,
                connect_args={"check_same_thread": False},
                future=True,
            )
        else:
            _engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_recycle=1800,
                future=True,
            )
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), expire_on_commit=False, future=True
        )
    return _session_factory


def get_db():
    """FastAPI bagimliligi: istek basina bir oturum acar ve kapatir."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """Arka plan gorevleri icin islem kapsami: hata olursa geri alir."""
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Tablolari olusturur (yoksa). Yeni kurulumda tek cagrida yeterlidir."""
    from app import models  # noqa: F401 - modeller Base'e kayit olur

    Base.metadata.create_all(bind=get_engine())
