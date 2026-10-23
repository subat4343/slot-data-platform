"""日次実績の正規化指標の定義。

``daily_machine_stats`` の数値列と 1 対 1 に対応する。BB/RB/AT/CZ はサイト・機種で意味が異なるため、
観測側（field_metadata）では表示ラベル原文と正規化カテゴリを併存させる。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDef:
    name: str
    label_ja: str
    unit: str
    non_negative: bool
    big: bool  # BIGINT 列か
    # 日次として不自然とみなす絶対値の上限（警告のみ。設定で上書き可能）
    warn_abs_limit: int | None = None


METRICS: tuple[MetricDef, ...] = (
    MetricDef("total_games", "総ゲーム数", "games", True, True, 30000),
    MetricDef("normal_games", "通常時ゲーム数", "games", True, True, 30000),
    MetricDef("net_medals", "差枚", "medals", False, True, 50000),
    MetricDef("bb_count", "BB回数", "count", True, False, 500),
    MetricDef("rb_count", "RB回数", "count", True, False, 500),
    MetricDef("at_count", "AT回数", "count", True, False, 500),
    MetricDef("cz_count", "CZ回数", "count", True, False, 1000),
    MetricDef("first_hit_count", "初当たり回数", "count", True, False, 1000),
    MetricDef("last_games", "最終ゲーム数", "games", True, True, 30000),
    MetricDef("max_net_medals", "最大差枚", "medals", False, True, 50000),
    MetricDef("min_net_medals", "最小差枚", "medals", False, True, 50000),
    MetricDef("max_payout_medals", "最大獲得枚数", "medals", True, True, 100000),
    # 取得元が BB/RB を区別せず合計だけを表示する場合（例: P's CUBE 機種画面の「BONUS」）
    MetricDef("bonus_count", "ボーナス合計回数", "count", True, False, 1000),
)

METRIC_NAMES: tuple[str, ...] = tuple(m.name for m in METRICS)
METRIC_BY_NAME: dict[str, MetricDef] = {m.name: m for m in METRICS}

# 正規化カテゴリ（表示ラベルが「BIG」「ボーナス」「AT」等でも、ここで定義したカテゴリへ割り当てる）
BONUS_CATEGORIES = ("BB", "RB", "AT", "ART", "CZ", "FIRST_HIT", "OTHER")
