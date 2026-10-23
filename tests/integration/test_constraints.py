"""Phase 1: DB 制約・トリガーのテスト。"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from slot_data.domain.ids import uuid7
from slot_data.models import (
    DailyMachineStat,
    HallIsland,
    HallLayoutVersion,
    MachineAdjacency,
    MachineInstallation,
    MachinePosition,
    SourceMapping,
)

pytestmark = pytest.mark.integration


def _expect_db_error(session, obj_or_stmt):
    with pytest.raises((IntegrityError, DBAPIError)):
        if isinstance(obj_or_stmt, str):
            session.execute(text(obj_or_stmt))
        else:
            session.add(obj_or_stmt)
        session.flush()
    session.rollback()


def test_daily_stats_unique_and_checks(session, hall):
    hall_id = hall.id
    session.add(
        DailyMachineStat(
            hall_id=hall_id,
            business_date=date(2026, 10, 20),
            machine_number="301",
            data_status="provisional",
            quality_status="valid",
            revision_number=1,
            net_medals=-1500,  # 差枚は負値可
        )
    )
    session.commit()
    dup = DailyMachineStat(
        hall_id=hall_id,
        business_date=date(2026, 10, 20),
        machine_number="301",
        data_status="provisional",
        quality_status="valid",
        revision_number=1,
    )
    _expect_db_error(session, dup)
    negative = DailyMachineStat(
        hall_id=hall_id,
        business_date=date(2026, 10, 21),
        machine_number="301",
        data_status="provisional",
        quality_status="valid",
        revision_number=1,
        total_games=-1,
    )
    _expect_db_error(session, negative)
    bad_status = DailyMachineStat(
        hall_id=hall_id,
        business_date=date(2026, 10, 22),
        machine_number="301",
        data_status="corrected",  # corrected は状態ではなくフラグ
        quality_status="valid",
        revision_number=1,
    )
    _expect_db_error(session, bad_status)


def test_installation_periods_cannot_overlap(session, hall):
    session.add(
        MachineInstallation(
            hall_id=hall.id, machine_number="301", valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 1)
        )
    )
    session.commit()
    # 半開区間なので終了日=開始日は重ならない
    session.add(MachineInstallation(hall_id=hall.id, machine_number="301", valid_from=date(2026, 3, 1)))
    session.commit()
    _expect_db_error(
        session,
        MachineInstallation(
            hall_id=hall.id, machine_number="301", valid_from=date(2026, 2, 15), valid_to=date(2026, 2, 20)
        ),
    )
    _expect_db_error(
        session,
        MachineInstallation(
            hall_id=hall.id, machine_number="302", valid_from=date(2026, 3, 1), valid_to=date(2026, 3, 1)
        ),
    )


def test_append_only_tables_reject_update_delete(ctx, hall, tmp_path):
    from slot_data.ingestion.pipeline import IngestionPipeline

    path = tmp_path / "obs.json"
    path.write_text(
        '{"format":"slot-data/manual-v1","hall_code":"H001","observed_at":"2026-10-20T23:00:00+09:00",'
        '"business_date":"2026-10-20","machines":[{"machine_number":"301","fields":{"total_games":100}}]}',
        encoding="utf-8",
    )
    IngestionPipeline(ctx).import_file("manual", "H001", path)
    with ctx.session_factory() as s:
        for stmt in (
            "UPDATE raw_payloads SET byte_size = 0",
            "DELETE FROM source_observations",
            "UPDATE daily_stat_revisions SET reason = 'initial'",
            "DELETE FROM machine_snapshots",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                s.execute(text(stmt))
            s.rollback()
        # finished な parse_run は変更不可
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE parse_runs SET status = 'failed'"))
        s.rollback()


def test_source_mapping_entity_must_exist(session, hall):
    from slot_data.services.masters import get_source

    source = get_source(session, "pscube")
    _expect_db_error(
        session,
        SourceMapping(
            source_id=source.id,
            entity_type="machine_model",
            external_id="X1",
            internal_entity_id=uuid7(),
            review_status="confirmed",
        ),
    )
    _expect_db_error(
        session,
        SourceMapping(
            source_id=source.id, entity_type="machine_model", external_id="X2", review_status="confirmed"
        ),
    )


def _layout(session, hall, status="draft", version=1, valid_from=date(2026, 10, 1), valid_to=None):
    v = HallLayoutVersion(
        hall_id=hall.id,
        version_number=version,
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        created_by="test",
    )
    session.add(v)
    session.flush()
    return v


def test_layout_integrity(session, hall):
    v1 = _layout(session, hall)
    v2 = _layout(session, hall, version=2, valid_from=date(2026, 11, 1))
    island1 = HallIsland(layout_version_id=v1.id, island_code="A", display_name="A")
    island2 = HallIsland(layout_version_id=v2.id, island_code="A", display_name="A")
    session.add_all([island1, island2])
    session.flush()
    p1 = MachinePosition(
        layout_version_id=v1.id, island_id=island1.id, machine_number="301", side="L", sequence_no=1
    )
    p2 = MachinePosition(
        layout_version_id=v1.id, island_id=island1.id, machine_number="302", side="L", sequence_no=2
    )
    session.add_all([p1, p2])
    session.commit()

    # 別の版の島を参照できない（複合 FK）
    _expect_db_error(
        session,
        MachinePosition(
            layout_version_id=v1.id, island_id=island2.id, machine_number="303", side="L", sequence_no=3
        ),
    )
    # 同一位置の重複
    _expect_db_error(
        session,
        MachinePosition(
            layout_version_id=v1.id, island_id=island1.id, machine_number="304", side="L", sequence_no=1
        ),
    )
    # 版内の台番号重複
    _expect_db_error(
        session,
        MachinePosition(
            layout_version_id=v1.id, island_id=island1.id, machine_number="301", side="R", sequence_no=1
        ),
    )
    a, b = sorted([p1.id, p2.id])
    # 自己参照・順序逆転の禁止
    _expect_db_error(
        session,
        MachineAdjacency(layout_version_id=v1.id, position_a_id=a, position_b_id=a, relation_type="next_to"),
    )
    _expect_db_error(
        session,
        MachineAdjacency(layout_version_id=v1.id, position_a_id=b, position_b_id=a, relation_type="next_to"),
    )
    # 別の版の位置との関係は作れない
    _expect_db_error(
        session,
        MachineAdjacency(layout_version_id=v2.id, position_a_id=a, position_b_id=b, relation_type="next_to"),
    )
    session.add(
        MachineAdjacency(layout_version_id=v1.id, position_a_id=a, position_b_id=b, relation_type="next_to")
    )
    session.commit()


def test_published_layout_is_immutable_and_non_overlapping(session, hall):
    v1 = _layout(session, hall)
    island = HallIsland(layout_version_id=v1.id, island_code="A", display_name="A")
    session.add(island)
    session.flush()
    v1.status = "published"
    session.commit()

    _expect_db_error(session, HallIsland(layout_version_id=v1.id, island_code="B", display_name="B"))
    _expect_db_error(
        session, f"UPDATE hall_layout_versions SET valid_from = '2026-09-01' WHERE id = '{v1.id}'"
    )
    _expect_db_error(session, f"DELETE FROM hall_layout_versions WHERE id = '{v1.id}'")
    # 後続版公開に伴う valid_to の確定は許可
    session.execute(text(f"UPDATE hall_layout_versions SET valid_to = '2026-11-01' WHERE id = '{v1.id}'"))
    session.commit()
    # 一度確定した valid_to は変更不可
    _expect_db_error(session, f"UPDATE hall_layout_versions SET valid_to = '2026-12-01' WHERE id = '{v1.id}'")
    # 公開済み期間の重複禁止（draft は重複可）
    _layout(session, hall, version=2, valid_from=date(2026, 10, 15), status="draft")
    session.commit()
    _expect_db_error(
        session,
        HallLayoutVersion(
            hall_id=hall.id,
            version_number=3,
            valid_from=date(2026, 10, 15),
            status="published",
            created_by="t",
        ),
    )
