"""Phase 4: 配置 CSV/Excel の取り込み・プレビュー・公開版管理・過去日付での配置再現。"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from slot_data.layout.features import machine_features
from slot_data.layout.importer import LayoutError, publish_import, reject_import, set_relation, stage_import
from slot_data.models import HallLayoutVersion, LayoutImport, MachineAdjacency

pytestmark = pytest.mark.integration

HEADER = "hall_code,layout_valid_from,island_code,side,sequence_no,machine_number,position_kind,notes\n"


def _csv(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


V1 = """H001,2026-10-01,A,LEFT,1,301,,
H001,2026-10-01,A,LEFT,2,302,,
H001,2026-10-01,A,LEFT,3,303,,
H001,2026-10-01,A,RIGHT,1,311,,
H001,2026-10-01,A,RIGHT,2,,empty,空き台
H001,2026-10-01,A,RIGHT,3,313,,
H001,2026-10-01,B,LEFT,1,401,,
H001,2026-10-01,B,LEFT,2,402,,
"""


def _publish(session, path, who="tester"):
    staged = stage_import(session, path, who)
    assert staged.draft_version_id is not None, staged.report
    version = publish_import(session, staged.import_id, who)
    session.commit()
    return staged, version


def test_validate_rejects_bad_csv_without_draft(session, hall, tmp_path):
    bad = _csv(
        tmp_path,
        "bad.csv",
        """H001,2026-10-01,A,LEFT,1,301,,
