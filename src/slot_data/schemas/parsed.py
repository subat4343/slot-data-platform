"""パーサーの出力（サイト非依存の中間表現）。

パーサーは取得元の原文・単位・ラベルを失わずにこの形へ変換する。値の状態（未公開/取得失敗/解析不能/未確定/真のゼロ）は
``ParsedField.status`` で区別し、値が無いときに 0 を入れない。
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from slot_data.domain.enums import FieldStatus, SeriesOrigin, Severity, ValueOrigin, XAxisType, YAxisType
from slot_data.domain.metrics import METRIC_NAMES


def normalize_machine_number(raw: str) -> str:
    """台番号の正規化: NFKC・空白除去・末尾の「番」除去、数字のみなら先頭ゼロ除去。原文は別に保持する。"""
    value = "".join(unicodedata.normalize("NFKC", raw).split())
    if value.endswith("番"):
        value = value[:-1]
    if value.isdigit():
        value = str(int(value))
    if not value:
        raise ValueError(f"empty machine number: {raw!r}")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParsedField(_Strict):
    value: int | None = None
    status: FieldStatus
    raw: str | None = None  # 表示原文
    label: str | None = None  # 表示ラベル原文（例: 「BIG」「ボーナス」）
    category: str | None = None  # 正規化カテゴリ（BB/RB/AT 等）
    unit: str | None = None
    origin: ValueOrigin = ValueOrigin.DISPLAYED
    definition: str | None = None  # 値の定義（例: 「当日 0 時からの累計」）

    @model_validator(mode="after")
    def _status_consistent(self) -> ParsedField:
        if self.status == FieldStatus.OBSERVED and self.value is None:
            raise ValueError("observed field requires value")
        if self.status != FieldStatus.OBSERVED and self.value is not None:
            raise ValueError(f"value must be None when status is {self.status}")
        return self


class ParsedBonusEvent(_Strict):
    ordinal: int = Field(ge=1)
    source_event_id: str | None = None
    event_time: datetime | None = None
    hit_games: int | None = None
    cumulative_games: int | None = None
    raw_label: str
    normalized_type: str | None = None
    payout_medals: int | None = None
    raw_attributes: dict[str, Any] = Field(default_factory=dict)
    estimated: bool = False


class ParsedBonusSeries(_Strict):
    status: Literal["complete", "partial", "empty", "unavailable"]
    events: list[ParsedBonusEvent] = Field(default_factory=list)

    @field_validator("events")
    @classmethod
    def _unique_ordinals(cls, events: list[ParsedBonusEvent]) -> list[ParsedBonusEvent]:
        ordinals = [e.ordinal for e in events]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("duplicate bonus event ordinal")
        return events


class ParsedSlumpPoint(_Strict):
    x_raw: str | None = None
    y_raw: str | None = None
    x_numeric: Decimal | None = None
    x_timestamp: datetime | None = None
    y_numeric: Decimal | None = None
    estimated_net_medals: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedSlumpSeries(_Strict):
    series_key: str = "main"
    x_axis_type: XAxisType
    x_unit: str | None = None
    y_axis_type: YAxisType
    y_unit: str | None = None
    origin_type: SeriesOrigin = SeriesOrigin.OBSERVED
    derivation_method: str | None = None
    derivation_version: str | None = None
    derived_from_series_key: str | None = None
    # 点の並びは取得元の順序そのまま。間引き・補間・重複除去をしない
    points: list[ParsedSlumpPoint]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedObservation(_Strict):
    source_machine_number: str
    observed_at: datetime
    business_date: date | None = None  # None なら observed_at と店舗の営業日境界から算出
    source_reported_at: datetime | None = None
    source_model_name: str | None = None
    source_model_id: str | None = None
    source_record_id: str | None = None
    fields: dict[str, ParsedField] = Field(default_factory=dict)
    # 集計範囲・データ時点など観測単位のメタデータ
    scope: str | None = None
    source_state: Literal["live", "closed", "final", "unknown"] = "unknown"
    bonus_series: ParsedBonusSeries | None = None
    slump_series: list[ParsedSlumpSeries] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def _known_metrics(cls, fields: dict[str, ParsedField]) -> dict[str, ParsedField]:
        unknown = set(fields) - set(METRIC_NAMES)
        if unknown:
            raise ValueError(f"unknown metrics: {sorted(unknown)}")
        return fields

    @field_validator("observed_at", "source_reported_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @property
    def machine_number(self) -> str:
        return normalize_machine_number(self.source_machine_number)


class ParseIssue(_Strict):
    code: str
    severity: Severity
    message: str
    location: str | None = None


class ParseResult(BaseModel):
    observations: list[ParsedObservation] = Field(default_factory=list)
    issues: list[ParseIssue] = Field(default_factory=list)
    # 原本自体が示す時点（ページの「○時○分更新」等）
    source_reported_at: datetime | None = None
