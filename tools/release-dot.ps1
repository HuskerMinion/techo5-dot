<#
.SYNOPSIS
  Build and publish a TECHO5 Dot release: the signed manifest a Dot's updater reads, the Dot daemon,
  and the root filesystem it installs into its spare slot.

.DESCRIPTION
  The Dot's daemon follows this repository's releases (TECHO5 echod internal/update/releases_dot.go),
  separate from the Echo Show's, so a Dot release never becomes the Show's latest.

  Built from the TECHO5 worktree on its dot/mic-average branch with -tags dot:
    echod-arm-dot                     the daemon (also what a Fire OS Dot would take as a binary update)
    techo5-dot-rootfs-<version>.tar.gz  the whole root filesystem for a slot (tools/linux/build-dot-rootfs.py)
    techo5-dot-kernel-bt.zImage-dtb   the Echo Dot 2's kernel rebuilt with Bluetooth (GPL-2.0)
    techo5-dot-kernel-bt-radar.zImage-dtb  the Echo 2nd gen's (radar), the same way (GPL-2.0)
    techo5-dot-kernel-61-radar.zImage-dtb  the Echo 2nd gen's Linux 6.1 kernel, LibreEcho's with our
                                      config (tools/linux/build-kernel-61.sh; GPL-2.0), taken only when
                                      asked for (install-dot.py / update-boot.py --linux-61)
    echod-arm-donut                   the Echo Dot 3rd gen's daemon (-tags dot,donut)
    techo5-donut-rootfs-<version>.tar.gz  its root filesystem (build-dot-rootfs.py --board donut), with
                                      the Bluetooth stack its stock kernel lacks as modules (GPL-2.0)
    techo5-donut-bt-modules-source.tar.xz  the complete source of those modules (build-bt-modules.sh)
    techo5-dot-rescue-apks.tar        the packages the rescue initramfs is built from
    manifest.json                     versions, URLs, sizes and sha256 of all of those (cmd/mkmanifest)
    manifest.json.sig                 the release key's ed25519 signature over manifest.json

  The release is tagged dot-vX.Y.Z; the version inside it is vX.Y.Z. Those are different strings and
  the script keeps them apart, because a manifest carrying the tag is an update card nobody can clear.

  Nothing unit-specific is in any of them: no firmware, keys, Wi-Fi or Home Assistant identity, and no
  boot image (those are built per unit by the installer from the unit's own backup).

.EXAMPLE
  ./tools/release-dot.ps1 -Version v0.2.0 -Notes "First release." -DryRun
  ./tools/release-dot.ps1 -Version v0.2.0 -Notes "First release."
#>
param(
    [Parameter(Mandatory)][ValidatePattern('^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$')][string]$Version,
    [Parameter(Mandatory)][string]$Notes,
    # A TECHO5 checkout on main (or dot/mic-average): $env:TECHO5, else ../techo5 beside this repository.
    [string]$Techo5 = $(if ($env:TECHO5) { $env:TECHO5 } else { Join-Path (Join-Path (Join-Path $PSScriptRoot '..') '..') 'techo5' }),
    # The release signing key (ed25519 seed, base64); only the maintainer has it: $env:TECHO5_SIGN_KEY.
    [string]$SignKey = $env:TECHO5_SIGN_KEY,
    # The Dot's Bluetooth kernel (tools/linux/build-kernel.sh -o build/kernel), published with the release.
    [string]$Kernel = $(if ($env:TECHO5_DOT_KERNEL) { $env:TECHO5_DOT_KERNEL } else { Join-Path (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'kernel') 'zImage-dtb' }),
    # The Echo 2nd gen's (radar) Bluetooth kernel (tools/linux/build-kernel.sh --board radar).
    [string]$KernelRadar = $(if ($env:TECHO5_DOT_KERNEL_RADAR) { $env:TECHO5_DOT_KERNEL_RADAR } else { Join-Path (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'kernel-radar') 'zImage-dtb' }),
    # The Echo 2nd gen's Linux 6.1 kernel (tools/linux/build-kernel-61.sh).
    [string]$KernelRadar61 = $(if ($env:TECHO5_DOT_KERNEL_RADAR_61) { $env:TECHO5_DOT_KERNEL_RADAR_61 } else { Join-Path (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'kernel-61') 'techo5-dot-kernel-61-radar.zImage-dtb' }),
    # Optional: the sha256 each kernel must have (from its build log), so a stale or swapped file is caught.
    [string]$KernelSha256 = '',
    [string]$KernelRadarSha256 = '',
    [string]$KernelRadar61Sha256 = '',
    # bluez-alsa with its crash fix (tools/linux/build-bluealsa.sh -o build/bluealsa).
    [string]$Bluealsa = $(if ($env:TECHO5_BLUEALSA) { $env:TECHO5_BLUEALSA } else { Join-Path (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'bluealsa') 'bluealsa' }),
    # The Echo Dot 3's wpa_supplicant (tools/linux/build-wpa-supplicant.sh -> build/wpa_supplicant).
    [string]$WpaSupplicant = (Join-Path (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'wpa_supplicant') 'wpa_supplicant'),
    # The Echo Dot 3's Bluetooth modules and their source (tools/linux/build-bt-modules.sh --source-tarball,
    # copied to build/bt-modules and build/techo5-donut-bt-modules-source.tar.xz).
    [string]$BtModules = (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'bt-modules'),
    [string]$BtModulesSource = (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'techo5-donut-bt-modules-source.tar.xz'),
    # The build inputs (docs/building.md): $env:TECHO5_INPUTS, else inputs/ in this repository.
    [string]$Inputs = $(if ($env:TECHO5_INPUTS) { $env:TECHO5_INPUTS } else { Join-Path (Join-Path $PSScriptRoot '..') 'inputs' }),
    [string]$Go = 'go',
    [switch]$Prerelease,
    # The echod-arm-dot TECHO5's "Build release binaries" GitHub Actions workflow built for this release
    # (in the TECHO5 checkout: git tag dot-$Version; git push origin dot-$Version). When given, skips
    # building the daemon here, so the release ships exactly what CI attested — but only once
    # `gh attestation verify` confirms CI built it from refs/tags/dot-$Version: a binary built from a
    # branch or by hand is stamped with another version, and Home Assistant would then offer the
    # update forever after it was installed.
    [string]$PrebuiltArmDot = '',
    # The same for the Echo Dot 3's echod-arm-donut, from the same CI run.
    [string]$PrebuiltArmDonut = '',
    # The sources of the programs the root filesystems carry built from others' sources (Shairport Sync,
    # nqptp, librespot; NOTICE), from TECHO5's tools/linux/third-party-sources.sh: their licenses want the
    # source beside the binaries. Published as techo5-third-party-sources.tar.gz.
    [string]$ThirdPartySources = '',
    # Build and sign everything into bin\release\<version>, publish nothing.
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if (-not $DryRun -and (-not $ThirdPartySources -or -not (Test-Path $ThirdPartySources))) {
    throw "no -ThirdPartySources archive: build it with TECHO5's tools/linux/third-party-sources.sh"
}
$repo = 'HuskerMinion/techo5-dot'
# The tag says which device a release is for; the version says which build it is. They are not the same
# string and must not be swapped: the tag names the release (and the CI tag the binary was attested
# from), while $Version is what the daemon is stamped with and the only thing that belongs in the
# manifest. A manifest naming the tag differs forever from what a Dot reports as running, and Home
# Assistant offers an update whenever the two differ - which is how v0.5.9 and v0.5.10 shipped with an
# update card nobody could clear. mkmanifest refuses a tag now; this script keeps them apart.
$tag = "dot-$Version"
$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$out = Join-Path (Join-Path (Join-Path $root 'bin') 'release') $Version
New-Item -ItemType Directory -Force $out | Out-Null
if (-not $SignKey -or -not (Test-Path $SignKey)) { throw "no release signing key: set TECHO5_SIGN_KEY or pass -SignKey" }
if (-not (Test-Path $Kernel)) { throw "no Bluetooth kernel at ${Kernel}: build it (tools/linux/build-kernel.sh -o build/kernel) or pass -Kernel" }
if (-not (Test-Path $KernelRadar)) { throw "no radar Bluetooth kernel at ${KernelRadar}: build it (tools/linux/build-kernel.sh --board radar) or pass -KernelRadar" }
if (-not (Test-Path $KernelRadar61)) { throw "no radar Linux 6.1 kernel at ${KernelRadar61}: build it (tools/linux/build-kernel-61.sh) or pass -KernelRadar61" }
# A kernel on the wrong board comes up on the other board's device trees, if at all, and leaves no rescue
# to reach, so each kernel is proved to be its board's before anything is built: the two must differ,
# and each must carry its own board's config (CONFIG_ARCH_MTK_PROJECT, read by tools/dotimage.py).
$py = if ($IsLinux -or $IsMacOS) { 'python3' } else { 'python' }
$kernelHash = (Get-FileHash -Algorithm SHA256 $Kernel).Hash.ToLower()
$kernelRadarHash = (Get-FileHash -Algorithm SHA256 $KernelRadar).Hash.ToLower()
$kernelRadar61Hash = (Get-FileHash -Algorithm SHA256 $KernelRadar61).Hash.ToLower()
if ($kernelHash -eq $kernelRadarHash) { throw "-Kernel and -KernelRadar are the same kernel ($kernelHash): each board needs its own" }
# dotimage.py prints "radar 6.1" for the 6.1 kernel, so each of the three is only ever taken for its own.
foreach ($k in @(
        @{ Name = '-Kernel'; Path = $Kernel; Board = 'biscuit'; Hash = $kernelHash; Want = $KernelSha256 },
        @{ Name = '-KernelRadar'; Path = $KernelRadar; Board = 'radar'; Hash = $kernelRadarHash; Want = $KernelRadarSha256 },
        @{ Name = '-KernelRadar61'; Path = $KernelRadar61; Board = 'radar 6.1'; Hash = $kernelRadar61Hash; Want = $KernelRadar61Sha256 })) {
    if ($k.Want -and $k.Hash -ne $k.Want.ToLower()) { throw "$($k.Name) $($k.Path) has sha256 $($k.Hash), not $($k.Want)" }
    $board = (& $py (Join-Path (Join-Path $root 'tools') 'dotimage.py') board $k.Path) -join ''
    if ($LASTEXITCODE -ne 0) { throw "could not read the board of $($k.Name) $($k.Path)" }
    if (-not $board) { throw "$($k.Name) $($k.Path) carries no kernel config (CONFIG_IKCONFIG) to tell its board by" }
    if ($board -ne $k.Board) { throw "$($k.Name) $($k.Path) was built for $board, not $($k.Board)" }
}
if (-not (Test-Path (Join-Path $Techo5 'echod'))) { throw "no TECHO5 checkout at ${Techo5}: set TECHO5 or pass -Techo5" }
if (-not (Test-Path $WpaSupplicant)) { throw "no Echo Dot 3 wpa_supplicant at ${WpaSupplicant}: build it (tools/linux/build-wpa-supplicant.sh) or pass -WpaSupplicant" }
foreach ($m in 'cmac', 'bluetooth', 'hci_vhci') {
    if (-not (Test-Path (Join-Path $BtModules "$m.ko"))) { throw "no $m.ko in ${BtModules}: build them (tools/linux/build-bt-modules.sh) or pass -BtModules" }
}
# The modules are GPL-2.0: they ship only with their complete source beside them.
if (-not (Test-Path $BtModulesSource)) { throw "no module source at ${BtModulesSource}: build-bt-modules.sh --source-tarball writes it" }

$branch = (git -C $Techo5 branch --show-current).Trim()
if ($branch -notin 'main', 'dot/mic-average') { throw "$Techo5 is on '$branch', not main or dot/mic-average" }
if (git -C $Techo5 status --porcelain -- echod cmd) { throw "$Techo5 has uncommitted daemon changes; commit them first" }
if (git -C $root status --porcelain -- tools) { throw "this repository has uncommitted tool changes; commit them first" }
$commit = (git -C $Techo5 rev-parse --short HEAD).Trim()
$date = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$pkg = 'github.com/HuskerMinion/techo5/echod/internal/layout'
$ldflags = "-s -w -X '$pkg.Version=$Version' -X '$pkg.GitCommit=$commit' -X '$pkg.BuildDate=$date'"

if ($PrebuiltArmDot) {
    Write-Host "== using prebuilt echod-arm-dot (CI, tag $tag)"
    if (-not (Test-Path -LiteralPath $PrebuiltArmDot -PathType Leaf)) { throw "prebuilt binary not found: $PrebuiltArmDot" }
    & gh attestation verify $PrebuiltArmDot --repo HuskerMinion/techo5 --source-ref "refs/tags/$tag" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$PrebuiltArmDot is not attested as built by CI from tag $tag" }
    Copy-Item $PrebuiltArmDot (Join-Path $out 'echod-arm-dot') -Force
} else {
    Write-Host "== building echod-arm-dot $Version (TECHO5 $commit)"
    $env:GOOS = 'linux'; $env:GOARCH = 'arm'; $env:GOARM = '7'; $env:CGO_ENABLED = '0'
    try {
        Push-Location (Join-Path $Techo5 'echod')
        & $Go build -tags dot -trimpath -ldflags $ldflags -o (Join-Path $out 'echod-arm-dot') ./cmd/echod
        if ($LASTEXITCODE -ne 0) { throw 'daemon build failed' }
        Pop-Location
    } finally {
        $env:GOOS = $null; $env:GOARCH = $null; $env:GOARM = $null; $env:CGO_ENABLED = $null
    }
}
if ($PrebuiltArmDonut) {
    Write-Host "== using prebuilt echod-arm-donut (CI, tag $tag)"
    if (-not (Test-Path -LiteralPath $PrebuiltArmDonut -PathType Leaf)) { throw "prebuilt binary not found: $PrebuiltArmDonut" }
    & gh attestation verify $PrebuiltArmDonut --repo HuskerMinion/techo5 --source-ref "refs/tags/$tag" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$PrebuiltArmDonut is not attested as built by CI from tag $tag" }
    Copy-Item $PrebuiltArmDonut (Join-Path $out 'echod-arm-donut') -Force
} else {
    Write-Host "== building echod-arm-donut $Version (TECHO5 $commit)"
    $env:GOOS = 'linux'; $env:GOARCH = 'arm'; $env:GOARM = '7'; $env:CGO_ENABLED = '0'
    try {
        Push-Location (Join-Path $Techo5 'echod')
        & $Go build -tags dot,donut -trimpath -ldflags $ldflags -o (Join-Path $out 'echod-arm-donut') ./cmd/echod
        if ($LASTEXITCODE -ne 0) { throw 'daemon build failed' }
        Pop-Location
    } finally {
        $env:GOOS = $null; $env:GOARCH = $null; $env:GOARM = $null; $env:CGO_ENABLED = $null
    }
}
$env:GOOS = 'linux'; $env:GOARCH = 'arm'; $env:GOARM = '7'; $env:CGO_ENABLED = '0'
try {
    Push-Location $Techo5
    & $Go build -trimpath -ldflags '-s -w' -o (Join-Path (Join-Path $root 'bin') 'btbridge') ./cmd/btbridge
    if ($LASTEXITCODE -ne 0) { throw 'btbridge build failed' }
    Pop-Location
    Push-Location $root
    & $Go build -trimpath -ldflags '-s -w' -o (Join-Path (Join-Path $root 'bin') 'wmtup') ./cmd/wmtup
    if ($LASTEXITCODE -ne 0) { throw 'wmtup build failed' }
    Pop-Location
} finally {
    $env:GOOS = $null; $env:GOARCH = $null; $env:GOARM = $null; $env:CGO_ENABLED = $null
}

Write-Host "== root filesystem"
$rootfs = Join-Path $out "techo5-dot-rootfs-$Version.tar.gz"
& $py (Join-Path (Join-Path (Join-Path $root 'tools') 'linux') 'build-dot-rootfs.py') --daemon (Join-Path $out 'echod-arm-dot') --release $Version --inputs $Inputs --bluealsa $Bluealsa --out $rootfs
if ($LASTEXITCODE -ne 0) { throw 'building the root filesystem failed' }
# The root filesystem records which release it is, and that is what a Dot reports as running once it
# boots. Home Assistant compares it against what the manifest offers and shows an update whenever the
# two differ, so a disagreement here becomes a card no install clears. Proved on the tarball that was
# just built, as the Spot's release does, rather than on somebody's unit.
$release = (& tar -xzOf $rootfs ./etc/techo5-release) -join ''
if ($release -notmatch "techo5-dot $([regex]::Escape($Version)) ") { throw "the root filesystem says '$release', not $Version" }
# AirPlay 2: build-dot-rootfs.py adds its receiver and nqptp only when bin/ has both, so a worktree without them
# copied in would ship the classic receiver alone.
$inRootfs = @(& tar -tzf $rootfs) -replace '^\./', ''
foreach ($b in 'techo5-shairport-sync', 'techo5-nqptp') {
    if ($inRootfs -notcontains "usr/local/bin/$b") { throw "$rootfs has no usr/local/bin/${b}: copy bin/$b-arm from techo5 (tools/linux/build-shairport-sync.sh) into bin/$b" }
}
$rootfsDonut = Join-Path $out "techo5-donut-rootfs-$Version.tar.gz"
& $py (Join-Path (Join-Path (Join-Path $root 'tools') 'linux') 'build-dot-rootfs.py') --board donut --daemon (Join-Path $out 'echod-arm-donut') --release $Version --inputs $Inputs --bluealsa $Bluealsa --wpa-supplicant $WpaSupplicant --bt-modules $BtModules --out $rootfsDonut
if ($LASTEXITCODE -ne 0) { throw 'building the Echo Dot 3 root filesystem failed' }
$release = (& tar -xzOf $rootfsDonut ./etc/techo5-release) -join ''
if ($release -notmatch "techo5-donut $([regex]::Escape($Version)) ") { throw "the Echo Dot 3 root filesystem says '$release', not $Version" }
# And AirPlay 2 there too.
$inDonut = @(& tar -tzf $rootfsDonut) -replace '^\./', ''
foreach ($b in 'techo5-shairport-sync', 'techo5-nqptp') {
    if ($inDonut -notcontains "usr/local/bin/$b") { throw "$rootfsDonut has no usr/local/bin/${b}: copy bin/$b-arm from techo5 (tools/linux/build-shairport-sync.sh) into bin/$b" }
}
Copy-Item -Force $BtModulesSource (Join-Path $out 'techo5-donut-bt-modules-source.tar.xz')

Write-Host "== Bluetooth kernel, rescue packages and checksums"
Copy-Item -Force $Kernel (Join-Path $out 'techo5-dot-kernel-bt.zImage-dtb')
Copy-Item -Force $KernelRadar (Join-Path $out 'techo5-dot-kernel-bt-radar.zImage-dtb')
Copy-Item -Force $KernelRadar61 (Join-Path $out 'techo5-dot-kernel-61-radar.zImage-dtb')
$apks = Get-ChildItem (Join-Path (Join-Path $Inputs 'apks-dot') '*.apk')
if (-not $apks) { throw "no rescue packages in $(Join-Path $Inputs 'apks-dot')" }
Push-Location (Join-Path $Inputs 'apks-dot')
tar -cf (Join-Path $out 'techo5-dot-rescue-apks.tar') ($apks | ForEach-Object { $_.Name })
if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'packing the rescue packages failed' }
Pop-Location

Write-Host "== signed manifest"
Push-Location (Join-Path $Techo5 'echod')
$from = "https://github.com/$repo/releases/download/$tag"
& $Go run ./cmd/mkmanifest -version $Version -title "TECHO5 Dot $Version" -notes $Notes `
    -release-url "https://github.com/$repo/releases/tag/$tag" -from $from `
    -arm-dot (Join-Path $out 'echod-arm-dot') -rootfs-arm-dot $rootfs `
    -arm-donut (Join-Path $out 'echod-arm-donut') -rootfs-arm-donut $rootfsDonut `
    -asset (Join-Path $out 'techo5-dot-kernel-bt.zImage-dtb') -asset (Join-Path $out 'techo5-dot-kernel-bt-radar.zImage-dtb') `
    -asset (Join-Path $out 'techo5-dot-kernel-61-radar.zImage-dtb') `
    -asset (Join-Path $out 'techo5-dot-rescue-apks.tar') `
    -out (Join-Path $out 'manifest.json') -sign-key $SignKey
if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'mkmanifest failed' }
Pop-Location
Get-Content (Join-Path $out 'manifest.json')

$names = 'echod-arm-dot', "techo5-dot-rootfs-$Version.tar.gz", 'manifest.json', 'manifest.json.sig', 'techo5-dot-kernel-bt.zImage-dtb', 'techo5-dot-kernel-bt-radar.zImage-dtb', 'techo5-dot-kernel-61-radar.zImage-dtb', 'techo5-dot-rescue-apks.tar',
    'echod-arm-donut', "techo5-donut-rootfs-$Version.tar.gz", 'techo5-donut-bt-modules-source.tar.xz'
if ($ThirdPartySources) {
    Copy-Item $ThirdPartySources (Join-Path $out 'techo5-third-party-sources.tar.gz') -Force
    $names += 'techo5-third-party-sources.tar.gz'
}
# SHA256SUMS: for checking a download by hand. The installer checks the kernels and the rescue packages
# against the signed manifest instead, which names them all: nothing signs this file, so it is no
# check against whoever served the files it describes.
$sums = $names | ForEach-Object { "$((Get-FileHash -Algorithm SHA256 (Join-Path $out $_)).Hash.ToLower())  $_" }
[IO.File]::WriteAllText((Join-Path $out 'SHA256SUMS'), ($sums -join "`n") + "`n")
$assets = ($names + 'SHA256SUMS') | ForEach-Object { Join-Path $out $_ }
if ($DryRun) {
    Write-Host "Dry run: release files are in $out; nothing published."
    return
}
Write-Host "== release $tag on $repo"
$Notes += "`n`nThe Bluetooth kernel (techo5-dot-kernel-bt.zImage-dtb, Linux 3.18.19, GPL-2.0) is built by tools/linux/build-kernel.sh from Amazon's GPL source for the Echo Dot 2nd gen (Echo_Dot_src-6.5.7.1) with the configuration and backports in tools/linux. The Echo 2nd gen's (techo5-dot-kernel-bt-radar.zImage-dtb) is built the same way, by build-kernel.sh --board radar, from Amazon's GPL source for the Echo 2nd gen (Echo_v2_src-6.5.7.1). The Echo 2nd gen's Linux 6.1 kernel (techo5-dot-kernel-61-radar.zImage-dtb, GPL-2.0) is LibreEcho's (https://github.com/aslater3/LibreEcho-Linux, commit 56a648f00166012bb1dce18ef9636adb5c8dcafc) with the configuration in tools/linux/kernel-61-radar.fragment, built by tools/linux/build-kernel-61.sh. The Echo Dot 3rd gen's Bluetooth modules (bluetooth.ko, hci_vhci.ko, cmac.ko in techo5-donut-rootfs, Linux 4.4.22, GPL-2.0) are built by tools/linux/build-bt-modules.sh from Amazon's GPL source for the Echo Input (Echo_Input_src-6.5.4.7) with the Bluetooth sources it lacks from mainline 4.4.22; their complete source is techo5-donut-bt-modules-source.tar.xz."
$ghArgs = @('release', 'create', $tag) + $assets + @('--repo', $repo, '--title', "TECHO5 Dot $Version", '--notes', $Notes)
# A version with a suffix (-rc.1, -beta) is a prerelease whether or not -Prerelease was given: GitHub
# otherwise makes it /releases/latest, which is what the installers and every unit's updater follow.
if ($Prerelease -or $Version -match '-') { $ghArgs += '--prerelease' }
& gh @ghArgs
if ($LASTEXITCODE -ne 0) { throw 'gh release create failed' }
# The dev channel moves forward with every release, stable ones too, so it never offers something
# older than a unit already runs: Install then refuses it and the card never clears (techo5 issue #42).
# It never moves back: a stable release after a newer prerelease leaves dev on the prerelease. Its
# manifest names this release's own files, so only the manifest and its signature move.
function Get-VersionRank([string]$v) {
    if ($v -notmatch '^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?') { return $null }
    [pscustomobject]@{ Core = [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])"; Pre = $Matches[4] }
}
function Test-VersionNewer([string]$a, [string]$b) {
    $x = Get-VersionRank $a; $y = Get-VersionRank $b
    if (-not $x -or -not $y) { return $true }
    if ($x.Core -ne $y.Core) { return $x.Core -gt $y.Core }
    if (-not $x.Pre) { return [bool]$y.Pre }
    if (-not $y.Pre) { return $false }
    return [string]::CompareOrdinal($x.Pre, $y.Pre) -gt 0
}
$devVersion = $null
try { $devVersion = ((& gh release download dev --repo $repo -p manifest.json -O - 2>$null) -join "`n" | ConvertFrom-Json).version } catch { }
if (-not $devVersion -or (Test-VersionNewer $Version $devVersion)) {
    & gh release upload dev (Join-Path $out 'manifest.json') (Join-Path $out 'manifest.json.sig') --repo $repo --clobber
    if ($LASTEXITCODE -ne 0) { throw "published, but the dev channel was not updated: upload manifest.json and manifest.json.sig to the dev release by hand" }
    Write-Host "dev channel: $Version (was $devVersion)"
} else {
    Write-Host "dev channel left on $devVersion, which is newer than $Version"
}
Write-Host "published: https://github.com/$repo/releases/tag/$tag"
