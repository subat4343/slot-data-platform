"""P's CUBE 巡回: 保存済み実画面 HTML を返す偽ブラウザで、巡回順・原本保存・欠損記録・再開を確認する。

実サイトには接続しない。
"""

from __future__ import annotations

from datetime import date

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import func, select

from slot_data.adapters.pscube import PsCubeAdapter
from slot_data.config import AccessPolicy, CollectTarget, RateLimitConfig, SourceConfig
from slot_data.ingestion.pipeline import IngestionPipeline
from slot_data.models import DailyMachineStat, DataQualityIssue, RawPayload, Source, SourceObservation
from tests.conftest import FIXTURES

pytestmark = pytest.mark.integration
D = FIXTURES / "pscube"


def _single_model_list() -> str:
    soup = BeautifulSoup((D / "model_list.html").read_text(encoding="utf-8"), "html.parser")
    items = soup.select("ul#ulKI > li")
    for li in items[1:]:
        li.decompose()
    return str(soup)


class FakeNavigator:
    """クリックされたセレクターに応じて保存済み HTML を返す。操作履歴を記録する。"""

    def __init__(self, detail_html: str) -> None:
        self.pages = {
            "list": _single_model_list(),
            "model": (D / "model_page_myjuggler6.html").read_text(encoding="utf-8"),
            "detail": detail_html,
        }
        self.current = "top"
        self.log: list[str] = []

    def open(self, url, ready_selector):
        self.log.append(f"open {url}")
        self.current = "top"

    def click(self, selector, ready_selector=None, has_text=None, ready_any=()):
        self.log.append(f"click {selector} {has_text or ''}".strip())
        if "nc-v03-001" in selector:
            self.current = "list"
        elif "btn-ki" in selector or has_text == "機種データ":
            self.current = "model"
        elif "btn-dai" in selector:
            self.current = "detail"

    def html(self):
        return self.pages.get(self.current, "<html></html>")

    def url(self):
        return f"https://www.pscube.jp/fake/{self.current}"

    def close(self):
        pass


def _adapter(**options) -> PsCubeAdapter:
    return PsCubeAdapter(
        SourceConfig(
            code="pscube",
            name="P's CUBE",
            access_policy=AccessPolicy(automated_fetch="permitted", permission_reference="test"),
            rate_limit=RateLimitConfig(min_interval_seconds=30),
            options={"headless": False, "crawl_details": True, **options},
        )
    )


TARGET = CollectTarget(source="pscube", hall_code="H001", source_hall_id="kanagawa/13152")


def test_crawl_order_and_blocked_detail_then_resume(ctx, hall):
    error_html = (D / "machine_detail_error451.html").read_text(encoding="utf-8")
    nav = FakeNavigator(error_html)
    pipeline = IngestionPipeline(ctx)
    first = pipeline.collect(_adapter(), TARGET, [date(2026, 10, 23)], nav)

    assert nav.log[0] == "open https://p-town.dmm.com/shops/kanagawa/13152/jackpot"
    assert nav.log[1].startswith("click a[href*='nc-v03-001.php']")
    assert any("btn-dai" in entry and "0277" in entry for entry in nav.log)
    # 台詳細が拒否されたら残りの台（0278, 0280）は巡回しない
    assert not any("0278" in entry for entry in nav.log)
    assert first.status == "partial"
    assert first.counts["fetch_failed"] == 1

    with ctx.session() as s:
        # 機種画面の日別 BONUS 回数は pscube_list として採用値になる
        stats = s.scalars(select(DailyMachineStat)).all()
        assert len(stats) == 15
        today = s.scalar(
            select(DailyMachineStat).where(
                DailyMachineStat.machine_number == "277", DailyMachineStat.business_date == date(2026, 10, 23)
            )
        )
        assert today.bonus_count == 39 and today.bb_count is None
        assert today.field_status["bb_count"] == "not_published"
        list_source = s.scalar(select(Source).where(Source.code == "pscube_list"))
        assert today.selected_source_id == list_source.id
        # 拒否画面も証跡として原本保存し、欠損理由を記録
        assert s.scalar(select(func.count()).select_from(RawPayload)) == 3
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED"))
        assert issue.machine_number == "0277" and issue.details["kind"] == "access_denied"

    # 再開: 機種一覧・機種画面は取得済みなので保存しない。拒否された台詳細は取り直す
    nav2 = FakeNavigator(error_html)
    resumed = pipeline.collect(_adapter(), TARGET, [date(2026, 10, 23)], nav2, resume_run_id=first.run_id)
    assert resumed.counts["pages"] == 1
    assert any("0277" in entry for entry in nav2.log)
    with ctx.session() as s:
        assert s.scalar(select(func.count()).select_from(SourceObservation)) == 15


def test_crawl_without_details_and_refuses_without_permission(ctx, hall):
    nav = FakeNavigator("<html></html>")
    summary = IngestionPipeline(ctx).collect(_adapter(crawl_details=False), TARGET, [date(2026, 10, 23)], nav)
    assert summary.status == "succeeded" and summary.counts["pages"] == 2
    assert not any("btn-dai" in entry for entry in nav.log)

    from slot_data.adapters.base import AccessProhibitedError

    denied = PsCubeAdapter(
        SourceConfig(code="pscube", name="p", access_policy=AccessPolicy(automated_fetch="prohibited"))
    )
    nav = FakeNavigator("<html></html>")
    with pytest.raises(AccessProhibitedError):
        IngestionPipeline(ctx).collect(denied, TARGET, [date(2026, 10, 23)], nav)
    with pytest.raises(AccessProhibitedError):
        denied.open_client()
    assert nav.log == []


