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
    """クリックされたセレクターに応じて保存済み HTML を返す。操作履歴を記録する。

    台詳細は「次台 >>」「<< 前台」で hall_order の順に移る（端で止まる。wrap=True なら一周する）。
    「台番号で探す」は hall_order にある台番号なら台詳細を開き、無ければ画面を変えない。
    台詳細の HTML は、フィクスチャの台番号 0277 を表示中の台番号に置き換えて返す。
    """

    def __init__(
        self,
        detail_html: str,
        hall_order: list[str] | None = None,
        wrap: bool = False,
        full_list: str | None = None,
        stop_at: set[str] | None = None,
    ) -> None:
        self.pages = {
            "list": _single_model_list(),
            "model": (D / "model_page_myjuggler6.html").read_text(encoding="utf-8"),
            "detail": detail_html,
        }
        self.hall_order = hall_order or ["0277", "0278", "0280"]
        self.wrap = wrap
        self.full_list = full_list
        self.stop_at = stop_at or set()  # 「次台 >>」を押しても進まない台
        self.current = "top"
        self.machine = None
        self.log: list[str] = []

    def open(self, url, ready_selector):
        self.log.append(f"open {url}")
        self.current = "top"

    def click(self, selector, ready_selector=None, has_text=None, ready_any=(), expect_machine_change=False):
        self.log.append(f"click {selector} {has_text or ''}".strip())
        if "nc-v03-001" in selector:
            self.current = "list"
        elif "btn-ki" in selector or has_text == "機種データ":
            self.current = "model"
        elif "btn-dai" in selector:
            self.current = "detail"
            self.machine = selector.split("cd_dai=")[-1].rstrip("']")
        elif selector == "#dai_next" and self.machine in self.stop_at:
            pass
        elif selector in ("#dai_next", "#dai_prev"):
            i = self.hall_order.index(self.machine) + (1 if selector == "#dai_next" else -1)
            if 0 <= i < len(self.hall_order):
                self.machine = self.hall_order[i]
            elif self.wrap:
                self.machine = self.hall_order[i % len(self.hall_order)]

    def scroll_to_end(self, item_selector, interval, max_rounds):
        self.log.append(f"scroll {item_selector} every {interval}s")
        if self.full_list is not None:
            self.pages["list"] = self.full_list
        return len(BeautifulSoup(self.pages["list"], "html.parser").select("ul#ulKI li a.btn-ki"))

    def search_machine(self, number):
        self.log.append(f"search {number}")
        found = next((m for m in self.hall_order if int(m) == int(number)), None)
        if found is not None:
            self.current, self.machine = "detail", found
        return self.current_machine()

    def current_machine(self):
        return self.machine if self.current == "detail" else None

    def exists(self, selector):
        return self.current == "detail" and selector in ("#dai_next", "#dai_prev")

    def html(self):
        page = self.pages.get(self.current, "<html></html>")
        if self.current == "detail" and self.machine:
            page = page.replace("0277", self.machine)
        return page

    def url(self):
        base = "https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c743201/cgi-bin/"
        return {
            "list": base + "nc-v03-001.php?cd_ps=2",
            "model": base + "nc-v05-003.php?cd_ps=2&bai=21.277",
            "detail": base + f"nc-v06-001.php?cd_dai={self.machine}",
        }.get(self.current, "https://p-town.dmm.com/shops/kanagawa/13152/jackpot")

    def close(self):
        pass


