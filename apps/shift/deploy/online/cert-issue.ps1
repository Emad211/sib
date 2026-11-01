<#
.SYNOPSIS
  Issues or renews the Let's Encrypt certificate for the public name on the clinic PC, by DNS-01 (O05).

.DESCRIPTION
  The certificate is made on the clinic PC, where it is used; the relay never holds it. DNS-01 through the
  Cloudflare API means no port has to be open for the challenge. Needs lego.exe in -ToolsDir (downloads
  nothing) and a Cloudflare API token limited to "Zone:DNS:Edit" for the clinic's zone, kept in
  <DataDir>\secrets\cloudflare-token.txt (Administrators and SYSTEM only). Run it by hand once with
  -RegisterTask; the weekly task then renews within 30 days of expiry, and the shift service is
  restarted only when a new certificate was written.

.EXAMPLE
  ./cert-issue.ps1 -DataDir C:/ShiftData -ToolsDir C:/ShiftTools -Domain shift.clinic.example -Email owner@example.com -RegisterTask
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $DataDir,
    [Parameter(Mandatory)] [string] $ToolsDir,
    [Parameter(Mandatory)] [string] $Domain,
    [Parameter(Mandatory)] [string] $Email,
    [string] $Service = 'ShiftApp',
    [switch] $RegisterTask
)
$ErrorActionPreference = 'Stop'

$lego = Join-Path $ToolsDir 'lego.exe'
$tokenFile = Join-Path $DataDir 'secrets/cloudflare-token.txt'
foreach ($required in $lego, $tokenFile) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Not found: $required" }
}
$legoHome = Join-Path $DataDir 'tls/lego'
$publicDir = Join-Path $DataDir 'tls/public'
New-Item -ItemType Directory -Force -Path $legoHome, $publicDir | Out-Null

$certFile = Join-Path $legoHome "certificates/$Domain.crt"
$before = if (Test-Path -LiteralPath $certFile) { (Get-FileHash -LiteralPath $certFile).Hash } else { '' }
$env:CF_DNS_API_TOKEN = (Get-Content -Raw -LiteralPath $tokenFile).Trim()
try {
    if ($before) {
        & $lego --accept-tos --email $Email --dns cloudflare --dns.resolvers 1.1.1.1:53 --domains $Domain --path $legoHome renew --days 30
    } else {
        & $lego --accept-tos --email $Email --dns cloudflare --dns.resolvers 1.1.1.1:53 --domains $Domain --path $legoHome run
    }
    if ($LASTEXITCODE -ne 0) { throw "lego failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item Env:CF_DNS_API_TOKEN -ErrorAction SilentlyContinue
}

$after = (Get-FileHash -LiteralPath $certFile).Hash
if ($after -ne $before) {
    Copy-Item -Force -LiteralPath $certFile -Destination (Join-Path $publicDir 'fullchain.pem')
    Copy-Item -Force -LiteralPath (Join-Path $legoHome "certificates/$Domain.key") -Destination (Join-Path $publicDir 'privkey.pem')
    icacls $publicDir /inheritance:r /grant:r 'Administrators:(OI)(CI)F' 'SYSTEM:(OI)(CI)F' | Out-Null
    if (Get-Service -Name $Service -ErrorAction SilentlyContinue) { Restart-Service -Name $Service }
    Write-Host "New certificate for $Domain in $publicDir; $Service restarted."
} else {
    Write-Host "Certificate for $Domain is still valid; nothing changed."
}

if ($RegisterTask) {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -DataDir "{1}" -ToolsDir "{2}" -Domain {3} -Email {4} -Service {5}' -f `
        $PSCommandPath, $DataDir, $ToolsDir, $Domain, $Email, $Service
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 4am
    Register-ScheduledTask -TaskName 'ShiftCertRenew' -Action $action -Trigger $trigger -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
    Write-Host 'Weekly renewal task ShiftCertRenew registered.'
}
