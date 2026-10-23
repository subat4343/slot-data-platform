"""日次実績の採用値決定と改訂履歴（選択ルール v1）。

店舗＋営業日＋台番号ごとに advisory lock を取り、候補観測を比較して ``daily_machine_stats`` を更新する。
採用値（指標・値の状態・確定段階・機種・設置）が変化した場合だけ ``daily_stat_revisions`` を追記する。

選択ルール v1（docs/decisions/0008-selection-rule-v1.md）:
1. 候補: 各原本の最新成功パース由来で、quarantined でない観測。取得元ごとに最新 observed_at の 1 件
2. 順位: 確定段階 > 取得元優先度 > 品質(valid>warning) > 完全性(観測値の数) > 鮮度(observed_at)
3. 確定段階: 閉店時刻前の観測は provisional、以降は final_candidate。同一取得元で同じ値が
   settle_hours 以上継続して観測されたら final
4. 有効な手動訂正は採用値の上に重ねる（元観測は改変しない）
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from slot_data.config import AppConfig
from slot_data.domain.enums import DataStatus, FieldStatus, QualityStatus, RevisionReason
from slot_data.domain.metrics import METRIC_NAMES
from slot_data.domain.timeutil import closing_moment, utcnow
from slot_data.models import (
    DailyMachineStat,
    DailyStatRevision,
    Hall,
    ManualCorrection,
    ParseRun,
    RawPayload,
    Source,
    SourceObservation,
)
from slot_data.quality.issues import record_issue
from slot_data.reconciliation.installations import link_installation
from slot_data.services.masters import resolve_model

RULE_VERSION = "v1"
STATUS_RANK = {DataStatus.PROVISIONAL: 0, DataStatus.FINAL_CANDIDATE: 1, DataStatus.FINAL: 2}
QUALITY_RANK = {QualityStatus.VALID: 1, QualityStatus.WARNING: 0}


@dataclass
class Candidate:
    observation: SourceObservation
    source_code: str
    priority: int
    status: DataStatus
    completeness: int

    def sort_key(self) -> tuple:
        return (
            -STATUS_RANK[self.status],
            self.priority,
            -QUALITY_RANK.get(QualityStatus(self.observation.quality_status), 0),
            -self.completeness,
            -self.observation.observed_at.timestamp(),
            str(self.observation.id),
        )


@dataclass
class ReconcileOutcome:
    daily_stat_id: uuid.UUID | None
    revised: bool
    reason: str | None = None
    selected_observation_id: uuid.UUID | None = None
    notes: list[str] = field(default_factory=list)


def _advisory_lock(session: Session, hall_id: uuid.UUID, business_date: date, machine_number: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"daily:{hall_id}:{business_date.isoformat()}:{machine_number}"},
    )


def _completeness(obs: SourceObservation) -> int:
    return sum(1 for name in METRIC_NAMES if obs.field_values.get(name) is not None)


def _base_status(obs: SourceObservation, hall: Hall) -> DataStatus:
    closing = closing_moment(obs.business_date, hall.closing_time, hall.timezone, hall.business_day_cutoff)
    return DataStatus.FINAL_CANDIDATE if obs.observed_at >= closing else DataStatus.PROVISIONAL


def _latest_observations(
    session: Session, hall_id: uuid.UUID, business_date: date, machine_number: str
) -> list[SourceObservation]:
    """各原本の最新成功パース由来の観測（再解析で古い版の観測を置き換える）。"""
    latest_parse = (
        select(ParseRun.raw_payload_id, func.max(ParseRun.started_at).label("started_at"))
        .where(ParseRun.status == "succeeded")
        .group_by(ParseRun.raw_payload_id)
        .subquery()
    )
    stmt = (
        select(SourceObservation)
        .join(ParseRun, ParseRun.id == SourceObservation.parse_run_id)
        .join(
            latest_parse,
            (latest_parse.c.raw_payload_id == ParseRun.raw_payload_id)
            & (latest_parse.c.started_at == ParseRun.started_at),
        )
        .where(
            SourceObservation.hall_id == hall_id,
            SourceObservation.business_date == business_date,
            SourceObservation.machine_number == machine_number,
        )
        .order_by(SourceObservation.observed_at.desc(), SourceObservation.id.desc())
    )
    return list(session.scalars(stmt))


def _settled(
    session: Session, chosen: SourceObservation, peers: list[SourceObservation], hall: Hall, settle: timedelta
) -> bool:
    """同一取得元で同じ値の final_candidate 観測（同一内容の再取得を含む）が settle 以上続いたか。"""
    same = [
        o
        for o in peers
        if o.source_id == chosen.source_id
        and o.field_values == chosen.field_values
        and _base_status(o, hall) == DataStatus.FINAL_CANDIDATE
    ]
    # 取得元の値が途中で変わっていれば、変化後の最初の観測から数える
    later_changed = [
        o
        for o in peers
        if o.source_id == chosen.source_id
        and o.field_values != chosen.field_values
        and _base_status(o, hall) == DataStatus.FINAL_CANDIDATE
    ]
    if later_changed:
        last_change = max(o.observed_at for o in later_changed)
        same = [o for o in same if o.observed_at > last_change]
    if not same:
        return False
    earliest = min(o.observed_at for o in same)
    raw_ids = (
        select(ParseRun.raw_payload_id)
        .join(SourceObservation, SourceObservation.parse_run_id == ParseRun.id)
        .where(SourceObservation.id.in_([o.id for o in same]))
    )
    duplicate_fetches = session.scalar(
        select(func.max(RawPayload.fetched_at)).where(RawPayload.duplicate_of_id.in_(raw_ids))
    )
    latest = max([o.observed_at for o in same] + ([duplicate_fetches] if duplicate_fetches else []))
    return latest - earliest >= settle


def _active_corrections(session: Session, hall_id, business_date, machine_number) -> list[ManualCorrection]:
    return list(
        session.scalars(
            select(ManualCorrection)
            .where(
                ManualCorrection.hall_id == hall_id,
                ManualCorrection.business_date == business_date,
                ManualCorrection.machine_number == machine_number,
                ManualCorrection.revoked_at.is_(None),
            )
            .order_by(ManualCorrection.created_at)
        )
    )


def _check_source_mismatch(
    session: Session, candidates: list[Candidate], hall_id, business_date, machine_number
) -> bool:
    """異なる取得元の最新観測どうしで、両方が観測した指標の値が食い違えば記録する。"""
    mismatch = False
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if a.source_code == b.source_code:
                continue
            diffs = {
                name: [a.observation.field_values.get(name), b.observation.field_values.get(name)]
                for name in METRIC_NAMES
                if a.observation.field_values.get(name) is not None
                and b.observation.field_values.get(name) is not None
                and a.observation.field_values[name] != b.observation.field_values[name]
            }
            if diffs and a.status == b.status:
                mismatch = True
                first, second = sorted([a, b], key=lambda c: str(c.observation.id))
                record_issue(
                    session,
                    "SOURCE_MISMATCH",
                    entity_type="daily_key",
                    hall_id=hall_id,
                    business_date=business_date,
                    machine_number=machine_number,
                    dedup_key=f"{first.observation.id}|{second.observation.id}",
                    details={
                        "sources": [first.source_code, second.source_code],
                        "observations": [str(first.observation.id), str(second.observation.id)],
                        "diffs": diffs,
                    },
                )
    return mismatch


def _snapshot(stat: DailyMachineStat) -> dict[str, Any]:
    return {
        **{name: getattr(stat, name) for name in METRIC_NAMES},
        "field_status": stat.field_status,
        "data_status": stat.data_status,
        "model_id": str(stat.model_id) if stat.model_id else None,
        "installation_id": str(stat.installation_id) if stat.installation_id else None,
        "selected_source_id": str(stat.selected_source_id) if stat.selected_source_id else None,
        "quality_status": stat.quality_status,
    }


def _comparable(values: dict[str, Any]) -> dict[str, Any]:
    # 採用元の取得元が変わっただけ（値が同一）では改訂しない
    return {k: v for k, v in values.items() if k != "selected_source_id"}


def reconcile_key(
    session: Session,
    config: AppConfig,
    hall: Hall,
    business_date: date,
    machine_number: str,
    now: datetime | None = None,
) -> ReconcileOutcome:
    _advisory_lock(session, hall.id, business_date, machine_number)
    now = now or utcnow()
    priorities = config.source_priority()
    sources = {s.id: s.code for s in session.scalars(select(Source))}

    peers = _latest_observations(session, hall.id, business_date, machine_number)
    usable = [o for o in peers if o.quality_status != QualityStatus.QUARANTINED]
    latest_per_source: dict[uuid.UUID, SourceObservation] = {}
    for obs in usable:  # observed_at 降順
        latest_per_source.setdefault(obs.source_id, obs)

    candidates = [
        Candidate(
            observation=obs,
            source_code=sources[obs.source_id],
            priority=priorities.get(sources[obs.source_id], 1000),
            status=_base_status(obs, hall),
            completeness=_completeness(obs),
        )
        for obs in latest_per_source.values()
    ]
    stat = session.scalar(
        select(DailyMachineStat)
        .where(
            DailyMachineStat.hall_id == hall.id,
            DailyMachineStat.business_date == business_date,
            DailyMachineStat.machine_number == machine_number,
        )
        .with_for_update()
    )
    if not candidates:
        return ReconcileOutcome(stat.id if stat else None, revised=False, notes=["no usable observation"])

    candidates.sort(key=Candidate.sort_key)
    chosen = candidates[0]
    obs = chosen.observation
    mismatch = _check_source_mismatch(session, candidates, hall.id, business_date, machine_number)

    status = chosen.status
    if status == DataStatus.FINAL_CANDIDATE and _settled(
        session, obs, peers, hall, timedelta(hours=config.selection.settle_hours)
    ):
        status = DataStatus.FINAL

    source = session.get(Source, obs.source_id)
    model_id = resolve_model(session, source, hall.id, obs.source_model_id, obs.source_model_name)
    if model_id is None and (obs.source_model_id or obs.source_model_name):
        record_issue(
            session,
            "MODEL_MAPPING_PENDING",
            entity_type="source_observation",
            entity_id=obs.id,
            hall_id=hall.id,
            business_date=business_date,
            machine_number=machine_number,
            dedup_key=f"{source.code}:{obs.source_model_id or obs.source_model_name}",
            details={"source_model_name": obs.source_model_name, "source_model_id": obs.source_model_id},
        )
    raw_id = session.scalar(select(ParseRun.raw_payload_id).where(ParseRun.id == obs.parse_run_id))
    link = link_installation(
        session,
        hall_id=hall.id,
        machine_number=machine_number,
        business_date=business_date,
        model_id=model_id,
        source_id=obs.source_id,
        evidence_raw_id=raw_id,
    )
    if link.conflict:
        record_issue(
            session,
            "INSTALLATION_CONFLICT",
            entity_type="source_observation",
            entity_id=obs.id,
            hall_id=hall.id,
            business_date=business_date,
            machine_number=machine_number,
            dedup_key=f"{obs.id}",
            details={"message": link.conflict},
        )

    values: dict[str, Any] = {name: obs.field_values.get(name) for name in METRIC_NAMES}
    field_status = {
        name: (obs.field_metadata.get(name) or {}).get("status", FieldStatus.NOT_PUBLISHED.value)
        for name in METRIC_NAMES
    }
    corrections = _active_corrections(session, hall.id, business_date, machine_number)
    for corr in corrections:
        values[corr.field_name] = int(corr.new_value) if corr.new_value is not None else None
        field_status[corr.field_name] = (
            corr.field_status if corr.new_value is None else FieldStatus.OBSERVED.value
        )

    quality = QualityStatus(obs.quality_status)
    if mismatch and quality == QualityStatus.VALID:
        quality = QualityStatus.WARNING

    new_values = {
        **values,
        "field_status": field_status,
        "data_status": status.value,
        "model_id": str(model_id) if model_id else None,
        "installation_id": str(link.installation_id) if link.installation_id else None,
        "selected_source_id": str(obs.source_id),
        "quality_status": quality.value,
    }

    if stat is not None:
        previous = _snapshot(stat)
        if _comparable(previous) == _comparable(new_values):
            return ReconcileOutcome(
                stat.id, revised=False, selected_observation_id=stat.selected_observation_id
            )
        metrics_changed = any(previous[n] != new_values[n] for n in METRIC_NAMES) or (
            previous["field_status"] != new_values["field_status"]
        )
        if corrections and _metrics_from_obs_differ(values, obs):
            reason = RevisionReason.MANUAL_CORRECTION
        elif metrics_changed and previous["data_status"] in (DataStatus.FINAL_CANDIDATE, DataStatus.FINAL):
            reason = RevisionReason.CORRECTION
        elif metrics_changed and previous["selected_source_id"] != new_values["selected_source_id"]:
            reason = RevisionReason.SOURCE_SWITCH
        elif metrics_changed:
            reason = RevisionReason.SOURCE_UPDATE
        else:
            reason = RevisionReason.STATUS_CHANGE
        revision_number = stat.revision_number + 1
        if reason == RevisionReason.CORRECTION:
            stat.is_corrected = True
            # 訂正後は改めて安定するまで final にしない
            if status == DataStatus.FINAL:
                status = DataStatus.FINAL_CANDIDATE
                new_values["data_status"] = status.value
    else:
        previous = None
        reason = RevisionReason.MANUAL_CORRECTION if corrections else RevisionReason.INITIAL
        revision_number = 1
        stat = DailyMachineStat(hall_id=hall.id, business_date=business_date, machine_number=machine_number)
        session.add(stat)

    for name in METRIC_NAMES:
        setattr(stat, name, values[name])
    stat.field_status = field_status
    stat.data_status = status.value
    stat.quality_status = quality.value
    stat.model_id = model_id
    stat.installation_id = link.installation_id
    stat.selected_observation_id = obs.id
    stat.selected_source_id = obs.source_id
    stat.revision_number = revision_number
    stat.updated_at = now
    session.flush()

    session.add(
        DailyStatRevision(
            daily_stat_id=stat.id,
            revision_number=revision_number,
            recorded_at=now,
            previous_values=previous,
            new_values=new_values,
            reason=reason.value,
            source_observation_id=obs.id,
            manual_correction_id=corrections[-1].id
            if corrections and reason == RevisionReason.MANUAL_CORRECTION
            else None,
            selection_rule_version=RULE_VERSION,
        )
    )
    session.flush()
    return ReconcileOutcome(stat.id, revised=True, reason=reason.value, selected_observation_id=obs.id)


def _metrics_from_obs_differ(values: dict[str, Any], obs: SourceObservation) -> bool:
    return any(values[name] != obs.field_values.get(name) for name in METRIC_NAMES)
