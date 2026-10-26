# 収集を 1 回実行する（タスクスケジューラから毎日 23:30 に呼ばれる想定）。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-collect.ps1 [-Source pscube] [-Hall H001]
# ログ: var\log\collect-YYYYMMDD-HHMMSS.log
#
# 外部プログラムは Start-Process で実行し、標準出力・標準エラーを一時ファイル経由でログに追記する。
# Windows PowerShell 5.1 では、$ErrorActionPreference = "Stop" のまま外部プログラムの出力を *>> でリダイレクトすると
# 標準エラーへの 1 行目（slot-data の INFO ログ）がエラー扱いになってスクリプトが止まるため（2026-10-27 に発生）。
param(
    [string]$Source = "pscube",
    [string]$Hall = "H001",
    [string]$Date = "today",
    # テスト用: slot-data の代わりに実行するプログラムと、その前に付ける引数
    [string]$SlotDataExe = "",
    [string[]]$PrefixArgs = @()
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$LogDir = Join-Path $Root "var\log"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir ("collect-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$env:PYTHONIOENCODING = "utf-8"
if (-not $SlotDataExe) { $SlotDataExe = Join-Path $Root ".venv\Scripts\slot-data.exe" }

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Invoke-Native([string]$FilePath, [string[]]$Arguments, [string]$OutputEncoding = "UTF8") {
    # 外部プログラムを実行して終了コードを返す。出力は標準エラー → 標準出力の順にログへ追記する
    $out = [System.IO.Path]::GetTempFileName()
    $err = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err
        foreach ($file in @($err, $out)) {
            $text = Get-Content -Path $file -Raw -Encoding $OutputEncoding
            if ($text) { Add-Content -Path $Log -Value $text.TrimEnd() -Encoding UTF8 }
        }
        return $process.ExitCode
    }
    finally {
        Remove-Item -Path $out, $err -ErrorAction SilentlyContinue
    }
}

Write-Log "start source=$Source hall=$Hall date=$Date"

# 開発用 PostgreSQL（scripts/dev-postgres.ps1 で導入した場合）が止まっていれば起動する
$PgCtl = Join-Path $Root ".devdb\pgsql\bin\pg_ctl.exe"
if (Test-Path $PgCtl) {
    $pgStatus = Invoke-Native $PgCtl @("-D", (Join-Path $Root ".devdb\data"), "status") "Default"
    if ($pgStatus -ne 0) {
        Write-Log "starting local PostgreSQL"
        $pgStart = Invoke-Native "powershell.exe" @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "dev-postgres.ps1"), "start"
        ) "Default"
        Write-Log "postgres start exit=$pgStart"
    }
}

$code = Invoke-Native $SlotDataExe ($PrefixArgs + @("--text-log", "collect", "--source", $Source, "--hall", $Hall, "--date", $Date))
Write-Log "collect exit=$code"

# 収集後に原本の整合を軽く確認（ハッシュ検証なし）
$auditCode = Invoke-Native $SlotDataExe ($PrefixArgs + @("--text-log", "audit", "--no-hash"))
Write-Log "audit exit=$auditCode"
exit $code
