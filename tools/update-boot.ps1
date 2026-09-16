<#
.SYNOPSIS
  Rebuild a Dot's boot image (kernel + initramfs) and put it in the recovery partition of a unit that
  is already running TECHO5 Linux, over SSH.

.DESCRIPTION
  Home Assistant updates carry the root filesystem only; the boot image is built per unit from the
  unit's own recovery backup and is never published. This is how a change to tools/linux/init, or a
  rebuilt kernel (-Kernel), reaches a unit without going back through Fire OS or TWRP.

  The previous image stays in the store as linux-prev.img, and linux.img (what back-to-linux.sh and
  to-twrp use) is only replaced once the new image has booted healthy. A new image that never comes up
  spends the boot tries and the unit stays in its rescue environment (USB console, SSH), where
  techo5-retry or the installer puts things right.

.EXAMPLE
  .\tools\update-boot.ps1 -Serial <serial> -Address <address>
  .\tools\update-boot.ps1 -Serial <serial> -Address <address> -Kernel D:\platform-tools\echodot\kernel-build\out\zImage-dtb
#>
param(
    [Parameter(Mandatory)][string]$Serial,
    [Parameter(Mandatory)][string]$Address,
    # The kernel: the Bluetooth build (tools/linux/build-kernel.sh) when it is there, otherwise the one in
    # the unit's recovery backup, which has no Bluetooth.
    [string]$Kernel = 'D:\platform-tools\echodot\kernel-build\out\zImage-dtb',
    [string]$BackupRoot = 'D:\platform-tools\echodot',
    [string]$Inputs = 'D:\platform-tools\echoshow\linux-image',
    [string]$Wmtup = (Join-Path $PSScriptRoot '..\bin\wmtup'),
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..')
$unit = Join-Path $BackupRoot $Serial
$rec = Join-Path $unit 'recovery.img'
if (-not (Test-Path $rec)) { throw "no recovery backup for $Serial at $rec (run the installer once)" }
$alpine = Get-ChildItem (Join-Path $Inputs 'alpine-minirootfs-*-armv7.tar.gz') | Select-Object -First 1
$image = Join-Path $unit 'techo5-dot-linux-next.img'

$mk = @((Join-Path $repo 'tools\linux\mkimage.py'), '--kernel-image', $rec,
    '--rootfs', $alpine.FullName, '--init', (Join-Path $repo 'tools\linux\init'),
    '--add', "$(Join-Path $Inputs 'busybox.static')=/bin/busybox.static",
    '--add', "$Wmtup=/usr/local/bin/wmtup",
    '--cmdline-drop', 'skip_initramfs', '--cmdline-drop', 'root=', '--cmdline-drop', 'dm=',
    '--cmdline-append', 'techo5.stay_minutes=15', '-o', $image)
if ($Kernel -and (Test-Path $Kernel)) { $mk += @('--kernel', $Kernel); Write-Host "   kernel $Kernel" } else { Write-Host '   kernel from the recovery backup (no Bluetooth)' }
Get-ChildItem (Join-Path $Inputs 'apks-dot\*.apk') | ForEach-Object { $mk += @('--apk', $_.FullName) }
foreach ($tool in 'slotctl', 'techo5-net', 'wifi-set', 'to-twrp', 'techo5-firewall') {
    $mk += @('--script', "$(Join-Path $repo "tools\linux\rootfs\usr\local\sbin\$tool")=/usr/local/sbin/$tool")
}
& $Python @mk
if ($LASTEXITCODE -ne 0) { throw "building the boot image failed" }
$want = (Get-FileHash -Algorithm MD5 $image).Hash.ToLower()
$len = (Get-Item $image).Length

$proc = Start-Process -FilePath ssh -ArgumentList @('-o', 'ConnectTimeout=10', "root@$Address", 'cat > /store/techo5/linux-next.img') `
    -RedirectStandardInput $image -NoNewWindow -Wait -PassThru
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
