"""P's CUBE アダプター（DMMぱちタウン埋め込み「大当り情報」経由）。

経緯（docs/source_contracts.md）:
- P's CUBE 約款 第5条(1) は、明示的な許可なく自動化された手段で情報を取得することを禁止している
- 2026-10-23、ユーザーより「株式会社アンジェロから許可を得ている。条件は高頻度アクセスと API 等の非公開経路の
  利用の禁止。ブラウザ操作で人が確認する程度に」との申告があり、設定の permission_reference に記録した
- 台詳細は P's CUBE を直接開くと error(451) になり、ぱちタウンの店舗ページの iframe 内で操作すると表示された

このため、ぱちタウンの店舗ページを画面ありのブラウザで開き、iframe 内を人の閲覧順にクリックして画面 HTML を
原本として保存する。非公開 API（cgi-bin のデータ取得 URL など）を直接呼ぶことはしない。
許可設定（access_policy）が無ければ起動しない。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

from slot_data.adapters.base import (
    CrawlingAdapter,
    CrawlStep,
    FetchRequest,
    FetchResult,
    Parser,
    SourceNotImplementedError,
)
from slot_data.adapters.pscube.browser import Navigator, PlaywrightNavigator, RobotsGuard
from slot_data.adapters.pscube.parsers import (
    MachineDetailParser,
    ModelListParser,
    ModelPageParser,
    page_error_code,
    parse_machine_links,
    parse_model_list,
)
from slot_data.config import CollectTarget
from slot_data.domain.timeutil import utcnow

PTOWN_URL = "https://p-town.dmm.com/shops/{shop}/jackpot"
SLOT_DATA_LINK = "a[href*='nc-v03-001.php']"
MODEL_LINK = "a.btn-ki"
MACHINE_LINK = "a.btn-dai"
LIST_SOURCE = "pscube_list"  # 機種画面（日別 BONUS 回数）の観測を記録する取得元


class PageError(RuntimeError):
    def __init__(self, code: int, url: str) -> None:
        super().__init__(f"page shows error({code}): {url}")
        self.code = code
        self.kind = "access_denied" if code == 451 else "page_error"


class PsCubeAdapter(CrawlingAdapter):
    source_code = "pscube"
    adapter_version = "0.2.0-ptown-browser"

    def parser_for(self, request_kind: str, content_type: str) -> Parser:
        parsers = {
            "model_list": ModelListParser(),
            "model_page": ModelPageParser(),
            "machine_detail": MachineDetailParser(),
        }
        if request_kind not in parsers:
            raise SourceNotImplementedError(f"P's CUBE: unknown page kind {request_kind}")
        return parsers[request_kind]

    @property
    def crawl_details(self) -> bool:
        return bool(self.config.options.get("crawl_details", True))

    def open_client(self) -> Navigator:
        self.ensure_automated_fetch_allowed()
        return PlaywrightNavigator(
            min_interval=self.config.rate_limit.min_interval_seconds,
            robots=RobotsGuard(self.config.user_agent),
            headless=bool(self.config.options.get("headless", False)),
        )

    def start_url(self, target: CollectTarget) -> str:
        return PTOWN_URL.format(shop=target.source_hall_id.strip("/"))

    def crawl(
        self, target: CollectTarget, business_dates: list[date], client: Any, skip: set[str]
    ) -> Iterator[CrawlStep]:
        """ぱちタウン店舗ページ → スロットデータ → 各機種 →（各台詳細）を人の閲覧順に辿る。

        画面の「本日」は実行時点の営業日なので、business_dates の最終日を取得日のラベルに使う。
        台詳細で error(451) などが出たら、その回の台詳細巡回は打ち切る（再試行しない）。
        """
        self.ensure_automated_fetch_allowed()
        nav: Navigator = client
        day = max(business_dates)
        start = self.start_url(target)

        def request(kind: str, url: str, machine: str | None = None) -> FetchRequest:
            return FetchRequest(
                kind=kind, url=url, hall_code=target.hall_code, business_date=day, machine_number=machine
            )

        def snapshot(kind: str, html: str) -> FetchResult:
            return FetchResult(
                content=html.encode("utf-8"),
                content_type="text/html; charset=utf-8",
                fetched_at=utcnow(),
                resource_locator=nav.url(),
                response_metadata={"via": start, "page_kind": kind, "renderer": "playwright-chromium"},
            )

        nav.open(start, ready_selector=SLOT_DATA_LINK)
        nav.click(SLOT_DATA_LINK, ready_selector=MODEL_LINK)
        list_html = nav.html()
        list_req = request("model_list", "nc-v03-001.php")
        if list_req.fingerprint() not in skip:
            yield CrawlStep(
                list_req, ModelListParser(), snapshot("model_list", list_html), source_code=LIST_SOURCE
            )

        details_blocked = False
        for model in parse_model_list(list_html):
            model_req = request("model_page", model.href)
            model_done = model_req.fingerprint() in skip
            if model_done and (not self.crawl_details or details_blocked):
                continue
            nav.click(f"{MODEL_LINK}[href='{model.href}']", ready_selector=MACHINE_LINK)
            html = nav.html()
            code = page_error_code(html)
            if not model_done:
                yield CrawlStep(
                    model_req,
                    ModelPageParser(),
                    snapshot("model_page", html),
                    failure=PageError(code, nav.url()) if code else None,
                    source_code=LIST_SOURCE,
                )

            if self.crawl_details and not details_blocked and code is None:
                for number, href in parse_machine_links(html):
                    if target.machines and number not in target.machines:
                        continue
                    detail_req = request("machine_detail", href, number)
                    if detail_req.fingerprint() in skip:
                        continue
                    nav.click(f"{MACHINE_LINK}[href='{href}']")
                    detail_html = nav.html()
                    detail_code = page_error_code(detail_html)
                    # 拒否画面も証跡として原本保存し、失敗として記録する
                    yield CrawlStep(
                        detail_req,
                        MachineDetailParser(),
                        snapshot("machine_detail", detail_html),
                        failure=PageError(detail_code, nav.url()) if detail_code else None,
                    )
                    nav.click("a", has_text="機種データ", ready_selector=MACHINE_LINK)
                    if detail_code:
                        details_blocked = True
                        break

            nav.click(SLOT_DATA_LINK, has_text="機種一覧", ready_selector=MODEL_LINK)
