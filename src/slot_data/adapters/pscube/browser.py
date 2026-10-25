"""ぱちタウン経由で P's CUBE の iframe を操作するブラウザ（Playwright）。

人が閲覧するのと同じく、ぱちタウンの店舗ページを開き、埋め込まれた iframe の中をクリックで進む。
- ボット検知回避（AutomationControlled の無効化および navigator.webdriver の隠蔽）を適用
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
    ) -> None: ...

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
        self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
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
    ) -> None:
        locator = self._frame().locator(selector, has_text=has_text).first
        href = locator.get_attribute("href")
        if href:
            self.robots.check(urljoin(self.url(), href))
        self._wait_interval()
        locator.click()
        self._settle(ready_selector, ready_any)

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
