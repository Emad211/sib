<#
.SYNOPSIS
  Runs the outward tunnel (rathole client) on the clinic PC as a Windows service (O05).

.DESCRIPTION
  Run once in an elevated PowerShell after the relay is set up. Needs rathole.exe and nssm.exe in -ToolsDir
  (downloads nothing) and the two secrets from the relay (/root/clinic-tunnel-secrets.txt). The secrets are
  written to <DataDir>\tunnel\client.toml with access for Administrators and SYSTEM only, never to the repo.

.EXAMPLE
  ./tunnel-install.ps1 -DataDir C:/ShiftData -ToolsDir C:/ShiftTools -Relay 203.0.113.10 -Token <token> -ServerPublicKey <key>
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $DataDir,
    [Parameter(Mandatory)] [string] $ToolsDir,
    [Parameter(Mandatory)] [string] $Relay,
    [Parameter(Mandatory)] [string] $Token,
    [Parameter(Mandatory)] [string] $ServerPublicKey,
    [int] $LocalPort = 8443
)
$ErrorActionPreference = 'Stop'

$rathole = Join-Path $ToolsDir 'rathole.exe'
$nssm = Join-Path $ToolsDir 'nssm.exe'
foreach ($required in $rathole, $nssm) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Not found: $required" }
}

$tunnelDir = Join-Path $DataDir 'tunnel'
$logDir = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force -Path $tunnelDir, $logDir | Out-Null
$template = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'rathole-client.toml.example')
$config = $template.Replace('RELAY_IP_OR_NAME', $Relay).
    Replace('REPLACE_WITH_THE_SHARED_TOKEN', $Token).
    Replace('REPLACE_WITH_THE_SERVER_PUBLIC_KEY', $ServerPublicKey).
    Replace('127.0.0.1:8443', "127.0.0.1:$LocalPort")
$configPath = Join-Path $tunnelDir 'client.toml'
Set-Content -LiteralPath $configPath -Value $config -Encoding utf8
# Only Administrators and SYSTEM read the token.
icacls $tunnelDir /inheritance:r /grant:r 'Administrators:(OI)(CI)F' 'SYSTEM:(OI)(CI)F' | Out-Null

$name = 'ShiftTunnel'
if (Get-Service -Name $name -ErrorAction SilentlyContinue) { & $nssm stop $name | Out-Null }
else { & $nssm install $name $rathole | Out-Null }
& $nssm set $name Application $rathole | Out-Null
& $nssm set $name AppParameters ('--client "' + $configPath + '"') | Out-Null
& $nssm set $name AppDirectory $tunnelDir | Out-Null
& $nssm set $name Start SERVICE_AUTO_START | Out-Null
& $nssm set $name AppStdout (Join-Path $logDir 'tunnel.log') | Out-Null
& $nssm set $name AppStderr (Join-Path $logDir 'tunnel.log') | Out-Null
& $nssm set $name AppRestartDelay 5000 | Out-Null
& $nssm start $name | Out-Null
Write-Host "ShiftTunnel is running; the relay forwards the public name to 127.0.0.1:$LocalPort."
