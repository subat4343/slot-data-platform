# 開発用 PostgreSQL 16（Docker が無い Windows 向け。インストーラ・管理者権限は不要）
# 使い方: ./scripts/dev-postgres.ps1 setup | start | stop | status | register-service | unregister-service
# trust 認証で localhost:55432 に起動する。開発専用で、本番には使わない。
# register-service: Windows サービス「slot-data-postgres」として登録し、PC 起動時に自動で起動させる
#   （ほかのアプリ・セッションの終了に巻き込まれて止まらないようにする。管理者の PowerShell で実行）
param([Parameter(Mandatory = $true)][ValidateSet("setup", "start", "stop", "status", "register-service", "unregister-service")] [string]$Command)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DevDb = Join-Path $Root ".devdb"
$Bin = Join-Path $DevDb "pgsql\bin"
$Data = Join-Path $DevDb "data"
$Port = 55432
$Version = "16.12-1"

switch ($Command) {
    "setup" {
        if (-not (Test-Path (Join-Path $Bin "pg_ctl.exe"))) {
            New-Item -ItemType Directory -Force $DevDb | Out-Null
            $Zip = Join-Path $DevDb "pg.zip"
            $Url = "https://get.enterprisedb.com/postgresql/postgresql-$Version-windows-x64-binaries.zip"
            Write-Host "downloading $Url"
            Invoke-WebRequest -Uri $Url -OutFile $Zip
            Expand-Archive -Path $Zip -DestinationPath $DevDb -Force
            Remove-Item $Zip
            Remove-Item -Recurse -Force (Join-Path $DevDb "pgsql\pgAdmin 4") -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path (Join-Path $Data "PG_VERSION"))) {
            & (Join-Path $Bin "initdb.exe") -D $Data -U postgres -A trust -E UTF8 --locale=C
        }
        & (Join-Path $Bin "pg_ctl.exe") -D $Data -o "-p $Port" -l (Join-Path $DevDb "pg.log") start -w
        foreach ($db in @("slot_data", "slot_data_test")) {
            $exists = & (Join-Path $Bin "psql.exe") -p $Port -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'"
            if (-not $exists) { & (Join-Path $Bin "createdb.exe") -p $Port -U postgres $db }
        }
        Write-Host "ready: postgresql+psycopg://postgres@localhost:$Port/slot_data"
    }
    "start" { & (Join-Path $Bin "pg_ctl.exe") -D $Data -o "-p $Port" -l (Join-Path $DevDb "pg.log") start -w }
    "stop" { & (Join-Path $Bin "pg_ctl.exe") -D $Data stop }
    "status" { & (Join-Path $Bin "pg_ctl.exe") -D $Data status }
    "register-service" {
        # 手動で起動中のサーバーは止めてからサービスとして起動する
        & (Join-Path $Bin "pg_ctl.exe") -D $Data status *> $null
        if ($LASTEXITCODE -eq 0) { & (Join-Path $Bin "pg_ctl.exe") -D $Data stop -m fast }
        & (Join-Path $Bin "pg_ctl.exe") register -N "slot-data-postgres" -D $Data -o "-p $Port" -S auto
        Start-Service -Name "slot-data-postgres"
        Get-Service -Name "slot-data-postgres"
    }
    "unregister-service" {
        Stop-Service -Name "slot-data-postgres" -ErrorAction SilentlyContinue
        & (Join-Path $Bin "pg_ctl.exe") unregister -N "slot-data-postgres"
    }
}
