"""Phase 2/3: 原本保存・解析版管理・観測追記・採用値更新・改訂履歴・冪等性・全履歴保持。"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from slot_data.config import SelectionConfig
from slot_data.domain.enums import DataStatus
from slot_data.ingestion.pipeline import IngestionPipeline
from slot_data.models import (
    BonusEvent,
    BonusEventSeries,
    DailyMachineStat,
    DailyStatRevision,
    DataQualityIssue,
    MachineInstallation,
    MachineModel,
    MachineSnapshot,
    ManualCorrection,
    ParseRun,
    RawPayload,
    SlumpPoint,
    SlumpSeries,
    SourceMapping,
    SourceObservation,
)
from tests.helpers import manual_doc, write_doc

pytestmark = pytest.mark.integration
D = date(2026, 10, 20)


def _count(ctx, model, *where):
    with ctx.session() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def _stat(ctx, machine="301", day=D) -> DailyMachineStat:
    with ctx.session() as s:
        return s.scalar(
            select(DailyMachineStat).where(
                DailyMachineStat.machine_number == machine, DailyMachineStat.business_date == day
            )
        )


def _revisions(ctx, stat_id):
    with ctx.session() as s:
        return list(
            s.scalars(
                select(DailyStatRevision)
                .where(DailyStatRevision.daily_stat_id == stat_id)
                .order_by(DailyStatRevision.revision_number)
            )
        )


def _issues(ctx, code):
    with ctx.session() as s:
        return list(s.scalars(select(DataQualityIssue).where(DataQualityIssue.issue_code == code)))


def test_import_creates_observation_stat_and_revision(ctx, hall, tmp_path):
    doc = manual_doc(
        [
            {
                "machine_number": "０３０１",  # 全角・先頭ゼロは正規化される
                "model_name": "テスト機A",
                "fields": {
                    "total_games": {"value": 5123, "raw": "5,123", "label": "総回転"},
                    "net_medals": -820,
                    "bb_count": {"value": 0, "label": "BIG"},  # 真のゼロ
                    "rb_count": None,  # 未公開
                    "at_count": {"status": "fetch_failed"},
                    "cz_count": {"status": "unparseable", "raw": "??"},
                },
            }
        ]
    )
    summary = IngestionPipeline(ctx).import_file("manual", "H001", write_doc(tmp_path, "a.json", doc))
    assert summary.status == "succeeded", summary
    stat = _stat(ctx)
    assert stat.total_games == 5123
    assert stat.net_medals == -820
    assert stat.bb_count == 0
    assert stat.rb_count is None and stat.field_status["rb_count"] == "not_published"
    assert stat.at_count is None and stat.field_status["at_count"] == "fetch_failed"
    assert stat.cz_count is None and stat.field_status["cz_count"] == "unparseable"
    # 入力に無い指標もゼロ補完しない
    assert stat.first_hit_count is None and stat.field_status["first_hit_count"] == "not_published"
    assert stat.data_status == DataStatus.FINAL_CANDIDATE  # 閉店(22:45)後の観測
    assert stat.revision_number == 1
    with ctx.session() as s:
        obs = s.get(SourceObservation, stat.selected_observation_id)
        assert obs.source_machine_number == "０３０１" and obs.machine_number == "301"
        assert obs.field_metadata["total_games"]["raw"] == "5,123"
        assert obs.field_metadata["bb_count"]["label"] == "BIG"
    revs = _revisions(ctx, stat.id)
    assert [r.reason for r in revs] == ["initial"]
    # 未知の機種名は自動作成せずレビュー待ち
    assert stat.model_id is None
    assert _count(ctx, SourceMapping, SourceMapping.review_status == "pending") == 1


def test_reimport_same_raw_is_idempotent(ctx, hall, tmp_path):
    path = write_doc(
        tmp_path, "a.json", manual_doc([{"machine_number": "301", "fields": {"total_games": 100}}])
    )
    pipeline = IngestionPipeline(ctx)
    first = pipeline.import_file("manual", "H001", path)
    second = pipeline.import_file("manual", "H001", path)
    assert first.counts["raw_stored"] == 1
    assert second.counts["raw_duplicate"] == 1
    # 取得イベントは 2 件残り、ファイル本体・解析・観測・改訂は増えない
    assert _count(ctx, RawPayload) == 2
    with ctx.session() as s:
        uris = set(s.scalars(select(RawPayload.storage_uri)))
        dup = s.scalar(select(RawPayload).where(RawPayload.capture_status == "duplicate_content"))
        assert dup.duplicate_of_id is not None
    assert len(uris) == 1
    assert _count(ctx, ParseRun) == 1
    assert _count(ctx, SourceObservation) == 1
    assert _count(ctx, DailyStatRevision) == 1


def test_multiple_fetches_same_day_keep_snapshots(ctx, hall, tmp_path):
    pipeline = IngestionPipeline(ctx)
    for i, (t, games) in enumerate([("12:00", 1000), ("18:00", 3000), ("21:00", 4500)]):
        doc = manual_doc(
            [{"machine_number": "301", "fields": {"total_games": games}}],
            observed_at=f"2026-10-20T{t}:00+09:00",
        )
        pipeline.import_file("manual", "H001", write_doc(tmp_path, f"{i}.json", doc))
    assert _count(ctx, MachineSnapshot) == 3
    stat = _stat(ctx)
    assert stat.total_games == 4500
    assert stat.data_status == DataStatus.PROVISIONAL  # 閉店前
    assert [r.reason for r in _revisions(ctx, stat.id)] == ["initial", "source_update", "source_update"]

    # 古い時点の観測を後から投入しても最新値は変わらず、改訂も作られない
    late = manual_doc(
        [{"machine_number": "301", "fields": {"total_games": 2000}}], observed_at="2026-10-20T15:00:00+09:00"
    )
    pipeline.import_file("manual", "H001", write_doc(tmp_path, "late.json", late))
    stat = _stat(ctx)
    assert stat.total_games == 4500 and stat.revision_number == 3


def test_value_regression_detected(ctx, hall, tmp_path):
    pipeline = IngestionPipeline(ctx)
    for i, (t, games) in enumerate([("12:00", 3000), ("13:00", 2500)]):
        doc = manual_doc(
            [{"machine_number": "301", "fields": {"total_games": games}}],
            observed_at=f"2026-10-20T{t}:00+09:00",
        )
        pipeline.import_file("manual", "H001", write_doc(tmp_path, f"{i}.json", doc))
    assert len(_issues(ctx, "VALUE_REGRESSION")) == 1


def test_correction_after_close_and_settle_to_final(ctx, hall, tmp_path):
    ctx.config.selection = SelectionConfig(settle_hours=24)
    pipeline = IngestionPipeline(ctx)

    def load(name, observed_at, games):
        doc = manual_doc(
            [{"machine_number": "301", "fields": {"total_games": games}}], observed_at=observed_at
        )
        pipeline.import_file("manual", "H001", write_doc(tmp_path, name, doc))

    load("close.json", "2026-10-20T23:00:00+09:00", 6000)
    stat = _stat(ctx)
    assert stat.data_status == DataStatus.FINAL_CANDIDATE  # 閉店後でも自動で final にしない
    load("next.json", "2026-10-21T10:00:00+09:00", 6000)
    assert _stat(ctx).data_status == DataStatus.FINAL_CANDIDATE  # 11 時間では未確定
    load("later.json", "2026-10-22T08:00:00+09:00", 6000)
    stat = _stat(ctx)
    assert stat.data_status == DataStatus.FINAL
    assert not stat.is_corrected

    # 確定後に取得元が値を訂正
    load("fix.json", "2026-10-22T12:00:00+09:00", 6050)
    stat = _stat(ctx)
    assert stat.total_games == 6050
    assert stat.is_corrected
    assert stat.data_status == DataStatus.FINAL_CANDIDATE
    revs = _revisions(ctx, stat.id)
    assert [r.reason for r in revs] == ["initial", "status_change", "correction"]
    assert revs[-1].previous_values["total_games"] == 6000
    assert revs[-1].new_values["total_games"] == 6050


def test_source_mismatch_recorded_and_priority_applied(ctx, hall, tmp_path):
    pipeline = IngestionPipeline(ctx)
    doc_a = manual_doc([{"machine_number": "301", "fields": {"total_games": 5000, "bb_count": 20}}])
    doc_b = manual_doc(
        [{"machine_number": "301", "fields": {"total_games": 5010, "bb_count": 20, "rb_count": 9}}]
    )
    pipeline.import_file("daidata_online", "H001", write_doc(tmp_path, "b.json", doc_b))
    pipeline.import_file("pscube", "H001", write_doc(tmp_path, "a.json", doc_a))
    stat = _stat(ctx)
    # 確定段階が同じなら取得元優先度（pscube=10 < daidata=20）で採用。指標を混ぜない
    assert stat.total_games == 5000
    assert stat.rb_count is None
    assert stat.quality_status == "warning"
    issues = _issues(ctx, "SOURCE_MISMATCH")
    assert len(issues) == 1
    assert issues[0].details["diffs"] == {"total_games": [5000, 5010]} or issues[0].details["diffs"] == {
        "total_games": [5010, 5000]
    }
    # 両取得元の観測は併存
    assert _count(ctx, SourceObservation) == 2


def test_quarantined_observation_does_not_change_published_value(ctx, hall, tmp_path):
    pipeline = IngestionPipeline(ctx)
    pipeline.import_file(
        "manual",
        "H001",
        write_doc(
            tmp_path, "ok.json", manual_doc([{"machine_number": "301", "fields": {"total_games": 100}}])
        ),
    )
    bad = manual_doc(
        [{"machine_number": "301", "fields": {"total_games": -5}}], observed_at="2026-10-20T23:50:00+09:00"
    )
    pipeline.import_file("manual", "H001", write_doc(tmp_path, "bad.json", bad))
    stat = _stat(ctx)
    assert stat.total_games == 100 and stat.revision_number == 1
    with ctx.session() as s:
        q = s.scalar(select(SourceObservation).where(SourceObservation.quality_status == "quarantined"))
        assert q is not None and q.field_values["total_games"] == -5  # 原文どおり保持
    assert len(_issues(ctx, "NEGATIVE_VALUE")) == 1


def test_model_swap_splits_installations(ctx, hall, tmp_path):
    with ctx.session() as s:
        s.add_all([MachineModel(canonical_name="機種A"), MachineModel(canonical_name="機種B")])
    pipeline = IngestionPipeline(ctx)
    for day, model in [("2026-10-18", "機種A"), ("2026-10-19", "機種A"), ("2026-10-20", "機種Ｂ")]:
        doc = manual_doc(
            [{"machine_number": "301", "model_name": model, "fields": {"total_games": 1}}],
            observed_at=f"{day}T23:00:00+09:00",
            business_date=day,
        )
        pipeline.import_file("manual", "H001", write_doc(tmp_path, f"{day}.json", doc))
    with ctx.session() as s:
        insts = list(s.scalars(select(MachineInstallation).order_by(MachineInstallation.valid_from)))
        names = {m.id: m.canonical_name for m in s.scalars(select(MachineModel))}
    assert [(i.valid_from, i.valid_to, names[i.model_id]) for i in insts] == [
        (date(2026, 10, 18), date(2026, 10, 20), "機種A"),
        (date(2026, 10, 20), None, "機種B"),
    ]
    assert _stat(ctx, day=date(2026, 10, 19)).installation_id == insts[0].id
    assert _stat(ctx, day=date(2026, 10, 20)).installation_id == insts[1].id

    # バックフィル: 過去日の同一機種観測は期間を前倒しする
    doc = manual_doc(
        [{"machine_number": "301", "model_name": "機種A", "fields": {"total_games": 1}}],
        observed_at="2026-10-15T23:00:00+09:00",
        business_date="2026-10-15",
    )
    pipeline.import_file("manual", "H001", write_doc(tmp_path, "old.json", doc))
    with ctx.session() as s:
        first = s.scalar(select(MachineInstallation).order_by(MachineInstallation.valid_from).limit(1))
        assert first.valid_from == date(2026, 10, 15)


def test_bonus_history_order_correction_keeps_all_series(ctx, hall, tmp_path):
    pipeline = IngestionPipeline(ctx)
    events_v1 = [
        {
            "ordinal": 1,
            "hit_games": 120,
            "cumulative_games": 120,
            "raw_label": "BIG",
            "normalized_type": "BB",
            "payout_medals": 240,
        },
        {
            "ordinal": 2,
            "hit_games": 300,
            "cumulative_games": 420,
            "raw_label": "REG",
            "normalized_type": "RB",
            "payout_medals": 96,
        },
    ]
    events_v2 = [  # 取得元が順番を訂正（REG と BIG が入れ替わり）+ 追加
        {
            "ordinal": 1,
            "hit_games": 300,
            "cumulative_games": 300,
            "raw_label": "REG",
            "normalized_type": "RB",
            "payout_medals": 96,
        },
        {
            "ordinal": 2,
            "hit_games": 120,
            "cumulative_games": 420,
            "raw_label": "BIG",
            "normalized_type": "BB",
            "payout_medals": 240,
        },
        {
            "ordinal": 3,
            "hit_games": 50,
            "cumulative_games": 470,
            "raw_label": "BIG",
            "normalized_type": "BB",
            "payout_medals": 240,
        },
    ]
    for i, (t, events) in enumerate([("15:00", events_v1), ("16:00", events_v2)]):
        doc = manual_doc(
            [
                {
                    "machine_number": "301",
                    "fields": {},
                    "bonus_events": {"status": "complete", "events": events},
                }
            ],
            observed_at=f"2026-10-20T{t}:00+09:00",
        )
        pipeline.import_file("manual", "H001", write_doc(tmp_path, f"{i}.json", doc))
    assert _count(ctx, BonusEventSeries) == 2
    assert _count(ctx, BonusEvent) == 5
    changed = _issues(ctx, "BONUS_ORDER_CHANGED")
    assert len(changed) == 1

    # 正常な追記（前回系列が先頭一致）は訂正扱いしない
    events_v3 = events_v2 + [
        {"ordinal": 4, "hit_games": 10, "cumulative_games": 480, "raw_label": "REG", "normalized_type": "RB"}
    ]
    doc = manual_doc(
        [
            {
                "machine_number": "301",
                "fields": {},
                "bonus_events": {"status": "complete", "events": events_v3},
            }
        ],
        observed_at="2026-10-20T17:00:00+09:00",
    )
    pipeline.import_file("manual", "H001", write_doc(tmp_path, "3.json", doc))
    assert len(_issues(ctx, "BONUS_ORDER_CHANGED")) == 1


def test_slump_all_points_kept_without_limit(ctx, hall, tmp_path):
    n = 12_345
    points = [
        {
            "x_numeric": i,
            "y_numeric": (i * 7) % 3000 - 1500,
            "x_raw": str(i),
            "y_raw": str((i * 7) % 3000 - 1500),
        }
        for i in range(n)
    ]
    points.append({"x_numeric": n - 1, "y_numeric": 0})  # 重複 x も削らない
    doc = manual_doc(
        [
            {
                "machine_number": "301",
                "fields": {},
                "slump_series": [
                    {
                        "series_key": "main",
                        "x_axis_type": "cumulative_games",
                        "x_unit": "games",
                        "y_axis_type": "net_medals",
                        "y_unit": "medals",
                        "points": points,
                    },
                    {
                        "series_key": "image:estimated",
                        "x_axis_type": "pixel",
                        "y_axis_type": "pixel",
                        "origin_type": "estimated",
                        "derivation_method": "image_trace",
                        "derivation_version": "0.1",
                        "derived_from_series_key": "main",
                        "points": [{"x_numeric": 0, "y_numeric": 120}, {"x_numeric": 1, "y_numeric": 118}],
                    },
                ],
            }
        ]
    )
    pipeline = IngestionPipeline(ctx)
    pipeline.import_file("manual", "H001", write_doc(tmp_path, "slump.json", doc))
    with ctx.session() as s:
        main = s.scalar(select(SlumpSeries).where(SlumpSeries.series_key == "main"))
        est = s.scalar(select(SlumpSeries).where(SlumpSeries.series_key == "image:estimated"))
        assert main.point_count == n + 1
        assert s.scalar(select(func.count()).where(SlumpPoint.series_id == main.id)) == n + 1
        assert s.scalar(select(func.max(SlumpPoint.point_index)).where(SlumpPoint.series_id == main.id)) == n
        # 画素座標は差枚として扱わない
        assert est.derived_from_series_id == main.id
        assert (
            s.scalar(
                select(func.count()).where(
                    SlumpPoint.series_id == est.id, SlumpPoint.estimated_net_medals.is_not(None)
                )
            )
            == 0
        )
    assert len(_issues(ctx, "SLUMP_DUPLICATE_X")) == 1

    # グラフ更新時は新系列を追記し、旧系列を残す
    doc["observed_at"] = "2026-10-20T23:45:00+09:00"
    doc["machines"][0]["slump_series"] = doc["machines"][0]["slump_series"][:1]
    pipeline.import_file("manual", "H001", write_doc(tmp_path, "slump2.json", doc))
    assert _count(ctx, SlumpSeries, SlumpSeries.series_key == "main") == 2


def test_reparse_with_new_parser_version(ctx, hall, tmp_path):
    from slot_data.adapters.manual.parsers import ManualJsonParser

    pipeline = IngestionPipeline(ctx)
    summary = pipeline.import_file(
        "manual",
        "H001",
        write_doc(
            tmp_path, "a.json", manual_doc([{"machine_number": "301", "fields": {"total_games": 100}}])
        ),
    )
    raw_id = summary.counts["raw_payload_id"]

    class FixedParser(ManualJsonParser):
        version = "1.1.0"

        def parse(self, data, pctx):
            result = super().parse(data, pctx)
            # 新版では総ゲーム数の定義が変わった想定（+1）
            result.observations = [
                o.model_copy(
                    update={
                        "fields": {"total_games": o.fields["total_games"].model_copy(update={"value": 101})}
                    }
                )
                for o in result.observations
            ]
            return result

    import uuid

    outcome = pipeline.parse_raw(uuid.UUID(raw_id), FixedParser())
    pipeline.reconcile_keys(outcome.keys)
    assert _stat(ctx).total_games == 101
    # 同じ版での再解析はスキップ（冪等）
    assert pipeline.parse_raw(uuid.UUID(raw_id), FixedParser()).skipped == "already_parsed"
    assert pipeline.reparse([uuid.UUID(raw_id)])["skipped"] == 1
    assert _count(ctx, ParseRun) == 2
    assert _count(ctx, SourceObservation) == 2  # 旧版の観測も残る


def test_parse_failure_keeps_raw_and_error(ctx, hall, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    summary = IngestionPipeline(ctx).import_file("manual", "H001", path)
    assert summary.status == "partial"
    assert _count(ctx, RawPayload) == 1
    with ctx.session() as s:
        run = s.scalar(select(ParseRun))
        assert run.status == "failed" and run.issues[0]["code"] == "INVALID_JSON"
    assert len(_issues(ctx, "PARSE_FAILED")) == 1
    assert _count(ctx, DailyMachineStat) == 0


def test_manual_correction_overlays_without_touching_observation(ctx, hall, tmp_path):
    pipeline = IngestionPipeline(ctx)
    pipeline.import_file(
        "manual",
        "H001",
        write_doc(
            tmp_path, "a.json", manual_doc([{"machine_number": "301", "fields": {"total_games": 100}}])
        ),
    )
    stat = _stat(ctx)
    with ctx.session() as s:
        s.add(
            ManualCorrection(
                hall_id=hall.id,
                business_date=D,
                machine_number="301",
                field_name="total_games",
                new_value=110,
                reason="店舗掲示の値で訂正",
                corrected_by="tester",
            )
        )
    pipeline.reconcile_range("H001", D, D)
    stat = _stat(ctx)
    assert stat.total_games == 110
    revs = _revisions(ctx, stat.id)
    assert revs[-1].reason == "manual_correction" and revs[-1].manual_correction_id is not None
    with ctx.session() as s:
        obs = s.get(SourceObservation, stat.selected_observation_id)
        assert obs.field_values["total_games"] == 100


def test_parallel_reconcile_is_serialized(ctx, hall, tmp_path):
    pipeline = IngestionPipeline(ctx)
    pipeline.import_file(
        "manual",
        "H001",
        write_doc(
            tmp_path, "a.json", manual_doc([{"machine_number": "301", "fields": {"total_games": 100}}])
        ),
    )
    # 採用値を一旦消した状態から 8 並列で照合しても、日次行は 1 行・改訂は 1 件
    with ctx.session() as s:
        s.execute(select(1))
    from sqlalchemy import text

    with ctx.session() as s:
        s.execute(text("SET LOCAL slot_data.allow_destructive = 'on'"))
        s.execute(text("DELETE FROM daily_stat_revisions"))
        s.execute(text("DELETE FROM daily_machine_stats"))
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait()
            pipeline.reconcile_range("H001", D, D)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert _count(ctx, DailyMachineStat) == 1
    assert _count(ctx, DailyStatRevision) == 1


def test_parallel_import_of_same_file(ctx, hall, tmp_path):
    path = write_doc(
        tmp_path, "a.json", manual_doc([{"machine_number": "301", "fields": {"total_games": 100}}])
    )
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def worker():
        try:
            barrier.wait()
            IngestionPipeline(ctx).import_file("manual", "H001", path)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert _count(ctx, RawPayload) == 4  # 取得イベントは全件
    assert _count(ctx, RawPayload, RawPayload.duplicate_of_id.is_(None)) == 1
    assert _count(ctx, ParseRun) == 1
    assert _count(ctx, DailyMachineStat) == 1
    assert _count(ctx, DailyStatRevision) == 1


def test_timestamps_and_dates_are_distinct(ctx, hall, tmp_path):
    # 営業日境界 05:00 前の観測は前日の営業日
    doc = manual_doc(
        [{"machine_number": "301", "fields": {"total_games": 1}}],
        observed_at="2026-10-21T02:00:00+09:00",
        business_date=None,
        source_reported_at="2026-10-20T22:50:00+09:00",
    )
    IngestionPipeline(ctx).import_file("manual", "H001", write_doc(tmp_path, "a.json", doc))
    with ctx.session() as s:
        obs = s.scalar(select(SourceObservation))
        raw = s.scalar(select(RawPayload))
    assert obs.business_date == D
    assert obs.observed_at == datetime(2026, 10, 20, 17, 0, tzinfo=UTC)
    assert obs.observed_at.utcoffset() == timedelta(0)  # 接続は UTC で扱う
    assert obs.source_reported_at == datetime(2026, 10, 20, 13, 50, tzinfo=UTC)
    assert raw.fetched_at > obs.observed_at - timedelta(days=365)


def test_negative_bonus_values_do_not_abort_ingestion(ctx, hall, tmp_path):
    """出枚pt のマイナスは原文どおり保存し、マイナスのゲーム数は値を空にして警告に留める。"""
    events = [
        {"ordinal": 1, "hit_games": 61, "raw_label": "REG", "normalized_type": "RB", "payout_medals": -7},
        {"ordinal": 2, "hit_games": -3, "raw_label": "BIG", "normalized_type": "BB", "payout_medals": 240},
    ]
    doc = manual_doc(
        [
            {
                "machine_number": "301",
                "fields": {"total_games": 1000},
                "bonus_events": {"status": "complete", "events": events},
            }
        ]
    )
    summary = IngestionPipeline(ctx).import_file("pscube", "H001", write_doc(tmp_path, "neg.json", doc))
    assert summary.status == "succeeded", summary.errors
    with ctx.session() as s:
        rows = {e.ordinal: e for e in s.scalars(select(BonusEvent))}
        assert rows[1].payout_medals == -7 and rows[1].quality_status == "valid"
        assert rows[2].hit_games is None and rows[2].raw_attributes["rejected_negative"] == {"hit_games": -3}
        assert rows[2].quality_status == "warning"
    # 日次の観測は隔離されず、採用値になる
    assert _stat(ctx).total_games == 1000


def test_persist_failure_keeps_raw_for_reparse(ctx, hall, tmp_path, monkeypatch):
    """保存時の例外はその原本だけを未解析に戻し、原因を直したあと reparse --pending で取り込める。"""
    import slot_data.ingestion.pipeline as pipeline_mod

    original = pipeline_mod.persist_parse_result

    def broken(*args, **kwargs):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(pipeline_mod, "persist_parse_result", broken)
    pipeline = IngestionPipeline(ctx)
    path = write_doc(
        tmp_path, "a.json", manual_doc([{"machine_number": "301", "fields": {"total_games": 5}}])
    )
    summary = pipeline.import_file("manual", "H001", path)
    assert summary.status == "partial"
    assert _count(ctx, RawPayload) == 1 and _count(ctx, ParseRun) == 0
    assert _issues(ctx, "PARSE_FAILED")[0].details["stage"] == "persist"

    monkeypatch.setattr(pipeline_mod, "persist_parse_result", original)
    stats = pipeline.reparse(pending_only=True)
    assert stats["parsed"] == 1
    assert _stat(ctx).total_games == 5
