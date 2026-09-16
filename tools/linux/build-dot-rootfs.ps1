<#
.SYNOPSIS
  Build the Echo Dot's root filesystem tarball for a release: what a Dot running TECHO5 Linux installs
  from Home Assistant's update card into its other slot.

.DESCRIPTION
  The same tree install-dot.ps1 puts in slot a, and nothing of any one unit: no firmware (each unit
  adopts its own into the store), no keys, no Wi-Fi, no Home Assistant identity. So it can be
  published. The boot image (kernel + initramfs) is not in it and is never published — it is built per
  unit from the unit's own recovery backup, by the installer.

  Hand the result to TECHO5's tools/release.ps1 as -DotRootfs.

.EXAMPLE
  .\tools\linux\build-dot-rootfs.ps1 -Daemon ..\techo5\bin\echod-arm-dot -Release v0.2.0
#>
param(
    # The daemon built with -tags dot (release.ps1 builds bin\echod-arm-dot).
    [Parameter(Mandatory)][string]$Daemon,
    [Parameter(Mandatory)][string]$Release,
    [string]$Wmtup = (Join-Path $PSScriptRoot '..\..\bin\wmtup'),
    [string]$Btbridge = (Join-Path $PSScriptRoot '..\..\bin\btbridge'),
    [string]$Bluealsa = 'D:\platform-tools\echodot\bluealsa-build\out\bluealsa',
    [string]$Inputs = 'D:\platform-tools\echoshow\linux-image',
    [string]$Python = 'python',
    [string]$Out = (Join-Path $PSScriptRoot '..\..\bin\techo5-dot-rootfs.tar.gz')
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
foreach ($f in @($Daemon, $Wmtup, $Btbridge, $Bluealsa, (Join-Path $Inputs 'busybox.static'))) {
    if (-not (Test-Path $f)) { throw "missing $f" }
}
$alpine = Get-ChildItem (Join-Path $Inputs 'alpine-minirootfs-*-armv7.tar.gz') | Select-Object -First 1
if (-not $alpine) { throw "no Alpine armv7 minirootfs in $Inputs" }
$commit = (git -C $repo rev-parse --short HEAD).Trim()

& $Python (Join-Path $repo 'tools\linux\mkrootfs.py') --rootfs $alpine.FullName --apkdir (Join-Path $Inputs 'apks-dot') --apkdir (Join-Path $Inputs 'apks-bt-dot') `
    --add "$(Join-Path $Inputs 'busybox.static')=/bin/busybox.static" --add "$Wmtup=/usr/local/bin/wmtup" `
    --add "$Daemon=/usr/local/bin/echod" --add "$Btbridge=/usr/local/bin/btbridge" --add "$Bluealsa=/usr/bin/bluealsa" --overlay (Join-Path $repo 'tools\linux\rootfs') `
    --release "techo5-dot $Release ($commit)" -o $Out
if ($LASTEXITCODE -ne 0) { throw "building the root filesystem failed" }
Write-Host "rootfs for release $Release -> $Out"
