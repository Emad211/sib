<#
.SYNOPSIS
  Registers the shift app and Caddy as Windows services on the clinic PC, so both start with the PC.

.DESCRIPTION
  Run once, in an elevated PowerShell, after docs/ops/O04_SHIFT_RUNBOOK.md section 2. Safe to run again:
  an existing service is stopped and reconfigured. It needs caddy.exe and nssm.exe in -ToolsDir; it
  downloads nothing. The shift app listens on 127.0.0.1 only, so phones reach it through Caddy alone.

.EXAMPLE
  ./install-services.ps1 -Site 192.168.1.10 -AppDir C:/ShiftApp/apps/shift -DataDir C:/ShiftData -ToolsDir C:/ShiftTools -AccountingDb C:/Hesabdari/clinic_new.db
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Site,
    [Parameter(Mandatory)] [string] $AppDir,
    [Parameter(Mandatory)] [string] $DataDir,
    [Parameter(Mandatory)] [string] $ToolsDir,
    [Parameter(Mandatory)] [string] $AccountingDb
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $AppDir '.venv/Scripts/python.exe'
$caddy = Join-Path $ToolsDir 'caddy.exe'
$nssm = Join-Path $ToolsDir 'nssm.exe'
foreach ($required in $python, $caddy, $nssm, $AccountingDb) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Not found: $required" }
}

$caddyData = Join-Path $DataDir 'caddy'
$caDir = Join-Path $caddyData 'caddy/pki/authorities/local'
$logDir = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force -Path $DataDir, $caddyData, $logDir | Out-Null

function Set-Service-Definition {
    param([string] $Name, [string] $Exe, [string] $Arguments, [string] $WorkDir, [string[]] $Environment)
    if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
        & $nssm stop $Name | Out-Null
    } else {
        & $nssm install $Name $Exe | Out-Null
    }
    & $nssm set $Name Application $Exe | Out-Null
    & $nssm set $Name AppParameters $Arguments | Out-Null
    & $nssm set $Name AppDirectory $WorkDir | Out-Null
    & $nssm set $Name AppEnvironmentExtra $Environment | Out-Null
    & $nssm set $Name Start SERVICE_AUTO_START | Out-Null
    & $nssm set $Name AppStdout (Join-Path $logDir "$Name.out.log") | Out-Null
    & $nssm set $Name AppStderr (Join-Path $logDir "$Name.err.log") | Out-Null
    & $nssm set $Name AppRotateFiles 1 | Out-Null
    & $nssm set $Name AppRotateBytes 10485760 | Out-Null
}

Set-Service-Definition -Name 'ShiftApp' -Exe $python -Arguments 'start.py' -WorkDir $AppDir -Environment @(
    'SHIFT_ENV=production',
    'SHIFT_HOST=127.0.0.1',
    'SHIFT_PORT=8443',
    "SHIFT_HOME=$DataDir",
    "SHIFT_ACCOUNTING_DB=$AccountingDb",
    'PYTHONIOENCODING=utf-8'
)

$caddyfile = Join-Path $AppDir 'deploy/Caddyfile'
Set-Service-Definition -Name 'ShiftCaddy' -Exe $caddy -Arguments "run --config `"$caddyfile`" --adapter caddyfile" -WorkDir $DataDir -Environment @(
    "SHIFT_SITE=$Site",
    "SHIFT_CA_DIR=$caDir",
    "XDG_DATA_HOME=$caddyData"
)

# Only the two ports Caddy serves are open to the LAN; the app's own port stays private.
foreach ($port in 80, 443) {
    $rule = "Shift app $port"
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $rule -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Private | Out-Null
    }
}

Start-Service ShiftApp
Start-Service ShiftCaddy
Write-Output "Done. Phones: http://$Site/ca.crt once, to trust the certificate; then https://$Site/"
