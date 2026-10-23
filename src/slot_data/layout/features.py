"""営業日に有効な配置から台ごとの配置特徴（角・隣・背中合わせ・対面）を作る。

配置が未登録・未公開の日は空を返す（実績収集は継続し、配置依存の分析だけ保留する）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from slot_data.domain.enums import RelationType
from slot_data.layout.geometry import Slot, corner_ranks
from slot_data.layout.importer import effective_version
from slot_data.models import HallIsland, MachineAdjacency, MachinePosition


@dataclass
class MachineLayoutFeature:
    machine_number: str
    layout_version: int
    island_code: str
    side: str
    sequence_no: int
    corner_rank: int
    next_to: list[str] = field(default_factory=list)
    back_to_back: list[str] = field(default_factory=list)
    across_aisle: list[str] = field(default_factory=list)

    @property
    def is_corner(self) -> bool:
        return self.corner_rank == 1


def machine_features(session: Session, hall_id: uuid.UUID, day: date) -> dict[str, MachineLayoutFeature]:
    version = effective_version(session, hall_id, day)
    if version is None:
        return {}
    rows = session.execute(
        select(MachinePosition, HallIsland.island_code)
        .join(HallIsland, HallIsland.id == MachinePosition.island_id)
        .where(MachinePosition.layout_version_id == version.id)
    ).all()
    positions = {p.id: (p, code) for p, code in rows}
    ranks = corner_ranks(
        [
            Slot(p.id, code, p.side, p.sequence_no, p.position_kind, p.machine_number)
            for p, code in positions.values()
        ]
    )
    features: dict[str, MachineLayoutFeature] = {}
    for pid, (p, code) in positions.items():
        if p.machine_number is None:
            continue
        features[p.machine_number] = MachineLayoutFeature(
            machine_number=p.machine_number,
            layout_version=version.version_number,
            island_code=code,
            side=p.side,
            sequence_no=p.sequence_no,
            corner_rank=ranks[pid],
        )
    for adj in session.scalars(
        select(MachineAdjacency).where(MachineAdjacency.layout_version_id == version.id)
    ):
        a = positions[adj.position_a_id][0].machine_number
        b = positions[adj.position_b_id][0].machine_number
        attr = {
            RelationType.NEXT_TO: "next_to",
            RelationType.BACK_TO_BACK: "back_to_back",
            RelationType.ACROSS_AISLE: "across_aisle",
        }[RelationType(adj.relation_type)]
        getattr(features[a], attr).append(b)
        getattr(features[b], attr).append(a)
    for f in features.values():
        for attr in ("next_to", "back_to_back", "across_aisle"):
            getattr(f, attr).sort(key=lambda n: (0, int(n)) if n.isdigit() else (1, n))
    return features
