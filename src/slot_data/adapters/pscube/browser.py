"""ぱちタウン経由で P's CUBE の iframe を操作するブラウザ（Playwright）。

人が閲覧するのと同じく、ぱちタウンの店舗ページを開き、埋め込まれた iframe の中をクリックで進む。
- 画面ありの Chromium を使う
- 画面遷移の間隔は min_interval 秒以上空ける。同時に開くのは 1 ページだけ
- 機種一覧は最後までスクロールして全件を読み込む（追加読み込みは 1 回ごとに 10 秒以上空ける。ADR-0013）
- robots.txt で禁止されたパスへは遷移しない
- 自動操作の目印（AutomationControlled、navigator.webdriver）を無効化する（ADR-0012）。
  運営側の線引きは「人が毎晩確認してノートに記録する程度の定点収集は可、人には不可能な頻度・差分取得は不可」
  （ユーザー申告 2026-10-26）。頻度を上げる変更、CAPTCHA の突破、トークンの解析はしない
"""

from __future__ import annotations

import contextlib
import time
import urllib.robotparser
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

IFRAME_SELECTOR = "#dedama-iframe"


class NavigationBlocked(RuntimeError):
    """robots.txt などで遷移が許されていない。"""


class Navigator(Protocol):
    def open(self, url: str, ready_selector: str) -> None: ...

    def click(
        self,
        selector: str,
        ready_selector: str | None = None,
        has_text: str | None = None,
        ready_any: tuple[str, ...] = (),
        expect_machine_change: bool = False,
    ) -> None: ...

    def scroll_to_end(self, item_selector: str, interval: float, max_rounds: int) -> int: ...

    def current_machine(self) -> str | None: ...

    def search_machine(self, number: str) -> str | None: ...

    def exists(self, selector: str) -> bool: ...

    def html(self) -> str: ...

    def url(self) -> str: ...

    def close(self) -> None: ...


class RobotsGuard:
    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        parser = self._parsers.get(base)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = httpx.get(base + "/robots.txt", timeout=20, headers={"User-Agent": self.user_agent})
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except httpx.HTTPError:
                parser.parse(["User-agent: *", "Disallow: /"])  # 確認できなければ取得しない
            self._parsers[base] = parser
        return parser.can_fetch(self.user_agent, url)

    def check(self, url: str) -> None:
        if not self.allowed(url):
            raise NavigationBlocked(f"robots.txt disallows {url}")


