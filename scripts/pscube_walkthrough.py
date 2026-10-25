"""P's CUBE（ぱちタウン埋め込み）の画面を、目視で確認しながら 1 ステップずつ辿る確認スクリプト。

人が閲覧するのと同じく、DMMぱちタウンの店舗ページを開き、埋め込まれた P's CUBE の iframe の中を
トップ → スロットデータ → 機種 → 台詳細 の順にクリックで進む。ブラウザを画面に表示し、各ステップの前に
ターミナルで Enter を押すまで待つ。各画面のスクリーンショット（ページ全体と iframe 部分）を保存する。

- 画面遷移の間は最低 MIN_INTERVAL 秒空ける（早く Enter を押しても待つ）
- ボット検知を回避するための偽装（UA の偽装・webdriver の隠蔽など）はしない
- 台詳細で「error(451)」が表示された場合は検知して報告し、再試行しない
- --direct を付けると、ぱちタウンを経由せず iframe の中身の URL を直接開く（比較用）
- --auto を付けると Enter を待たずに --interval 秒間隔で進む（--headless で画面なし）
- 各画面の HTML（ぱちタウン経由なら iframe の中身）も保存する（パーサーのテスト用サンプル）

使い方:
    python scripts/pscube_walkthrough.py [--model マイジャグラーⅥ] [--machine 0277] [--direct]
                                         [--auto] [--headless] [--interval 30] [--next-machines 1]
VS Code では「実行とデバッグ」から「P's CUBE 台詳細までの確認（目視）」を選んで F5。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import FrameLocator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

PTOWN = "https://p-town.dmm.com/shops/kanagawa/13152/jackpot"
PSCUBE = "https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c743201/"
IFRAME = "#dedama-iframe"
MIN_INTERVAL = 15.0
DETAIL_DATA_TEXT = "累計ゲーム"
DETAIL_WAIT_SECONDS = 30
OUT_ROOT = Path("var/walkthrough")


class Walkthrough:
    def __init__(self, page: Page, out: Path, via_ptown: bool, auto: bool, interval: float) -> None:
        self.page = page
        self.out = out
        self.via_ptown = via_ptown
        self.auto = auto
        self.interval = max(interval, MIN_INTERVAL)
        self.step_no = 0
        self.last_nav = 0.0
        self.detail_wait = DETAIL_WAIT_SECONDS
        # 原因調査用: P's CUBE への通信とブラウザのコンソール出力を画面ごとに記録する
        self.events: list[dict] = []
        self.started = time.monotonic()
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_failed)
        page.on("console", self._on_console)

    def _t(self) -> float:
        return round(time.monotonic() - self.started, 2)

    def _on_response(self, response) -> None:
        if "pscube.jp" not in response.url:
            return
        request = response.request
        event = {
            "t": self._t(),
            "event": "response",
            "type": request.resource_type,
            "method": request.method,
            "url": response.url,
            "status": response.status,
            "content_type": response.headers.get("content-type"),
            "referer": request.headers.get("referer"),
        }
        if request.resource_type in ("xhr", "fetch"):
            with contextlib.suppress(Exception):
                body = response.body()
                event["length"] = len(body)
                name = f"body_{len(self.events):03d}.txt"
                (self.out / name).write_bytes(body[:500_000])
                event["body_file"] = name
        self.events.append(event)

    def _on_failed(self, request) -> None:
        if "pscube.jp" in request.url:
            self.events.append(
                {"t": self._t(), "event": "failed", "url": request.url, "error": request.failure}
            )

    def _on_console(self, message) -> None:
        self.events.append(
            {"t": self._t(), "event": "console", "type": message.type, "text": message.text[:500]}
        )

    def flush_events(self, name: str) -> None:
        path = self.out / f"{self.step_no:02d}_{name}_network.json"
        path.write_text(json.dumps(self.events, ensure_ascii=False, indent=1), encoding="utf-8")
        xhr = [e for e in self.events if e.get("type") in ("xhr", "fetch")]
        for e in xhr:
            print(f"  通信: {e['status']} {e['method']} {e['url'][:110]} ({e.get('length', '?')} bytes)")
        self.events = []

    @property
    def view(self) -> Page | FrameLocator:
        """操作対象。ぱちタウン経由なら iframe の中、直接ならページそのもの。"""
        return self.page.frame_locator(IFRAME) if self.via_ptown else self.page

    def frame(self):
        if not self.via_ptown:
            return self.page.main_frame
        return self.page.locator(IFRAME).element_handle().content_frame()

    def current_url(self) -> str:
        if not self.via_ptown:
            return self.page.url
        frame = self.frame()
        return f"{self.page.url}  （iframe: {frame.url if frame else '未読み込み'}）"

    def confirm(self, message: str) -> None:
        self.step_no += 1
        if self.auto:
            print(f"\n[ステップ {self.step_no}] {message}")
        else:
            input(f"\n[ステップ {self.step_no}] {message}\n  → Enter で実行（中止は Ctrl+C）: ")
        remaining = self.interval - (time.monotonic() - self.last_nav)
        if self.last_nav and remaining > 0:
            print(f"  サイト負荷を抑えるため {remaining:.0f} 秒待ちます…")
            time.sleep(remaining)

    def wait_detail(self) -> None:
        """台詳細のデータ（「累計ゲーム」）かエラーのお知らせが出るまで待つ（データは表示後の通信で届く）。"""
        print(f"  台詳細のデータを待っています（最大 {self.detail_wait} 秒）…")
        ready = self.view.get_by_text(DETAIL_DATA_TEXT).or_(
            self.view.locator("span.notifyMessage", has_text="error")
        )
        with contextlib.suppress(PlaywrightTimeoutError):
            ready.first.wait_for(timeout=self.detail_wait * 1000)

    def loaded(self, name: str, ready_selector: str | None = None) -> None:
        if ready_selector:
            self.view.locator(ready_selector).first.wait_for(timeout=60_000)
        # ぱちタウンは広告・計測の通信が続き networkidle にならないことがあるため、上限を設けて先へ進む
        with contextlib.suppress(PlaywrightTimeoutError):
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        self.last_nav = time.monotonic()
        shot = self.out / f"{self.step_no:02d}_{name}.png"
        self.page.screenshot(path=str(shot), full_page=True)
        if self.via_ptown:
            iframe = self.page.locator(IFRAME)
            iframe.scroll_into_view_if_needed()
            iframe.screenshot(path=str(self.out / f"{self.step_no:02d}_{name}_iframe.png"))
        frame = self.frame()
        if frame is not None:
            (self.out / f"{self.step_no:02d}_{name}.html").write_text(frame.content(), encoding="utf-8")
        self.flush_events(name)
        print(f"  表示: {self.current_url()}")
        print(f"  スクリーンショット: {shot}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", default="マイジャグラーⅥ", help="機種一覧でクリックする機種名（部分一致）")
    parser.add_argument("--machine", default=None, help="台番号（省略時は機種画面の先頭の台）")
    parser.add_argument("--direct", action="store_true", help="ぱちタウンを経由せず iframe の中身を直接開く")
    parser.add_argument("--auto", action="store_true", help="Enter を待たずに自動で進む")
    parser.add_argument("--headless", action="store_true", help="ブラウザを画面に表示しない")
    parser.add_argument("--interval", type=float, default=MIN_INTERVAL, help="画面遷移の最小間隔（秒）")
    parser.add_argument(
        "--detail-wait", type=int, default=DETAIL_WAIT_SECONDS, help="台詳細のデータを待つ最大秒数"
    )
    parser.add_argument("--next-machines", type=int, default=0, help="台詳細の後に「次台 >>」を押す回数")
    args = parser.parse_args()

    out = OUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    route = "P's CUBE を直接" if args.direct else "ぱちタウン経由（iframe の中を操作）"
    print(f"P's CUBE 台詳細までの確認を開始します（{route}）。ブラウザのウィンドウを見ながら進めてください。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=0 if args.headless else 300)
        viewport = {"width": 430, "height": 932} if args.direct else {"width": 1280, "height": 1000}
        page = browser.new_page(viewport=viewport, locale="ja-JP")
        w = Walkthrough(page, out, via_ptown=not args.direct, auto=args.auto, interval=args.interval)
        w.detail_wait = args.detail_wait
        try:
            start = PSCUBE if args.direct else PTOWN
            w.confirm(f"ページを開きます: {start}")
            page.goto(start)
            w.loaded("top", "a[href*='nc-v03-001.php']")

            w.confirm("「スロットデータ」をクリックして機種一覧を開きます")
            w.view.locator("a[href*='nc-v03-001.php']").first.click()
            w.loaded("model_list", "a[href*='nc-v05-003.php']")
            models = [t.strip() for t in w.view.locator("a[href*='nc-v05-003.php']").all_inner_texts()]
            print(f"  機種数: {len(models)}")

            w.confirm(f"機種「{args.model}」をクリックします")
            w.view.locator("a[href*='nc-v05-003.php']", has_text=args.model).first.click()
            w.loaded("model", "a.btn-dai")
            machines = [t.strip() for t in w.view.locator("a.btn-dai").all_inner_texts()]
            print(f"  台番号: {machines}")
            if not machines:
                print("  台番号が見つからないため終了します。")
                return

            machine = args.machine or machines[0]
            w.confirm(f"台番号「{machine}」をクリックして台詳細を開きます")
            w.view.locator("a.btn-dai", has_text=machine).first.click()
            w.wait_detail()
            w.loaded("machine_detail")
            body = w.view.locator("body").inner_text()
            if "error(" in body:
                print("\n  ★ 台詳細はエラー表示で、データは表示されませんでした。再試行はしません。")
            elif DETAIL_DATA_TEXT not in body:
                print(f"\n  ★ {w.detail_wait} 秒待ちましたが、台詳細のデータが表示されませんでした。")
            else:
                print("\n  ★ 台詳細が表示されました。ブラウザの画面を確認してください。")
                for i in range(args.next_machines):
                    w.confirm("「次台 >>」をクリックします")
                    w.view.get_by_text("次台").first.click()
                    w.wait_detail()
                    w.loaded(f"next_machine_{i + 1}")

            if not args.auto:
                input("\n確認が終わったら Enter でブラウザを閉じます: ")
        except KeyboardInterrupt:
            print("\n中止しました。")
        finally:
            browser.close()
    print(f"保存先: {out.resolve()}")


if __name__ == "__main__":
    main()