def _adapter(**options) -> PsCubeAdapter:
    return PsCubeAdapter(
        SourceConfig(
            code="pscube",
            name="P's CUBE",
            access_policy=AccessPolicy(automated_fetch="permitted", permission_reference="test"),
            rate_limit=RateLimitConfig(min_interval_seconds=30),
            options={"headless": False, "crawl_details": True, "first_machine": "0277", **options},
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
    assert "search 0277" in nav.log  # 台番号検索で開始台を開く
    # 台詳細が拒否されたら残りの台（0278, 0280）は巡回しない
    assert not any("0278" in entry for entry in nav.log)
    assert first.status == "partial"
    assert first.counts["fetch_failed"] == 1

    with ctx.session() as s:
        # 機種画面は開かず（検索で台詳細を開く）、台詳細は拒否されたので日次データは作られない
        assert s.scalar(select(func.count()).select_from(DailyMachineStat)) == 0
        # 拒否画面も証跡として原本保存し（機種一覧 + 拒否された台詳細）、欠損理由を記録
        assert s.scalar(select(func.count()).select_from(RawPayload)) == 2
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED"))
        assert issue.machine_number == "0277" and issue.details["kind"] == "access_denied"

    # 再開: 機種一覧・機種画面は取得済みなので保存しない。拒否された台詳細は取り直す
    nav2 = FakeNavigator(error_html)
    resumed = pipeline.collect(_adapter(), TARGET, [date(2026, 10, 23)], nav2, resume_run_id=first.run_id)
    assert resumed.counts["pages"] == 1
    assert any("0277" in entry for entry in nav2.log)
    with ctx.session() as s:
        assert s.scalar(select(func.count()).select_from(SourceObservation)) == 0


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
    # 一覧を最後までスクロールし、検索で開始台を開いて「次台 >>」だけで進む（端で止まったら終わり）
    assert nav.log[2].startswith("scroll a.btn-ki every 10.0s")
    assert [e for e in nav.log if "dai_" in e or "機種一覧" in e or e.startswith("search")] == [
        "search 0277",
        "click #dai_next",
        "click #dai_next",
        "click #dai_next",
    ]
    assert not any("機種データ" in e or "btn-ki" in e.split(" ")[1] for e in nav.log if e.startswith("click"))
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
        # 対象外の台（0278）は辿るだけで保存しない
        assert s.scalar(select(func.count()).where(DailyMachineStat.machine_number == "278")) == 0
        # 特賞履歴（本日分）も保存
        from slot_data.models import BonusEvent

        assert s.scalar(select(func.count()).select_from(BonusEvent)) == 20


def test_crawl_abort_is_recorded_as_issue(ctx, hall):
    class BrokenNavigator(FakeNavigator):
        def search_machine(self, number):
            raise RuntimeError("browser crashed")

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


def test_detail_click_that_does_not_navigate_is_not_saved(ctx, hall):
    """台番号をクリックしても台詳細に遷移しなかった場合、遷移前の画面を台詳細として保存しない。"""

    class StuckNavigator(FakeNavigator):
        def search_machine(self, number):
            self.log.append(f"search {number}")
            return None  # 検索しても台詳細が開かない

        def click(
            self, selector, ready_selector=None, has_text=None, ready_any=(), expect_machine_change=False
        ):
            if "btn-dai" in selector:
                self.log.append(f"click {selector}")
                return  # 画面が変わらない（機種画面のまま）
            super().click(selector, ready_selector, has_text, ready_any, expect_machine_change)

    nav = StuckNavigator((D / "machine_detail_0277.html").read_text(encoding="utf-8"))
    summary = IngestionPipeline(ctx).collect(
        _adapter(), TARGET.model_copy(update={"machines": ["0277", "0278"]}), [date(2026, 10, 25)], nav
    )
    assert summary.status == "partial"
    # 1 台目で止まったら、その機種の残り（0278）は飛ばし、「機種データ」も押さずに機種一覧へ戻る
    assert not any("0278" in e for e in nav.log)
    assert not any("機種データ" in e or "dai_next" in e for e in nav.log)
    assert "btn-dai" in nav.log[-1]
    with ctx.session() as s:
        kinds = [r.response_metadata.get("page_kind") for r in s.scalars(select(RawPayload))]
        assert "machine_detail" not in kinds
        kinds = {
            i.details["kind"]: i
            for i in s.scalars(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED"))
        }
        # 検索で開けず（search_failed）、機種画面から入ろうとしたが遷移しなかった（wrong_page）
        assert set(kinds) == {"search_failed", "wrong_page"} and kinds["wrong_page"].machine_number == "0277"


def test_crawl_stops_at_deadline(ctx, hall, monkeypatch):
    import slot_data.adapters.pscube as pscube_mod
    from slot_data.domain.timeutil import utcnow

    monkeypatch.setattr(pscube_mod, "next_deadline", lambda start, at: utcnow())  # すでに締め切り
    nav = FakeNavigator("<html></html>")
    summary = IngestionPipeline(ctx).collect(_adapter(), TARGET, [date(2026, 10, 23)], nav)
    assert summary.status == "partial"
    assert not any(e.startswith("click a.btn-ki") for e in nav.log)  # 機種画面には進まない
    with ctx.session() as s:
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED"))
        assert issue.details["kind"] == "deadline"


def test_resume_retries_raws_that_failed_to_parse(ctx, hall, monkeypatch):
    """解析に失敗した原本は、--resume で取得済みとして飛ばさずに取り直す。"""
    import slot_data.ingestion.pipeline as pipeline_mod

    original = pipeline_mod.persist_parse_result

    def fail_on_detail(*args, **kwargs):
        if kwargs["parse_run"].parser_name == "pscube_machine_detail":
            raise RuntimeError("simulated")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_mod, "persist_parse_result", fail_on_detail)
    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    target = TARGET.model_copy(update={"machines": ["0277"]})
    pipeline = IngestionPipeline(ctx)
    first = pipeline.collect(_adapter(), target, [date(2026, 10, 25)], FakeNavigator(detail))
    monkeypatch.setattr(pipeline_mod, "persist_parse_result", original)

    nav = FakeNavigator(detail)
    resumed = pipeline.collect(_adapter(), target, [date(2026, 10, 25)], nav, resume_run_id=first.run_id)
    assert "search 0277" in nav.log  # 解析に失敗した台は取得済みとみなさず、取り直した
    assert resumed.counts["parse_failed"] == 0


def test_truncated_model_list_is_reported(ctx, hall):
    """機種一覧がちょうど 20 件（読み込みの区切り）なら MODEL_LIST_TRUNCATED を出す。"""
    nav = FakeNavigator("<html></html>")
    nav.pages["list"] = (D / "model_list.html").read_text(encoding="utf-8")  # 20 機種で切れた実画面
    nav.click = (
        lambda selector, ready_selector=None, has_text=None, ready_any=(), expect_machine_change=False: (
            setattr(nav, "current", "list") if "nc-v03-001" in selector else setattr(nav, "current", "error")
        )
    )
    IngestionPipeline(ctx).collect(_adapter(crawl_details=False), TARGET, [date(2026, 10, 23)], nav)
    with ctx.session() as s:
        issue = s.scalar(
            select(DataQualityIssue).where(DataQualityIssue.issue_code == "MODEL_LIST_TRUNCATED")
        )
        assert issue is not None and "20 件" in issue.details["message"]


def _list_with_extra_model() -> str:
    """20 件で切れた実画面の一覧に、21 件目（1 台の機種）を足した「全件表示後」の一覧。"""
    soup = BeautifulSoup((D / "model_list.html").read_text(encoding="utf-8"), "html.parser")
    items = soup.select("ul#ulKI > li")
    extra = BeautifulSoup(str(items[-1]), "html.parser")
    extra.select_one(".nc-label").string = "Lスマスロ秘宝伝"
    extra.select_one(".nc-lower div div").string = "1台"
    extra.select_one("a")["href"] = "nc-v05-003.php?cd_ps=2&bai=21.277&nmk_kisyu=hihouden"
    items[-1].insert_after(extra)
    return str(soup)


def test_full_traversal_with_next_machine(ctx, hall):
    """一覧をスクロールして全件読み込み、先頭の台から「次台 >>」でホール全体を 1 台 1 遷移で辿る。"""
    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    nav = FakeNavigator(
        detail, hall_order=["0001", "0101", "0277", "0278", "0280"], full_list=_list_with_extra_model()
    )
    summary = IngestionPipeline(ctx).collect(
        _adapter(first_machine="0001"), TARGET, [date(2026, 10, 25)], nav
    )
    # 検索で 0001 を開き、「次台 >>」だけで 0280 まで進む。機種画面・前台・機種一覧へは戻らない。
    # 一覧の台数合計（78 台）に届かないまま進めなくなったので、0280 を検索で開き直して 1 回押し直し、
    # 続きの台（観測が無いので 0281〜0285）を検索で探す。見つからなければ取りこぼし（walk_incomplete）として
    # 記録し、run は partial になる
    assert summary.status == "partial"
    assert [e["kind"] for e in summary.errors] == ["walk_incomplete"]
    assert [e for e in nav.log if e.startswith("search")] == [
        "search 0001",
        "search 0280",
        "search 0281",
        "search 0282",
        "search 0283",
        "search 0284",
        "search 0285",
    ]
    assert not any("btn-dai" in e or "dai_prev" in e or "機種一覧" in e for e in nav.log)
    with ctx.session() as s:
        details = [
            r.request_fingerprint
            for r in s.scalars(select(RawPayload))
            if r.response_metadata.get("page_kind") == "machine_detail"
        ]
        assert len(details) == 5
        machines = set(
            s.scalars(
                select(DailyMachineStat.machine_number).where(
                    DailyMachineStat.business_date == date(2026, 10, 25)
                )
            )
        )
        assert machines == {"1", "101", "277", "278", "280"}
        # 全件表示後の一覧（21 件）は取りこぼし扱いにならない
        assert (
            s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "MODEL_LIST_TRUNCATED"))
            is None
        )


