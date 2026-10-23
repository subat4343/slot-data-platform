"""品質課題の理由コードと記録。"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from slot_data.domain.enums import IssueStatus, Severity
from slot_data.domain.ids import uuid7
from slot_data.domain.timeutil import utcnow
from slot_data.models import DataQualityIssue

# 理由コード → (既定の重大度, 説明)
ISSUE_CODES: dict[str, tuple[Severity, str]] = {
    "NEGATIVE_VALUE": (Severity.ERROR, "負であってはならない値（ゲーム数・回数）が負"),
    "OUT_OF_RANGE": (Severity.WARNING, "日次として不自然な範囲外の値"),
    "NORMAL_GT_TOTAL": (Severity.ERROR, "通常時ゲーム数が総ゲーム数を超える"),
    "MAX_LT_MIN": (Severity.WARNING, "最大差枚が最小差枚より小さい（定義が確かな場合のみ）"),
    "BUSINESS_DATE_MISMATCH": (Severity.ERROR, "観測時刻が営業日の開始より前"),
    "DUPLICATE_MACHINE_NUMBER": (Severity.ERROR, "同一原本内で台番号が重複"),
    "INSTALLATION_CONFLICT": (Severity.WARNING, "機種設置期間と観測機種が矛盾"),
    "MODEL_MAPPING_PENDING": (Severity.INFO, "機種の対応付けがレビュー待ち"),
    "BONUS_ORDER_INCONSISTENT": (Severity.WARNING, "当たり履歴の累積ゲーム数が順番と逆行"),
    "BONUS_ORDER_CHANGED": (Severity.INFO, "前回系列から当たり履歴の順序・内容が訂正された"),
    "SLUMP_AXIS_UNKNOWN": (Severity.WARNING, "スランプグラフの軸種別・単位が不明"),
    "SLUMP_X_NOT_MONOTONIC": (Severity.WARNING, "スランプグラフの横軸が逆行"),
    "SLUMP_DUPLICATE_X": (Severity.INFO, "スランプグラフに同一横軸値の点が複数ある（そのまま保持）"),
    "SOURCE_MISMATCH": (Severity.WARNING, "取得元間で値が食い違う"),
    "VALUE_REGRESSION": (Severity.WARNING, "同一取得元の後続観測で累積値が減少"),
    "PARSE_FAILED": (Severity.ERROR, "原本を解析できない"),
    "PARSE_RECORD_REJECTED": (Severity.ERROR, "原本内のレコードを解析できない"),
    "FETCH_FAILED": (Severity.WARNING, "取得に失敗（欠損理由）"),
    "RAW_MISSING": (Severity.CRITICAL, "原本ファイルが存在しない（参照切れ）"),
    "RAW_HASH_MISMATCH": (Severity.CRITICAL, "原本ファイルのハッシュが一致しない"),
    "RAW_ORPHAN_FILE": (Severity.INFO, "DB から参照されない原本ファイル"),
    "RAW_UNPARSED": (Severity.WARNING, "解析されていない原本"),
    "INSTALLATION_OVERLAP": (Severity.CRITICAL, "設置期間が重複"),
}


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    field: str | None = None
    severity: Severity | None = None
    details: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def effective_severity(self) -> Severity:
        return self.severity or ISSUE_CODES[self.code][0]


def is_fatal(findings: list[Finding]) -> bool:
    return any(f.effective_severity in (Severity.ERROR, Severity.CRITICAL) for f in findings)


def record_issue(
    session: Session,
    code: str,
    *,
    entity_type: str,
    dedup_key: str,
    entity_id: uuid.UUID | None = None,
    hall_id: uuid.UUID | None = None,
    business_date: date | None = None,
    machine_number: str | None = None,
    severity: Severity | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """同一コード・同一 dedup_key は 1 件にまとめる（再実行しても重複起票しない）。"""
    if code not in ISSUE_CODES:
        raise KeyError(f"unknown issue code: {code}")
    stmt = (
        insert(DataQualityIssue)
        .values(
            id=uuid7(),
            detected_at=utcnow(),
            issue_code=code,
            severity=(severity or ISSUE_CODES[code][0]).value,
            entity_type=entity_type,
            entity_id=entity_id,
            hall_id=hall_id,
            business_date=business_date,
            machine_number=machine_number,
            dedup_key=dedup_key,
            details=details or {},
            status=IssueStatus.OPEN.value,
        )
        .on_conflict_do_nothing(index_elements=["issue_code", "dedup_key"])
    )
    session.execute(stmt)
