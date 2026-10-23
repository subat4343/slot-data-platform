"""手動インポート形式のパーサー。

サイトの自動取得が利用条件上できない場合の代替経路。人が閲覧・記録した値を標準形式のファイルにまとめて投入する。
ファイルそのものを原本として保存し、以降は自動取得と同じパイプライン（解析版管理・観測追記・採用値更新）を通る。

JSON（manual-v1）: 当たり履歴・スランプ点まで表現できる完全形式。仕様は docs/source_contracts.md。
CSV（manual-csv-v1）: 日次指標のみの簡易形式。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from slot_data.adapters.base import ParseContext
from slot_data.domain.enums import FieldStatus, Severity, ValueOrigin
from slot_data.domain.metrics import METRIC_NAMES
from slot_data.schemas.parsed import (
    ParsedBonusSeries,
    ParsedField,
    ParsedObservation,
    ParsedSlumpSeries,
    ParseIssue,
    ParseResult,
)

JSON_FORMAT = "slot-data/manual-v1"

# CSV セルの特殊値。空欄は「未公開」とみなし、0 を補完しない
CSV_STATUS_TOKENS = {
    "": FieldStatus.NOT_PUBLISHED,
    "-": FieldStatus.NOT_PUBLISHED,
    "#not_published": FieldStatus.NOT_PUBLISHED,
    "#fetch_failed": FieldStatus.FETCH_FAILED,
    "#unparseable": FieldStatus.UNPARSEABLE,
    "#unconfirmed": FieldStatus.UNCONFIRMED,
    "#not_applicable": FieldStatus.NOT_APPLICABLE,
}


def _parse_dt(value: str, tz: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))


def parse_int_text(raw: str) -> int | None:
    """「1,234」「+1,234」「−567」「1234枚」等の表示値を整数へ。解析できなければ None。"""
    text = (
        raw.strip().replace(",", "").replace("，", "").replace("−", "-").replace("－", "-").replace("＋", "+")
    )
    for suffix in ("枚", "G", "g", "回", "ゲーム"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number != number.to_integral_value():
        return None
    return int(number)


def _field_from_json(name: str, spec: Any) -> ParsedField:
    if spec is None:
        return ParsedField(status=FieldStatus.NOT_PUBLISHED)
    if isinstance(spec, bool):
        raise ValueError(f"{name}: boolean is not a valid metric value")
    if isinstance(spec, int):
        return ParsedField(value=spec, status=FieldStatus.OBSERVED, origin=ValueOrigin.MANUAL)
    if isinstance(spec, dict):
        data = dict(spec)
        data.setdefault("origin", ValueOrigin.MANUAL)
        if "status" not in data:
            data["status"] = (
                FieldStatus.OBSERVED if data.get("value") is not None else FieldStatus.NOT_PUBLISHED
            )
        return ParsedField.model_validate(data)
    raise ValueError(f"{name}: unsupported value {spec!r}")


class ManualJsonParser:
    name = "manual_json"
    version = "1.0.0"

    def parse(self, data: bytes, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        try:
            doc = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            result.issues.append(ParseIssue(code="INVALID_JSON", severity=Severity.ERROR, message=str(exc)))
            return result
        if doc.get("format") != JSON_FORMAT:
            result.issues.append(
                ParseIssue(
                    code="UNKNOWN_FORMAT", severity=Severity.ERROR, message=f"format must be {JSON_FORMAT}"
                )
            )
            return result
        if doc.get("hall_code") and doc["hall_code"] != ctx.hall_code:
            result.issues.append(
                ParseIssue(
                    code="HALL_MISMATCH",
                    severity=Severity.ERROR,
                    message=f"file hall_code {doc['hall_code']} != {ctx.hall_code}",
                )
            )
            return result

        default_observed = doc.get("observed_at")
        default_date = doc.get("business_date")
        reported = doc.get("source_reported_at")
        if reported:
            result.source_reported_at = _parse_dt(reported, ctx.timezone)
        for index, machine in enumerate(doc.get("machines", [])):
            location = f"machines[{index}]"
            try:
                observed_at = machine.get("observed_at") or default_observed
                if not observed_at:
                    raise ValueError("observed_at is required")
                business_date = machine.get("business_date") or default_date
                fields = {k: _field_from_json(k, v) for k, v in (machine.get("fields") or {}).items()}
                bonus = machine.get("bonus_events")
                slumps = machine.get("slump_series") or []
                obs = ParsedObservation(
                    source_machine_number=str(machine["machine_number"]),
                    observed_at=_parse_dt(observed_at, ctx.timezone),
                    business_date=date.fromisoformat(business_date) if business_date else None,
                    source_reported_at=result.source_reported_at,
                    source_model_name=machine.get("model_name"),
                    source_model_id=machine.get("model_id"),
                    source_record_id=machine.get("record_id"),
                    fields=fields,
                    scope=machine.get("scope") or doc.get("scope"),
                    source_state=machine.get("source_state") or doc.get("source_state") or "unknown",
                    bonus_series=ParsedBonusSeries.model_validate(bonus) if bonus is not None else None,
                    slump_series=[ParsedSlumpSeries.model_validate(s) for s in slumps],
                    extra=machine.get("extra") or {},
                )
                obs.machine_number  # noqa: B018  台番号の正規化可否を検証
            except (KeyError, ValueError, ValidationError) as exc:
                result.issues.append(
                    ParseIssue(
                        code="INVALID_RECORD", severity=Severity.ERROR, message=str(exc), location=location
                    )
                )
                continue
            result.observations.append(obs)
        return result


class ManualCsvParser:
    """列: machine_number, model_name, business_date, observed_at, <metric...>, source_state（任意）。

    数値セルの空欄/「-」は未公開。``#fetch_failed`` 等で状態を明示できる。数値として読めない値は解析不能として原文を残す。
    """

    name = "manual_csv"
    version = "1.0.0"

    def parse(self, data: bytes, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp932")
        reader = csv.DictReader(io.StringIO(text))
        headers = set(reader.fieldnames or [])
        required = {"machine_number", "observed_at"}
        if not required <= headers:
            result.issues.append(
                ParseIssue(
                    code="MISSING_COLUMNS",
                    severity=Severity.ERROR,
                    message=f"required columns missing: {sorted(required - headers)}",
                )
            )
            return result
        unknown = (
            headers
            - required
            - {"model_name", "model_id", "business_date", "source_state", "scope"}
            - set(METRIC_NAMES)
        )
        if unknown:
            result.issues.append(
                ParseIssue(
                    code="UNKNOWN_COLUMNS", severity=Severity.WARNING, message=f"ignored: {sorted(unknown)}"
                )
            )
        for line_no, row in enumerate(reader, start=2):
            location = f"line {line_no}"
            try:
                fields: dict[str, ParsedField] = {}
                for metric in METRIC_NAMES:
                    if metric not in headers:
                        continue
                    raw = (row.get(metric) or "").strip()
                    if raw in CSV_STATUS_TOKENS:
                        fields[metric] = ParsedField(status=CSV_STATUS_TOKENS[raw], raw=raw or None)
                        continue
                    value = parse_int_text(raw)
                    if value is None:
                        fields[metric] = ParsedField(status=FieldStatus.UNPARSEABLE, raw=raw)
                    else:
                        fields[metric] = ParsedField(
                            value=value, status=FieldStatus.OBSERVED, raw=raw, origin=ValueOrigin.MANUAL
                        )
                obs = ParsedObservation(
                    source_machine_number=row["machine_number"],
                    observed_at=_parse_dt(row["observed_at"], ctx.timezone),
                    business_date=date.fromisoformat(row["business_date"])
                    if row.get("business_date")
                    else None,
                    source_model_name=row.get("model_name") or None,
                    source_model_id=row.get("model_id") or None,
                    fields=fields,
                    scope=row.get("scope") or None,
                    source_state=row.get("source_state") or "unknown",
                )
                obs.machine_number  # noqa: B018
            except (KeyError, ValueError, ValidationError) as exc:
                result.issues.append(
                    ParseIssue(
                        code="INVALID_RECORD", severity=Severity.ERROR, message=str(exc), location=location
                    )
                )
                continue
            result.observations.append(obs)
        return result


PARSERS = {"json": ManualJsonParser(), "csv": ManualCsvParser()}


def parser_for_file(filename: str) -> tuple[ManualJsonParser | ManualCsvParser, str]:
    lower = filename.lower()
    if lower.endswith(".json"):
        return PARSERS["json"], "application/json"
    if lower.endswith(".csv"):
        return PARSERS["csv"], "text/csv"
    raise ValueError(f"unsupported manual import file: {filename}（.json / .csv のみ）")
