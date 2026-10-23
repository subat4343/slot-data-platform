"""配置の位置関係と角台の算出（DB 非依存の純粋関数）。

前提（ADR-0009）:
- sequence_no は同じ島の両側で共通の物理的基準端から数えた台枠番号
- 通路(aisle)・柱(pillar) は列を分断する。空き台枠(empty) は列を分断しないが台は無い
- 欠番（sequence_no が飛んでいる）は位置不明として隣接を生成しない
- 隣（next_to）: 同じ島・同じ側で sequence_no が 1 違いの machine 同士
- 背中合わせ（back_to_back）: 同じ島のちょうど 2 側で同じ sequence_no の machine 同士
- 対面（across_aisle）: 島どうしの向かい合わせは CSV から一意に決まらないため自動生成せず、人が登録する
- 角: 分断されない列（区間）の端からの台枠数。端の台枠が空きなら、その隣の台は角2
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from slot_data.domain.enums import PositionKind, RelationType

BREAKING_KINDS = {PositionKind.AISLE, PositionKind.PILLAR}


@dataclass(frozen=True)
class Slot:
    key: object  # 位置の識別子（DB では position id、検証時は行番号）
    island_code: str
    side: str
    sequence_no: int
    position_kind: str
    machine_number: str | None


@dataclass(frozen=True)
class Relation:
    a: object
    b: object
    relation_type: str


def _rows(slots: list[Slot]) -> dict[tuple[str, str], list[Slot]]:
    rows: dict[tuple[str, str], list[Slot]] = defaultdict(list)
    for slot in slots:
        rows[(slot.island_code, slot.side)].append(slot)
    for row in rows.values():
        row.sort(key=lambda s: s.sequence_no)
    return rows


def derive_relations(slots: list[Slot]) -> list[Relation]:
    relations: list[Relation] = []
    rows = _rows(slots)
    for row in rows.values():
        for left, right in zip(row, row[1:], strict=False):
            if (
                right.sequence_no - left.sequence_no == 1
                and left.position_kind == PositionKind.MACHINE
                and right.position_kind == PositionKind.MACHINE
            ):
                relations.append(Relation(left.key, right.key, RelationType.NEXT_TO))
    sides_by_island: dict[str, list[str]] = defaultdict(list)
    for island, side in rows:
        sides_by_island[island].append(side)
    for island, sides in sides_by_island.items():
        if len(sides) != 2:
            continue
        first = {
            s.sequence_no: s for s in rows[(island, sides[0])] if s.position_kind == PositionKind.MACHINE
        }
        for slot in rows[(island, sides[1])]:
            other = first.get(slot.sequence_no)
            if other is not None and slot.position_kind == PositionKind.MACHINE:
                relations.append(Relation(other.key, slot.key, RelationType.BACK_TO_BACK))
    return relations


def corner_ranks(slots: list[Slot]) -> dict[object, int]:
    """machine の各位置について、所属区間の近い方の端からの台枠数（1=角、2=角2 ...）。"""
    ranks: dict[object, int] = {}
    for row in _rows(slots).values():
        segments: list[list[Slot]] = [[]]
        for slot in row:
            if slot.position_kind in BREAKING_KINDS:
                segments.append([])
            else:
                segments[-1].append(slot)
        for segment in segments:
            if not segment:
                continue
            start, end = segment[0].sequence_no, segment[-1].sequence_no
            for slot in segment:
                if slot.position_kind == PositionKind.MACHINE:
                    ranks[slot.key] = min(slot.sequence_no - start, end - slot.sequence_no) + 1
    return ranks


def sequence_gaps(slots: list[Slot]) -> list[tuple[str, str, int, int]]:
    gaps = []
    for (island, side), row in _rows(slots).items():
        for left, right in zip(row, row[1:], strict=False):
            if right.sequence_no - left.sequence_no > 1:
                gaps.append((island, side, left.sequence_no, right.sequence_no))
    return gaps