class PlaywrightNavigator:
    """ぱちタウンのページを開き、iframe の中を操作する。"""

    def __init__(
        self,
        min_interval: float,
        robots: RobotsGuard,
        headless: bool = False,
        timeout_ms: int = 60_000,
        sleep=time.sleep,
    ) -> None:
        from playwright.sync_api import sync_playwright

        self.min_interval = min_interval
        self.robots = robots
        self.timeout_ms = timeout_ms
        self._sleep = sleep
        self._last_nav = 0.0
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._browser.new_page(viewport={"width": 1280, "height": 1000}, locale="ja-JP")
        self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        self._page.set_default_timeout(timeout_ms)

    def _wait_interval(self) -> None:
        remaining = self.min_interval - (time.monotonic() - self._last_nav)
        if self._last_nav and remaining > 0:
            self._sleep(remaining)

    def _frame(self):
        return self._page.frame_locator(IFRAME_SELECTOR)

    def _content_frame(self):
        handle = self._page.locator(IFRAME_SELECTOR).element_handle()
        return handle.content_frame() if handle else None

    def _settle(self, ready_selector: str | None, ready_any: tuple[str, ...] = ()) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if ready_selector:
            self._frame().locator(ready_selector).first.wait_for(timeout=self.timeout_ms)
        elif ready_any:
            # どれか 1 つが出るまで待つ（データが表示後の通信で届く画面向け）。出なくても今の画面を返す
            ready = self._frame().locator(ready_any[0])
            for selector in ready_any[1:]:
                ready = ready.or_(self._frame().locator(selector))
            with contextlib.suppress(PlaywrightTimeoutError):
                ready.first.wait_for(timeout=self.timeout_ms)
        else:
            self._page.wait_for_timeout(3000)
        # ぱちタウンは広告・計測の通信が続くため networkidle を待ち切らない
        with contextlib.suppress(PlaywrightTimeoutError):
            self._page.wait_for_load_state("networkidle", timeout=10_000)
        self._last_nav = time.monotonic()

    def open(self, url: str, ready_selector: str) -> None:
        self.robots.check(url)
        self._wait_interval()
        self._page.goto(url)
        self._settle(ready_selector)

    def click(
        self,
        selector: str,
        ready_selector: str | None = None,
        has_text: str | None = None,
        ready_any: tuple[str, ...] = (),
        expect_machine_change: bool = False,
    ) -> None:
        locator = self._frame().locator(selector, has_text=has_text).first
        href = locator.get_attribute("href")
        if href:
            self.robots.check(urljoin(self.url(), href))
        self._wait_interval()
        before = self.current_machine() if expect_machine_change else None
        locator.click()
        if expect_machine_change:
            # 「次台 >>」は URL を変えずに画面の中身だけを次の台に切り替える（2026-11-13 確認）。
            # 表示中の台番号が変わるまで待つ。待たないと古い画面の要素で ready_any が満たされてしまう。
            # 変わらなければ（端に達したなど）呼び出し側が台番号で判断する
            deadline = time.monotonic() + self.timeout_ms / 1000
            while self.current_machine() == before and time.monotonic() < deadline:
                self._page.wait_for_timeout(200)
        self._settle(ready_selector, ready_any)

    def scroll_to_end(self, item_selector: str, interval: float, max_rounds: int) -> int:
        """一覧の最後の項目までスクロールして追加読み込みさせ、件数が増えなくなるまで繰り返す。件数を返す。

        追加読み込みは 1 回ごとに interval 秒以上空ける。件数が増えなかった時点（または max_rounds 回）で終える。
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        items = self._frame().locator(item_selector)
        count = items.count()
        for _ in range(max_rounds):
            last_load = time.monotonic()
            items.nth(count - 1).scroll_into_view_if_needed()
            grown = False
            wait_until = time.monotonic() + max(interval, 15)
            while time.monotonic() < wait_until:
                self._page.wait_for_timeout(500)
                if items.count() > count:
                    grown = True
                    break
            with contextlib.suppress(PlaywrightTimeoutError):
                self._frame().locator("div.nc-loader").first.wait_for(state="hidden", timeout=15_000)
            new_count = items.count()
            if not grown and new_count <= count:
                break
            count = new_count
            remaining = interval - (time.monotonic() - last_load)
            if remaining > 0:
                self._sleep(remaining)
        self._last_nav = time.monotonic()
        return count

    def current_machine(self) -> str | None:
        """表示中の台詳細の台番号（body の nc-da-XXXX クラス）。台詳細でなければ None。"""
        frame = self._content_frame()
        if frame is None:
            return None
        classes = frame.evaluate("() => document.body ? document.body.className : ''") or ""
        for cls in classes.split():
            if cls.startswith("nc-da-"):
                return cls.removeprefix("nc-da-")
        return None

    def search_machine(self, number: str) -> str | None:
        """画面の「台番号で探す」欄に台番号を入れて送信し、表示された台詳細の台番号を返す（開けなければ None）。

        送信先の URL は直接開かず、画面の入力欄とボタンを使う。送信は画面遷移 1 回として min_interval を守る。
        「台番号で探す」タブの切り替えは同じ画面内の表示切り替えなので間隔の対象にしない。
        """
        frame = self._frame()
        tab = frame.locator("li.search-da")
        if tab.count():
            tab.first.click()
        form = frame.locator("form.nc-da-search-form").first
        form.locator("input[name=cd_dai]").fill(number)
        self._wait_interval()
        before = self.current_machine()
        form.locator(".nc-da-search-btn-submit button").first.click()
        # 台詳細に切り替わる（表示中の台番号が変わる）か、お知らせ（該当なしなど）が出るまで待つ
        notify = frame.locator("span.notifyMessage")
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            current = self.current_machine()
            if current is not None and current != before:
                break
            if notify.count() and notify.first.inner_text().strip():
                break
            self._page.wait_for_timeout(300)
        self._settle(None, ("text=累計ゲーム", "span.notifyMessage:has-text('error')"))
        # 開いた台番号を返す（呼び出し側が検索した台番号と一致するかを確かめる）
        return self.current_machine()

    def exists(self, selector: str) -> bool:
        return self._frame().locator(selector).count() > 0

    def html(self) -> str:
        frame = self._content_frame()
        return frame.content() if frame else ""

    def url(self) -> str:
        frame = self._content_frame()
        return frame.url if frame else self._page.url

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._browser.close()
        with contextlib.suppress(Exception):
            self._pw.stop()