def test_crawl_with_detail_prefers_detail_values(ctx, hall):
    detail_html = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    nav = FakeNavigator(detail_html)
    summary = IngestionPipeline(ctx).collect(
        _adapter(), TARGET.model_copy(update={"machines": ["0277"]}), [date(2026, 10, 25)], nav
    )
    assert summary.status == "succeeded", summary.errors
    # 台詳細を開いたあとは「機種データ」で機種画面へ戻り、「機種一覧」へ戻る
    assert [e for e in nav.log if "機種データ" in e or "機種一覧" in e] == [
        "click a 機種データ",
        "click a[href*='nc-v03-001.php'] 機種一覧",
    ]
    with ctx.session() as s:
        pscube = s.scalar(select(Source).where(Source.code == "pscube"))

        def stat(day):
            return s.scalar(
                select(DailyMachineStat).where(
                    DailyMachineStat.machine_number == "277", DailyMachineStat.business_date == day
                )
            )

        # 台詳細にしかない日（10/24, 10/25）
        d25 = stat(date(2026, 10, 25))
        assert (d25.bb_count, d25.rb_count, d25.total_games, d25.net_medals) == (20, 19, 7506, -1981)
        assert d25.selected_source_id == pscube.id
        # 機種画面と重なる日は台詳細（優先度が高い）を採用し、BONUS 回数の差は不一致として記録
        d23 = stat(date(2026, 10, 23))
        assert d23.selected_source_id == pscube.id and d23.bb_count == 32
        # 機種画面にしかない台（0278）は機種画面の値
        assert (
            s.scalar(
                select(DailyMachineStat.bonus_count).where(
                    DailyMachineStat.machine_number == "278",
                    DailyMachineStat.business_date == date(2026, 10, 23),
                )
            )
            == 24
        )
        # 特賞履歴（本日分）も保存
        from slot_data.models import BonusEvent

        assert s.scalar(select(func.count()).select_from(BonusEvent)) == 20


def test_crawl_abort_is_recorded_as_issue(ctx, hall):
    class BrokenNavigator(FakeNavigator):
        def click(self, selector, ready_selector=None, has_text=None, ready_any=()):
            if "btn-dai" in selector:
                raise RuntimeError("browser crashed")
            super().click(selector, ready_selector, has_text, ready_any)

    nav = BrokenNavigator("<html></html>")
    summary = IngestionPipeline(ctx).collect(_adapter(), TARGET, [date(2026, 10, 23)], nav)
    assert summary.status == "partial"  # 機種一覧・機種画面は保存済み
    with ctx.session() as s:
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED"))
        assert issue.details["kind"] == "crawl_aborted"
        assert issue.details["recover"] == f"collect --resume {summary.run_id}"


def test_reparse_pending_pscube_raws(ctx, hall, monkeypatch):
    """台詳細の保存に失敗した原本を、修正後に reparse --pending で取り込める（収集用 PC の障害の再現）。"""
    import slot_data.ingestion.pipeline as pipeline_mod

    original = pipeline_mod.persist_parse_result

    def fail_on_detail(*args, **kwargs):
        if kwargs["parse_run"].parser_name == "pscube_machine_detail":
            raise RuntimeError("simulated constraint violation")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_mod, "persist_parse_result", fail_on_detail)
    nav = FakeNavigator((D / "machine_detail_0277.html").read_text(encoding="utf-8"))
    pipeline = IngestionPipeline(ctx)
    first = pipeline.collect(
        _adapter(), TARGET.model_copy(update={"machines": ["0277"]}), [date(2026, 10, 25)], nav
    )
    assert first.counts["parse_failed"] == 1  # 巡回は止まらない

    monkeypatch.setattr(pipeline_mod, "persist_parse_result", original)
    stats = pipeline.reparse(pending_only=True)
    assert stats["parsed"] == 1 and stats["failed"] == 0
    with ctx.session() as s:
        d25 = s.scalar(
            select(DailyMachineStat).where(
                DailyMachineStat.machine_number == "277", DailyMachineStat.business_date == date(2026, 10, 25)
            )
        )
        assert d25.bb_count == 20


def test_reparse_continues_after_unknown_parser(ctx, hall):
    from slot_data.adapters.base import FetchResult
    from slot_data.domain.timeutil import utcnow

    pipeline = IngestionPipeline(ctx)
    run_id = pipeline.start_run("pscube", "H001", {}, "test")
    for name, body in (
        ("no_such_parser", b"x"),
        ("pscube_model_page", (D / "model_page_myjuggler6.html").read_bytes()),
    ):
        pipeline.persist_raw(
            run_id=run_id,
            source_code="pscube_list",
            hall_code="H001",
            request_fingerprint=name,
            result=FetchResult(content=body, content_type="text/html", fetched_at=utcnow()),
            parser_name=name,
        )
    stats = pipeline.reparse(pending_only=True)
    assert stats["failed"] == 1 and stats["parsed"] == 1
    with ctx.session() as s:
        issue = s.scalar(
            select(DataQualityIssue).where(
                DataQualityIssue.issue_code == "PARSE_FAILED", DataQualityIssue.entity_type == "raw_payload"
            )
        )
        assert "parser not registered" in issue.details["message"]