def test_next_machine_wraps_around(ctx, hall):
    """「次台 >>」が一周して開始台に戻る場合は、前台方向は辿らない。"""
    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    nav = FakeNavigator(detail, hall_order=["0101", "0277", "0278"], wrap=True)
    summary = IngestionPipeline(ctx).collect(_adapter(), TARGET, [date(2026, 10, 25)], nav)
    assert summary.status == "succeeded", summary.errors
    assert not any("dai_prev" in e or "機種一覧" in e for e in nav.log)
    assert sum("dai_next" in e for e in nav.log) == 3  # 0278 → 0101 → 0277（一周）で終了


def test_first_machine_missing_uses_known_minimum(ctx, hall, tmp_path):
    """0001 が無い（入れ替えなど）ときは、これまでに観測した最小の台番号で検索し直す。"""
    from tests.helpers import manual_doc, write_doc

    doc = manual_doc([{"machine_number": "0101", "fields": {"total_games": 1}}])
    IngestionPipeline(ctx).import_file("pscube", "H001", write_doc(tmp_path, "old.json", doc))
    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    nav = FakeNavigator(detail, hall_order=["0101", "0277", "0278"])
    summary = IngestionPipeline(ctx).collect(
        _adapter(first_machine="0001"), TARGET, [date(2026, 10, 25)], nav
    )
    assert summary.status == "succeeded", summary.errors
    assert [e for e in nav.log if e.startswith("search")] == ["search 0001", "search 0101"]
    with ctx.session() as s:
        saved = {
            r.response_metadata.get("machine_number")
            for r in s.scalars(select(RawPayload))
            if r.response_metadata.get("page_kind") == "machine_detail"
        }
        assert saved == {"0101", "0277", "0278"}


