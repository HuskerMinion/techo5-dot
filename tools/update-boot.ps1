<#
.SYNOPSIS
  Rebuild a Dot's boot image (kernel + initramfs) and put it in the recovery partition of a unit that
  is already running TECHO5 Linux, over SSH.

.DESCRIPTION
  Home Assistant updates carry the root filesystem only; the boot image is built per unit from the
  unit's own recovery backup (kept by the installer) and is never published. This is how a new kernel
  or a change to the rescue environment reaches a unit without going back through Fire OS or TWRP: for
  example, Bluetooth for a Dot installed without the Bluetooth kernel.

  By default the kernel and the rescue packages come from the signed release (checked against its
  checksums). -Kernel uses a kernel built locally (tools/linux/build-kernel.sh); -FromSource takes the
  rest from local inputs too (docs/building.md).

  The previous image stays in the store as linux-prev.img, and linux.img (what back-to-linux.sh and
  to-twrp use) is only replaced once the new image has booted healthy. A new image that never comes up
  spends the boot tries and the unit stays in its rescue environment (USB console, SSH), where
  techo5-retry or the installer puts things right.

  Needs SSH to the unit: turn on its SSH switch in Home Assistant, with a key set (ssh_keys).
  Runs on Windows, Linux and macOS (PowerShell 7, or Windows PowerShell 5.1).

.EXAMPLE
  ./tools/update-boot.ps1 -Serial <serial> -Address <address>
  ./tools/update-boot.ps1 -Serial <serial> -Address <address> -Kernel build/kernel/zImage-dtb
#>
param(
    [Parameter(Mandatory)][string]$Serial,
    [Parameter(Mandatory)][string]$Address,
    # A kernel built locally; by default the release's Bluetooth kernel.
    [string]$Kernel,
    # Keep the kernel from the unit's own recovery backup, which has no Bluetooth.
    [switch]$NoBluetoothKernel,
    [string]$Release = 'latest',
    [switch]$FromSource,
    [string]$BackupRoot = $(if ($env:TECHO5_BACKUPS) { $env:TECHO5_BACKUPS } else { Join-Path (Join-Path $PSScriptRoot '..') 'backups' }),
    [string]$WorkDir = $(if ($env:TECHO5_WORK) { $env:TECHO5_WORK } else { Join-Path (Join-Path $PSScriptRoot '..') 'build' }),
    [string]$Inputs = $(if ($env:TECHO5_INPUTS) { $env:TECHO5_INPUTS } else { Join-Path (Join-Path $PSScriptRoot '..') 'inputs' }),
    [string]$Wmtup = (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'bin') 'wmtup'),
    [string]$Python
)
$ErrorActionPreference = 'Stop'
. (Join-Path (Join-Path $PSScriptRoot 'lib') 'release.ps1')
if (-not $Python) { $Python = Get-Python }

$unit = Join-Path $BackupRoot $Serial
$rec = Join-Path $unit 'recovery.img'
if (-not (Test-Path $rec)) { throw "no recovery backup for $Serial at $rec (the installer keeps one; -BackupRoot points elsewhere)" }
New-Item -ItemType Directory -Force $WorkDir | Out-Null
$image = Join-Path $unit 'techo5-dot-linux-next.img'

if ($FromSource) {
    $busybox = Join-Path $Inputs 'busybox.static'
    $apks = Join-Path $Inputs 'apks-dot'
    $alpine = Get-ChildItem (Join-Path $Inputs 'alpine-minirootfs-*-armv7.tar.gz') -ErrorAction SilentlyContinue | Select-Object -First 1
    foreach ($f in @($busybox, $apks, $Wmtup)) { if (-not (Test-Path $f)) { throw "missing ${f} (docs/building.md)" } }
    if (-not $alpine) { throw "no Alpine armv7 minirootfs in $Inputs (docs/building.md)" }
    $alpine = $alpine.FullName
    if (-not $Kernel -and -not $NoBluetoothKernel) { $Kernel = Join-Path (Join-Path $WorkDir 'kernel') 'zImage-dtb' }
} else {
    $rel = Get-DotRelease -WorkDir $WorkDir -Release $Release
    Write-Host "   TECHO5 Dot $($rel.Version): Bluetooth kernel and rescue packages checked"
    $alpine, $apks, $Wmtup, $busybox = $rel.Alpine, $rel.Apks, $rel.Wmtup, $rel.Busybox
    if (-not $Kernel -and -not $NoBluetoothKernel) { $Kernel = $rel.Kernel }
}
if ($NoBluetoothKernel) { $Kernel = $null }
if ($Kernel -and -not (Test-Path $Kernel)) { throw "no kernel at $Kernel" }
if ($Kernel) { Write-Host "   kernel $(Split-Path -Leaf $Kernel) (Bluetooth)" } else { Write-Host '   kernel from the recovery backup (no Bluetooth)' }

