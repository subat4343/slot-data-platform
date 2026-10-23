"""CSV / Excel / Parquet 出力。

店舗・日付範囲・機種・台番号で絞り込める。数値の欠損は空（NULL）のまま出し、``<指標>__status`` 列で理由を、
``source_code`` / ``selected_observation_id`` 列で出典を残す。大量履歴（当たり・スランプ点）は Parquet を推奨。
"""

from __future__ import annotations

import csv
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import jpholiday
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, aliased

from slot_data.domain.metrics import METRIC_NAMES
from slot_data.models import (
    BonusEvent,
    BonusEventSeries,
    DailyMachineStat,
    Hall,
    MachineModel,
    SlumpPoint,
    SlumpSeries,
    Source,
    SourceObservation,
)

WEEKDAYS_JA = "月火水木金土日"
FORMATS = ("csv", "xlsx", "parquet")


@dataclass
class ExportFilter:
    hall_codes: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    model_name: str | None = None  # 部分一致（canonical_name）
    machine_numbers: list[str] | None = None


def _apply(stmt: Select, f: ExportFilter, hall_col, date_col, machine_col, model_col=None) -> Select:
    if f.hall_codes:
        stmt = stmt.where(hall_col.in_(f.hall_codes))
    if f.date_from:
        stmt = stmt.where(date_col >= f.date_from)
    if f.date_to:
        stmt = stmt.where(date_col <= f.date_to)
    if f.machine_numbers:
        stmt = stmt.where(machine_col.in_(f.machine_numbers))
    if f.model_name and model_col is not None:
        stmt = stmt.where(model_col.ilike(f"%{f.model_name}%"))
    return stmt


def daily_rows(session: Session, f: ExportFilter, with_layout: bool = False) -> Iterator[dict[str, Any]]:
    stmt = (
        select(DailyMachineStat, Hall.code, Hall.id, MachineModel.canonical_name, Source.code)
        .join(Hall, Hall.id == DailyMachineStat.hall_id)
        .outerjoin(MachineModel, MachineModel.id == DailyMachineStat.model_id)
        .outerjoin(Source, Source.id == DailyMachineStat.selected_source_id)
        .order_by(Hall.code, DailyMachineStat.business_date, DailyMachineStat.machine_number)
    )
    stmt = _apply(
        stmt,
        f,
        Hall.code,
        DailyMachineStat.business_date,
        DailyMachineStat.machine_number,
        MachineModel.canonical_name,
    )
    layout_cache: dict[tuple[uuid.UUID, date], dict] = {}
    for stat, hall_code, hall_id, model_name, source_code in session.execute(stmt):
        day = stat.business_date
        row: dict[str, Any] = {
            "hall_code": hall_code,
            "business_date": day,
            "weekday": WEEKDAYS_JA[day.weekday()],
            "holiday_name": jpholiday.is_holiday_name(day),
            "machine_number": stat.machine_number,
            "model_name": model_name,
        }
        for name in METRIC_NAMES:
            row[name] = getattr(stat, name)
            row[f"{name}__status"] = stat.field_status.get(name)
        row.update(
            {
                "data_status": stat.data_status,
                "quality_status": stat.quality_status,
                "is_corrected": stat.is_corrected,
                "revision_number": stat.revision_number,
                "source_code": source_code,
                "selected_observation_id": str(stat.selected_observation_id)
                if stat.selected_observation_id
                else None,
            }
        )
        if with_layout:
            from slot_data.layout.features import machine_features

            key = (hall_id, day)
            if key not in layout_cache:
                layout_cache[key] = machine_features(session, hall_id, day)
            feat = layout_cache[key].get(stat.machine_number)
            row.update(
                {
                    "layout_version": feat.layout_version if feat else None,
                    "island_code": feat.island_code if feat else None,
                    "side": feat.side if feat else None,
                    "sequence_no": feat.sequence_no if feat else None,
                    "corner_rank": feat.corner_rank if feat else None,
                    "next_to": " ".join(feat.next_to) if feat else None,
                    "back_to_back": " ".join(feat.back_to_back) if feat else None,
                    "across_aisle": " ".join(feat.across_aisle) if feat else None,
                }
            )
        yield row


