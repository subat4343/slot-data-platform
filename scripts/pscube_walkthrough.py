"""P's CUBE（ぱちタウン埋め込み）の画面を、目視で確認しながら 1 ステップずつ辿る確認スクリプト。

人が閲覧するのと同じく、DMMぱちタウンの店舗ページを開き、埋め込まれた P's CUBE の iframe の中を
トップ → スロットデータ → 機種 → 台詳細 の順にクリックで進む。ブラウザを画面に表示し、各ステップの前に
ターミナルで Enter を押すまで待つ。各画面のスクリーンショット（ページ全体と iframe 部分）を保存する。

- 画面遷移の間は最低 MIN_INTERVAL 秒空ける（早く Enter を押しても待つ）
- ボット検知を回避するための偽装（UA の偽装・webdriver の隠蔽など）はしない
- 台詳細で「error(451)」が表示された場合は検知して報告し、再試行しない
- --direct を付けると、ぱちタウンを経由せず iframe の中身の URL を直接開く（比較用）

使い方:
    python scripts/pscube_walkthrough.py [--model マイジャグラーⅥ] [--machine 0277] [--direct]
VS Code では「実行とデバッグ」から「P's CUBE 台詳細までの確認（目視）」を選んで F5。
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import FrameLocator, Page, sync_playwright

PTOWN = "https://p-town.dmm.com/shops/kanagawa/13152/jackpot"
PSCUBE = "https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c743201/"
IFRAME = "#dedama-iframe"
MIN_INTERVAL = 15.0
OUT_ROOT = Path("var/walkthrough")


class Walkthrough:
    def __init__(self, page: Page, out: Path, via_ptown: bool) -> None:
        self.page = page
        self.out = out
        self.via_ptown = via_ptown
        self.step_no = 0
        self.last_nav = 0.0

    @property
    def view(self) -> Page | FrameLocator:
        """操作対象。ぱちタウン経由なら iframe の中、直接ならページそのもの。"""
        return self.page.frame_locator(IFRAME) if self.via_ptown else self.page

    def current_url(self) -> str:
        if not self.via_ptown:
            return self.page.url
        frame = self.page.locator(IFRAME).element_handle().content_frame()
        return f"{self.page.url}  （iframe: {frame.url if frame else '未読み込み'}）"

    def confirm(self, message: str) -> None:
        self.step_no += 1
        input(f"\n[ステップ {self.step_no}] {message}\n  → Enter で実行（中止は Ctrl+C）: ")
        remaining = MIN_INTERVAL - (time.monotonic() - self.last_nav)
        if self.last_nav and remaining > 0:
            print(f"  サイト負荷を抑えるため {remaining:.0f} 秒待ちます…")
            time.sleep(remaining)

    def loaded(self, name: str, ready_selector: str | None = None) -> None:
        if ready_selector:
            self.view.locator(ready_selector).first.wait_for(timeout=60_000)
        self.page.wait_for_load_state("networkidle")
        self.last_nav = time.monotonic()
        shot = self.out / f"{self.step_no:02d}_{name}.png"
        self.page.screenshot(path=str(shot), full_page=True)
        if self.via_ptown:
            iframe = self.page.locator(IFRAME)
            iframe.scroll_into_view_if_needed()
            iframe.screenshot(path=str(self.out / f"{self.step_no:02d}_{name}_iframe.png"))
        print(f"  表示: {self.current_url()}")
        print(f"  スクリーンショット: {shot}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", default="マイジャグラーⅥ", help="機種一覧でクリックする機種名（部分一致）")
    parser.add_argument("--machine", default=None, help="台番号（省略時は機種画面の先頭の台）")
    parser.add_argument("--direct", action="store_true", help="ぱちタウンを経由せず iframe の中身を直接開く")
    args = parser.parse_args()

    out = OUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    route = "P's CUBE を直接" if args.direct else "ぱちタウン経由（iframe の中を操作）"
    print(f"P's CUBE 台詳細までの確認を開始します（{route}）。ブラウザのウィンドウを見ながら進めてください。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        viewport = {"width": 430, "height": 932} if args.direct else {"width": 1280, "height": 1000}
        page = browser.new_page(viewport=viewport, locale="ja-JP")
        w = Walkthrough(page, out, via_ptown=not args.direct)
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
            page.wait_for_timeout(3000)
            w.loaded("machine_detail")
            body = w.view.locator("body").inner_text()
            if "error(451)" in body:
                print("\n  ★ 台詳細は「error(451)」で表示されませんでした。再試行はしません。")
            else:
                print("\n  ★ 台詳細が表示されました。ブラウザの画面を確認してください。")

            input("\n確認が終わったら Enter でブラウザを閉じます: ")
        except KeyboardInterrupt:
            print("\n中止しました。")
        finally:
            browser.close()
    print(f"保存先: {out.resolve()}")


if __name__ == "__main__":
    main()
