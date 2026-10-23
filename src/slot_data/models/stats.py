"""台別実績・改訂履歴・スナップショット・当たり履歴・スランプグラフ。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from slot_data.domain.enums import (
    DataStatus,
    QualityStatus,
    RevisionReason,
    SeriesOrigin,
    XAxisType,
    YAxisType,
)
from slot_data.domain.metrics import METRICS

from .base import Base, enum_check, tstz, uuid_pk


def _metric_checks() -> list[CheckConstraint]:
    return [
        CheckConstraint(f"{m.name} IS NULL OR {m.name} >= 0", name=f"{m.name}_non_negative")
        for m in METRICS
        if m.non_negative
    ]


class DailyMachineStat(Base):
    """公開用の最新採用値。このテーブルのみ UPDATE で最新化し、変化は daily_stat_revisions に追記する。"""

    __tablename__ = "daily_machine_stats"
    __table_args__ = (
        Index("uq_daily_machine_stats_key", "hall_id", "business_date", "machine_number", unique=True),
        Index("ix_daily_machine_stats_hall_date", "hall_id", "business_date"),
        Index("ix_daily_machine_stats_model_date", "model_id", "business_date"),
        enum_check("data_status", DataStatus),
        enum_check("quality_status", QualityStatus),
        CheckConstraint("revision_number >= 1", name="revision_positive"),
        CheckConstraint(
            "normal_games IS NULL OR total_games IS NULL OR normal_games <= total_games",
            name="normal_le_total",
        ),
        *_metric_checks(),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    installation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machine_installations.id"))
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machine_models.id"))
    total_games: Mapped[int | None] = mapped_column(BigInteger)
    normal_games: Mapped[int | None] = mapped_column(BigInteger)
    net_medals: Mapped[int | None] = mapped_column(BigInteger)
    bb_count: Mapped[int | None] = mapped_column(Integer)
    rb_count: Mapped[int | None] = mapped_column(Integer)
    at_count: Mapped[int | None] = mapped_column(Integer)
    cz_count: Mapped[int | None] = mapped_column(Integer)
    first_hit_count: Mapped[int | None] = mapped_column(Integer)
    last_games: Mapped[int | None] = mapped_column(BigInteger)
    max_net_medals: Mapped[int | None] = mapped_column(BigInteger)
    min_net_medals: Mapped[int | None] = mapped_column(BigInteger)
    max_payout_medals: Mapped[int | None] = mapped_column(BigInteger)
    bonus_count: Mapped[int | None] = mapped_column(Integer)
    # 値が NULL の理由（metric 名 → FieldStatus）。ゼロ補完せず理由を残す（ADR-0006）
    field_status: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    data_status: Mapped[str] = mapped_column(Text, nullable=False)
    quality_status: Mapped[str] = mapped_column(Text, nullable=False)
    is_corrected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    selected_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_observations.id"))
    selected_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailyStatRevision(Base):
    __tablename__ = "daily_stat_revisions"
    __table_args__ = (
        Index("uq_daily_stat_revisions_stat_rev", "daily_stat_id", "revision_number", unique=True),
        enum_check("reason", RevisionReason),
        CheckConstraint(
            "source_observation_id IS NOT NULL OR manual_correction_id IS NOT NULL", name="has_evidence"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    daily_stat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("daily_machine_stats.id"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = tstz(nullable=False)
    previous_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_observations.id"))
    manual_correction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("manual_corrections.id"))
    selection_rule_version: Mapped[str] = mapped_column(Text, nullable=False)


class MachineSnapshot(Base):
    """営業中の各時点の指標。元観測と 1 対 1。"""

    __tablename__ = "machine_snapshots"
    __table_args__ = (
        Index("uq_machine_snapshots_observation", "source_observation_id", unique=True),
        Index(
            "ix_machine_snapshots_key_captured",
            "hall_id",
            "business_date",
            "machine_number",
            text("captured_at DESC"),
        ),
        CheckConstraint(
            "completeness_status IN ('complete', 'partial', 'empty')", name="completeness_status_valid"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_observations.id"), nullable=False
    )
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = tstz(nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    completeness_status: Mapped[str] = mapped_column(Text, nullable=False)


class BonusEventSeries(Base):
    """ある取得時点の当たり履歴一覧全体。系列ごとに全件保持し、古い系列は消さない。"""

    __tablename__ = "bonus_event_series"
    __table_args__ = (
        Index("uq_bonus_event_series_observation", "source_observation_id", unique=True),
        Index("ix_bonus_event_series_key", "hall_id", "business_date", "machine_number"),
        CheckConstraint(
            "series_status IN ('complete', 'partial', 'empty', 'unavailable')", name="series_status_valid"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_observations.id"), nullable=False
    )
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    series_status: Mapped[str] = mapped_column(Text, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)


class BonusEvent(Base):
    __tablename__ = "bonus_events"
    __table_args__ = (
        Index("uq_bonus_events_series_ordinal", "series_id", "ordinal", unique=True),
        Index("ix_bonus_events_event_key", "event_key"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint("hit_games IS NULL OR hit_games >= 0", name="hit_games_non_negative"),
        CheckConstraint("cumulative_games IS NULL OR cumulative_games >= 0", name="cumulative_non_negative"),
        CheckConstraint("payout_medals IS NULL OR payout_medals >= 0", name="payout_non_negative"),
        enum_check("quality_status", QualityStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bonus_event_series.id"), nullable=False)
    # 取得元での順番（1 始まり）
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(Text)
    # 取得元 ID が無い場合の安定キー（内容ハッシュ + 同一内容内の出現順）。系列間の対応付けに使う
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_time: Mapped[datetime | None] = tstz()
    hit_games: Mapped[int | None] = mapped_column(BigInteger)
    cumulative_games: Mapped[int | None] = mapped_column(BigInteger)
    raw_label: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_type: Mapped[str | None] = mapped_column(Text)
    payout_medals: Mapped[int | None] = mapped_column(BigInteger)
    raw_attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    quality_status: Mapped[str] = mapped_column(Text, nullable=False)


class SlumpSeries(Base):
    __tablename__ = "slump_series"
    __table_args__ = (
        Index("uq_slump_series_observation_key", "source_observation_id", "series_key", unique=True),
        Index(
            "ix_slump_series_key_captured",
            "hall_id",
            "business_date",
            "machine_number",
            text("captured_at DESC"),
        ),
        enum_check("x_axis_type", XAxisType),
        enum_check("y_axis_type", YAxisType),
        enum_check("origin_type", SeriesOrigin),
        enum_check("quality_status", QualityStatus),
        CheckConstraint(
            "origin_type = 'observed' OR (derivation_method IS NOT NULL AND derivation_version IS NOT NULL)",
            name="estimated_has_derivation",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_observations.id"), nullable=False
    )
    # 同一観測内の系列識別（例: 'main', 'image:estimated'）
    series_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'main'"))
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = tstz(nullable=False)
    x_axis_type: Mapped[str] = mapped_column(Text, nullable=False)
    x_unit: Mapped[str | None] = mapped_column(Text)
    y_axis_type: Mapped[str] = mapped_column(Text, nullable=False)
    y_unit: Mapped[str | None] = mapped_column(Text)
    origin_type: Mapped[str] = mapped_column(Text, nullable=False)
    derivation_method: Mapped[str | None] = mapped_column(Text)
    derivation_version: Mapped[str | None] = mapped_column(Text)
    # 推定系列の派生元（画像から推定した場合の元系列など）
    derived_from_series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slump_series.id"))
    raw_payload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_payloads.id"), nullable=False)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    series_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    quality_status: Mapped[str] = mapped_column(Text, nullable=False)


class SlumpPoint(Base):
    __tablename__ = "slump_points"
    __table_args__ = (
        # 系列単位の連続取得はこの一意索引（series_id, point_index）の範囲走査で行う
        Index("uq_slump_points_series_index", "series_id", "point_index", unique=True),
        CheckConstraint("point_index >= 0", name="point_index_non_negative"),
        enum_check("quality_status", QualityStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slump_series.id"), nullable=False)
    point_index: Mapped[int] = mapped_column(Integer, nullable=False)
    x_raw: Mapped[str | None] = mapped_column(Text)
    y_raw: Mapped[str | None] = mapped_column(Text)
    x_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    x_timestamp: Mapped[datetime | None] = tstz()
    y_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    estimated_net_medals: Mapped[Decimal | None] = mapped_column(Numeric)
    point_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    quality_status: Mapped[str] = mapped_column(Text, nullable=False)
