"""マスター（取得元・店舗・機種・ID 対応）の操作。"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from slot_data.domain.enums import ReviewStatus
from slot_data.models import Hall, MachineModel, Source, SourceMapping


class NotFoundError(LookupError):
    pass


def ensure_source(session: Session, code: str, name: str | None = None) -> Source:
    source = session.scalar(select(Source).where(Source.code == code))
    if source is None:
        source = Source(code=code, name=name or code)
        session.add(source)
        session.flush()
    return source


def get_source(session: Session, code: str) -> Source:
    source = session.scalar(select(Source).where(Source.code == code))
    if source is None:
        raise NotFoundError(f"source not registered: {code}")
    return source


def create_hall(
    session: Session,
    code: str,
    name: str,
    address: str | None = None,
    timezone: str = "Asia/Tokyo",
    business_day_cutoff: time | None = None,
    closing_time: time | None = None,
) -> Hall:
    hall = Hall(
        code=code,
        name=name,
        address=address,
        timezone=timezone,
        business_day_cutoff=business_day_cutoff,
        closing_time=closing_time,
    )
    session.add(hall)
    session.flush()
    return hall


def get_hall(session: Session, code: str) -> Hall:
    hall = session.scalar(select(Hall).where(Hall.code == code))
    if hall is None:
        raise NotFoundError(f"hall not registered: {code}")
    return hall


def map_hall(
    session: Session, source: Source, external_id: str, hall: Hall, external_name: str | None = None
) -> SourceMapping:
    """取得元の店舗 ID を内部店舗に対応付ける（人が登録するので confirmed）。"""
    mapping = session.scalar(
        select(SourceMapping).where(
            SourceMapping.source_id == source.id,
            SourceMapping.entity_type == "hall",
            SourceMapping.external_id == external_id,
        )
    )
    if mapping is None:
        mapping = SourceMapping(
            source_id=source.id,
            entity_type="hall",
            external_id=external_id,
            internal_entity_id=hall.id,
            external_name=external_name,
            review_status=ReviewStatus.CONFIRMED,
            confidence=Decimal(1),
        )
        session.add(mapping)
    else:
        mapping.internal_entity_id = hall.id
        mapping.review_status = ReviewStatus.CONFIRMED
    session.flush()
    return mapping


def normalize_model_name(name: str) -> str:
    """照合用の機種名キー（NFKC・空白除去・小文字化）。原文は別途保持する。"""
    folded = unicodedata.normalize("NFKC", name).lower()
    return "".join(folded.split())


def resolve_model(
    session: Session,
    source: Source,
    hall_id: uuid.UUID,
    source_model_id: str | None,
    source_model_name: str | None,
) -> uuid.UUID | None:
    """取得元の機種情報から内部機種 ID を引く。曖昧な場合は pending の対応を起票して None を返す。

    1. 確認済み/自動一致の対応があればそれを使う
    2. 未登録なら、正規化名が一致する機種がちょうど 1 件の場合のみ auto_matched で対応付ける
    3. それ以外は pending（人のレビュー待ち）
    """
    if not source_model_id and not source_model_name:
        return None
    external_id = source_model_id or f"name:{normalize_model_name(source_model_name or '')}"
    mappings = session.scalars(
        select(SourceMapping).where(
            SourceMapping.source_id == source.id,
            SourceMapping.entity_type == "machine_model",
            SourceMapping.external_id == external_id,
            (SourceMapping.scope_hall_id.is_(None)) | (SourceMapping.scope_hall_id == hall_id),
        )
    ).all()
    # 店舗スコープの対応を優先
    mappings.sort(key=lambda m: m.scope_hall_id is None)
    for mapping in mappings:
        if mapping.review_status in (ReviewStatus.CONFIRMED, ReviewStatus.AUTO_MATCHED):
            return mapping.internal_entity_id
    if mappings:
        return None  # pending / rejected が既にある

    candidates: list[MachineModel] = []
    if source_model_name:
        key = normalize_model_name(source_model_name)
        candidates = [
            m
            for m in session.scalars(select(MachineModel)).all()
            if normalize_model_name(m.canonical_name) == key
        ]
    if len(candidates) == 1:
        mapping = SourceMapping(
            source_id=source.id,
            entity_type="machine_model",
            external_id=external_id,
            internal_entity_id=candidates[0].id,
            external_name=source_model_name,
            review_status=ReviewStatus.AUTO_MATCHED,
            confidence=Decimal("0.9"),
        )
        session.add(mapping)
        session.flush()
        return candidates[0].id

    session.add(
        SourceMapping(
            source_id=source.id,
            entity_type="machine_model",
            external_id=external_id,
            internal_entity_id=None,
            external_name=source_model_name,
            review_status=ReviewStatus.PENDING,
            confidence=None,
        )
    )
    session.flush()
    return None


def auto_resolve_pending_models(session: Session, source_codes: list[str] | None = None) -> dict[str, int]:
    """レビュー待ちの機種対応を一括で解決する。

    正規化名が一致する機種がちょうど 1 件あればそれに対応付け、無ければ取得元の機種名で新しい機種を作る。
    どちらも review_status は auto_matched（人の確認前）とし、後から resolve で付け替えられる。
    """
    stmt = select(SourceMapping).where(
        SourceMapping.entity_type == "machine_model", SourceMapping.review_status == ReviewStatus.PENDING
    )
    if source_codes:
        stmt = stmt.join(Source, Source.id == SourceMapping.source_id).where(Source.code.in_(source_codes))
    counts = {"pending": 0, "matched_existing": 0, "created": 0, "skipped": 0}
    models = {m.id: m for m in session.scalars(select(MachineModel))}
    for mapping in session.scalars(stmt).all():
        counts["pending"] += 1
        name = mapping.external_name
        if not name:
            counts["skipped"] += 1
            continue
        key = normalize_model_name(name)
        candidates = [m for m in models.values() if normalize_model_name(m.canonical_name) == key]
        if len(candidates) == 1:
            model = candidates[0]
            counts["matched_existing"] += 1
        elif not candidates:
            model = MachineModel(canonical_name=name)
            session.add(model)
            session.flush()
            models[model.id] = model
            counts["created"] += 1
        else:
            counts["skipped"] += 1  # 同名の機種が複数あり曖昧
            continue
        mapping.internal_entity_id = model.id
        mapping.review_status = ReviewStatus.AUTO_MATCHED
        mapping.confidence = Decimal("0.8")
    session.flush()
    return counts


def resolve_pending_mapping(
    session: Session,
    mapping_id: uuid.UUID,
    model_id: uuid.UUID | None = None,
    create_model_name: str | None = None,
) -> SourceMapping:
    mapping = session.get(SourceMapping, mapping_id)
    if mapping is None:
        raise NotFoundError(f"mapping not found: {mapping_id}")
    if create_model_name:
        model = MachineModel(canonical_name=create_model_name)
        session.add(model)
        session.flush()
        model_id = model.id
    if model_id is None:
        raise ValueError("model_id or create_model_name is required")
    mapping.internal_entity_id = model_id
    mapping.review_status = ReviewStatus.CONFIRMED
    mapping.confidence = Decimal(1)
    session.flush()
    return mapping
