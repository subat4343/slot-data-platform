"""物理配置（配置版・島・台位置・位置関係）と取り込み履歴。

公開済みの配置版とその配下（島・位置・関係）はトリガーで不変にする。
期間は [valid_from, valid_to) の半開区間で、公開済み版の期間重複は排他制約で禁止する。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from slot_data.domain.enums import LayoutStatus, PositionKind, RelationType

from .base import Base, created_at_col, enum_check, uuid_pk


class HallLayoutVersion(Base):
    __tablename__ = "hall_layout_versions"
    __table_args__ = (
        UniqueConstraint("hall_id", "version_number"),
        enum_check("status", LayoutStatus),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        CheckConstraint("version_number >= 1", name="version_positive"),
        ExcludeConstraint(
            ("hall_id", "="),
            (text("daterange(valid_from, valid_to, '[)')"), "&&"),
            name="ex_hall_layout_versions_published_no_overlap",
            using="gist",
            where=text("status = 'published'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_document_uri: Mapped[str | None] = mapped_column(Text)
    # 島端（sequence_no の基準端）の定義など
    layout_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class HallIsland(Base):
    __tablename__ = "hall_islands"
    __table_args__ = (
        UniqueConstraint("layout_version_id", "island_code"),
        # machine_positions からの複合 FK 用
        UniqueConstraint("id", "layout_version_id", name="uq_hall_islands_id_version"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    layout_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hall_layout_versions.id"), nullable=False
    )
    island_code: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)


class MachinePosition(Base):
    __tablename__ = "machine_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["island_id", "layout_version_id"],
            ["hall_islands.id", "hall_islands.layout_version_id"],
            name="fk_machine_positions_island_same_version",
        ),
        # 台番号は版内で一意（machine 以外の位置種別は台番号を持たないので部分一意）
        Index(
            "uq_machine_positions_version_machine",
            "layout_version_id",
            "machine_number",
            unique=True,
            postgresql_where=text("machine_number IS NOT NULL"),
        ),
        UniqueConstraint(
            "layout_version_id", "island_id", "side", "sequence_no", name="uq_machine_positions_slot"
        ),
        UniqueConstraint("id", "layout_version_id", name="uq_machine_positions_id_version"),
        enum_check("position_kind", PositionKind),
        CheckConstraint(
            "(position_kind = 'machine') = (machine_number IS NOT NULL)", name="machine_number_iff_machine"
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint("side <> ''", name="side_not_empty"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    layout_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hall_layout_versions.id"), nullable=False
    )
    island_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    machine_number: Mapped[str | None] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    x: Mapped[Decimal | None] = mapped_column(Numeric)
    y: Mapped[Decimal | None] = mapped_column(Numeric)
    rotation_deg: Mapped[Decimal | None] = mapped_column(Numeric)
    position_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'machine'"))
    notes: Mapped[str | None] = mapped_column(Text)


class MachineAdjacency(Base):
    """位置関係（無向）。position_a_id < position_b_id に正規化して重複を禁止する。"""

    __tablename__ = "machine_adjacencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["position_a_id", "layout_version_id"],
            ["machine_positions.id", "machine_positions.layout_version_id"],
            name="fk_machine_adjacencies_a_same_version",
        ),
        ForeignKeyConstraint(
            ["position_b_id", "layout_version_id"],
            ["machine_positions.id", "machine_positions.layout_version_id"],
            name="fk_machine_adjacencies_b_same_version",
        ),
        CheckConstraint("position_a_id < position_b_id", name="ordered_pair"),
        UniqueConstraint("layout_version_id", "position_a_id", "position_b_id", "relation_type"),
        enum_check("relation_type", RelationType),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    layout_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hall_layout_versions.id"), nullable=False
    )
    position_a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position_b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(Text)


class LayoutImport(Base):
    __tablename__ = "layout_imports"
    __table_args__ = (
        CheckConstraint("status IN ('invalid', 'previewed', 'published', 'rejected')", name="status_valid"),
        CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    file_uri: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    target_layout_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hall_layout_versions.id"))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