def bonus_rows(session: Session, f: ExportFilter) -> Iterator[dict[str, Any]]:
    """当たり履歴は観測系列ごとに全件出力する（同じイベントが複数系列に現れる）。"""
    stmt = (
        select(BonusEvent, BonusEventSeries, Hall.code, SourceObservation.observed_at, Source.code)
        .join(BonusEventSeries, BonusEventSeries.id == BonusEvent.series_id)
        .join(Hall, Hall.id == BonusEventSeries.hall_id)
        .join(SourceObservation, SourceObservation.id == BonusEventSeries.source_observation_id)
        .join(Source, Source.id == SourceObservation.source_id)
        .order_by(
            Hall.code,
            BonusEventSeries.business_date,
            BonusEventSeries.machine_number,
            SourceObservation.observed_at,
            BonusEvent.ordinal,
        )
    )
    stmt = _apply(stmt, f, Hall.code, BonusEventSeries.business_date, BonusEventSeries.machine_number)
    for event, series, hall_code, observed_at, source_code in session.execute(
        stmt.execution_options(yield_per=5000)
    ):
        yield {
            "hall_code": hall_code,
            "business_date": series.business_date,
            "machine_number": series.machine_number,
            "source_code": source_code,
            "series_id": str(series.id),
            "series_observed_at": observed_at,
            "series_status": series.series_status,
            "ordinal": event.ordinal,
            "event_key": event.event_key,
            "source_event_id": event.source_event_id,
            "event_time": event.event_time,
            "hit_games": event.hit_games,
            "cumulative_games": event.cumulative_games,
            "raw_label": event.raw_label,
            "normalized_type": event.normalized_type,
            "payout_medals": event.payout_medals,
            "quality_status": event.quality_status,
        }


def slump_rows(session: Session, f: ExportFilter) -> Iterator[dict[str, Any]]:
    series_alias = aliased(SlumpSeries)
    stmt = (
        select(SlumpPoint, series_alias, Hall.code)
        .join(series_alias, series_alias.id == SlumpPoint.series_id)
        .join(Hall, Hall.id == series_alias.hall_id)
        .order_by(
            Hall.code,
            series_alias.business_date,
            series_alias.machine_number,
            series_alias.captured_at,
            series_alias.series_key,
            SlumpPoint.point_index,
        )
    )
    stmt = _apply(stmt, f, Hall.code, series_alias.business_date, series_alias.machine_number)
    for point, series, hall_code in session.execute(stmt.execution_options(yield_per=10000)):
        yield {
            "hall_code": hall_code,
            "business_date": series.business_date,
            "machine_number": series.machine_number,
            "series_id": str(series.id),
            "series_key": series.series_key,
            "captured_at": series.captured_at,
            "x_axis_type": series.x_axis_type,
            "x_unit": series.x_unit,
            "y_axis_type": series.y_axis_type,
            "y_unit": series.y_unit,
            "origin_type": series.origin_type,
            "derivation_method": series.derivation_method,
            "derivation_version": series.derivation_version,
            "point_index": point.point_index,
            "x_raw": point.x_raw,
            "y_raw": point.y_raw,
            "x_numeric": point.x_numeric,
            "x_timestamp": point.x_timestamp,
            "y_numeric": point.y_numeric,
            "estimated_net_medals": point.estimated_net_medals,
            "quality_status": point.quality_status,
        }


def _cell(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date | Decimal):
        return str(value)
    return value


def write_rows(rows: Iterable[dict[str, Any]], path: Path, fmt: str) -> int:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        return _write_csv(rows, path)
    if fmt == "xlsx":
        return _write_xlsx(rows, path)
    return _write_parquet(rows, path)


def _write_csv(rows: Iterable[dict[str, Any]], path: Path) -> int:
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = None
        for row in rows:
            if writer is None:
                writer = csv.DictWriter(fh, fieldnames=list(row))
                writer.writeheader()
            writer.writerow({k: "" if v is None else _cell(v) for k, v in row.items()})
            count += 1
    return count


def _write_xlsx(rows: Iterable[dict[str, Any]], path: Path) -> int:
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("data")
    count = 0
    header: list[str] | None = None
    for row in rows:
        if header is None:
            header = list(row)
            ws.append(header)
        ws.append([_xlsx_cell(row[k]) for k in header])
        count += 1
        if count >= 1_048_000:
            raise ValueError("Excel の行数上限を超えます。Parquet か CSV を使ってください")
    wb.save(path)
    return count


def _xlsx_cell(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone().replace(tzinfo=None)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _write_parquet(rows: Iterable[dict[str, Any]], path: Path, batch_size: int = 50_000) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    writer = None
    batch: list[dict[str, Any]] = []
    count = 0
    schema = None

    def flush() -> None:
        nonlocal writer, schema
        if not batch:
            return
        columns = {k: [_parquet_cell(r[k]) for r in batch] for k in batch[0]}
        table = pa.table(columns) if schema is None else pa.table(columns, schema=schema)
        if writer is None:
            schema = table.schema
            writer = pq.ParquetWriter(path, schema, compression="zstd")
        writer.write_table(table)
        batch.clear()

    for row in rows:
        batch.append(row)
        count += 1
        if len(batch) >= batch_size:
            flush()
    flush()
    if writer is not None:
        writer.close()
    else:
        pq.write_table(pa.table({}), path)
    return count


def _parquet_cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)  # 精度を落とさない（読み手で decimal 変換）
    return value


EXPORTS = {"daily": daily_rows, "bonus": bonus_rows, "slump": slump_rows}
