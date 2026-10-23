"""観測値の検証（DB 非依存の純粋関数）。不正値は補完せず Finding として返す。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from slot_data.domain.enums import FieldStatus, Severity, XAxisType, YAxisType
from slot_data.domain.metrics import METRIC_BY_NAME
from slot_data.quality.issues import Finding
from slot_data.schemas.parsed import ParsedBonusSeries, ParsedObservation, ParsedSlumpSeries


def observed_values(obs: ParsedObservation) -> dict[str, int]:
    return {
        k: f.value for k, f in obs.fields.items() if f.status == FieldStatus.OBSERVED and f.value is not None
    }


def validate_metrics(
    obs: ParsedObservation, business_day_start: datetime, warn_abs_limits: dict[str, int] | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    values = observed_values(obs)
    limits = warn_abs_limits or {}
    for name, value in values.items():
        metric = METRIC_BY_NAME[name]
        if metric.non_negative and value < 0:
            findings.append(
                Finding("NEGATIVE_VALUE", f"{name}={value}", field=name, details={"value": value})
            )
        limit = limits.get(name, metric.warn_abs_limit)
        if limit is not None and abs(value) > limit:
            findings.append(
                Finding(
                    "OUT_OF_RANGE", f"{name}={value} exceeds {limit}", field=name, details={"value": value}
                )
            )
    total, normal = values.get("total_games"), values.get("normal_games")
    if total is not None and normal is not None and normal > total:
        findings.append(Finding("NORMAL_GT_TOTAL", f"normal_games={normal} > total_games={total}"))
    # 最大・最小差枚は「定義が確か」なときだけ整合チェックする（定義が記録されている場合）
    max_f, min_f = obs.fields.get("max_net_medals"), obs.fields.get("min_net_medals")
    if (
        max_f is not None
        and min_f is not None
        and max_f.definition
        and min_f.definition
        and max_f.value is not None
        and min_f.value is not None
        and max_f.value < min_f.value
    ):
        findings.append(Finding("MAX_LT_MIN", f"max={max_f.value} < min={min_f.value}"))
    if obs.observed_at < business_day_start:
        findings.append(
            Finding(
                "BUSINESS_DATE_MISMATCH",
                f"observed_at {obs.observed_at.isoformat()} is before business day start {business_day_start.isoformat()}",
            )
        )
    return findings


def validate_bonus_series(series: ParsedBonusSeries) -> list[Finding]:
    findings: list[Finding] = []
    events = sorted(series.events, key=lambda e: e.ordinal)
    previous: int | None = None
    for event in events:
        if event.cumulative_games is None:
            continue
        if previous is not None and event.cumulative_games < previous:
            findings.append(
                Finding(
                    "BONUS_ORDER_INCONSISTENT",
                    f"ordinal {event.ordinal}: cumulative_games {event.cumulative_games} < {previous}",
                    details={"ordinal": event.ordinal},
                )
            )
        previous = event.cumulative_games
    for event in events:
        for name in ("hit_games", "cumulative_games", "payout_medals"):
            value = getattr(event, name)
            if value is not None and value < 0:
                findings.append(
                    Finding("NEGATIVE_VALUE", f"bonus ordinal {event.ordinal}: {name}={value}", field=name)
                )
    return findings


def validate_slump_series(series: ParsedSlumpSeries) -> list[Finding]:
    findings: list[Finding] = []
    if series.x_axis_type == XAxisType.UNKNOWN or series.y_axis_type == YAxisType.UNKNOWN:
        findings.append(Finding("SLUMP_AXIS_UNKNOWN", f"series {series.series_key}: axis type unknown"))
    elif series.y_axis_type == YAxisType.NET_MEDALS and not series.y_unit:
        findings.append(Finding("SLUMP_AXIS_UNKNOWN", f"series {series.series_key}: y unit unknown"))

    def x_of(point) -> Decimal | datetime | None:
        if series.x_axis_type == XAxisType.TIME:
            return point.x_timestamp
        return point.x_numeric

    xs = [x_of(p) for p in series.points]
    known = [x for x in xs if x is not None]
    if series.x_axis_type in (XAxisType.TIME, XAxisType.CUMULATIVE_GAMES, XAxisType.PIXEL):
        for i in range(1, len(known)):
            if known[i] < known[i - 1]:
                findings.append(
                    Finding("SLUMP_X_NOT_MONOTONIC", f"series {series.series_key}: x decreases at point {i}")
                )
                break
    if len(set(known)) != len(known):
        findings.append(
            Finding(
                "SLUMP_DUPLICATE_X",
                f"series {series.series_key}: duplicate x values (kept as-is)",
                severity=Severity.INFO,
            )
        )
    return findings
