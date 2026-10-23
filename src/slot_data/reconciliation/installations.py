"""観測された機種から設置期間（machine_installations）を維持する。

機種入替は配置版を作らず、ここで設置期間を区切る。過去日の観測（バックフィル）で既存期間と矛盾する場合は
期間を書き換えず品質課題としてレビューに回す。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from slot_data.models import MachineInstallation


@dataclass(frozen=True)
class InstallationLink:
    installation_id: uuid.UUID | None
    conflict: str | None = None


def _covers(inst: MachineInstallation, day: date) -> bool:
    return inst.valid_from <= day and (inst.valid_to is None or day < inst.valid_to)


def link_installation(
    session: Session,
    *,
    hall_id: uuid.UUID,
    machine_number: str,
    business_date: date,
    model_id: uuid.UUID | None,
    source_id: uuid.UUID | None,
    evidence_raw_id: uuid.UUID | None,
) -> InstallationLink:
    installations = list(
        session.scalars(
            select(MachineInstallation)
            .where(
                MachineInstallation.hall_id == hall_id, MachineInstallation.machine_number == machine_number
            )
            .order_by(MachineInstallation.valid_from)
            .with_for_update()
        )
    )
    covering = next((i for i in installations if _covers(i, business_date)), None)

    if model_id is None:
        # 機種が未同定なら設置期間は変更しない（根拠不足）
        return InstallationLink(covering.id if covering else None)

    if covering is not None:
        if covering.model_id == model_id:
            return InstallationLink(covering.id)
        if covering.model_id is None and covering.valid_from == business_date:
            covering.model_id = model_id
            return InstallationLink(covering.id)
        if covering.valid_to is None and business_date > covering.valid_from:
            # 機種入替: 現在の設置を締めて新しい設置を開始
            covering.valid_to = business_date
            session.flush()
            return InstallationLink(
                _new(
                    session,
                    hall_id,
                    machine_number,
                    business_date,
                    None,
                    model_id,
                    source_id,
                    evidence_raw_id,
                )
            )
        return InstallationLink(
            None,
            conflict=(
                f"installation {covering.id} ({covering.valid_from}..{covering.valid_to}) has model "
                f"{covering.model_id}, observed {model_id}"
            ),
        )

    later = [i for i in installations if i.valid_from > business_date]
    earlier = [i for i in installations if i.valid_to is not None and i.valid_to <= business_date]
    nxt = later[0] if later else None
    prev = earlier[-1] if earlier else None

    if nxt is not None and nxt.model_id == model_id and (prev is None or prev.valid_to <= business_date):
        # バックフィル: 同一機種の後続設置の開始日を前倒しする
        nxt.valid_from = business_date
        session.flush()
        return InstallationLink(nxt.id)
    if prev is not None and prev.model_id == model_id and prev.valid_to == business_date:
        prev.valid_to = nxt.valid_from if nxt else None
        session.flush()
        return InstallationLink(prev.id)
    return InstallationLink(
        _new(
            session,
            hall_id,
            machine_number,
            business_date,
            nxt.valid_from if nxt else None,
            model_id,
            source_id,
            evidence_raw_id,
        )
    )


def _new(
    session, hall_id, machine_number, valid_from, valid_to, model_id, source_id, evidence_raw_id
) -> uuid.UUID:
    inst = MachineInstallation(
        hall_id=hall_id,
        machine_number=machine_number,
        model_id=model_id,
        valid_from=valid_from,
        valid_to=valid_to,
        source_id=source_id,
        evidence_raw_id=evidence_raw_id,
    )
    session.add(inst)
    session.flush()
    return inst.id
