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
go build -o bin/ubiblock ./cmd/ubiblock   # for the Echo Dot 5th gen's root filesystem
unset GOOS GOARCH GOARM CGO_ENABLED
```

AirPlay 2 comes from TECHO5's `tools/linux/build-shairport-sync.sh` (Linux or WSL, with zig), which writes
`bin/techo5-shairport-sync-arm` and `bin/techo5-nqptp-arm` there. Copy them into this repository's `bin/` as
`techo5-shairport-sync` and `techo5-nqptp`; without them the image has the classic AirPlay receiver only, and
`tools/release-dot.ps1` refuses to publish it.

## 4. The Bluetooth kernel (Linux)

The stock Fire OS kernel has no Bluetooth stack. [tools/linux/build-kernel.sh](../tools/linux/build-kernel.sh)
rebuilds it from Amazon's GPL source for Fire OS 6574.1 with Bluetooth added and the security backports
in [tools/linux/kernel-patches](../tools/linux/kernel-patches). It downloads the source, the upstream
Bluetooth files and the toolchain itself; the header explains each. On Linux, or inside WSL's Ubuntu:

```
bash tools/linux/build-kernel.sh          # -> build/kernel/zImage-dtb
```

`--board radar` builds the Echo 2nd gen's instead, into `build/kernel-radar/`
([hardware-radar.md](hardware-radar.md)). A release carries both.

The Echo 2nd gen's optional Linux 6.1 kernel (LibreEcho's) needs the Arm GNU Toolchain
(`arm-none-linux-gnueabihf`) on PATH:

```
bash tools/linux/build-kernel-61.sh       # -> build/kernel-61/techo5-dot-kernel-61-radar.zImage-dtb
```

A release carries it too (`release-dot.ps1 -KernelRadar61`).

## 5. bluez-alsa (Linux)

Alpine's `bluealsa` crashes at start-up on the Dot. [tools/linux/build-bluealsa.sh](../tools/linux/build-bluealsa.sh)
builds 4.3.1 the way Alpine does, plus the upstream fix, from checksummed downloads:

```
bash tools/linux/build-bluealsa.sh        # -> build/bluealsa/bluealsa
```

Skipping steps 4 and 5? Take the release's kernel (the installer's default), and its bluez-alsa out of
the release's root filesystem (`usr/bin/bluealsa` in `techo5-dot-rootfs.tar.gz`).

### The Echo Dot 3rd gen's Bluetooth modules and wpa_supplicant (Linux)

The Echo Dot 3rd gen keeps its own kernel, so it needs no kernel from step 4. Its Bluetooth stack is
built as modules for that kernel instead, and its wpa_supplicant carries one fix
([hardware-donut.md](hardware-donut.md)):

```
bash tools/linux/build-bt-modules.sh --source-tarball build/techo5-donut-bt-modules-source.tar.xz
mkdir -p build/bt-modules && cp /tmp/techo5-donut-kernel/modules/*.ko build/bt-modules/
bash tools/linux/build-wpa-supplicant.sh  # -> build/wpa_supplicant/wpa_supplicant
```

## 6. The root filesystem

```
python3 tools/linux/build-dot-rootfs.py --daemon bin/echod-dot --release v0.0.0-test --out build/rootfs.tar.gz
```

For the Echo Dot 3rd gen, build the daemon with `-tags dot,donut` and add `--board donut`; it takes
the modules and wpa_supplicant from `build/` and adds
[tools/linux/rootfs-donut](../tools/linux/rootfs-donut) over the Dot 2's files. The Echo Input runs
that same build and root filesystem.

[mkrootfs.py](../tools/linux/mkrootfs.py) unpacks the Alpine base and the packages, adds the binaries
and [tools/linux/rootfs](../tools/linux/rootfs), and writes the tarball with root ownership. It needs no
root, QEMU or Linux.

Spotify Connect and AirPlay 2 are in the image only if `bin/` has their programs: TECHO5's
`tools/linux/build-librespot.sh` output as `bin/techo5-librespot`, and the output of
`build-shairport-sync.sh` as `bin/techo5-shairport-sync` and `bin/techo5-nqptp`. Without them the script
says so and builds the image without that feature.

## 7. Install your build

On a Dot in TWRP or rooted Fire OS, the installer takes your files in place of the release's:

```
python3 tools/install-dot.py --serial <serial> --rootfs build/rootfs.tar.gz --kernel build/kernel/zImage-dtb --dry-run
python3 tools/install-dot.py --serial <serial> --rootfs build/rootfs.tar.gz --kernel build/kernel/zImage-dtb
```

On a Dot already running TECHO5 (SSH switched on in Home Assistant, with a key):

- a new kernel: `python3 tools/update-boot.py --serial <serial> --address <address> --kernel build/kernel/zImage-dtb`;
- a new daemon, until the next reboot:
  `scp bin/echod-dot root@<address>:/run/echod-test` then
  `ssh root@<address> 'mount --bind /run/echod-test /usr/local/bin/echod && killall echod'`. It goes in
  `/run`, root's alone and not capped, rather than `/tmp`, which is bounded at 32 MB, less than a daemon,
  and which anyone on the device can write to.

## The Echo Dot 5th gen's first Linux boot (bring-up)

The Echo Dot 5th gen (2022, `cannoli`) installs with `tools/install-cannoli.py`
([install-cannoli.md](install-cannoli.md)), which runs the steps below for you. Its Linux image goes in
slot B, beside Fire OS in slot A. The image's ramdisk boots a root filesystem from a store on userdata
(see Root filesystem below). When there is none, or it is broken, the ramdisk stays up as a rescue
environment. The rescue has a root shell on the UART (921600 8N1) and over USB (a COM port), a log on
userdata, Wi-Fi on the network Fire OS saved, and SSH with an `authorized_keys` on userdata.

It is built from the unit's own folder: `bundle/` (the OS bundle cannoli-os-builder made for it) and
`m0/` (its backup, for the Wi-Fi driver and firmware). With the inputs of step 2, first build
`cannoli_hwfixup.ko` in WSL (see Kernel modules below), then the image:

```
FILL=<6.5.5.5 drop>/kernel/mediatek/5.4 \
  tools/linux/build-cannoli-hwfixup.sh <6.5.7.1 drop>/platform.tar <unit>/stock-8349-parts/boot.img <unit>/m0/modules/*.ko
python3 tools/linux/build-cannoli-image.py --unit ../cannoli-<serial>
```

U-Boot leaves the ring, the amplifier and the microphone converter disabled on a slot-B boot, and the
module creates their devices from the device tree.

The image is the unit's stock FIT with only the ramdisk replaced. The kernel and device tree are
signed and the ramdisk is not, so the signature still holds. The script checks that everything else is
the stock FIT byte for byte, and that the image fits within what the unit's U-Boot reads of `boot_b`.
It is the unit's own kernel and driver, so it is never redistributable. The script prints how to
write it to slot B and how to get TWRP back (`bundle/boot_b.img`). Holding Volume-Up at power-on boots
it; any other power-on boots Fire OS.

### Bluetooth

The stock kernel is built without a Bluetooth stack, and BlueZ does not fit in the ramdisk. So
Bluetooth is a payload on userdata, `/data/techo5-linux/bt.tar.gz`, that the init unpacks and starts
after Wi-Fi. Build the stack's modules in WSL, then the payload:

```
FILL=<6.5.5.5 drop>/kernel/mediatek/5.4 \
  tools/linux/build-cannoli-bt-modules.sh [--source-tarball build/techo5-cannoli-bt-modules-source.tar.xz] <6.5.7.1 drop>/platform.tar <unit>/stock-8349-parts/boot.img <unit>/m0/modules/*.ko
GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -trimpath -o ../techo5-dot/build/btbridge/btbridge ./cmd/btbridge   # in TECHO5
python3 tools/linux/build-cannoli-bt-payload.py --unit ../cannoli-<serial>
```

A root filesystem that is passed on carries these GPL-2.0 modules, so it goes with their source:
`--source-tarball` writes it (NOTICE).

The radio driver stays the unit's own `btmtksdio.ko`: it makes `/dev/stpbt` without any stack, and
`btbridge` puts it behind `hci0` through `hci_vhci`, as on the Echo Dot 3rd gen.

### Kernel modules

The kernel has `MODVERSIONS` on: it refuses a module whose imported CRCs differ from its own. Both
module scripts share `tools/linux/cannoli-kernel.sh`, which builds the whole GPL tree once (about 8
minutes; the second script then takes seconds). It uses the shipped kernel's own config, read out of
the stock `boot.img`, and compares every CRC our modules import with the shipped kernel's, read by
`tools/linux/kcrc.py` (from echo-dot-assist, MIT; see NOTICE). A CRC that differs means a struct differs between the GPL tree and
the shipped kernel, and the build stops.

Use Amazon's 6.5.7.1 drop (`Echo_Dot_5th_Gen_src-6.5.7.1-20251027`). With it every CRC matches
(Bluetooth's 265, the hwfixup's 16), while the 6.5.5.5 tree differs in half of them. The 6.5.7.1 drop is
trimmed to what Amazon compiled and has no Bluetooth sources or `scripts/config`, so `FILL` adds the
files it lacks from the full 6.5.5.5 drop, never replacing one. Plain GCC works: the script hides the
attributes Amazon's GCC 4.9 didn't have, since genksyms hashes them too.

Copy the payload over SSH from slot-B Linux and start it (or reboot into slot B):

```
cmd /c "ssh root@<address> ""cat > /data/techo5-linux/bt.tar.gz"" < techo5-cannoli-bt.tar.gz"
ssh root@<address> /sbin/techo5-bt-up
```

The payload is only for the rescue environment now: the root filesystem below carries BlueZ,
bluez-alsa, btbridge and the stack's modules itself.

### Root filesystem

The root filesystem is the Echo Dot 3rd gen's layout: a store on userdata,
`/data/techo5-store/techo5`, with slots `a` and `b` that `slotctl` manages
(`tools/linux/rootfs/usr/local/sbin/slotctl`). The slot-B ramdisk (`tools/linux/init-cannoli`) picks
the slot and `switch_root`s into it. Its `boot.sh` (`tools/linux/rootfs-cannoli`) brings up USB, Wi-Fi,
Bluetooth and the network, then echod starts. With `build/echod-cannoli` built in TECHO5
(`-tags dot,cannoli`, see TECHO5's building.md), and the Bluetooth modules and btbridge from above:

```
python3 tools/linux/build-dot-rootfs.py --board cannoli --daemon build/echod-cannoli --release v0.0.0-cannoli --out build/techo5-cannoli-rootfs.tar.gz
python3 tools/linux/push-cannoli-rootfs.py --host <address> build/techo5-cannoli-rootfs.tar.gz
```

The push copies the tarball over SSH to a unit running slot-B Linux (rescue or a slot). It unpacks it
into the slot that isn't running, on trial, and makes that slot the next to boot. Then power on holding
Volume-Up. Without the patched wpa_supplicant (`build-wpa-supplicant.sh`) the build stops: Alpine's
does not join on this driver. bluez-alsa is `build-bluealsa.sh`'s when built, else Alpine's.

Nothing of the unit's own is in the tarball. The Wi-Fi and Bluetooth driver and firmware are in the
slot-B ramdisk, built from the unit's backup. On every slot boot the ramdisk copies whatever differs
into the store's `modules/` and `firmware/`, where both slots find them. The Bluetooth stack's
modules are built from Amazon's GPL drop for the stock kernel, so they are in the tarball.

The ramdisk stays in rescue when:

- there is no store or no slot to boot, or the slot lacks `/sbin/init`, `boot.sh` or echod;
- `slotctl rescue` was run (for one boot);
- five slot boots in a row never became healthy (`boot.sh` counts a boot healthy once echod has stayed
  up, and resets the count in the store's `tries`). `/sbin/techo5-retry` in the rescue clears it.

A slot whose echod does not stay up starts dropbear itself, so it can still be looked at over SSH.
Its trial runs out after two boots, and `slotctl next` then falls back to the other slot if that one is
good. `to-fireos` reboots, which is Fire OS unless Volume-Up is held.

### Wi-Fi address

Linux in slot B has no MediaTek `nvram_daemon`, so the Wi-Fi driver makes up a new address at every
load, and DHCP hands out a new lease each boot. Before wpa_supplicant starts, the rescue (`wifi_mac` in
`init-cannoli`) and the root filesystem (`techo5-wlan-mac`) both set `wlan0` to a stable address. They
use the factory one from idme (`/proc/idme/mac_addr`) when the unit has it. Otherwise they make a
locally administered one once and keep it in `/data/techo5-linux/wlan-mac`, which both read.

## Package versions

The package lists name exact Alpine versions. Alpine keeps only the newest build of each package, so an
old version eventually disappears from its mirror; `fetch-inputs.py` then takes the newest and says so.
Releases don't depend on this: the rescue packages are published with each release, and the lists are
brought up to date (and tested) before a release.

## Releases (maintainer)

```
pwsh ./tools/release-dot.ps1 -Version v0.6.0 -Notes "..." -DryRun
```

A PowerShell script for the maintainer's Windows machine. A real release (not `-DryRun`) needs
`-ThirdPartySources`, the archive from TECHO5's `tools/linux/third-party-sources.sh`, published as
`techo5-third-party-sources.tar.gz`. The root filesystem asset is named
`techo5-dot-rootfs-<version>.tar.gz`. The script builds the daemon from the TECHO5
checkout, `btbridge`, `wmtup` and both root filesystems (the Dot 2's and the Echo Dot 3rd gen's), and
publishes the kernels, the rescue packages and the Echo Dot 3rd gen's module source with them. The manifest names all of them, with their sha256 and size, and is signed with
`TECHO5_SIGN_KEY`: that signature is the installer's only check on what it downloads. `SHA256SUMS` is
published too, for checking a file by hand; nothing signs it, so no installer reads it.

## Where things default

Everything goes into git-ignored folders in this checkout, and each can be moved with an environment
variable: `inputs/` (`TECHO5_INPUTS`), `build/` (`TECHO5_WORK`), `backups/` (`TECHO5_BACKUPS`).
