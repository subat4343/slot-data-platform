"""収集・原本・解析。原本と観測は追記専用（UPDATE/DELETE はトリガーで禁止）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from slot_data.domain.enums import CaptureStatus, ParseStatus, QualityStatus, RunStatus

from .base import Base, enum_check, tstz, uuid_pk


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        enum_check("status", RunStatus),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="finished_after_start"),
        Index("ix_collection_runs_source_started", "source_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    hall_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("halls.id"))
    started_at: Mapped[datetime] = tstz(nullable=False)
    finished_at: Mapped[datetime | None] = tstz()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # 例: {"kind": "collect", "dates": ["2026-10-20"], "machines": null}
    requested_range: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    error_summary: Mapped[str | None] = mapped_column(Text)
    adapter_version: Mapped[str] = mapped_column(Text, nullable=False)
    # 途中再開した場合の元ジョブ
    resumed_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("collection_runs.id"))


class RawPayload(Base):
    """取得イベント 1 件 = 1 行。ファイル本体は sha256 で重複排除され、同一内容は同じ storage_uri を指す。"""

    __tablename__ = "raw_payloads"
    __table_args__ = (
        enum_check("capture_status", CaptureStatus),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("byte_size >= 0", name="byte_size_non_negative"),
        CheckConstraint(
            "(capture_status = 'duplicate_content') = (duplicate_of_id IS NOT NULL)",
            name="duplicate_has_origin",
        ),
        Index("ix_raw_payloads_sha256", "sha256"),
        Index("ix_raw_payloads_run_fingerprint", "collection_run_id", "request_fingerprint"),
        Index("ix_raw_payloads_hall_fetched", "hall_id", "fetched_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    collection_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collection_runs.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    hall_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("halls.id"))
    fetched_at: Mapped[datetime] = tstz(nullable=False)
    source_reported_at: Mapped[datetime | None] = tstz()
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    resource_locator: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    response_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    capture_status: Mapped[str] = mapped_column(Text, nullable=False)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_payloads.id"))


class ParseRun(Base):
    __tablename__ = "parse_runs"
    __table_args__ = (
        enum_check("status", ParseStatus),
        # 同一原本・同一パーサー版の重複解析を防ぐ
        Index("uq_parse_runs_raw_parser", "raw_payload_id", "parser_name", "parser_version", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    raw_payload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_payloads.id"), nullable=False)
    parser_name: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = tstz(nullable=False)
    finished_at: Mapped[datetime | None] = tstz()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    issues: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))


class SourceObservation(Base):
    """取得元別・時点別の観測値。field_values は正規化名→値、field_metadata は正規化名→{status, raw, label, ...}。"""

    __tablename__ = "source_observations"
    __table_args__ = (
        enum_check("quality_status", QualityStatus),
        Index(
            "uq_source_observations_parse_key",
            "parse_run_id",
            "hall_id",
            "business_date",
            "machine_number",
            unique=True,
        ),
        Index(
            "ix_source_observations_key_observed",
            "hall_id",
            "business_date",
            "machine_number",
            text("observed_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    parse_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parse_runs.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    hall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    # 取得元の台番号原文（正規化前）
    source_machine_number: Mapped[str] = mapped_column(Text, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[datetime] = tstz(nullable=False)
    source_reported_at: Mapped[datetime | None] = tstz()
    # 機種名原文と取得元機種 ID（照合前の生情報）
    source_model_name: Mapped[str | None] = mapped_column(Text)
    source_model_id: Mapped[str | None] = mapped_column(Text)
    source_record_id: Mapped[str | None] = mapped_column(Text)
    field_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    field_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    quality_status: Mapped[str] = mapped_column(Text, nullable=False)
