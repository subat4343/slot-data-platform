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

import re
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
from slot_data.observability.logging import get_logger

log = get_logger(__name__)

PTOWN_URL = "https://p-town.dmm.com/shops/{shop}/jackpot"
SLOT_DATA_LINK = "a[href*='nc-v03-001.php']"
MODEL_LINK = "a.btn-ki"
MACHINE_LINK = "a.btn-dai"
# 台詳細のデータは表示後の通信で届くため、データ（「累計ゲーム」）かエラーのお知らせが出るまで待つ
DETAIL_READY = ("text=累計ゲーム", "span.notifyMessage:has-text('error')")
LIST_SOURCE = "pscube_list"  # 機種画面（日別 BONUS 回数）の観測を記録する取得元
NEXT_MACHINE = (
    "#dai_next"  # 台詳細の「次台 >>」（ホール全体の台番号順。URL は変わらず画面の中身が切り替わる）
)
PREV_MACHINE = "#dai_prev"  # 台詳細の「<< 前台」
MAX_JUMP_ATTEMPTS = 5  # 「次台 >>」の切れ目の続きを検索で探すときに試す台番号の数
MAX_NEXT_RECOVERIES = 2  # 「次台 >>」が効かないときに検索で開き直す回数の上限（1 回の巡回あたり）
DEFAULT_FIRST_MACHINE = "0001"  # 台詳細の巡回を始める台番号（options.first_machine で変更できる）
# 機種一覧の追加読み込み（スクロール）の間隔（秒）と回数の上限。options.list_scroll_interval で変更できる
DEFAULT_LIST_SCROLL_INTERVAL = 10.0
MAX_LIST_ROUNDS = 20
_MACHINE_IN_URL = re.compile(r"nc-v06-001\.php\?cd_dai=([0-9A-Za-z-]+)")


def search_number(number: str) -> str:
    """検索欄に入れる台番号（数字だけなら 4 桁にゼロ埋め。画面の表示「0001」に合わせる）。"""
    return number.zfill(4) if number.isdigit() else number


def same_machine(a: str, b: str) -> bool:
    return (int(a) == int(b)) if a.isdigit() and b.isdigit() else a == b


def machine_in_url(url: str) -> str | None:
    m = _MACHINE_IN_URL.search(url)
    return m.group(1) if m else None


class PageError(RuntimeError):
    def __init__(self, code: int, url: str) -> None:
        super().__init__(f"page shows error({code}): {url}")
        self.code = code
        self.kind = "access_denied" if code == 451 else "page_error"


class WrongPageError(RuntimeError):
    """クリックしたのに期待した画面へ遷移していない（遷移前の画面を保存しないために使う）。"""

    kind = "wrong_page"

    def __init__(self, expected: str, url: str) -> None:
        super().__init__(f"expected {expected} but at {url}")


class SearchFailedError(RuntimeError):
    """「台番号で探す」で台詳細を開けなかった。"""

    kind = "search_failed"

    def __init__(self, numbers: str) -> None:
        super().__init__(f"could not open machine detail by search: {numbers}")


class WalkIncompleteError(RuntimeError):
    """「次台 >>」と検索で辿れた台が、一覧の台数合計に届かないまま終わった（取りこぼし）。"""

    kind = "walk_incomplete"

    def __init__(self, saved: int, expected: int, last: str | None, reason: str) -> None:
        super().__init__(f"saved {saved} of {expected} machines; last={last}; reason={reason}")


class DeadlineReached(RuntimeError):
    """営業日の区切りまでに終わらせるための打ち切り時刻に達した。"""

    kind = "deadline"


# 店の営業日の区切り（05:00）までに終えるための打ち切り時刻（店舗ローカル）。options.deadline で変更できる
DEFAULT_DEADLINE = time(4, 45)
DEADLINE_TZ = ZoneInfo("Asia/Tokyo")


def next_deadline(start: datetime, at: time) -> datetime:
    """start より後で最初に来る at（店舗ローカル時刻）。"""
    local = start.astimezone(DEADLINE_TZ)
    candidate = datetime.combine(local.date(), at, tzinfo=DEADLINE_TZ)
    return candidate if candidate > local else candidate + timedelta(days=1)