def test_next_machine_stall_recovers_by_search(ctx, hall):
    """途中で「次台 >>」が効かなくなったら、現在の台を検索で開き直して続ける。"""

    class StallOnce(FakeNavigator):
        stalled = False

        def click(
            self, selector, ready_selector=None, has_text=None, ready_any=(), expect_machine_change=False
        ):
            if selector == "#dai_next" and self.machine == "0278" and not self.stalled:
                self.stalled = True
                self.log.append("click #dai_next (no effect)")
                return
            super().click(selector, ready_selector, has_text, ready_any, expect_machine_change)

    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    nav = StallOnce(detail, hall_order=["0001", "0277", "0278", "0280"])
    # 想定台数（一覧の台数合計）を 4 台にするため、1 機種 4 台の一覧にする
    nav.pages["list"] = nav.pages["list"].replace(">3台<", ">4台<")
    summary = IngestionPipeline(ctx).collect(
        _adapter(first_machine="0001"), TARGET, [date(2026, 10, 25)], nav
    )
    assert summary.status == "succeeded", summary.errors
    assert "search 0278" in nav.log  # 開き直した
    with ctx.session() as s:
        saved = {
            r.response_metadata.get("machine_number")
            for r in s.scalars(select(RawPayload))
            if r.response_metadata.get("page_kind") == "machine_detail"
        }
        assert saved == {"0001", "0277", "0278", "0280"}


