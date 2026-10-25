# 収集用 PC の Claude への指示文

別の Windows PC の Claude（デスクトップアプリ。ローカルでコマンドを実行できるモード）に、下の「指示文」の枠内をそのまま貼り付けて使います。

---

## 指示文

```text
このPCを、スロットデータ収集システム「slot-data-platform」の収集用PCとしてセットアップしてください。
OS以外は何も入っていません。手順の途中で判断が必要なこと、うまくいかないことがあれば、勝手に回避せず私に確認してください。

■ 目的
DMMぱちタウンの店舗ページ（スロットハトヤ）に埋め込まれた P's CUBE の台データを、
毎日23:30に画面ありのブラウザで1回巡回して収集し、ローカルのPostgreSQLに保存する。
リポジトリ: https://github.com/subat4343/slot-data-platform （ブランチ feature/initial-platform）
詳しい手順はリポジトリの docs/deploy_windows.md、経緯は docs/source_contracts.md と docs/decisions/0012-pscube-automation-markers.md にあります。

■ 守ること（必ず）
1. 収集の頻度・方法は変えない。1日1回（23:30）、画面遷移30秒間隔、同時1ページ。
   config/sources/pscube.yaml の rate_limit・access_policy・options と、src/slot_data/adapters/pscube/browser.py は変更しない。
2. サイトの拒否を回避する変更はしない（CAPTCHAの突破、トークンの解析、非公開URLの直接呼び出し、人の操作の模倣、頻度を上げる、IPを変える など）。
3. 収集結果に error(451)、access_denied、CAPTCHA の確認画面、HTTP 403/429 が出たら、再実行せずに止めてログを私に見せる。
4. 収集（collect）を実行するのは、私の確認を取った1回だけ。テストや確認のために何度も実行しない。
5. GitHub へのコミット・プッシュはしない。リポジトリ内のファイルも変更しない（.env と config/app.yaml の作成は除く）。

■ 手順
1. 前提ソフトのインストール（winget を使う。無ければ私に伝える）
   - Git:         winget install --id Git.Git -e --source winget
   - Python 3.12: winget install --id Python.Python.3.12 -e --source winget
   インストール後、新しい PowerShell で git --version と python --version（3.12以上）を確認する。
   python が Microsoft Store のスタブになる場合は py -3.12 を使い、setup-windows.ps1 に -Python "py -3.12" ではなく
   フルパス（例: $env:LOCALAPPDATA\Programs\Python\Python312\python.exe）を渡す。

2. リポジトリの取得（場所は D:\slot-data-platform。Dドライブが無ければ C:\slot-data-platform。日本語や空白を含むパスは避ける）
   git clone https://github.com/subat4343/slot-data-platform.git D:\slot-data-platform
   cd D:\slot-data-platform
   git switch feature/initial-platform

3. セットアップ（Python環境・Chromium・開発用PostgreSQL・DB初期化・店舗登録。約500MBのダウンロードあり）
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
   ポート55432が使用中などで失敗したら、原因を調べて私に報告する。

4. 動作確認（実サイトには接続しない）
   .venv\Scripts\python -m pytest -q
   すべて passed になることを確認する（skipped が多い場合は PostgreSQL が起動していない）。

5. 初回の収集（実行前に必ず私に確認を取る）
   - Chromium のウィンドウが開き、約1時間かけて自動で巡回すること、その間このPCを操作しないことを私に伝え、了承を得てから実行する。
   .venv\Scripts\slot-data --text-log collect --source pscube --hall H001 --date today
   - 終了後、出力の "status"（succeeded / partial / failed）と "counts"、"errors" を要約して報告する。
   - あわせて .venv\Scripts\slot-data issues --code FETCH_FAILED の結果も報告する。

6. 毎日23:30の定期実行の登録（初回の収集が succeeded または私が了承した場合のみ）
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
   - 登録されたタスク「slot-data collect」の次回実行時刻を報告する。
   - 23:30 にこのPCがスリープしない／ログオンしたままになるよう、電源設定で確認すべき点を私に伝える（設定の変更は私の了承を得てから）。

7. 最後に、実施した内容・結果・残っている課題を箇条書きで報告する。

■ 参考
- ログ: D:\slot-data-platform\var\log\collect-*.log
- 途中で止まった収集の再開は、ログの run_id を使い --resume <run_id>（私の確認を取ってから）。
- DB が止まっている場合: powershell -ExecutionPolicy Bypass -File scripts\dev-postgres.ps1 start
```