class PsCubeAdapter(CrawlingAdapter):
    source_code = "pscube"
    adapter_version = "0.4.0-ptown-search-next"

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

    @property
    def deadline_time(self) -> time:
        value = self.config.options.get("deadline")
        return time.fromisoformat(value) if value else DEFAULT_DEADLINE

    def open_client(self) -> Navigator:
        self.ensure_automated_fetch_allowed()
        return PlaywrightNavigator(
            min_interval=self.config.rate_limit.min_interval_seconds,
            robots=RobotsGuard(self.config.user_agent),
            headless=bool(self.config.options.get("headless", False)),
        )

    def start_url(self, target: CollectTarget) -> str:
        return PTOWN_URL.format(shop=target.source_hall_id.strip("/"))

    @property
    def first_machine(self) -> str:
        return str(self.config.options.get("first_machine", DEFAULT_FIRST_MACHINE))

    @property
    def list_scroll_interval(self) -> float:
        return float(self.config.options.get("list_scroll_interval", DEFAULT_LIST_SCROLL_INTERVAL))

    def crawl(
        self,
        target: CollectTarget,
        business_dates: list[date],
        client: Any,
        skip: set[str],
        hints: dict[str, Any] | None = None,
    ) -> Iterator[CrawlStep]:
        """ぱちタウン店舗ページ → スロットデータ（一覧を最後までスクロール）→ 台詳細を「次台 >>」で順に辿る。

        - 機種一覧は 20 件ずつ追加で読み込まれるため、最後までスクロールして全件を読み込んでから保存する
        - 台詳細は、画面の「台番号で探す」欄で先頭の台（options.first_machine、既定 0001）を開き、「次台 >>」だけで
          台番号の昇順に最後の台まで進む（1 台につき画面遷移 1 回。ADR-0014）。開けなければ前回までの最小の台番号
          （hints.known_min_machine）で検索し直し、それでも開けなければ先頭の機種の先頭の台から入る
        - 「次台 >>」が効かないときは、現在の台を検索で開き直して続ける（1 回の巡回で 2 回まで）
        - 「次台 >>」が途中の台から先へ進まない（区画の切れ目など）ときは、これまでに観測した台番号のうち次に大きい
          台を検索で開いて続ける（hints.known_machines）。一覧の台数合計に届かないまま終わったら walk_incomplete を記録
        - 再開（--resume）では、取得済みの最後の台（hints.resume_after）を検索で開き、そこから続ける
        - crawl_details が false のときは、従来どおり機種画面（日別 BONUS 回数）を機種ごとに開く
        - 画面の「本日」は実行時点の営業日なので、business_dates の最終日を取得日のラベルに使う
        - 台詳細で error(451) などが出たら、その回の台詳細巡回は打ち切る（再試行しない）
        """
        self.ensure_automated_fetch_allowed()
        nav: Navigator = client
        day = max(business_dates)
        start = self.start_url(target)
        deadline = next_deadline(utcnow(), self.deadline_time)

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

        def past_deadline() -> bool:
            return utcnow() >= deadline

        def deadline_step(req: FetchRequest) -> CrawlStep:
            return CrawlStep(
                req, None, failure=DeadlineReached(f"stopped at deadline {deadline.isoformat()}")
            )

        nav.open(start, ready_selector=SLOT_DATA_LINK)
        nav.click(SLOT_DATA_LINK, ready_selector=MODEL_LINK)
        nav.scroll_to_end(MODEL_LINK, interval=self.list_scroll_interval, max_rounds=MAX_LIST_ROUNDS)
        list_html = nav.html()
        list_req = request("model_list", "nc-v03-001.php")
        if list_req.fingerprint() not in skip:
            yield CrawlStep(
                list_req, ModelListParser(), snapshot("model_list", list_html), source_code=LIST_SOURCE
            )
        models = parse_model_list(list_html)
        if not models:
            return

        if not self.crawl_details:
            for model in models:
                model_req = request("model_page", model.href)
                if model_req.fingerprint() in skip:
                    continue
                if past_deadline():
                    yield deadline_step(model_req)
                    return
                nav.click(f"{MODEL_LINK}[href='{model.href}']", ready_selector=MACHINE_LINK)
                html = nav.html()
                code = page_error_code(html)
                yield CrawlStep(
                    model_req,
                    ModelPageParser(),
                    snapshot("model_page", html),
                    failure=PageError(code, nav.url()) if code else None,
                    source_code=LIST_SOURCE,
                )
                nav.click(SLOT_DATA_LINK, has_text="機種一覧", ready_selector=MODEL_LINK)
            return

        # ---- 台詳細: 台番号検索で先頭の台を開き、「次台 >>」だけで最後まで進む ----------------
        seen: set[str] = set()
        expected = sum(m.machine_count or 0 for m in models)
        limit = expected * 2 + 50  # 想定外のループを防ぐ上限
        state = {"blocked": False, "recoveries": MAX_NEXT_RECOVERIES}

        def open_by_search(number: str) -> Iterator[CrawlStep]:
            """画面の「台番号で探す」欄で台番号を検索して台詳細を開く。開けた台番号を返す（開けなければ None）。"""
            if past_deadline():
                yield deadline_step(request("machine_detail", "search", number))
                return None
            got = nav.search_machine(search_number(number))
            if got is not None and same_machine(got, number):
                return got
            return None

        def open_start_machine() -> Iterator[CrawlStep]:
            """検索で開けなかった場合の入口: 先頭の機種の機種画面から先頭の台を開く。"""
            first_model = models[0]
            model_req = request("model_page", first_model.href)
            if past_deadline():
                yield deadline_step(model_req)
                return None
            nav.click(f"{MODEL_LINK}[href='{first_model.href}']", ready_selector=MACHINE_LINK)
            html = nav.html()
            code = page_error_code(html)
            if model_req.fingerprint() not in skip:
                yield CrawlStep(
                    model_req,
                    ModelPageParser(),
                    snapshot("model_page", html),
                    failure=PageError(code, nav.url()) if code else None,
                    source_code=LIST_SOURCE,
                )
            links = parse_machine_links(html)
            if code or not links:
                return None
            number, href = links[0]
            if past_deadline():
                yield deadline_step(request("machine_detail", href, number))
                return None
            nav.click(f"{MACHINE_LINK}[href='{href}']", ready_any=DETAIL_READY)
            if machine_in_url(nav.url()) != number or nav.current_machine() != number:
                yield CrawlStep(
                    request("machine_detail", href, number),
                    None,
                    failure=WrongPageError(f"nc-v06-001.php?cd_dai={number}", nav.url()),
                )
                return None
            return number

        def save_detail(number: str) -> Iterator[CrawlStep]:
            detail_req = request("machine_detail", f"nc-v06-001.php?cd_dai={number}", number)
            wanted = not target.machines or number in target.machines
            if detail_req.fingerprint() in skip or not wanted:
                return
            html = nav.html()
            code = page_error_code(html)
            result = snapshot("machine_detail", html)
            result.response_metadata["machine_number"] = number  # 「次台 >>」では URL が変わらないため
            # 拒否画面も証跡として原本保存し、失敗として記録する
            yield CrawlStep(
                detail_req,
                MachineDetailParser(),
                result,
                failure=PageError(code, nav.url()) if code else None,
            )
            if code:
                state["blocked"] = True

        def recover(current: str) -> Iterator[CrawlStep]:
            """「次台 >>」が効かないとき、現在の台を検索で開き直す（1 回の巡回で MAX_NEXT_RECOVERIES 回まで）。"""
            if state["recoveries"] <= 0:
                return False
            state["recoveries"] -= 1
            log.info("next-machine did not advance at %s; reopening it by search", current)
            got = yield from open_by_search(current)
            return got is not None

        def walk(current: str) -> Iterator[CrawlStep]:
            """「次台 >>」を押し続けて台詳細を保存する。終わった理由を返す（最後に表示した台は state["last"]）。"""
            state["last"] = current
            while len(seen) < limit:
                if past_deadline():
                    yield deadline_step(request("machine_detail", "nc-v06-001.php", None))
                    return "deadline"
                if not nav.exists(NEXT_MACHINE):
                    return "end"
                try:
                    nav.click(NEXT_MACHINE, ready_any=DETAIL_READY, expect_machine_change=True)
                except Exception:
                    # 画面の状態がおかしくなった（タイムアウトなど）。現在の台を検索で開き直して続ける
                    log.warning("next-machine click failed at %s", current, exc_info=True)
                    if (yield from recover(current)):
                        continue
                    raise
                number = nav.current_machine()
                if number is None or number == current:
                    # 台が変わらない: 最後の台に達したか、「次台 >>」がその台から先へ進まない（区画の切れ目など）。
                    # まだ想定台数に届いていなければ、現在の台を検索で開き直して一度だけ押し直す
                    first_stall = state["recoveries"] == MAX_NEXT_RECOVERIES
                    if len(seen) < expected and first_stall and (yield from recover(current)):
                        continue
                    if number is None:
                        yield CrawlStep(
                            request("machine_detail", "nc-v06-001.php", None),
                            None,
                            failure=WrongPageError("nc-v06-001.php?cd_dai=<番号>", nav.url()),
                        )
                        return "stuck"
                    return "end"
                if number in seen:
                    return "wrapped"  # 一周した
                seen.add(number)
                yield from save_detail(number)
                if state["blocked"]:
                    return "blocked"
                current = number
                state["last"] = current
            return "limit"

        def jump_forward(after: str) -> Iterator[CrawlStep]:
            """「次台 >>」で進めなくなった台の続きを、台番号検索で開く。開けた台番号を返す（無ければ None）。

            候補は、これまでに観測した台番号のうち after より大きいもの（小さい順）。観測が無い区間は after+1 から
            数件だけ試す。存在しない台番号の検索は画面が変わらないだけなので、候補を順に試す。
            """
            seen_numbers = {int(n) for n in seen if n.isdigit()}
            known = [n for n in (hints or {}).get("known_machines", []) if n.isdigit()]
            candidates = sorted({int(n) for n in known if int(n) > int(after)} - seen_numbers)
            if not candidates and after.isdigit():
                candidates = [int(after) + i for i in range(1, MAX_JUMP_ATTEMPTS + 1)]
            for value in candidates[:MAX_JUMP_ATTEMPTS]:
                number = search_number(str(value))
                got = yield from open_by_search(number)
                if got is not None:
                    log.info("next-machine stopped at %s; continued from %s by search", after, got)
                    return got
                if past_deadline():
                    return None
            log.info("next-machine stopped at %s; no following machine could be opened by search", after)
            return None

        def walk_to_end(current: str) -> Iterator[CrawlStep]:
            """walk を続け、「次台 >>」の切れ目では検索で続きを開いて最後まで進む。最後に取りこぼしを記録する。"""
            while True:
                reason = yield from walk(current)
                last = state["last"]
                log.info(
                    "walk finished: reason=%s last=%s saved=%d expected=%d", reason, last, len(seen), expected
                )
                if reason != "end" or len(seen) >= expected:
                    break
                nxt = yield from jump_forward(last)
                if nxt is None:
                    break
                seen.add(nxt)
                yield from save_detail(nxt)
                if state["blocked"]:
                    return
                current = nxt
            if reason in ("end", "stuck", "limit") and len(seen) < expected:
                # 想定台数（一覧の台数合計）に届かないまま終わった。取りこぼしとして記録する（run は partial になる）
                yield CrawlStep(
                    request("machine_detail", "walk", state["last"]),
                    None,
                    failure=WalkIncompleteError(len(seen), expected, state["last"], reason),
                )

        resume_after = (hints or {}).get("resume_after")
        if resume_after:
            # 再開: 取得済みの最後の台を検索で開き、そこから「次台 >>」で続ける（取得済みの区間はたどり直さない）
            current = yield from open_by_search(resume_after)
            if current is None:
                yield CrawlStep(
                    request("machine_detail", "search", resume_after),
                    None,
                    failure=SearchFailedError(resume_after),
                )
                return
            seen.add(current)
            yield from walk_to_end(current)
            return

        current = None
        candidates = [self.first_machine]
        known_min = (hints or {}).get("known_min_machine")
        if known_min and not same_machine(known_min, self.first_machine):
            candidates.append(known_min)
        for candidate in candidates:
            current = yield from open_by_search(candidate)
            if current is not None:
                break
        if current is None:
            # 検索で開けなかった。記録を残し、先頭の機種の先頭の台から「次台 >>」方向だけを辿る
            yield CrawlStep(
                request("machine_detail", "search", candidates[0]),
                None,
                failure=SearchFailedError(", ".join(candidates)),
            )
            current = yield from open_start_machine()
            if current is None:
                return
        seen.add(current)
        yield from save_detail(current)
        if state["blocked"]:
            return
        yield from walk_to_end(current)
