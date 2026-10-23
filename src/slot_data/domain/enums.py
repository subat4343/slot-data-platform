"""ドメインで使う列挙値。DB では TEXT + CHECK 制約で保持する（値の追加はマイグレーションで行う）。"""

from __future__ import annotations

from enum import StrEnum


class FieldStatus(StrEnum):
    """個々の値の状態。未公開・取得失敗・解析不能・未確定・真の値（ゼロ含む）を区別する。"""

    OBSERVED = "observed"  # 値あり（0 は真のゼロ）
    NOT_PUBLISHED = "not_published"  # 取得元が公開していない
    FETCH_FAILED = "fetch_failed"  # 取得に失敗
    UNPARSEABLE = "unparseable"  # 原文はあるが解析できない
    UNCONFIRMED = "unconfirmed"  # 表示はあるが未確定（集計中など）
    NOT_APPLICABLE = "not_applicable"  # 機種仕様上存在しない（例: AT 機の RB）


class ValueOrigin(StrEnum):
    """値の出自。サイト表示値・計算値・グラフ画像からの推定値を区別する。"""

    DISPLAYED = "displayed"
    CALCULATED = "calculated"
    GRAPH_ESTIMATED = "graph_estimated"
    MANUAL = "manual"


class QualityStatus(StrEnum):
    VALID = "valid"
    WARNING = "warning"
    QUARANTINED = "quarantined"
    UNAVAILABLE = "unavailable"


class DataStatus(StrEnum):
    """日次実績の確定段階。corrected は状態ではなくフラグ（is_corrected）で表す。"""

    PROVISIONAL = "provisional"
    FINAL_CANDIDATE = "final_candidate"
    FINAL = "final"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class ParseStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CaptureStatus(StrEnum):
    STORED = "stored"  # 原本保存済み・解析対象
    DUPLICATE_CONTENT = "duplicate_content"  # 既存原本と同一内容（取得イベントのみ記録）
    MANUAL_IMPORT = "manual_import"  # 人手で投入されたファイル


class ReviewStatus(StrEnum):
    PENDING = "pending"
    AUTO_MATCHED = "auto_matched"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class XAxisType(StrEnum):
    TIME = "time"
    CUMULATIVE_GAMES = "cumulative_games"
    PIXEL = "pixel"
    UNKNOWN = "unknown"


class YAxisType(StrEnum):
    NET_MEDALS = "net_medals"
    PIXEL = "pixel"
    UNKNOWN = "unknown"


class SeriesOrigin(StrEnum):
    OBSERVED = "observed"  # 取得元が数値で提供
    ESTIMATED = "estimated"  # 画像等からの推定（派生系列）


class LayoutStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    REJECTED = "rejected"


class PositionKind(StrEnum):
    MACHINE = "machine"
    EMPTY = "empty"  # 台の置かれていない台枠（列は途切れない）
    AISLE = "aisle"  # 通路（列を分断する）
    PILLAR = "pillar"  # 柱など（列を分断する）


class RelationType(StrEnum):
    NEXT_TO = "next_to"
    ACROSS_AISLE = "across_aisle"
    BACK_TO_BACK = "back_to_back"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class IssueStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class RevisionReason(StrEnum):
    INITIAL = "initial"
    SOURCE_UPDATE = "source_update"
    STATUS_CHANGE = "status_change"
    CORRECTION = "correction"  # final_candidate / final 到達後の値変化
    MANUAL_CORRECTION = "manual_correction"
    SOURCE_SWITCH = "source_switch"


def values_of(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]
