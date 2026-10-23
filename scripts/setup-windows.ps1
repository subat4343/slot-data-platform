# 収集用 PC の初回セットアップ（リポジトリを clone した直後に 1 回実行）。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#
# 1. Python 仮想環境（.venv）を作り、slot-data と Playwright を入れる
# 2. Playwright 用の Chromium を入れる
# 3. 開発用 PostgreSQL 16 を .devdb に展開・起動する（-SkipPostgres で省略。外部の DB を使う場合）
# 4. .env と config\app.yaml を用意する（既にあれば上書きしない）
# 5. DB を初期化し、店舗 H001（スロットハトヤ）を登録する
param(
    [switch]$SkipPostgres,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONIOENCODING = "utf-8"

$version = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.12") { throw "Python 3.12 以上が必要です（検出: $version）" }
Write-Host "Python $version"

if (-not (Test-Path ".venv")) { & $Python -m venv .venv }
$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -e ".[browser]"
& $Py -m playwright install chromium

if (-not $SkipPostgres) {
    & (Join-Path $PSScriptRoot "dev-postgres.ps1") setup
}

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
if (-not (Test-Path "config\app.yaml")) { Copy-Item "config\app.example.yaml" "config\app.yaml" }

$SlotData = Join-Path $Root ".venv\Scripts\slot-data.exe"
& $SlotData --text-log init-db
$halls = & $SlotData --text-log hall list | Out-String
if ($halls -notmatch '"H001"') {
    & $SlotData --text-log hall add H001 "スロットハトヤ" --address "神奈川県川崎市多摩区登戸3414" --cutoff 05:00 --closing 22:30
}
& $SlotData --text-log hall map-source H001 pscube "kanagawa/13152" --external-name "スロットハトヤ（DMMぱちタウン）"

Write-Host ""
Write-Host "セットアップ完了。次の手順:"
Write-Host "  1. 動作確認: .venv\Scripts\slot-data collect --source pscube --hall H001 --date today"
Write-Host "  2. 定期実行の登録: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register-tasks.ps1"