H001,2026-10-01,A,LEFT,1,302,,
H001,2026-10-01,A,LEFT,2,301,,
H001,2026-10-01,A,LEFT,x,303,,
H001,2026-10-01,A,LEFT,4,,aisle,
H001,2026-10-01,A,LEFT,5,,machine,
""",
    )
    staged = stage_import(session, bad, "tester")
    session.commit()
    codes = {e["code"] for e in staged.report["errors"]}
    assert {
        "DUPLICATE_POSITION",
        "DUPLICATE_MACHINE_NUMBER",
        "INVALID_SEQUENCE",
        "MISSING_MACHINE_NUMBER",
    } <= codes
    assert staged.draft_version_id is None
    assert session.scalar(select(func.count()).select_from(HallLayoutVersion)) == 0
    assert session.get(LayoutImport, staged.import_id).status == "invalid"


def test_import_preview_publish_and_features(session, hall, tmp_path):
    staged = stage_import(session, _csv(tmp_path, "v1.csv", V1), "tester")
    assert staged.preview["added"] == ["301", "302", "303", "311", "313", "401", "402"]
    assert staged.preview["base_version"] is None
    # draft のうちは分析に使わない
    assert machine_features(session, hall.id, date(2026, 10, 5)) == {}
    version = publish_import(session, staged.import_id, "tester")
    session.commit()
    assert version.status == "published" and version.version_number == 1

    f = machine_features(session, hall.id, date(2026, 10, 5))
    assert f["301"].corner_rank == 1 and f["302"].corner_rank == 2 and f["303"].corner_rank == 1
    assert f["302"].next_to == ["301", "303"]
    assert f["301"].back_to_back == ["311"]
    assert f["302"].back_to_back == []  # 向かいは空き台枠
    assert f["311"].next_to == [] and f["311"].corner_rank == 1
    assert f["313"].corner_rank == 1
    # 発効前の日付には配置が無い
    assert machine_features(session, hall.id, date(2026, 9, 30)) == {}


def test_layout_switch_and_past_reproduction(session, hall, tmp_path):
    _publish(session, _csv(tmp_path, "v1.csv", V1))
    # 台移動・台番号変更・島再編を含む新版
    v2 = (
        V1.replace("2026-10-01", "2026-11-01")
        .replace("A,LEFT,3,303", "A,LEFT,3,305")
        .replace("B,LEFT,2,402", "B,LEFT,3,402")
    )
    staged = stage_import(session, _csv(tmp_path, "v2.csv", v2), "tester")
    assert staged.preview["base_version"] == 1
    assert staged.preview["added"] == ["305"] and staged.preview["removed"] == ["303"]
    assert staged.preview["renumbered_candidates"] == [{"slot": ["A", "LEFT", 3], "from": "303", "to": "305"}]
    assert [m["machine_number"] for m in staged.preview["moved"]] == ["402"]
    publish_import(session, staged.import_id, "tester")
    session.commit()

    versions = list(session.scalars(select(HallLayoutVersion).order_by(HallLayoutVersion.version_number)))
    assert [(v.valid_from, v.valid_to, v.status) for v in versions] == [
        (date(2026, 10, 1), date(2026, 11, 1), "published"),
        (date(2026, 11, 1), None, "published"),
    ]
    old = machine_features(session, hall.id, date(2026, 10, 31))
    new = machine_features(session, hall.id, date(2026, 11, 1))
    assert "303" in old and "303" not in new and "305" in new
    assert old["402"].next_to == ["401"]
    assert new["402"].next_to == []  # 移動で 401 と離れた（欠番 2）
    assert old["301"].layout_version == 1 and new["301"].layout_version == 2


def test_identical_layout_needs_no_new_version(session, hall, tmp_path):
    _publish(session, _csv(tmp_path, "v1.csv", V1))
    same = stage_import(session, _csv(tmp_path, "same.csv", V1.replace("2026-10-01", "2026-12-01")), "tester")
    assert any(e["code"] == "NO_CHANGES" for e in same.report["errors"])
    assert same.draft_version_id is None


def test_cannot_publish_overlapping_or_earlier_period(session, hall, tmp_path):
    _publish(session, _csv(tmp_path, "v1.csv", V1))
    earlier = stage_import(
        session, _csv(tmp_path, "early.csv", V1.replace("2026-10-01", "2026-09-01")), "tester"
    )
    assert any(e["code"] == "PERIOD_OVERLAP" for e in earlier.report["errors"])


def test_manual_relation_override_and_conflict_check(session, hall, tmp_path):
    staged = stage_import(session, _csv(tmp_path, "v1.csv", V1), "tester")
    set_relation(session, staged.draft_version_id, "303", "401", "across_aisle", notes="通路を挟んで対面")
    publish_import(session, staged.import_id, "tester")
    session.commit()
    f = machine_features(session, hall.id, date(2026, 10, 2))
    assert f["303"].across_aisle == ["401"] and f["401"].across_aisle == ["303"]
    manual = session.scalar(select(MachineAdjacency).where(MachineAdjacency.relation_type == "across_aisle"))
    assert manual.is_manual_override
    # 公開済み版は補正できない（新しい版を作る）
    with pytest.raises(LayoutError):
        set_relation(session, staged.draft_version_id, "301", "401", "across_aisle")
    session.rollback()

    v2 = stage_import(
        session,
        _csv(
            tmp_path, "v2.csv", V1.replace("2026-10-01", "2026-11-01").replace("B,LEFT,2,402", "B,LEFT,2,403")
        ),
        "t",
    )
    set_relation(session, v2.draft_version_id, "301", "401", "next_to")  # 別の島どうしの「隣」は矛盾
    session.commit()
    with pytest.raises(LayoutError, match="next_to across"):
        publish_import(session, v2.import_id, "tester")
    session.rollback()
    reject_import(session, v2.import_id)
    session.commit()
    assert session.get(HallLayoutVersion, v2.draft_version_id).status == "rejected"


def test_excel_import(session, hall, tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(HEADER.strip().split(","))
    for line in V1.strip().splitlines():
        values = line.split(",")
        values[1] = date.fromisoformat(values[1])  # Excel の日付セル
        values[4] = int(values[4])
        ws.append([v if v != "" else None for v in values])
    path = tmp_path / "layout.xlsx"
    wb.save(path)
    _, version = _publish(session, path)
    assert version.valid_from == date(2026, 10, 1)
    assert len(machine_features(session, hall.id, date(2026, 10, 1))) == 7