New-DotBootImage -Recovery $rec -Out $image -Alpine $alpine -Busybox $busybox -Wmtup $Wmtup -Apks $apks -Kernel $Kernel -Python $Python
$want = (Get-FileHash -Algorithm MD5 $image).Hash.ToLower()
$len = (Get-Item $image).Length

# The image over ssh's standard input as raw bytes: a text redirect would corrupt it.
$psi = New-Object Diagnostics.ProcessStartInfo 'ssh'
$psi.Arguments = "-o ConnectTimeout=10 root@$Address ""cat > /store/techo5/linux-next.img"""
$psi.RedirectStandardInput = $true
$psi.UseShellExecute = $false
$proc = [Diagnostics.Process]::Start($psi)
$src = [IO.File]::OpenRead($image)
try { $src.CopyTo($proc.StandardInput.BaseStream) } finally { $src.Close(); $proc.StandardInput.Close() }
$proc.WaitForExit()
if ($proc.ExitCode -ne 0) { throw "sending the image failed" }

$flash = @"
set -e
S=/store/techo5
[ "`$(md5sum < `$S/linux-next.img | cut -d' ' -f1)" = $want ] || { echo "transfer mismatch"; exit 1; }
cp -f `$S/linux.img `$S/linux-prev.img
dd if=`$S/linux-next.img of=/dev/mmcblk0p12 bs=1048576 2>/dev/null
sync
got=`$(head -c $len /dev/mmcblk0p12 | md5sum | cut -d' ' -f1)
[ "`$got" = $want ] || { dd if=`$S/linux-prev.img of=/dev/mmcblk0p12 bs=1048576 2>/dev/null; sync; echo "read back `$got; previous image restored"; exit 1; }
echo "flashed `$got"
"@
$out = ssh "root@$Address" $flash.Replace("`r`n", "`n") 2>&1
Write-Host "   $out"
if ("$out" -notmatch "flashed $want") { throw "flashing failed" }

# A boot is told apart from the one before it by the kernel's boot id, so the healthy mark the old boot
# left in MISC is never mistaken for the new one's.
$bootId = (ssh "root@$Address" 'cat /proc/sys/kernel/random/boot_id').Trim()
ssh "root@$Address" 'sync; (sleep 2; reboot) >/dev/null 2>&1 </dev/null &' | Out-Null
Write-Host "   rebooting into the new image; waiting for a healthy boot"
Start-Sleep -Seconds 40
$deadline = (Get-Date).AddMinutes(6)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    $r = ssh -o ConnectTimeout=4 -o BatchMode=yes "root@$Address" 'cat /proc/sys/kernel/random/boot_id; dd if=/dev/mmcblk0p8 bs=512 skip=14 count=1 2>/dev/null | tr -d "\000"' 2>$null
    if ("$r" -match 'healthy' -and "$r" -notmatch [regex]::Escape($bootId)) { $healthy = $true; break }
    Start-Sleep -Seconds 10
}
if (-not $healthy) { throw "the unit did not report a healthy boot; after five tries it stays in rescue (USB console, SSH)" }
ssh "root@$Address" "mv -f /store/techo5/linux-next.img /store/techo5/linux.img; sync; uname -r" 2>&1 | ForEach-Object { Write-Host "   kernel $_" }
Write-Host "Done: $Serial booted the new image healthy; it is now the unit's linux.img."
