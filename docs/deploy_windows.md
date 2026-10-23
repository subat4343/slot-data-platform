# 収集用 PC への導入手順（Windows）

P's CUBE（DMMぱちタウン経由）の収集を、毎日 23:30 に別の Windows PC で自動実行するための手順です。
収集は**画面ありのブラウザ**で行うため、23:30 にはその PC でユーザーがログオンしている必要があります（画面ロックは可）。

## 1. 前提

| 項目 | 内容 |
|---|---|
| OS | Windows 10 / 11 |
| Python | 3.12 以上（https://www.python.org/ から。インストール時に「Add python.exe to PATH」を選ぶ） |
| Git | Git for Windows |
| ネットワーク | p-town.dmm.com・www.pscube.jp へ接続できること。初回のみ PostgreSQL・Chromium のダウンロード（約 500MB） |
| 電源 | 23:30 にスリープしていないこと（タスクは「スリープ解除して実行」で登録されるが、電源設定で無効の場合がある） |

## 2. 取得とセットアップ（初回のみ）

PowerShell で実行します。

```powershell
cd D:\
git clone https://github.com/subat4343/slot-data-platform.git
cd slot-data-platform
git switch feature/initial-platform        # main に取り込まれるまでは作業ブランチ
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

`setup-windows.ps1` は次の処理を行います（数分〜十数分）。

1. `.venv` を作り、slot-data と Playwright をインストール
2. Playwright 用の Chromium をインストール
3. 開発用 PostgreSQL 16 を `.devdb\` に展開・起動（ポート 55432。外部の DB を使う場合は `-SkipPostgres` を付け、`.env` の接続先を編集）
4. `.env` と `config\app.yaml` を作成（既にあれば上書きしない）
5. DB を初期化し、店舗 H001（スロットハトヤ）を登録

## 3. 動作確認（初回のみ）

```powershell
.venv\Scripts\slot-data --text-log collect --source pscube --hall H001 --date today
```

- Chromium のウィンドウが開き、ぱちタウンの店舗ページ → スロットデータ → 各機種 → 各台の順に自動で進みます。
- 画面遷移の間隔は 30 秒です。機種約 20・台約 100 だと、全体で 1 時間程度かかります。
- 途中でウィンドウを閉じたり操作したりしないでください。

確認だけなら台詳細を回らない設定で短時間に試せます（`config\sources\pscube.yaml` の `crawl_details: false`）。

## 4. 定期実行の登録

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
```

| タスク名 | 実行 | 内容 |
|---|---|---|
| slot-data collect | 毎日 23:30 | `scripts\run-collect.ps1`（収集 → 簡易監査） |
| slot-data postgres | ログオン時 | 開発用 PostgreSQL を起動 |

- 時刻を変える場合: `register-tasks.ps1 -At 23:45`
- 解除: `register-tasks.ps1 -Unregister`
- 23:30 に PC が起動していなかった場合は、次に起動したときに実行されます（StartWhenAvailable）。
- 実行中に次の回が来た場合は、新しい回を起動しません。3 時間を超えたら打ち切ります。

## 5. 結果の確認

| 確認したいこと | 方法 |
|---|---|
| 実行ログ | `var\log\collect-YYYYMMDD-HHMMSS.log` |
| 収集結果（件数・失敗） | ログ内の `"status"` と `"counts"`。`succeeded` / `partial`（一部失敗）/ `failed` |
| 取れなかった日・台と理由 | `.venv\Scripts\slot-data issues --code FETCH_FAILED` |
| 機種名のレビュー待ち | `.venv\Scripts\slot-data mapping list --pending` |
| 出力 | `.venv\Scripts\slot-data export daily --out out\daily.xlsx --format xlsx --hall H001` |

## 6. 失敗したとき

| 症状 | 対応 |
|---|---|
| ログに `error(451)` / `access_denied` | 台詳細が拒否された。その回の台詳細巡回は自動で打ち切られる（機種画面の BONUS 回数は保存済み）。続く場合はアンジェロ社へ相談する。拒否を回避する設定変更はしない |
| 途中で止まった（PC のスリープ・ネットワーク断など） | ログ内の `run_id` を使い `slot-data collect --source pscube --hall H001 --date <日付> --resume <run_id>`。取得済みの画面は飛ばす |
| `collect` が「収集を中止しました」で終わる | `config\sources\pscube.yaml` の `access_policy` を確認 |
| DB に接続できない | `scripts\dev-postgres.ps1 status` / `start` |

台詳細は画面の「本日」から過去日まで列が並ぶため、1〜2 日取りこぼしても後日の収集で補えます（台詳細のパーサー追加後）。

## 7. 更新

```powershell
git pull
.venv\Scripts\python -m pip install -q -e ".[browser]"
.venv\Scripts\slot-data init-db       # マイグレーションの適用
```
