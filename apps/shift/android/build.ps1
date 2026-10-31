<#
.SYNOPSIS
  Builds the signed shift app APK with the Android SDK tools only (no Gradle), for sending in Telegram.

.DESCRIPTION
  The clinic's root certificate (the ca.crt serve_lan.py made under <home>/tls) is copied into the
  package, so a phone trusts the clinic server without installing anything. The signing key lives
  outside git: keep the keystore, because only an APK signed by the same key updates an installed one.

.EXAMPLE
  pwsh -File build.ps1 -CaCert D:/ShiftData/tls/ca.crt -Keystore D:/keys/shift.jks -KeystorePassword '...'
#>
param(
    [Parameter(Mandatory)] [string] $CaCert,
    [Parameter(Mandatory)] [string] $Keystore,
    [Parameter(Mandatory)] [string] $KeystorePassword,
    [string] $Out = (Join-Path $PSScriptRoot 'dist')
)
$ErrorActionPreference = 'Stop'

$sdk = 'C:\Users\Public\Android\sdk'
$buildTools = Join-Path $sdk 'build-tools\35.0.0'
$androidJar = Join-Path $sdk 'platforms\android-35\android.jar'
if (-not $env:JAVA_HOME) { $env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-17.0.16.8-hotspot' }
$javac = Join-Path $env:JAVA_HOME 'bin\javac.exe'
$keytool = Join-Path $env:JAVA_HOME 'bin\keytool.exe'
$work = Join-Path $env:TEMP 'shift-apk-build'

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
foreach ($d in 'res', 'classes', 'dex', 'gen') { New-Item -ItemType Directory -Force -Path (Join-Path $work $d) | Out-Null }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Run($exe, $arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe failed with exit code $LASTEXITCODE" }
}

# The resources are copied so the certificate lands in res/raw without ever being committed.
$resources = Join-Path $work 'src-res'
Copy-Item -Recurse (Join-Path $PSScriptRoot 'res') $resources
New-Item -ItemType Directory -Force -Path (Join-Path $resources 'raw') | Out-Null
Copy-Item $CaCert (Join-Path $resources 'raw\shift_ca.pem')

Run (Join-Path $buildTools 'aapt2.exe') @('compile', '--dir', $resources, '-o', (Join-Path $work 'res\resources.zip'))
Run (Join-Path $buildTools 'aapt2.exe') @('link', '-o', (Join-Path $work 'base.apk'), '-I', $androidJar,
    '--manifest', (Join-Path $PSScriptRoot 'AndroidManifest.xml'), '--min-sdk-version', '26',
    '--target-sdk-version', '34', '--java', (Join-Path $work 'gen'), (Join-Path $work 'res\resources.zip'))

$sources = @(Get-ChildItem -Recurse -Filter *.java (Join-Path $work 'gen') | ForEach-Object FullName)
$sources += Get-ChildItem -Recurse -Filter *.java (Join-Path $PSScriptRoot 'java') | ForEach-Object FullName
Run $javac (@('-encoding', 'UTF-8', '--release', '8', '-Xlint:-options', '-classpath', $androidJar,
    '-d', (Join-Path $work 'classes')) + $sources)

$classes = Get-ChildItem -Recurse -Filter *.class (Join-Path $work 'classes') | ForEach-Object FullName
Run (Join-Path $buildTools 'd8.bat') (@('--release', '--min-api', '26', '--lib', $androidJar,
    '--output', (Join-Path $work 'dex')) + $classes)

Copy-Item (Join-Path $work 'base.apk') (Join-Path $work 'unaligned.apk') -Force
$packer = Join-Path $work 'pack_dex.py'
Set-Content -Path $packer -Encoding ASCII -Value @'
import sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as z:
    z.write(dex, "classes.dex")
'@
python $packer (Join-Path $work 'unaligned.apk') (Join-Path $work 'dex\classes.dex')
if ($LASTEXITCODE -ne 0) { throw 'packaging dex failed' }
Run (Join-Path $buildTools 'zipalign.exe') @('-f', '-p', '4', (Join-Path $work 'unaligned.apk'), (Join-Path $work 'aligned.apk'))

if (-not (Test-Path $Keystore)) {
    Run $keytool @('-genkeypair', '-keystore', $Keystore, '-alias', 'shift', '-keyalg', 'RSA', '-keysize', '2048',
        '-validity', '10950', '-storepass', $KeystorePassword, '-keypass', $KeystorePassword,
        '-dname', 'CN=Shift, O=Shift')
}
$apk = Join-Path $Out 'shift.apk'
Run (Join-Path $buildTools 'apksigner.bat') @('sign', '--ks', $Keystore, '--ks-key-alias', 'shift',
    '--ks-pass', "pass:$KeystorePassword", '--key-pass', "pass:$KeystorePassword", '--out', $apk,
    (Join-Path $work 'aligned.apk'))
Run (Join-Path $buildTools 'apksigner.bat') @('verify', $apk)
Write-Host "BUILT: $apk ($((Get-Item $apk).Length) bytes)"