def test_resume_starts_after_last_saved_machine(ctx, hall):
    """--resume では、取得済みの最後の台を検索で開いて「次台 >>」から続け、取得済みの区間をたどり直さない。"""

    class CrashAfter0278(FakeNavigator):
        def click(
            self, selector, ready_selector=None, has_text=None, ready_any=(), expect_machine_change=False
        ):
            if selector == "#dai_next" and self.machine == "0278":
                raise RuntimeError("browser crashed")
            super().click(selector, ready_selector, has_text, ready_any, expect_machine_change)

        def search_machine(self, number):
            if int(number) == 278:
                raise RuntimeError("browser crashed")  # 復帰の検索も失敗して止まる
            return super().search_machine(number)

    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    order = ["0001", "0277", "0278", "0280", "0300"]
    pipeline = IngestionPipeline(ctx)
    first = pipeline.collect(
        _adapter(first_machine="0001"), TARGET, [date(2026, 10, 25)], CrashAfter0278(detail, hall_order=order)
    )
    assert first.status == "partial"

    nav = FakeNavigator(detail, hall_order=order)
    resumed = pipeline.collect(
        _adapter(first_machine="0001"), TARGET, [date(2026, 10, 25)], nav, resume_run_id=first.run_id
    )
    assert resumed.status == "succeeded", resumed.errors
    assert [e for e in nav.log if e.startswith("search")] == ["search 0278"]  # 0001 からたどり直さない
    assert resumed.counts["pages"] == 2  # 0280, 0300 だけ
    with ctx.session() as s:
        saved = [
            r.response_metadata.get("machine_number")
            for r in s.scalars(select(RawPayload))
            if r.response_metadata.get("page_kind") == "machine_detail"
        ]
        assert sorted(saved) == ["0001", "0277", "0278", "0280", "0300"]


def test_next_machine_gap_continues_by_search(ctx, hall, tmp_path):
    """「次台 >>」が途中の台（0186）から先へ進まないとき、観測済みの次の台番号を検索で開いて最後まで進む。"""
    from tests.helpers import manual_doc, write_doc

    # 前回までに 0236・0237 を観測している（続きの候補になる）
    doc = manual_doc([{"machine_number": n, "fields": {"total_games": 1}} for n in ("0236", "0237")])
    IngestionPipeline(ctx).import_file("pscube", "H001", write_doc(tmp_path, "old.json", doc))

    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    nav = FakeNavigator(detail, hall_order=["0001", "0185", "0186", "0236", "0237"], stop_at={"0186"})
    nav.pages["list"] = nav.pages["list"].replace(">3台<", ">5台<")  # 一覧の台数合計 = 5 台
    summary = IngestionPipeline(ctx).collect(
        _adapter(first_machine="0001"), TARGET, [date(2026, 10, 25)], nav
    )
    assert summary.status == "succeeded", summary.errors
    # 0186 で進まない → 0186 を開き直して押し直す → それでも進まないので 0236 を検索で開いて続ける
    assert [e for e in nav.log if e.startswith("search")] == ["search 0001", "search 0186", "search 0236"]
    with ctx.session() as s:
        saved = sorted(
            r.response_metadata.get("machine_number")
            for r in s.scalars(select(RawPayload))
            if r.response_metadata.get("page_kind") == "machine_detail"
        )
        assert saved == ["0001", "0185", "0186", "0236", "0237"]
        assert s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED")) is None


def test_walk_incomplete_is_recorded(ctx, hall):
    """続きを開けないまま想定台数に届かなかったら、保存台数・想定台数・最後の台番号を記録する。"""
    detail = (D / "machine_detail_0277.html").read_text(encoding="utf-8")
    nav = FakeNavigator(detail, hall_order=["0001", "0002"], stop_at={"0002"})
    summary = IngestionPipeline(ctx).collect(
        _adapter(first_machine="0001"), TARGET, [date(2026, 10, 25)], nav
    )
    assert summary.status == "partial"
    with ctx.session() as s:
        issue = s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED"))
        assert issue.details["kind"] == "walk_incomplete"
        assert "saved 2 of 3 machines; last=0002" in issue.details["message"]
