# 開発用 PostgreSQL 16（Docker が無い Windows 向け。インストーラ・管理者権限は不要）
# 使い方: ./scripts/dev-postgres.ps1 setup | start | stop | status
# trust 認証で localhost:55432 に起動する。開発専用で、本番には使わない。
param([Parameter(Mandatory = $true)][ValidateSet("setup", "start", "stop", "status")] [string]$Command)

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
}
