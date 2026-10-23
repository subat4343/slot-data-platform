"""時刻・営業日の計算。

保存は常に UTC の TIMESTAMPTZ、営業日の計算と表示は店舗のタイムゾーン（既定 Asia/Tokyo）で行う。
営業日境界 ``business_day_cutoff`` より前のローカル時刻は前日の営業日に属する。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Tokyo"
DEFAULT_CUTOFF = time(0, 0)


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime, tz: str = DEFAULT_TZ) -> datetime:
    """naive な datetime は店舗ローカル時刻とみなして tz を付与する。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(tz))
    return value


def business_date_of(moment: datetime, tz: str = DEFAULT_TZ, cutoff: time | None = None) -> date:
    local = ensure_aware(moment, tz).astimezone(ZoneInfo(tz))
    cutoff = cutoff or DEFAULT_CUTOFF
    if local.time() < cutoff:
        return local.date() - timedelta(days=1)
    return local.date()


def business_day_start(business_date: date, tz: str = DEFAULT_TZ, cutoff: time | None = None) -> datetime:
    cutoff = cutoff or DEFAULT_CUTOFF
    return datetime.combine(business_date, cutoff, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def business_day_end(business_date: date, tz: str = DEFAULT_TZ, cutoff: time | None = None) -> datetime:
    """営業日の終端（排他的）。翌日の営業日境界。"""
    return business_day_start(business_date + timedelta(days=1), tz, cutoff)


def closing_moment(
    business_date: date, closing_time: time | None, tz: str = DEFAULT_TZ, cutoff: time | None = None
) -> datetime:
    """閉店時刻（UTC）。未設定なら営業日終端。

    閉店時刻が営業日境界より前（例: 境界 05:00、閉店 00:30）の場合は翌日のローカル日付として扱う。
    """
    if closing_time is None:
        return business_day_end(business_date, tz, cutoff)
    cutoff = cutoff or DEFAULT_CUTOFF
    day = business_date + timedelta(days=1) if closing_time < cutoff else business_date
    return datetime.combine(day, closing_time, tzinfo=ZoneInfo(tz)).astimezone(UTC)
