# Shared by install-dot.ps1 and update-boot.ps1: the signed release and the boot image built from it.
# Dot-sourced; the caller has set $ErrorActionPreference = 'Stop'.

$Script:DotRepo = (Resolve-Path (Join-Path $PSScriptRoot (Join-Path '..' '..'))).Path
$Script:DotReleases = 'https://github.com/HuskerMinion/techo5-dot/releases'

# Alpine's base image, pinned: the boot image's initramfs is built on it.
$Script:AlpineUrl = 'https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/alpine-minirootfs-3.24.1-armv7.tar.gz'
$Script:AlpineSha256 = '50942d567e6ee422c16cb46d5c282ed9d8adc9007c2a483faf4148a18c64ce32'

function Sha256File([string]$path) { (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower() }

# A path inside the repository, built with the platform's own separator.
function RepoPath([string[]]$parts) { $p = $Script:DotRepo; foreach ($x in $parts) { $p = Join-Path $p $x }; $p }

# tar: on Windows, its own (bsdtar). A GNU tar from Git or MSYS earlier on the PATH reads C:\... as a
# remote host.
function Get-Tar {
    if (-not $IsLinux -and -not $IsMacOS -and $env:SystemRoot) {
        $own = Join-Path (Join-Path $env:SystemRoot 'System32') 'tar.exe'
        if (Test-Path $own) { return $own }
    }
    'tar'
}

# The Python to run the image tools with.
function Get-Python { if ($IsLinux -or $IsMacOS) { 'python3' } else { 'python' } }

# A download kept only once its sha256 is the one expected; one already there and right is not fetched again.
function Get-Checked([string]$url, [string]$out, [string]$sha256) {
    if ((Test-Path $out) -and (Sha256File $out) -eq $sha256) { return }
    $partial = "$out.partial"
    Invoke-WebRequest -Uri $url -OutFile $partial -UseBasicParsing
    $got = Sha256File $partial
    if ($got -ne $sha256) { Remove-Item -Force $partial; throw "$url does not match its checksum ($got, wanted $sha256)" }
    Move-Item -Force $partial $out
}

# The release's files, downloaded into $WorkDir and checked: its manifest names the root filesystem and its
# checksum, and SHA256SUMS covers the Bluetooth kernel and the rescue packages. Returns where each is.
function Get-DotRelease([string]$WorkDir, [string]$Release = 'latest') {
    $tar = Get-Tar
    $dl = if ($Release -eq 'latest') { "$Script:DotReleases/latest/download" } else { "$Script:DotReleases/download/$Release" }
    $manifest = Invoke-RestMethod -Uri "$dl/manifest.json" -UseBasicParsing
    $rel = Join-Path $WorkDir "release-$($manifest.version)"
    New-Item -ItemType Directory -Force $rel | Out-Null

    $sums = @{}
    $text = (Invoke-WebRequest -Uri "$dl/SHA256SUMS" -UseBasicParsing).Content
    if ($text -is [byte[]]) { $text = [Text.Encoding]::ASCII.GetString($text) }
    foreach ($line in ($text -split "`n")) {
        if ($line -match '^([0-9a-f]{64})\s+\*?(\S+)') { $sums[$Matches[2]] = $Matches[1] }
    }
    foreach ($want in 'techo5-dot-rescue-apks.tar', 'techo5-dot-kernel-bt.zImage-dtb') {
        if (-not $sums[$want]) { throw "release $($manifest.version) has no $want in SHA256SUMS; pick another with -Release" }
    }

    $rootfs = Join-Path $rel 'techo5-dot-rootfs.tar.gz'
    Get-Checked $manifest.rootfs.'arm-dot'.url $rootfs $manifest.rootfs.'arm-dot'.sha256

    $apksTar = Join-Path $rel 'techo5-dot-rescue-apks.tar'
    Get-Checked "$dl/techo5-dot-rescue-apks.tar" $apksTar $sums['techo5-dot-rescue-apks.tar']
    $apks = Join-Path $rel 'apks-dot'
    New-Item -ItemType Directory -Force $apks | Out-Null
    & $tar -xf $apksTar -C $apks
    if ($LASTEXITCODE -ne 0) { throw "unpacking the rescue packages failed" }

    $kernel = Join-Path $rel 'techo5-dot-kernel-bt.zImage-dtb'
    Get-Checked "$dl/techo5-dot-kernel-bt.zImage-dtb" $kernel $sums['techo5-dot-kernel-bt.zImage-dtb']

    # What the boot image needs from the root filesystem: the Wi-Fi bring-up and busybox.
    $files = Join-Path $rel 'files'
    New-Item -ItemType Directory -Force $files | Out-Null
    & $tar -xzf $rootfs -C $files usr/local/bin/wmtup bin/busybox.static
    if ($LASTEXITCODE -ne 0) { throw "taking wmtup and busybox out of the root filesystem failed" }

    $alpine = Join-Path $WorkDir 'alpine-minirootfs-3.24.1-armv7.tar.gz'
    Get-Checked $Script:AlpineUrl $alpine $Script:AlpineSha256

    [pscustomobject]@{
        Version = $manifest.version
        Rootfs  = $rootfs
        Kernel  = $kernel
        Apks    = $apks
        Wmtup   = Join-Path $files (Join-Path 'usr' (Join-Path 'local' (Join-Path 'bin' 'wmtup')))
        Busybox = Join-Path $files (Join-Path 'bin' 'busybox.static')
        Alpine  = $alpine
    }
}

# A unit's boot image: its own recovery backup's header (and kernel, when $Kernel is empty), the rescue
# initramfs on Alpine's base, and the slot, network and TWRP tools.
function New-DotBootImage {
    param(
        [Parameter(Mandatory)][string]$Recovery,
        [Parameter(Mandatory)][string]$Out,
        [Parameter(Mandatory)][string]$Alpine,
        [Parameter(Mandatory)][string]$Busybox,
        [Parameter(Mandatory)][string]$Wmtup,
        [Parameter(Mandatory)][string]$Apks,
        [string]$Kernel,
        [string]$Python = (Get-Python)
    )
    $mk = @((RepoPath 'tools', 'linux', 'mkimage.py'), '--kernel-image', $Recovery,
        '--rootfs', $Alpine, '--init', (RepoPath 'tools', 'linux', 'init'),
        '--add', "$Busybox=/bin/busybox.static",
        '--add', "$Wmtup=/usr/local/bin/wmtup",
        '--cmdline-drop', 'skip_initramfs', '--cmdline-drop', 'root=', '--cmdline-drop', 'dm=',
        '--cmdline-append', 'techo5.stay_minutes=15', '-o', $Out)
    if ($Kernel) { $mk += @('--kernel', $Kernel) }
    Get-ChildItem (Join-Path $Apks '*.apk') | ForEach-Object { $mk += @('--apk', $_.FullName) }
    foreach ($tool in 'slotctl', 'techo5-net', 'wifi-set', 'to-twrp', 'techo5-firewall') {
        $mk += @('--script', "$(RepoPath 'tools', 'linux', 'rootfs', 'usr', 'local', 'sbin', $tool)=/usr/local/sbin/$tool")
    }
    & $Python @mk
    if ($LASTEXITCODE -ne 0) { throw "building the boot image failed" }
}
