# Building TECHO5 Dot yourself

You don't need any of this to install or update a Dot. `tools/install-dot.py` and Home Assistant's
update card use the signed releases. This page is for changing the daemon, the kernel or the root
filesystem.

## 1. Set up your computer

Everything is Go, Python 3 and bash. The kernel and bluez-alsa build on Linux.

**Linux** (Ubuntu 24.04 or Debian; other distributions have the same packages under similar names):

```
sudo apt install git python3 build-essential bc bison flex libssl-dev curl xz-utils bzip2
```

and Go 1.26 or later from [go.dev/dl](https://go.dev/dl/) (distribution packages are often older).

**Windows:** install [Git for Windows](https://git-scm.com/download/win) (Git Bash runs the `.sh`
scripts), [Go](https://go.dev/dl/) and [Python 3](https://www.python.org/downloads/). For the kernel
and bluez-alsa, install WSL with Ubuntu (`wsl --install -d Ubuntu`) and the Linux packages above inside
it. On Windows, type `python` where this page says `python3`.

**macOS:** `xcode-select --install` (git, bash, Python 3), then `brew install go`. Everything but the
kernel and bluez-alsa builds on macOS; for those two, use a Linux machine or virtual machine, or the
release's kernel and root filesystem.

Get the code. The Dot's daemon lives in TECHO5, so both repositories go side by side:

```
git clone https://github.com/HuskerMinion/techo5
git clone https://github.com/HuskerMinion/techo5-dot
cd techo5-dot
```

## 2. Fetch the inputs

```
python3 ../techo5/tools/fetch-inputs.py --device dot --dot . --out inputs
```

That fills `inputs/` (git-ignored) with Alpine's base image, `busybox.static`, the wake word models, and
the packages in [tools/linux/packages-rescue.txt](../tools/linux/packages-rescue.txt) (`apks-dot/`: the
rescue environment's Wi-Fi, SSH and firewall), [tools/linux/packages-bt.txt](../tools/linux/packages-bt.txt)
(`apks-bt-dot/`: BlueZ, bluez-alsa's libraries and codecs) and
[tools/linux/packages-rootfs.txt](../tools/linux/packages-rootfs.txt) (`apks-rootfs-dot/`: what only the
running system needs, which the rescue initramfs is too small to carry).

No firmware is an input: each Dot adopts its own from its Fire OS system partition into the slot store
on first boot (`tools/linux/rootfs/etc/techo5/boot.sh`).

## 3. The daemon and tools

In bash (Linux, macOS, or Git Bash on Windows):

```
export GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0
(cd ../techo5/echod && go build -tags dot -o ../../techo5-dot/bin/echod-dot ./cmd/echod)
(cd ../techo5 && go build -o ../techo5-dot/bin/btbridge ./cmd/btbridge)
go build -o bin/wmtup ./cmd/wmtup
unset GOOS GOARCH GOARM CGO_ENABLED
```

## 4. The Bluetooth kernel (Linux)

The stock Fire OS kernel has no Bluetooth stack. [tools/linux/build-kernel.sh](../tools/linux/build-kernel.sh)
rebuilds it from Amazon's GPL source for Fire OS 6574.1 with Bluetooth added and the security backports
in [tools/linux/kernel-patches](../tools/linux/kernel-patches). It downloads the source, the upstream
Bluetooth files and the toolchain itself; the header explains each. On Linux, or inside WSL's Ubuntu:

```
bash tools/linux/build-kernel.sh          # -> build/kernel/zImage-dtb
```

## 5. bluez-alsa (Linux)

Alpine's `bluealsa` crashes at start-up on the Dot. [tools/linux/build-bluealsa.sh](../tools/linux/build-bluealsa.sh)
builds 4.3.1 the way Alpine does, plus the upstream fix, from checksummed downloads:

```
bash tools/linux/build-bluealsa.sh        # -> build/bluealsa/bluealsa
```

Skipping steps 4 and 5? Take the release's kernel (the installer's default), and its bluez-alsa out of
the release's root filesystem (`usr/bin/bluealsa` in `techo5-dot-rootfs.tar.gz`).

## 6. The root filesystem

```
python3 tools/linux/build-dot-rootfs.py --daemon bin/echod-dot --release v0.0.0-test --out build/rootfs.tar.gz
```

[mkrootfs.py](../tools/linux/mkrootfs.py) unpacks the Alpine base and the packages, adds the binaries
and [tools/linux/rootfs](../tools/linux/rootfs), and writes the tarball with root ownership. It needs no
root, QEMU or Linux.

## 7. Install your build

On a Dot in TWRP or rooted Fire OS, the installer takes your files in place of the release's:

```
python3 tools/install-dot.py --serial <serial> --rootfs build/rootfs.tar.gz --kernel build/kernel/zImage-dtb --dry-run
python3 tools/install-dot.py --serial <serial> --rootfs build/rootfs.tar.gz --kernel build/kernel/zImage-dtb
```

On a Dot already running TECHO5 (SSH switched on in Home Assistant, with a key):

- a new kernel: `python3 tools/update-boot.py --serial <serial> --address <address> --kernel build/kernel/zImage-dtb`;
- a new daemon, until the next reboot:
  `scp bin/echod-dot root@<address>:/tmp/echod-test` then
  `ssh root@<address> 'mount --bind /tmp/echod-test /usr/local/bin/echod && killall echod'`.

## Package versions

The package lists name exact Alpine versions. Alpine keeps only the newest build of each package, so an
old version eventually disappears from its mirror; `fetch-inputs.py` then takes the newest and says so.
Releases don't depend on this: the rescue packages are published with each release, and the lists are
brought up to date (and tested) before a release.

## Releases (maintainer)

```
pwsh ./tools/release-dot.ps1 -Version v0.6.0 -Notes "..." -DryRun
```

A PowerShell script for the maintainer's Windows machine: it builds the daemon from the TECHO5
checkout, `btbridge`, `wmtup` and the root filesystem, and publishes the kernel and the rescue
packages with it. The manifest names all of them, with their sha256 and size, and is signed with
`TECHO5_SIGN_KEY`: that signature is the installer's only check on what it downloads. `SHA256SUMS` is
published too, for checking a file by hand; nothing signs it, so no installer reads it.

## Where things default

Everything goes into git-ignored folders in this checkout, and each can be moved with an environment
variable: `inputs/` (`TECHO5_INPUTS`), `build/` (`TECHO5_WORK`), `backups/` (`TECHO5_BACKUPS`).
