"""
Zaman yardimcilari.

PostgreSQL datetime'leri timezone bilgisiyle doner; SQLite (test ortami) ise
naive dondurur. Tum karsilastirmalarin her iki ortamda da dogru calismasi icin
naive degerler UTC kabul edilir.
"""

from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(value: datetime | None) -> datetime | None:
    """Naive datetime'i UTC kabul edip farkinda (aware) UTC degerine cevirir."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
