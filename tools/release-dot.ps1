<#
.SYNOPSIS
  Build and publish a TECHO5 Dot release: the signed manifest a Dot's updater reads, the Dot daemon,
  and the root filesystem it installs into its spare slot.

.DESCRIPTION
  The Dot's daemon follows this repository's releases (TECHO5 echod internal/update/releases_dot.go),
  separate from the Echo Show's, so a Dot release never becomes the Show's latest.

  Built from the TECHO5 worktree on its dot/mic-average branch with -tags dot:
    echod-arm-dot             the daemon (also what a Fire OS Dot would take as a binary update)
    techo5-dot-rootfs.tar.gz  the whole root filesystem for a slot (tools/linux/build-dot-rootfs.ps1)
    manifest.json             versions, URLs, sizes and sha256 of both (cmd/mkmanifest)
    manifest.json.sig         the release key's ed25519 signature over manifest.json

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
    # bluez-alsa with its crash fix (tools/linux/build-bluealsa.sh -o build/bluealsa).
    [string]$Bluealsa = $(if ($env:TECHO5_BLUEALSA) { $env:TECHO5_BLUEALSA } else { Join-Path (Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'build') 'bluealsa') 'bluealsa' }),
    # The build inputs (docs/building.md): $env:TECHO5_INPUTS, else inputs/ in this repository.
    [string]$Inputs = $(if ($env:TECHO5_INPUTS) { $env:TECHO5_INPUTS } else { Join-Path (Join-Path $PSScriptRoot '..') 'inputs' }),
    [string]$Go = 'go',
    [switch]$Prerelease,
    # Build and sign everything into bin\release\<version>, publish nothing.
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$repo = 'HuskerMinion/techo5-dot'
$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$out = Join-Path (Join-Path (Join-Path $root 'bin') 'release') $Version
New-Item -ItemType Directory -Force $out | Out-Null
if (-not $SignKey -or -not (Test-Path $SignKey)) { throw "no release signing key: set TECHO5_SIGN_KEY or pass -SignKey" }
if (-not (Test-Path $Kernel)) { throw "no Bluetooth kernel at ${Kernel}: build it (tools/linux/build-kernel.sh -o build/kernel) or pass -Kernel" }
if (-not (Test-Path (Join-Path $Techo5 'echod'))) { throw "no TECHO5 checkout at ${Techo5}: set TECHO5 or pass -Techo5" }

$branch = (git -C $Techo5 branch --show-current).Trim()
if ($branch -notin 'main', 'dot/mic-average') { throw "$Techo5 is on '$branch', not main or dot/mic-average" }
if (git -C $Techo5 status --porcelain -- echod cmd) { throw "$Techo5 has uncommitted daemon changes; commit them first" }
if (git -C $root status --porcelain -- tools) { throw "this repository has uncommitted tool changes; commit them first" }
$commit = (git -C $Techo5 rev-parse --short HEAD).Trim()
$date = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$pkg = 'github.com/HuskerMinion/techo5/echod/internal/layout'
$ldflags = "-s -w -X '$pkg.Version=$Version' -X '$pkg.GitCommit=$commit' -X '$pkg.BuildDate=$date'"

Write-Host "== building echod-arm-dot $Version (TECHO5 $commit)"
$env:GOOS = 'linux'; $env:GOARCH = 'arm'; $env:GOARM = '7'; $env:CGO_ENABLED = '0'
try {
    Push-Location (Join-Path $Techo5 'echod')
    & $Go build -tags dot -trimpath -ldflags $ldflags -o (Join-Path $out 'echod-arm-dot') ./cmd/echod
    if ($LASTEXITCODE -ne 0) { throw 'daemon build failed' }
    Pop-Location
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
$rootfs = Join-Path $out 'techo5-dot-rootfs.tar.gz'
& (Join-Path (Join-Path (Join-Path $root 'tools') 'linux') 'build-dot-rootfs.ps1') -Daemon (Join-Path $out 'echod-arm-dot') -Release $Version -Inputs $Inputs -Bluealsa $Bluealsa -Out $rootfs

Write-Host "== Bluetooth kernel, rescue packages and checksums"
Copy-Item -Force $Kernel (Join-Path $out 'techo5-dot-kernel-bt.zImage-dtb')
$apks = Get-ChildItem (Join-Path (Join-Path $Inputs 'apks-dot') '*.apk')
if (-not $apks) { throw "no rescue packages in $(Join-Path $Inputs 'apks-dot')" }
Push-Location (Join-Path $Inputs 'apks-dot')
tar -cf (Join-Path $out 'techo5-dot-rescue-apks.tar') ($apks | ForEach-Object { $_.Name })
if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'packing the rescue packages failed' }
Pop-Location

Write-Host "== signed manifest"
Push-Location (Join-Path $Techo5 'echod')
$from = "https://github.com/$repo/releases/download/$Version"
& $Go run ./cmd/mkmanifest -version $Version -title "TECHO5 Dot $Version" -notes $Notes `
    -release-url "https://github.com/$repo/releases/tag/$Version" -from $from `
    -arm-dot (Join-Path $out 'echod-arm-dot') -rootfs-arm-dot $rootfs `
    -out (Join-Path $out 'manifest.json') -sign-key $SignKey
if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'mkmanifest failed' }
Pop-Location
Get-Content (Join-Path $out 'manifest.json')

$names = 'echod-arm-dot', 'techo5-dot-rootfs.tar.gz', 'manifest.json', 'manifest.json.sig', 'techo5-dot-kernel-bt.zImage-dtb', 'techo5-dot-rescue-apks.tar'
# SHA256SUMS: what the installer checks the kernel and the rescue packages against.
$sums = $names | ForEach-Object { "$((Get-FileHash -Algorithm SHA256 (Join-Path $out $_)).Hash.ToLower())  $_" }
[IO.File]::WriteAllText((Join-Path $out 'SHA256SUMS'), ($sums -join "`n") + "`n")
$assets = ($names + 'SHA256SUMS') | ForEach-Object { Join-Path $out $_ }
if ($DryRun) {
    Write-Host "Dry run: release files are in $out; nothing published."
    return
}
Write-Host "== release $Version on $repo"
$Notes += "`n`nThe Bluetooth kernel (techo5-dot-kernel-bt.zImage-dtb, Linux 3.18.19, GPL-2.0) is built by tools/linux/build-kernel.sh from Amazon's GPL source for the Echo Dot 2nd gen (Echo_Dot_src-6.5.7.1) with the configuration and backports in tools/linux."
$ghArgs = @('release', 'create', $Version) + $assets + @('--repo', $repo, '--title', "TECHO5 Dot $Version", '--notes', $Notes)
if ($Prerelease) { $ghArgs += '--prerelease' }
& gh @ghArgs
if ($LASTEXITCODE -ne 0) { throw 'gh release create failed' }
Write-Host "published: https://github.com/$repo/releases/tag/$Version"
