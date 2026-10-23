# Windows タスクスケジューラに収集タスクを登録する（収集を動かす PC で 1 回実行）。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register-tasks.ps1 [-At 23:30] [-Unregister]
#
# 登録するタスク
#   slot-data collect  : 毎日 -At（既定 23:30）に scripts\run-collect.ps1 を実行
#   slot-data postgres : ログオン時に開発用 PostgreSQL を起動（.devdb を使う場合のみ）
#
# 収集は画面ありのブラウザを使うため「ユーザーがログオンしているときのみ実行」で登録する。
# 23:30 にはこの PC でユーザーがログオンしている（画面ロックは可）必要がある。
param(
    [string]$At = "23:30",
    [string]$Source = "pscube",
    [string]$Hall = "H001",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$User = "$env:USERDOMAIN\$env:USERNAME"
$CollectName = "slot-data collect"
$PgName = "slot-data postgres"

if ($Unregister) {
    foreach ($name in @($CollectName, $PgName)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "unregistered: $name"
        }
    }
    return
}

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

# 収集タスク
$collectAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`" -Source {1} -Hall {2}" -f (Join-Path $Root "scripts\run-collect.ps1"), $Source, $Hall) `
    -WorkingDirectory $Root
$collectTrigger = New-ScheduledTaskTrigger -Daily -At $At
$collectSettings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $CollectName -Action $collectAction -Trigger $collectTrigger `
    -Settings $collectSettings -Principal $principal -Force `
    -Description "slot-data: P's CUBE（ぱちタウン経由）を画面ありのブラウザで巡回して収集" | Out-Null
Write-Host "registered: $CollectName (daily $At)"

# 開発用 PostgreSQL をログオン時に起動
if (Test-Path (Join-Path $Root ".devdb\pgsql\bin\pg_ctl.exe")) {
    $pgAction = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`" start" -f (Join-Path $Root "scripts\dev-postgres.ps1")) `
        -WorkingDirectory $Root
    $pgTrigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    Register-ScheduledTask -TaskName $PgName -Action $pgAction -Trigger $pgTrigger `
        -Principal $principal -Force -Description "slot-data: 開発用 PostgreSQL を起動" | Out-Null
    Write-Host "registered: $PgName (at logon)"
}

Get-ScheduledTask -TaskName $CollectName | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
