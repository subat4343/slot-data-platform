"""マスター: 取得元・店舗・機種・取得元ID対応・設置期間。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from slot_data.domain.enums import ReviewStatus

from .base import Base, created_at_col, enum_check, uuid_pk

MAPPING_ENTITY_TYPES = ("hall", "machine_model")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_col()


class Hall(Base):
    __tablename__ = "halls"
    __table_args__ = (CheckConstraint("code <> ''", name="code_not_empty"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # 配置CSVの hall_code 等で使う内部コード（仕様の論理定義に追加。ADR-0005）
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Asia/Tokyo'"))
    business_day_cutoff: Mapped[time | None] = mapped_column(Time)
    # 閉店時刻。これ以降の観測は final_candidate 候補（ADR-0007）
    closing_time: Mapped[time | None] = mapped_column(Time)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MachineModel(Base):
    __tablename__ = "machine_models"
    __table_args__ = (Index("ix_machine_models_canonical_name", "canonical_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    model_type: Mapped[str | None] = mapped_column(Text)
    release_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = created_at_col()


class SourceMapping(Base):
    """取得元の店舗/機種 ID・名称と内部エンティティの対応。

    internal_entity_id の参照整合性はトリガー ``trg_source_mappings_validate`` で entity_type 別に検証する。
    未レビュー（pending）の間は internal_entity_id を NULL にできる。
    """

    __tablename__ = "source_mappings"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN (" + ", ".join(f"'{t}'" for t in MAPPING_ENTITY_TYPES) + ")",
            name="entity_type_valid",
        ),
        enum_check("review_status", ReviewStatus),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="valid_range"
        ),
        CheckConstraint(
            "review_status NOT IN ('confirmed', 'auto_matched') OR internal_entity_id IS NOT NULL",
            name="matched_has_entity",
        ),
        # 機種の external_id は店舗ごとにずれることがあるため scope_hall_id で区別可能にする
        Index(
            "uq_source_mappings_key",
            "source_id",
            "entity_type",
            "external_id",
            text("coalesce(scope_hall_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            text("coalesce(valid_from, '-infinity'::date)"),
            unique=True,
        ),
        Index("ix_source_mappings_internal", "entity_type", "internal_entity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    internal_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    external_name: Mapped[str | None] = mapped_column(Text)
    scope_hall_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("halls.id"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = created_at_col()


class MachineInstallation(Base):
    """店舗＋台番号ごとの機種設置期間。期間は [valid_from, valid_to) の半開区間。"""

    __tablename__ = "machine_installations"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        CheckConstraint("machine_number <> ''", name="machine_number_not_empty"),
        ExcludeConstraint(
            ("hall_id", "="),
            ("machine_number", "="),
            (text("daterange(valid_from, valid_to, '[)')"), "&&"),
            name="ex_machine_installations_no_overlap",
            using="gist",
        ),
        Index("ix_machine_installations_model", "model_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machine_models.id"))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"))
    evidence_raw_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_payloads.id"))
    created_at: Mapped[datetime] = created_at_col()
