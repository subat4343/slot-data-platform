# 収集を 1 回実行する（タスクスケジューラから毎日 23:30 に呼ばれる想定）。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-collect.ps1 [-Source pscube] [-Hall H001]
# ログ: var\log\collect-YYYYMMDD-HHMMSS.log
param(
    [string]$Source = "pscube",
    [string]$Hall = "H001",
    [string]$Date = "today"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$LogDir = Join-Path $Root "var\log"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir ("collect-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$env:PYTHONIOENCODING = "utf-8"

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

Write-Log "start source=$Source hall=$Hall date=$Date"

# 開発用 PostgreSQL（scripts/dev-postgres.ps1 で導入した場合）が止まっていれば起動する
$PgCtl = Join-Path $Root ".devdb\pgsql\bin\pg_ctl.exe"
if (Test-Path $PgCtl) {
    & $PgCtl -D (Join-Path $Root ".devdb\data") status *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "starting local PostgreSQL"
        & (Join-Path $PSScriptRoot "dev-postgres.ps1") start *>> $Log
    }
}

$SlotData = Join-Path $Root ".venv\Scripts\slot-data.exe"
& $SlotData --text-log collect --source $Source --hall $Hall --date $Date *>> $Log
$code = $LASTEXITCODE
Write-Log "collect exit=$code"

# 収集後に原本の整合を軽く確認（ハッシュ検証なし）
& $SlotData --text-log audit --no-hash *>> $Log
Write-Log "audit exit=$LASTEXITCODE"
exit $code
