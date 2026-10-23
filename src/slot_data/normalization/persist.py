"""解析結果（ParseResult）を観測・スナップショット・当たり履歴・スランプ系列として追記保存する。

既存の行は一切更新しない。旧系列を残したまま新系列を追加し、前回系列との差分は品質課題として記録する。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from slot_data.domain.enums import FieldStatus, QualityStatus, Severity
from slot_data.domain.ids import uuid7
from slot_data.domain.metrics import METRIC_NAMES
from slot_data.domain.timeutil import business_date_of, business_day_start
from slot_data.models import (
    BonusEvent,
    BonusEventSeries,
    Hall,
    MachineSnapshot,
    ParseRun,
    RawPayload,
    SlumpPoint,
    SlumpSeries,
    SourceObservation,
)
from slot_data.normalization.validate import (
    observed_values,
    validate_bonus_series,
    validate_metrics,
    validate_slump_series,
)
from slot_data.quality.issues import Finding, is_fatal, record_issue
from slot_data.schemas.parsed import ParsedBonusEvent, ParsedObservation, ParseResult

SLUMP_POINT_BATCH = 5000


@dataclass(frozen=True)
class ObservationKey:
    hall_id: Any
    business_date: date
    machine_number: str


def event_keys(events: list[ParsedBonusEvent]) -> list[str]:
    """取得元 ID が無い当たりイベントの安定キー。内容ハッシュ + 同一内容内の出現回数。"""
    seen: Counter[str] = Counter()
    keys = []
    for event in sorted(events, key=lambda e: e.ordinal):
        if event.source_event_id:
            keys.append(f"id:{event.source_event_id}")
            continue
        content = json.dumps(
            [
                event.event_time.isoformat() if event.event_time else None,
                event.hit_games,
                event.cumulative_games,
                event.raw_label,
                event.payout_medals,
            ],
            ensure_ascii=False,
        )
        digest = hashlib.sha1(content.encode()).hexdigest()[:16]
        seen[digest] += 1
        keys.append(f"h:{digest}#{seen[digest]}")
    return keys


def _quality_of(findings: list[Finding]) -> str:
    if is_fatal(findings):
        return QualityStatus.QUARANTINED
    if any(f.effective_severity == Severity.WARNING for f in findings):
        return QualityStatus.WARNING
    return QualityStatus.VALID


def _field_metadata(obs: ParsedObservation, findings: list[Finding]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for name in METRIC_NAMES:
        f = obs.fields.get(name)
        if f is None:
            # 取得元がその項目自体を持たない。0 ではなく未公開として扱う
            meta[name] = {"status": FieldStatus.NOT_PUBLISHED.value, "implicit": True}
            continue
        meta[name] = f.model_dump(mode="json", exclude={"value"}, exclude_none=True)
    meta["_observation"] = {
        "scope": obs.scope,
        "source_state": obs.source_state,
        "extra": obs.extra,
        "findings": [
            {"code": f.code, "severity": f.effective_severity.value, "field": f.field, "message": f.message}
            for f in findings
        ],
    }
    return meta


def persist_parse_result(
    session: Session,
    *,
    raw: RawPayload,
    parse_run: ParseRun,
    result: ParseResult,
    hall: Hall,
    warn_abs_limits: dict[str, int] | None = None,
) -> list[ObservationKey]:
    keys: list[ObservationKey] = []
    numbers = Counter(o.machine_number for o in result.observations)
    for obs in result.observations:
        machine_number = obs.machine_number
        bdate = obs.business_date or business_date_of(
            obs.observed_at, hall.timezone, hall.business_day_cutoff
        )
        day_start = business_day_start(bdate, hall.timezone, hall.business_day_cutoff)
        findings = validate_metrics(obs, day_start, warn_abs_limits)
        if numbers[machine_number] > 1:
            findings.append(
                Finding(
                    "DUPLICATE_MACHINE_NUMBER",
                    f"machine {machine_number} appears {numbers[machine_number]} times",
                )
            )
        if obs.bonus_series is not None:
            findings.extend(validate_bonus_series(obs.bonus_series))
        for series in obs.slump_series:
            findings.extend(validate_slump_series(series))

        regression = _value_regression(session, raw.source_id, hall.id, bdate, machine_number, obs)
        findings.extend(regression)

        observation = SourceObservation(
            id=uuid7(),
            parse_run_id=parse_run.id,
            source_id=raw.source_id,
            hall_id=hall.id,
            machine_number=machine_number,
            source_machine_number=obs.source_machine_number,
            business_date=bdate,
            observed_at=obs.observed_at,
            source_reported_at=obs.source_reported_at or result.source_reported_at or raw.source_reported_at,
            source_model_name=obs.source_model_name,
            source_model_id=obs.source_model_id,
            source_record_id=obs.source_record_id,
            field_values={
                name: obs.fields[name].value if name in obs.fields else None for name in METRIC_NAMES
            },
            field_metadata=_field_metadata(obs, findings),
            quality_status=_quality_of(findings),
        )
        session.add(observation)
        session.flush()

        _persist_snapshot(session, observation, obs)
        if obs.bonus_series is not None:
            _persist_bonus_series(session, observation, obs)
        _persist_slump_series(session, observation, obs, raw)

        for finding in findings:
            record_issue(
                session,
                finding.code,
                severity=finding.effective_severity,
                entity_type="source_observation",
                entity_id=observation.id,
                hall_id=hall.id,
                business_date=bdate,
                machine_number=machine_number,
                dedup_key=f"{observation.id}:{finding.field or ''}:{finding.message}",
                details={"message": finding.message, "field": finding.field, **finding.details},
            )
        keys.append(ObservationKey(hall.id, bdate, machine_number))
    return keys


def _value_regression(
    session, source_id, hall_id, bdate, machine_number, obs: ParsedObservation
) -> list[Finding]:
    """同一取得元の直前観測より累積値（総ゲーム数等）が減っていないか。"""
    previous = session.scalar(
        select(SourceObservation)
        .where(
            SourceObservation.source_id == source_id,
            SourceObservation.hall_id == hall_id,
            SourceObservation.business_date == bdate,
            SourceObservation.machine_number == machine_number,
            SourceObservation.observed_at < obs.observed_at,
        )
        .order_by(SourceObservation.observed_at.desc())
        .limit(1)
    )
    if previous is None:
        return []
    findings = []
    current = observed_values(obs)
    for name in ("total_games", "bb_count", "rb_count", "at_count", "first_hit_count"):
        before, now = previous.field_values.get(name), current.get(name)
        if before is not None and now is not None and now < before:
            findings.append(
                Finding(
                    "VALUE_REGRESSION",
                    f"{name} decreased {before} -> {now}",
                    field=name,
                    details={"previous_observation_id": str(previous.id)},
                )
            )
    return findings


def _persist_snapshot(session: Session, observation: SourceObservation, obs: ParsedObservation) -> None:
    present = [f for f in obs.fields.values() if f.status == FieldStatus.OBSERVED]
    if not present:
        completeness = "empty"
    elif len(present) == len(obs.fields):
        completeness = "complete"
    else:
        completeness = "partial"
    session.add(
        MachineSnapshot(
            id=uuid7(),
            source_observation_id=observation.id,
            hall_id=observation.hall_id,
            business_date=observation.business_date,
            machine_number=observation.machine_number,
            captured_at=observation.observed_at,
            metrics={name: {"value": f.value, "status": f.status.value} for name, f in obs.fields.items()},
            completeness_status=completeness,
        )
    )


def _persist_bonus_series(session: Session, observation: SourceObservation, obs: ParsedObservation) -> None:
    assert obs.bonus_series is not None
    series = BonusEventSeries(
        id=uuid7(),
        source_observation_id=observation.id,
        hall_id=observation.hall_id,
        business_date=observation.business_date,
        machine_number=observation.machine_number,
        series_status=obs.bonus_series.status,
        event_count=len(obs.bonus_series.events),
    )
    session.add(series)
    session.flush()
    events = sorted(obs.bonus_series.events, key=lambda e: e.ordinal)
    keys = event_keys(events)
    if events:
        session.execute(
            insert(BonusEvent),
            [
                {
                    "id": uuid7(),
                    "series_id": series.id,
                    "ordinal": e.ordinal,
                    "source_event_id": e.source_event_id,
                    "event_key": key,
                    "event_time": e.event_time,
                    "hit_games": e.hit_games,
                    "cumulative_games": e.cumulative_games,
                    "raw_label": e.raw_label,
                    "normalized_type": e.normalized_type,
                    "payout_medals": e.payout_medals,
                    "raw_attributes": {**e.raw_attributes, **({"estimated": True} if e.estimated else {})},
                    "quality_status": QualityStatus.WARNING if e.estimated else QualityStatus.VALID,
                }
                for e, key in zip(events, keys, strict=True)
            ],
        )
    _compare_with_previous_series(session, observation, series, keys)


def _compare_with_previous_series(
    session: Session, observation: SourceObservation, series: BonusEventSeries, keys: list[str]
) -> None:
    previous = session.scalar(
        select(BonusEventSeries)
        .join(SourceObservation, SourceObservation.id == BonusEventSeries.source_observation_id)
        .where(
            SourceObservation.source_id == observation.source_id,
            BonusEventSeries.hall_id == observation.hall_id,
            BonusEventSeries.business_date == observation.business_date,
            BonusEventSeries.machine_number == observation.machine_number,
            BonusEventSeries.id != series.id,
            SourceObservation.observed_at <= observation.observed_at,
        )
        .order_by(SourceObservation.observed_at.desc(), BonusEventSeries.id.desc())
        .limit(1)
    )
    if previous is None:
        return
    prev_keys = list(
        session.scalars(
            select(BonusEvent.event_key)
            .where(BonusEvent.series_id == previous.id)
            .order_by(BonusEvent.ordinal)
        )
    )
    # 正常な追記は「前回の系列が今回の先頭部分と一致」。それ以外は訂正・並べ替え
    if keys[: len(prev_keys)] != prev_keys:
        record_issue(
            session,
            "BONUS_ORDER_CHANGED",
            entity_type="bonus_event_series",
            entity_id=series.id,
            hall_id=observation.hall_id,
            business_date=observation.business_date,
            machine_number=observation.machine_number,
            dedup_key=f"{previous.id}->{series.id}",
            details={
                "previous_series_id": str(previous.id),
                "removed": [k for k in prev_keys if k not in keys],
                "added": [k for k in keys if k not in prev_keys],
            },
        )


def _persist_slump_series(
    session: Session, observation: SourceObservation, obs: ParsedObservation, raw: RawPayload
) -> None:
    by_key: dict[str, SlumpSeries] = {}
    # 推定系列が派生元を参照できるよう、観測系列を先に保存する
    ordered = sorted(obs.slump_series, key=lambda s: s.derived_from_series_key is not None)
    for parsed in ordered:
        findings = validate_slump_series(parsed)
        series = SlumpSeries(
            id=uuid7(),
            source_observation_id=observation.id,
            series_key=parsed.series_key,
            hall_id=observation.hall_id,
            business_date=observation.business_date,
            machine_number=observation.machine_number,
            captured_at=observation.observed_at,
            x_axis_type=parsed.x_axis_type,
            x_unit=parsed.x_unit,
            y_axis_type=parsed.y_axis_type,
            y_unit=parsed.y_unit,
            origin_type=parsed.origin_type,
            derivation_method=parsed.derivation_method,
            derivation_version=parsed.derivation_version,
            derived_from_series_id=by_key[parsed.derived_from_series_key].id
            if parsed.derived_from_series_key in by_key
            else None,
            raw_payload_id=raw.id,
            point_count=len(parsed.points),
            series_metadata=parsed.metadata,
            quality_status=_quality_of(findings) if not is_fatal(findings) else QualityStatus.WARNING,
        )
        session.add(series)
        session.flush()
        by_key[parsed.series_key] = series
        rows = [
            {
                "id": uuid7(),
                "series_id": series.id,
                "point_index": index,
                "x_raw": p.x_raw,
                "y_raw": p.y_raw,
                "x_numeric": p.x_numeric,
                "x_timestamp": p.x_timestamp,
                "y_numeric": p.y_numeric,
                "estimated_net_medals": p.estimated_net_medals,
                "point_metadata": p.metadata,
                "quality_status": QualityStatus.VALID,
            }
            for index, p in enumerate(parsed.points)
        ]
        # 点数に上限を設けず、バッチに分けて全点を保存する
        for start in range(0, len(rows), SLUMP_POINT_BATCH):
            session.execute(insert(SlumpPoint), rows[start : start + SLUMP_POINT_BATCH])
