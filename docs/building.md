# Building TECHO5 Dot yourself

You don't need any of this to install or update a Dot. `tools/install-dot.ps1` and Home Assistant's
update card use the signed releases. This page is for changing the daemon, the kernel or the root
filesystem. The shared parts (the daemon, Go, the environment variables) are in TECHO5's
[docs/building.md](https://github.com/HuskerMinion/techo5/blob/main/docs/building.md).

## Checkouts and tools

```
git clone https://github.com/HuskerMinion/techo5
git clone https://github.com/HuskerMinion/techo5-dot
```

Side by side, as above, the scripts find each other; otherwise set `TECHO5` to the TECHO5 checkout.
You need Go, Python 3 and PowerShell 7 (`pwsh`) on Windows, Linux or macOS. The kernel and bluez-alsa
build in Linux or WSL (Ubuntu 24.04), with no root needed.

Everything goes into git-ignored folders in this checkout: `inputs/` (`TECHO5_INPUTS`), `build/`
(`TECHO5_WORK`) and `backups/` (`TECHO5_BACKUPS`).

## 1. Inputs

```
cd techo5
pwsh ./tools/fetch-inputs.ps1 -Device dot -Dot ../techo5-dot -Out ../techo5-dot/inputs
```

That fetches the Alpine base image, `busybox.static`, the wake word models, and the packages listed in
[tools/linux/packages-rescue.txt](../tools/linux/packages-rescue.txt) (`inputs/apks-dot/`: the rescue
environment's Wi-Fi, SSH and firewall) and [tools/linux/packages-bt.txt](../tools/linux/packages-bt.txt)
(`inputs/apks-bt-dot/`: BlueZ, bluez-alsa's libraries and codecs), all from Alpine v3.24 armv7.

No firmware is an input: each Dot adopts its own firmware from its Fire OS system partition into the
slot store on first boot (`tools/linux/rootfs/etc/techo5/boot.sh`).

## 2. The Bluetooth kernel

The stock Fire OS kernel has no Bluetooth stack. [tools/linux/build-kernel.sh](../tools/linux/build-kernel.sh)
rebuilds it from Amazon's GPL source for Fire OS 6574.1 with Bluetooth added and the security
backports in [tools/linux/kernel-patches](../tools/linux/kernel-patches). It downloads the source, the
upstream Bluetooth files and the toolchain itself; the header explains each.

```
# in Linux or WSL, from this checkout
bash tools/linux/build-kernel.sh          # -> build/kernel/zImage-dtb
bash tools/linux/build-kernel.sh --stock  # the control build: should match the unit's own kernel
```

## 3. bluez-alsa

Alpine's `bluealsa` crashes at start-up on the Dot.
[tools/linux/build-bluealsa.sh](../tools/linux/build-bluealsa.sh) builds 4.3.1 the way Alpine does, plus
the upstream fix, from checksummed downloads:

```
bash tools/linux/build-bluealsa.sh        # -> build/bluealsa/bluealsa
```

## 4. The daemon and tools

`tools/release-dot.ps1` builds all three. By hand, for a test:

```
cd ../techo5/echod
GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -tags dot -o ../../techo5-dot/bin/echod-dot ./cmd/echod
cd .. && GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -o ../techo5-dot/bin/btbridge ./cmd/btbridge
cd ../techo5-dot && GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -o bin/wmtup ./cmd/wmtup
```

(In PowerShell, set `$env:GOOS='linux'; $env:GOARCH='arm'; $env:GOARM='7'; $env:CGO_ENABLED='0'` first.)

## 5. The root filesystem

```
pwsh ./tools/linux/build-dot-rootfs.ps1 -Daemon bin/echod-dot -Release v0.0.0-test -Out build/rootfs.tar.gz
```

[mkrootfs.py](../tools/linux/mkrootfs.py) unpacks the Alpine base and the packages, adds the binaries
and [tools/linux/rootfs](../tools/linux/rootfs), and writes the tarball with root ownership, with no
root, QEMU or WSL needed.

## 6. Installing your build

```
pwsh ./tools/install-dot.ps1 -Serial <serial> -FromSource -Daemon bin/echod-dot -DryRun
pwsh ./tools/install-dot.ps1 -Serial <serial> -FromSource -Daemon bin/echod-dot
```

`-FromSource` takes the root filesystem, packages and busybox from `inputs/` and the kernel from
`build/kernel/zImage-dtb` (or `-Kernel`, or `-NoBluetoothKernel`). On a Dot already running TECHO5,
a new kernel goes in with `tools/update-boot.ps1 -Kernel build/kernel/zImage-dtb`, and a new daemon
can be copied over `/usr/local/bin/techo5` over SSH for a quick test.

## 7. Releases (maintainer)

```
pwsh ./tools/release-dot.ps1 -Version v0.6.0 -Notes "..." -DryRun
```

It builds the daemon from the TECHO5 checkout, `btbridge` and `wmtup`, the root filesystem, and
signs the manifest with `TECHO5_SIGN_KEY`. It publishes the kernel, the rescue packages and
`SHA256SUMS` with it, which is what the installer downloads.
