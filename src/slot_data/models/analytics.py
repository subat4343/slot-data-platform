"""分析補助（店舗イベント・機種スペック・分析結果）と品質・訂正管理。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from slot_data.domain.enums import IssueStatus, Severity

from .base import Base, created_at_col, enum_check, tstz, uuid_pk


class HallEvent(Base):
    __tablename__ = "hall_events"
    __table_args__ = (
        Index("ix_hall_events_hall_date", "hall_id", "business_date"),
        CheckConstraint(
            "confirmation_status IN ('unconfirmed', 'confirmed', 'rejected')",
            name="confirmation_status_valid",
        ),
        CheckConstraint(
            "reliability IS NULL OR (reliability >= 0 AND reliability <= 1)", name="reliability_range"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    event_category: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)  # 根拠（URL・文書・目視等）
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"))
    source_description: Mapped[str | None] = mapped_column(Text)
    reliability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    confirmation_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unconfirmed'")
    )
    created_at: Mapped[datetime] = created_at_col()


class MachineSpec(Base):
    """公表値（published）と推定値（estimated）を value_kind で分離する。"""

    __tablename__ = "machine_specs"
    __table_args__ = (
        Index("ix_machine_specs_model_setting", "model_id", "setting_value", "metric_name"),
        CheckConstraint("value_kind IN ('published', 'estimated')", name="value_kind_valid"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="valid_range"
        ),
        CheckConstraint("denominator IS NULL OR denominator > 0", name="denominator_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machine_models.id"), nullable=False)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)  # '1'..'6', 'L', 'H' 等
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    numerator: Mapped[Decimal | None] = mapped_column(Numeric)
    denominator: Mapped[Decimal | None] = mapped_column(Numeric)
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str | None] = mapped_column(Text)
    value_kind: Mapped[str] = mapped_column(Text, nullable=False)
    citation: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = created_at_col()


class AnalysisResult(Base):
    """設定推測等の分析結果。確定設定としては扱わない（観測事実テーブルには書かない）。"""

    __tablename__ = "analysis_results"
    __table_args__ = (
        Index("ix_analysis_results_target", "target_type", "target_id"),
        CheckConstraint(
            "period_to IS NULL OR period_from IS NULL OR period_to >= period_from", name="period_range"
        ),
        CheckConstraint("sample_size IS NULL OR sample_size >= 0", name="sample_size_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    target_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'daily_stat', 'hall', 'machine' 等
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_descriptor: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_data_version: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = tstz(nullable=False)
    reliability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    sample_size: Mapped[int | None] = mapped_column(Integer)


class DataQualityIssue(Base):
    """品質検証の検出結果。理由コード・重大度・対象・検出日時を記録する。"""

    __tablename__ = "data_quality_issues"
    __table_args__ = (
        enum_check("severity", Severity),
        enum_check("status", IssueStatus),
        Index("ix_data_quality_issues_key", "hall_id", "business_date", "machine_number"),
        Index("ix_data_quality_issues_entity", "entity_type", "entity_id"),
        Index("ix_data_quality_issues_open", "status", "severity"),
        # 同一対象・同一コードの重複起票を防ぐ（dedup_key は検出側で決める）
        Index("uq_data_quality_issues_dedup", "issue_code", "dedup_key", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    detected_at: Mapped[datetime] = tstz(nullable=False)
    issue_code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    hall_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("halls.id"))
    business_date: Mapped[date | None] = mapped_column(Date)
    machine_number: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    resolved_at: Mapped[datetime | None] = tstz()
    resolution_note: Mapped[str | None] = mapped_column(Text)


class ManualCorrection(Base):
    """人手による訂正。元観測は改変せず、採用値への上書きとして監査可能に保持する。"""

    __tablename__ = "manual_corrections"
    __table_args__ = (
        Index("ix_manual_corrections_key", "hall_id", "business_date", "machine_number"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="revoked_after_created"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL は「値を NULL（不明）にする」訂正。field_status で理由を指定する
    new_value: Mapped[int | None] = mapped_column(Numeric(20, 0))
    field_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'observed'"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    revoked_at: Mapped[datetime | None] = tstz()
    revoked_by: Mapped[str | None] = mapped_column(Text)
