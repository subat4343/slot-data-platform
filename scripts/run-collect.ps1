# 収集を 1 回実行する（タスクスケジューラから毎日 23:30 に呼ばれる想定）。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-collect.ps1 [-Source pscube] [-Hall H001]
# ログ: var\log\collect-YYYYMMDD-HHMMSS.log（まとめ）
#       var\log\collect-YYYYMMDD-HHMMSS-<処理名>.stderr.log / .stdout.log（実行中から書き込まれる。打ち切られても残る）
#
# 外部プログラムは Start-Process で実行し、標準出力・標準エラーを処理ごとのファイルに書き、終了後にまとめのログへ追記する。
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
$LogBase = Join-Path $LogDir ("collect-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$Log = "$LogBase.log"
$env:PYTHONIOENCODING = "utf-8"
if (-not $SlotDataExe) { $SlotDataExe = Join-Path $Root ".venv\Scripts\slot-data.exe" }

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Invoke-Native([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$OutputEncoding = "UTF8", [switch]$Keep) {
    # 外部プログラムを実行して終了コードを返す。標準エラー・標準出力は実行中から処理ごとのファイルに書かれ、
    # 終了後にまとめのログへ追記する。-Keep を付けた処理のファイルは残す（長時間の収集が打ち切られた場合の調査用）
    $err = "$LogBase-$Name.stderr.log"
    $out = "$LogBase-$Name.stdout.log"
    Write-Log "run $Name (output: $(Split-Path -Leaf $err))"
    # -Wait は子孫プロセス（起動した PostgreSQL サーバーなど）の終了まで待ってしまうため使わず、
    # 起動したプロセス本体の終了だけを待つ。Handle を先に参照しないと ExitCode が取れない（PowerShell 5.1）
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -NoNewWindow -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $null = $process.Handle
    $process.WaitForExit()
    foreach ($file in @($err, $out)) {
        $text = Get-Content -Path $file -Raw -Encoding $OutputEncoding
        if ($text) { Add-Content -Path $Log -Value $text.TrimEnd() -Encoding UTF8 }
    }
    if (-not $Keep) { Remove-Item -Path $out, $err -ErrorAction SilentlyContinue }
    return $process.ExitCode
}

Write-Log "start source=$Source hall=$Hall date=$Date"

# 開発用 PostgreSQL（scripts/dev-postgres.ps1 で導入した場合）が止まっていれば起動する
$PgCtl = Join-Path $Root ".devdb\pgsql\bin\pg_ctl.exe"
$PgReady = Join-Path $Root ".devdb\pgsql\bin\pg_isready.exe"
if (Test-Path $PgCtl) {
    # 接続できるかで判断する（Windows サービスで動かしている場合、pg_ctl status は「動作していない」と出ることがある）
    $pgStatus = Invoke-Native "pg-ready" $PgReady @("-h", "localhost", "-p", "55432") "Default"
    if ($pgStatus -ne 0) {
        Write-Log "starting local PostgreSQL (server log: .devdb\pg.log)"
        # 起動したサーバーがリダイレクト先のファイルを開いたまま引き継ぐため、出力はリダイレクトしない
        $pg = Start-Process -FilePath $PgCtl -ArgumentList @(
            "-D", "`"$(Join-Path $Root '.devdb\data')`"", "-o", "`"-p 55432`"",
            "-l", "`"$(Join-Path $Root '.devdb\pg.log')`"", "start", "-w"
        ) -WindowStyle Hidden -PassThru
        $null = $pg.Handle
        $pg.WaitForExit()
        Write-Log "postgres start exit=$($pg.ExitCode)"
    }
}

$code = Invoke-Native "collect" $SlotDataExe ($PrefixArgs + @("--text-log", "collect", "--source", $Source, "--hall", $Hall, "--date", $Date)) -Keep
Write-Log "collect exit=$code"

# 収集後に原本の整合を軽く確認（ハッシュ検証なし）
$auditCode = Invoke-Native "audit" $SlotDataExe ($PrefixArgs + @("--text-log", "audit", "--no-hash"))
Write-Log "audit exit=$auditCode"
exit $code
