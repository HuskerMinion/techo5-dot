<#
.SYNOPSIS
  Take an unlocked Echo Dot 2 (biscuit) from Fire OS to the TECHO5 Linux image, in one run.

.DESCRIPTION
  What it does, in order, stopping at the first thing that is not right:
    1. checks the device: biscuit, Fire OS 6, adb running as root, a saved Wi-Fi network
    2. backs up every partition that boots the unit (preloader through recovery) to the PC and checks
       each copy against an md5 read on the device
    3. gets the release (root filesystem, Bluetooth kernel, rescue packages) and builds this unit's boot
       image from its own recovery backup, so an image is never shared between units
    4. keeps the unit's Home Assistant identity if it has one (/data/misc/echolocal name and key, as
       EchoLocal leaves them), or makes one from -Name
    5. writes the boot image to the recovery partition and checks it read back
    6. unpacks the root filesystem into slot a of the store on the cache partition and makes it
       active
    7. arms the Linux boot, reboots, finds the unit's USB serial console by its serial number, and
       waits for the boot to call itself healthy and for Home Assistant's API port to answer

  After it: a power-on boots Linux; five boots in a row that never become healthy leave it in its
  rescue environment (USB console, SSH); Fire OS boots only when asked (/sbin/to-android). Nothing of Android is changed: both boot slots and both system partitions stay
  as they were. The recovery partition held TWRP; its backup is the way back
  (docs/porting-plan.md, "The way back").

  Prerequisites on the Dot: unlocked with amonet, and either booted in TWRP (where unlocking leaves it)
  or in Fire OS with adb as root (EchoLocal's install, or boot-root.zip, leaves it that way). Fire OS 6
  must be in its system slot. Wi-Fi comes from the network Fire OS saved; with none, the installer asks.

  On the PC: PowerShell 7 (pwsh; Windows PowerShell 5.1 also works on Windows), adb, python 3 and tar.
  Windows, Linux and macOS alike. Nothing needs building: by default everything comes from this
  repository's signed release (the root filesystem, the Bluetooth kernel and the rescue environment's
  packages, each checked against the release's checksums) and Alpine's pinned base image. -FromSource
  builds the root filesystem from local inputs instead (docs/building.md).

  Step by step, from a stock Dot: https://github.com/HuskerMinion/techo5/blob/main/docs/getting-started.md
.EXAMPLE
  ./tools/install-dot.ps1 -Serial <serial> -DryRun
  ./tools/install-dot.ps1 -Serial <serial>
  ./tools/install-dot.ps1 -Serial <serial> -Name "Kitchen"
  (a unit with no Home Assistant identity asks for a name when -Name is not given, and makes the key)
#>
param(
    [Parameter(Mandatory)][string]$Serial,
    # Only for a unit with no Home Assistant identity yet; one that has one keeps it.
    [string]$Name,
    [string]$KeyFile,
    [string]$Adb = 'adb',
    [string]$Python = $(if ($IsWindows -or $env:OS -eq 'Windows_NT') { 'python' } else { 'python3' }),
    # Where each unit's partition backups and its boot image go: the repository's backups/ unless set.
    [string]$BackupRoot = $(if ($env:TECHO5_BACKUPS) { $env:TECHO5_BACKUPS } else { Join-Path (Join-Path $PSScriptRoot '..') 'backups' }),
    # Downloads and extracted release files: the repository's build/ unless set.
    [string]$WorkDir = $(if ($env:TECHO5_WORK) { $env:TECHO5_WORK } else { Join-Path (Join-Path $PSScriptRoot '..') 'build' }),
    # The release to install ("latest", or a tag such as v0.5.0).
    [string]$Release = 'latest',
    # Build the root filesystem from local inputs rather than use the release's (docs/building.md).
    [switch]$FromSource,
    # With -FromSource: the inputs directory, and the daemon and tools built for the Dot.
    [string]$Inputs = $(if ($env:TECHO5_INPUTS) { $env:TECHO5_INPUTS } else { Join-Path (Join-Path $PSScriptRoot '..') 'inputs' }),
    [string]$Daemon = (Join-Path (Join-Path $PSScriptRoot '..') (Join-Path 'bin' 'echod-dot')),
    [string]$Wmtup = (Join-Path (Join-Path $PSScriptRoot '..') (Join-Path 'bin' 'wmtup')),
    [string]$Btbridge = (Join-Path (Join-Path $PSScriptRoot '..') (Join-Path 'bin' 'btbridge')),
    [string]$Bluealsa,
    # The kernel with Bluetooth. By default the release's; a path here uses that build instead
    # (tools/linux/build-kernel.sh). -NoBluetoothKernel keeps the unit's own kernel, which has no Bluetooth.
    [string]$Kernel,
    [switch]$NoBluetoothKernel,
    [string[]]$WakeWords = @('okay_nabu', 'hey_jarvis', 'hey_mycroft'),
    # Public key allowed to log in over SSH as root (keys only; no password exists). Only when given.
    [string]$SshKey,
    # A Wi-Fi network to join instead of the one Fire OS saved; asked for when there is neither.
    [string]$WifiSsid,
    # Checks, backs up and builds, and writes nothing to the unit.
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$Repo = Resolve-Path (Join-Path $PSScriptRoot '..')

function Step([string]$what) { Write-Host "== $what" }
function Note([string]$what) { Write-Host "   $what" }
function Sh([string]$cmd) { (& $Adb -s $Serial shell $cmd) -join "`n" }
function Push([string]$local, [string]$remote) {
    & $Adb -s $Serial push $local $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "adb push $local failed" }
}
function Md5File([string]$path) { (Get-FileHash -Algorithm MD5 $path).Hash.ToLower() }
# The release and the boot image (Get-DotRelease, New-DotBootImage, RepoPath, Get-Checked).
. (Join-Path (Join-Path $PSScriptRoot 'lib') 'release.ps1')

# A partition read straight into a file as bytes. Start-Process's redirect reads the child's output as text
# under PowerShell 7, which corrupts binary data; the raw stream does not.
function Save-Partition([string]$src, [string]$dest) {
    $psi = New-Object Diagnostics.ProcessStartInfo $Adb
    $psi.Arguments = "-s $Serial exec-out ""cat $src"""
    $psi.RedirectStandardOutput = $true
    $psi.UseShellExecute = $false
    $proc = [Diagnostics.Process]::Start($psi)
    $file = [IO.File]::Create($dest)
    try { $proc.StandardOutput.BaseStream.CopyTo($file) } finally { $file.Close() }
    $proc.WaitForExit()
    $proc.ExitCode
}



# ---------------------------------------------------------------------------------------------- 1
Step "device $Serial"
$state = (& $Adb -s $Serial get-state 2>$null)
# Two starting points: Fire OS with root adb (EchoLocal), or TWRP, which is where unlocking leaves a unit.
switch ($state) {
    'device'   { $Twrp = $false }
    'recovery' { $Twrp = $true }
    default    { throw "adb does not see $Serial (state: '$state'). Boot it into Fire OS (root adb) or TWRP with USB connected." }
}
$product = (Sh 'getprop ro.product.device; getprop ro.build.product').Trim()
if ($product -notmatch 'biscuit') { throw "$Serial reports '$product', not biscuit" }
$id = (Sh 'id').Trim()
if ($id -notmatch '^uid=0') { throw "adb is not root on $Serial ($id). EchoLocal's install gives root adb; install that first, or boot TWRP." }
$slot = (Sh 'getprop ro.boot.slot_suffix').Trim()

# The partition names: Fire OS has bootdevice; a recovery may name the controller instead.
$BN = '/dev/block/platform/bootdevice/by-name'
if ((Sh "test -e $BN/recovery && echo yes").Trim() -ne 'yes') {
    $BN = (Sh 'for d in /dev/block/platform/*/by-name /dev/block/by-name; do [ -e $d/recovery ] && { echo $d; break; }; done').Trim()
    if (-not $BN) { throw "no by-name partition links on $Serial" }
}

if ($Twrp) {
    # TWRP runs from RAM, so writing the recovery partition under it is safe. userdata (the Wi-Fi
    # credentials and the Home Assistant identity) and cache (the slot store) are mounted here if TWRP
    # has not already, and the Fire OS release is read off the running slot's system partition.
    $Stage = '/tmp/techo5'
    $mounts = Sh ("grep -q ' /data ' /proc/mounts || mount -t ext4 $BN/userdata /data; " +
        "grep -q ' /cache ' /proc/mounts || mount -t ext4 $BN/cache /cache; " +
        "grep -q ' /data ' /proc/mounts && echo data; grep -q ' /cache ' /proc/mounts && echo cache")
    if ($mounts -notmatch 'data' -or $mounts -notmatch 'cache') { throw "TWRP could not mount userdata and cache on $Serial ($mounts)" }
    $fireos = (Sh "mkdir -p /tmp/t5sys; mount -t ext4 -o ro $BN/system$slot /tmp/t5sys 2>/dev/null; sed -n 's/^ro.build.version.name=//p' /tmp/t5sys/system/build.prop 2>/dev/null; umount /tmp/t5sys 2>/dev/null").Trim()
} else {
    $Stage = '/data/local/tmp/techo5'
    $fireos = (Sh 'getprop ro.build.version.name').Trim()
}
if ($fireos -notmatch 'Fire OS 6') { throw "$Serial has '$fireos' in slot $slot; this installer is for Fire OS 6 (32-bit kernel)" }
Note "biscuit, $fireos, slot $slot, $(if ($Twrp) { 'in TWRP' } else { 'root adb in Fire OS' })"

# Wi-Fi: the network Fire OS saved, one set on the unit before (wifi-set), or one given here. With none
# of them the installer asks, and only the derived key goes to the unit: the passphrase is turned into
# WPA's 256-bit key here (PBKDF2-HMAC-SHA1, 4096 rounds, the SSID as salt), the same key wpa_passphrase
# would make, and the name travels as hex.
$wifi = (Sh 'sed -n "s/^[ \t]*ssid=//p" /data/misc/wifi/wpa_supplicant.conf 2>/dev/null | head -1').Trim()
$ownWifi = (Sh 'test -s /data/techo5-linux/wifi.conf && echo yes').Trim() -eq 'yes'
$wifiConf = $null
if ($WifiSsid -or (-not $wifi -and -not $ownWifi)) {
    if (-not $WifiSsid) {
        Note "$Serial has no saved Wi-Fi network"
        while (-not $WifiSsid) { $WifiSsid = (Read-Host "   Wi-Fi network name").Trim() }
    }
    $wifiConf = & (Join-Path $PSScriptRoot 'wifi-key.ps1') -Ssid $WifiSsid
    Note "will join '$WifiSsid'"
} elseif ($ownWifi) {
    Note "Wi-Fi network set on the unit before (wifi-set); keeping it"
} else {
    Note "saved Wi-Fi network $wifi"
}
# Fire OS's shell has no ip applet reachable by adb's PATH; ifconfig (toybox) is, and getprop is the last resort.
# Only a hint: the Linux address is read from the unit's serial console after the reboot.
$ip = $null
if (-not $Twrp) {
    $ip = (Sh 'ifconfig wlan0 2>/dev/null | sed -n "s/.*inet addr:\([0-9.]*\).*/\1/p" | head -1').Trim()
    if (-not $ip) { $ip = (Sh 'getprop dhcp.wlan0.ipaddress').Trim() }
    if ($ip) { Note "address in Fire OS: $ip" }
}

# ---------------------------------------------------------------------------------------------- 2
$unit = Join-Path $BackupRoot $Serial
New-Item -ItemType Directory -Force $unit | Out-Null
Step "backups to $unit"
$parts = 'preloader', 'kb', 'dkb', 'lk_a', 'lk_b', 'tee1', 'tee2', 'expdb', 'misc', 'persist', 'boot_a', 'boot_b', 'recovery'
foreach ($p in $parts) {
    $out = Join-Path $unit "$p.img"
    # amonet's TWRP points preloader, lk and tee at decoy files in /tmp/ota-decoy, so an OTA flashed
    # from it cannot overwrite the unlock. The real partitions are the *_real links, and the preloader
    # is the eMMC boot area. A copy of a decoy would look like a changed bootloader and restore nothing.
    $src = (Sh "s=$BN/$p; [ -e `${s}_real ] && s=`${s}_real; case `$(readlink `$s) in /tmp/*) if [ $p = preloader ]; then s=/dev/block/mmcblk0boot0; else s=; fi;; esac; echo `$s").Trim()
    if (-not $src) { throw "$p on $Serial points at a decoy with no real partition beside it" }
    $dev = (Sh "md5sum $src").Split(' ')[0].Trim()
    if ((Test-Path $out) -and (Md5File $out) -eq $dev) { Note "$p already backed up"; continue }
    $same = Get-ChildItem (Join-Path $unit "$p-*.img") -ErrorAction SilentlyContinue | Where-Object { (Md5File $_.FullName) -eq $dev } | Select-Object -First 1
    if ($same) { Note "$p already backed up as $($same.Name)"; continue }
    if ((Test-Path $out) -and $p -eq 'recovery') {
        # A recovery backup that no longer matches is the TWRP copy from before a Linux image went in;
        # it is the way back, so it is kept rather than overwritten with the Linux image.
        $head = [IO.File]::ReadAllBytes($out)[0..7]
        if ([Text.Encoding]::ASCII.GetString($head) -eq 'ANDROID!') { Note "recovery: keeping the earlier backup (the device's recovery has changed since)"; continue }
    }
    # An existing backup is never replaced. misc changes on every Linux boot (the bootloader message
    # and the try count), so a mismatch there is expected; the new copy goes beside the old one.
    $dest = $out
    if (Test-Path $out) { $dest = Join-Path $unit ("$p-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.img') }
    # cat, not dd: exec-out has no separate stderr, so anything a command prints lands in the file, and
    # Start-Process hands a redirect to the device shell as a literal operand. A copy is written to a
    # temporary name first and kept only once its md5 matches the device.
    $tmp = "$dest.partial"
    if ((Save-Partition $src $tmp) -ne 0) { Remove-Item -Force $tmp -ErrorAction SilentlyContinue; throw "reading $p failed" }
    $host5 = Md5File $tmp
    if ($host5 -ne $dev) { Remove-Item -Force $tmp; throw "$p copy does not match the device ($host5 vs $dev)" }
    Move-Item -Force $tmp $dest
    Note "$p $((Get-Item $dest).Length) bytes, md5 ok$(if ($dest -ne $out) { " (changed since $p.img; saved as $(Split-Path -Leaf $dest))" })"
}

# The recovery backup has to be TWRP, or at least an Android image, because the Linux image is built
# from its header and kernel.
$rec = [IO.File]::ReadAllBytes((Join-Path $unit 'recovery.img'))
if ([Text.Encoding]::ASCII.GetString($rec[0..7]) -ne 'ANDROID!') { throw "recovery backup is not an Android boot image" }

# ---------------------------------------------------------------------------------------------- 3
Step "the release, and this unit's boot image"
New-Item -ItemType Directory -Force $WorkDir | Out-Null
$image = Join-Path $unit 'techo5-dot-linux.img'
$rootfs = Join-Path $unit 'techo5-dot-rootfs.tar.gz'

if ($FromSource) {
    # Everything from local inputs, as a developer builds it (docs/building.md).
    if (-not $Bluealsa) { $Bluealsa = Join-Path $Inputs 'bluealsa' }
    $Busybox = Join-Path $Inputs 'busybox.static'
    foreach ($f in @($Daemon, $Wmtup, $Btbridge, $Bluealsa, $Busybox)) {
        if (-not (Test-Path $f)) { throw "missing ${f}: -FromSource needs the inputs and builds described in docs/building.md" }
    }
    $alpine = Get-ChildItem (Join-Path $Inputs 'alpine-minirootfs-*-armv7.tar.gz') -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $alpine) { throw "no Alpine armv7 minirootfs in $Inputs (docs/building.md)" }
    $alpine = $alpine.FullName
    $apks = Join-Path $Inputs 'apks-dot'
    if (-not $NoBluetoothKernel -and -not $Kernel) { $Kernel = Join-Path (Join-Path $WorkDir 'kernel') 'zImage-dtb' }
    $commit = (git -C $Repo rev-parse --short HEAD).Trim()
    & $Python (RepoPath 'tools', 'linux', 'mkrootfs.py') --rootfs $alpine --apkdir $apks --apkdir (Join-Path $Inputs 'apks-bt-dot') `
        --add "$Busybox=/bin/busybox.static" --add "$Wmtup=/usr/local/bin/wmtup" `
        --add "$Daemon=/usr/local/bin/echod" --add "$Btbridge=/usr/local/bin/btbridge" --add "$Bluealsa=/usr/bin/bluealsa" --overlay (RepoPath 'tools', 'linux', 'rootfs') `
        --release "techo5-dot ($commit)" -o $rootfs
    if ($LASTEXITCODE -ne 0) { throw "building the root filesystem failed" }
} else {
    # The signed release, each file checked against its checksum.
    $rel = Get-DotRelease -WorkDir $WorkDir -Release $Release
    Note "TECHO5 Dot $($rel.Version): root filesystem, Bluetooth kernel and rescue packages checked"
    Copy-Item -Force $rel.Rootfs $rootfs
    $alpine, $apks, $Wmtup, $Busybox = $rel.Alpine, $rel.Apks, $rel.Wmtup, $rel.Busybox
    if (-not $NoBluetoothKernel -and -not $Kernel) { $Kernel = $rel.Kernel }
}
if ($NoBluetoothKernel) { $Kernel = $null }
if ($Kernel -and -not (Test-Path $Kernel)) { throw "no kernel at $Kernel (build it with tools/linux/build-kernel.sh, or leave -Kernel out to use the release's)" }
if ($Kernel) { Note "kernel: $(Split-Path -Leaf $Kernel) (Bluetooth)" } else { Note "kernel: the unit's own (no Bluetooth)" }

New-DotBootImage -Recovery (Join-Path $unit 'recovery.img') -Out $image -Alpine $alpine -Busybox $Busybox `
    -Wmtup $Wmtup -Apks $apks -Kernel $Kernel -Python $Python
Note "boot image $((Get-Item $image).Length) bytes, root filesystem $((Get-Item $rootfs).Length) bytes"

# ---------------------------------------------------------------------------------------------- 4
Step "Home Assistant identity"
$haveName = (Sh 'cat /data/misc/echolocal/name 2>/dev/null').Trim()
$haveKey = (Sh 'test -s /data/misc/echolocal/psk && echo yes').Trim()
$newIdentity = $false
if ($haveName -and $haveKey -eq 'yes') {
    Note "keeping '$haveName' and its key: Home Assistant sees the same device"
} else {
    Note "$Serial has no Home Assistant identity yet"
    # Asked for here rather than required up front, so a first install is one command. The name is
    # what Home Assistant shows and what its entity ids are made from ("Kitchen" -> kitchen_...).
    while (-not $Name) {
        $Name = (Read-Host "   Name for this Dot in Home Assistant (for example: Kitchen)").Trim()
    }
    if ($Name.Length -gt 31) { throw "'$Name' is longer than the 31 characters the ESPHome API allows" }
    # The key is made here and kept beside the unit's backups, so it is never lost with a console.
    if (-not $KeyFile) { $KeyFile = Join-Path $unit 'home-assistant.key' }
    if (Test-Path $KeyFile) {
        $psk = (Get-Content $KeyFile -Raw).Trim()
    } else {
        $bytes = [byte[]]::new(32); [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $psk = [Convert]::ToBase64String($bytes)
        [IO.File]::WriteAllText($KeyFile, $psk)
        Note "new key written to $KeyFile; Home Assistant asks for it when the device is added"
    }
    if ([Convert]::FromBase64String($psk).Length -ne 32) { throw "the key in $KeyFile is not 32 bytes of base64" }
    $newIdentity = $true
    Note "will provision '$Name'"
}

$models = Join-Path ([IO.Path]::GetTempPath()) 'techo5-dot-models'
$haveModels = (Sh 'ls /data/misc/echolocal/models/*.tflite 2>/dev/null | wc -l').Trim()
if ([int]$haveModels -eq 0) {
    New-Item -ItemType Directory -Force $models | Out-Null
    foreach ($w in $WakeWords) {
        foreach ($ext in 'json', 'tflite') {
            Invoke-WebRequest -Uri "https://raw.githubusercontent.com/esphome/micro-wake-word-models/main/models/v2/$w.$ext" `
                -OutFile (Join-Path $models "$w.$ext") -UseBasicParsing
        }
    }
    Note "wake word models downloaded: $($WakeWords -join ', ')"
} else {
    Note "$haveModels wake word models already on the unit"
}

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run: checks, backups and builds done; nothing written to $Serial."
    Write-Host "  boot image:      $image"
    Write-Host "  root filesystem: $rootfs"
    return
}

# ---------------------------------------------------------------------------------------------- 5
Step "boot image into the recovery partition"
# The image goes into the store first and stays there: to-twrp and back-to-linux.sh use that copy.
Sh "mkdir -p $Stage /cache/techo5" | Out-Null
Push $image /cache/techo5/linux.img
Push $Busybox $Stage/busybox
$len = (Get-Item $image).Length
$want = Md5File $image
$got = (Sh "chmod 755 $Stage/busybox; dd if=/cache/techo5/linux.img of=$BN/recovery bs=1048576 2>/dev/null; sync; $Stage/busybox head -c $len $BN/recovery | md5sum").Split(' ')[0].Trim()
if ($got -ne $want) { throw "recovery partition reads back $got, wanted $want. The TWRP backup is at $(Join-Path $unit 'recovery.img')." }
Note "written and read back, md5 $got"

# ---------------------------------------------------------------------------------------------- 6
Step "root filesystem into slot a"
Push $rootfs $Stage/rootfs.tar.gz
$slotScript = @'
set -e
T=$1
B=$T/busybox
S=/cache/techo5
mkdir -p $S/slots
# A slot a from an earlier install is set aside until the new one has unpacked, then removed.
rm -rf $S/slots/a.old $S/slots/b.old
if [ -d $S/slots/a ]; then mv $S/slots/a $S/slots/a.old; fi
mkdir -p $S/slots/a
cd $S/slots/a
$B tar xzf $T/rootfs.tar.gz
[ -x $S/slots/a/sbin/init ] || { echo "unpacked slot has no /sbin/init"; exit 1; }
# The unit's firmware moves from the old slot into the store, where both slots find it.
if [ ! -e $S/firmware/WIFI_RAM_CODE_8163 ] && [ -e $S/slots/a.old/etc/firmware/WIFI_RAM_CODE_8163 ]; then
	mkdir -p $S/firmware; cp $S/slots/a.old/etc/firmware/* $S/firmware/
fi
rm -rf $S/slots/a.old
# Installed and checked from here, so it starts good rather than on trial. A slot b is left as it is.
: > $S/.techo5-store
echo good > $S/slots/a.state
echo a > $S/active
rm -f $S/rescue
sync
echo "slot a: $(cat $S/slots/a/etc/techo5-release), $(ls $S/slots/a/bin | wc -l) commands in /bin"
'@
$tmpScript = Join-Path ([IO.Path]::GetTempPath()) 'techo5-dot-slot.sh'
[IO.File]::WriteAllText($tmpScript, ($slotScript -replace "`r`n", "`n"))
Push $tmpScript $Stage/slot.sh
$result = Sh "sh $Stage/slot.sh $Stage 2>&1"
Remove-Item $tmpScript -Force
if ($result -notmatch 'slot a: ') { throw "installing the slot failed: $result" }
Note $result.Trim()

# The way back to TWRP without a PC: the unit's own TWRP (its recovery backup, from before Linux went in)
# and the script that undoes it, beside the Linux image already in the store.
$rec = Join-Path $unit 'recovery.img'
$recHead = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($rec)[0..1023])
if ($recHead.StartsWith('ANDROID!') -and $recHead -notmatch 'techo5') {
    Push $rec /cache/techo5/twrp.img
    $tw = (Sh 'md5sum /cache/techo5/twrp.img').Split(' ')[0].Trim()
    if ($tw -ne (Md5File $rec)) { throw "the TWRP copy on the unit does not match $rec" }
    Push (RepoPath 'tools', 'linux', 'back-to-linux.sh') /cache/techo5/back-to-linux.sh
    Note "TWRP copy kept on the unit (to-twrp puts it back; /cache/techo5/back-to-linux.sh returns)"
} else {
    Note "no TWRP backup for $Serial to keep on the unit (to-twrp will say so)"
}

# SSH keys and Wi-Fi go to userdata, where they outlast slots and updates. The key goes where the daemon
# looks (its state directory); the daemon runs SSH only while the SSH switch in Home Assistant is on,
# and a new install starts with it off.
Sh 'mkdir -p /data/misc/echolocal/ssh; chmod 700 /data/misc/echolocal/ssh' | Out-Null
if ($SshKey -and (Test-Path $SshKey)) {
    $key = (Get-Content $SshKey -Raw).Trim()
    $have = (Sh 'cat /data/misc/echolocal/ssh/authorized_keys /data/techo5-linux/ssh/authorized_keys 2>/dev/null').Trim()
    if (-not $have.Contains(($key -split ' ')[1])) {
        $keysTmp = Join-Path ([IO.Path]::GetTempPath()) 'techo5-dot-keys'
        [IO.File]::WriteAllText($keysTmp, ((@($have, $key) | Where-Object { $_ }) -join "`n") + "`n")
        Push $keysTmp /data/misc/echolocal/ssh/authorized_keys
        Remove-Item $keysTmp -Force
    }
    Sh 'chmod 600 /data/misc/echolocal/ssh/authorized_keys' | Out-Null
    Note "SSH key $(Split-Path -Leaf $SshKey) set; turn on the SSH switch in Home Assistant to use it"
} else {
    Note "no SSH key at '$SshKey'; SSH stays off until authorized_keys has one"
}
if ($wifiConf) {
    $wifiTmp = Join-Path ([IO.Path]::GetTempPath()) 'techo5-dot-wifi'
    [IO.File]::WriteAllText($wifiTmp, $wifiConf)
    Push $wifiTmp /data/techo5-linux/wifi.conf
    Remove-Item $wifiTmp -Force
    Sh 'chmod 600 /data/techo5-linux/wifi.conf' | Out-Null
    Note "Wi-Fi network written to the unit"
}

if ($newIdentity) {
    Step "provisioning '$Name'"
    $nameTmp = Join-Path ([IO.Path]::GetTempPath()) 'techo5-dot-name'; [IO.File]::WriteAllText($nameTmp, $Name)
    $pskTmp = Join-Path ([IO.Path]::GetTempPath()) 'techo5-dot-psk'; [IO.File]::WriteAllText($pskTmp, $psk)
    Sh 'mkdir -p /data/misc/echolocal/models; chmod 700 /data/misc/echolocal' | Out-Null
    Push $nameTmp /data/misc/echolocal/name
    Push $pskTmp /data/misc/echolocal/psk
    Sh 'chmod 600 /data/misc/echolocal/name /data/misc/echolocal/psk' | Out-Null
    Remove-Item $nameTmp, $pskTmp -Force
}
if (Test-Path $models) {
    Sh 'mkdir -p /data/misc/echolocal/models' | Out-Null
    Get-ChildItem $models | ForEach-Object { Push $_.FullName "/data/misc/echolocal/models/$($_.Name)" }
    Sh 'chmod 644 /data/misc/echolocal/models/*' | Out-Null
    Remove-Item $models -Recurse -Force
}

# ---------------------------------------------------------------------------------------------- 7
Step "arming the Linux boot and rebooting"
# A fresh try count, so the new install gets its full set of chances.
Sh "printf 'TECHO5-TRIES 0 installed\n' | dd of=$BN/misc bs=512 seek=14 count=1 conv=notrunc 2>/dev/null; rm -f $Stage/rootfs.tar.gz $Stage/busybox $Stage/slot.sh; sync" | Out-Null
& $Adb -s $Serial reboot recovery

# The unit is found on its USB serial console, by its own serial number, and asked how the boot went:
# that works whatever address DHCP hands Linux, and from TWRP, where no address was known at all.
function Find-LinuxPort {
    # An image from before the gadget carried the serial reports a placeholder: only trusted when alone.
    $placeholder = '0123456789ABCDEF'
    if ($IsLinux) {
        # Linux: the ACM ports, each asked for its USB ids and serial.
        $all = @()
        foreach ($tty in @(Get-ChildItem /dev/ttyACM* -ErrorAction SilentlyContinue)) {
            $props = (& udevadm info -q property -n $tty.FullName 2>$null) -join "`n"
            if ($props -match '(?m)^ID_VENDOR_ID=18d1$' -and $props -match '(?m)^ID_MODEL_ID=4ee7$') {
                $all += [pscustomobject]@{ Port = $tty.FullName; Serial = [regex]::Match($props, '(?m)^ID_SERIAL_SHORT=(.*)$').Groups[1].Value }
            }
        }
    } elseif ($IsMacOS) {
        # macOS names a USB modem port after the device's serial number.
        $all = @(Get-ChildItem /dev/cu.usbmodem* -ErrorAction SilentlyContinue | ForEach-Object {
            [pscustomobject]@{ Port = $_.FullName; Serial = $_.Name.Substring('cu.usbmodem'.Length) } })
    } else {
        $all = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -like 'USB\VID_18D1&PID_4EE7\*' -and $_.Name -match '\(COM\d+\)' } | ForEach-Object {
            [pscustomobject]@{ Port = [regex]::Match($_.Name, 'COM\d+').Value; Serial = ($_.PNPDeviceID -split '\\')[-1] } })
    }
    $mine = @($all | Where-Object { $_.Serial -like "$Serial*" })
    if (-not $mine -and $all.Count -eq 1 -and $all[0].Serial -like "$placeholder*") { $mine = $all }
    if ($mine) { $mine[0].Port }
}
function Invoke-Console([string]$port, [string]$cmd) {
    $sp = New-Object IO.Ports.SerialPort $port, 115200
    try {
        $sp.Open()
        $sp.DiscardInBuffer()
        # The markers are printed by printf, so the shell's echo of the typed line never matches them.
        $sp.Write("printf 'T5%s\n' BEGIN; $cmd; printf 'T5%s\n' END`n")
        $out = ''
        $until = (Get-Date).AddSeconds(8)
        while ((Get-Date) -lt $until -and $out -notmatch 'T5END') { Start-Sleep -Milliseconds 100; $out += $sp.ReadExisting() }
    } catch { return $null } finally { $sp.Close() }
    $m = [regex]::Match($out, 'T5BEGIN\r?\n(.*?)T5END', 'Singleline')
    if (-not $m.Success) { return $null }
    $kv = @{}
    foreach ($line in ($m.Groups[1].Value -split "\r?\n")) { if ($line -match '^(\w+)=(.*)$') { $kv[$Matches[1]] = $Matches[2].Trim() } }
    $kv
}
function Test-ApiPort([string]$address) {
    $tcp = New-Object Net.Sockets.TcpClient
    try { $wait = $tcp.BeginConnect($address, 6053, $null, $null); return ($wait.AsyncWaitHandle.WaitOne(2000) -and $tcp.Connected) }
    catch { return $false } finally { $tcp.Close() }
}

Note "waiting for the unit's Linux console on USB"
$port = $null
$deadline = (Get-Date).AddMinutes(3)
while (-not $port -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3; $port = Find-LinuxPort }
$healthy = $false
$up = $false
if ($port) {
    Note "console on $port"
    $query = @'
echo slot=$(cat /run/techo5/slot 2>/dev/null); echo release=$(cat /etc/techo5-release 2>/dev/null); echo ip=$(ifconfig wlan0 2>/dev/null | sed -n 's/.*inet addr:\([0-9.]*\).*/\1/p'); echo echod=$(pidof echod); echo tries=$(dd if=/dev/mmcblk0p8 bs=512 skip=14 count=1 2>/dev/null | tr -d '\000')
'@
    # Healthy is the boot's own verdict: echod up for 90 s, and the try count back to 0.
    $deadline = (Get-Date).AddMinutes(5)
    $said = ''
    while ((Get-Date) -lt $deadline) {
        $kv = Invoke-Console $port $query.Trim()
        if ($kv) {
            $now = "slot $($kv.slot), $($kv.release), address $(if ($kv.ip) { $kv.ip } else { 'none yet' }), echod $(if ($kv.echod) { 'running' } else { 'not running' })"
            if ($now -ne $said) { Note $now; $said = $now }
            if ($kv.ip) { $ip = $kv.ip }
            if ($kv.tries -match 'healthy') { $healthy = $true; break }
        }
        Start-Sleep -Seconds 5
    }
} else {
    Note "no console appeared for $Serial"
}
if ($ip) {
    $deadline = (Get-Date).AddMinutes($(if ($port) { 1 } else { 4 }))
    while (-not ($up = Test-ApiPort $ip) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
}

Write-Host ""
if ($newIdentity) {
    Write-Host "Home Assistant will discover '$Name' as an ESPHome device. When it asks for the encryption key, paste:"
    Write-Host ""
    Write-Host "    $psk"
    Write-Host ""
    Write-Host "(also saved in $KeyFile)"
    Write-Host ""
}
if ($healthy -and $up) {
    Write-Host "Done: $Serial is running TECHO5 Linux, healthy, and Home Assistant's API port answers at ${ip}:6053."
    Write-Host "      SSH: switch it on in Home Assistant, then ssh root@$ip (wifi-set, slotctl status, to-twrp)"
} elseif ($up) {
    Write-Host "$Serial answers at ${ip}:6053, but the boot was not confirmed healthy (no console, or not within five minutes)."
} else {
    Write-Host "Rebooted, but the unit was not confirmed up$(if ($ip) { " (${ip}:6053 did not answer)" })."
    Write-Host "After five boots that never become healthy it stays in rescue (techo5-retry tries again). The USB console"
    Write-Host "shows the boot and the crumbs (tools/linux/readmisc.sh)."
    exit 1
}
