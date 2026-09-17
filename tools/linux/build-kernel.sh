#!/bin/bash
# build-kernel.sh — the Dot's Fire OS 6 kernel (3.18.19, 32-bit ARM) with Bluetooth added, so
# btbridge can turn the vendor driver's /dev/stpbt into hci0 through /dev/vhci and BlueZ and
# bluez-alsa run as on TECHO5. Run inside WSL/Linux; no root needed.
#
#   tools/linux/build-kernel.sh [--stock] [-o DIR]
#
# Source: Amazon's GPL release for Echo Dot (2nd Generation), Echo_Dot_src-6.5.7.1-20251024
# (kernel/mediatek/mt8163/3.18_hl in platform.tar). Built from the running kernel's own config
# (docs/dumps/biscuit-fireos6-echolocal-boot-3.18.19.config) it reproduces the unit's recovery
# kernel (Fire OS 6574.1, 3.18.19-gecb8cb46060-dirty): the same strings apart from the version
# banner, and the two appended device trees byte for byte.
#
# Amazon's tarball only carries the files their build compiles, so net/bluetooth,
# drivers/bluetooth and include/net/bluetooth are missing. They come from upstream
# linux-3.18.19, only where Amazon's tree has no file (the Kconfig files it does have are
# identical to upstream's).
#
# 3.18.19's Bluetooth code predates years of fixes, and this is an always-on speaker running
# bluetoothd, so tools/linux/kernel-patches/NNNN-*.patch backports the remotely reachable ones
# on top of the copied files (the build stops if one does not apply). Each patch names its
# upstream commit, the stable backport it was taken from, its CVE and any 3.18 adaptation.
# Covered: L2CAP config parsing (CVE-2017-1000251 BlueBorne, CVE-2017-1000410, CVE-2019-3459,
# CVE-2019-3460, CVE-2022-42895, CVE-2022-45934), LE credit-based connect (CVE-2022-42896, invalid
# CIDs, le_credits lock leak), L2CAP channel lifetime and use-after-free races (CVE-2022-20566,
# CVE-2022-3564, CVE-2022-3640, CVE-2022-50386, CVE-2023-40283, CVE-2023-53297, CVE-2023-53305,
# CVE-2023-53827, CVE-2023-54214, CVE-2024-27399, CVE-2025-39860 and fixes without CVEs), A2MP
# info leak (CVE-2020-12352 BadChoice), HCI event length checks (CVE-2020-36386, CVE-2021-47620,
# advertising report checks), BR/EDR same-address/NULL link key pairing (CVE-2020-26555), HIDP
# (CVE-2018-9363), RFCOMM (CVE-2024-26903), SCO disconnect crashes, SMP unexpected key
# distribution. Not backported: KNOB (CVE-2019-9506; 3.18 never reads the BR/EDR encryption key
# size, so the check would refuse every encrypted link), BIAS (CVE-2020-10135; depends on the
# 4.x encryption-change rework and only affects Security Level 4), CVE-2020-26558/CVE-2021-0129
# and CVE-2020-12351/CVE-2020-24490 (3.18 has no LE Secure Connections, no sk_filter in L2CAP
# receive and no extended advertising). See docs/hardware.md (Kernels).
#
# Toolchain: AOSP arm-eabi-4.8 (branch lollipop-release), the compiler Amazon's
# build_kernel_config.sh names and the running kernel's banner reports. Cloned if missing.
#
# Environment:
#   WORK    scratch on a case-sensitive Linux filesystem (default ~/biscuit-build; the kernel
#           tree has names differing only in case, so not /mnt/<drive>)
#   DL      downloads (default WORK/dl)
#   OUTDIR  results (default build/kernel in this repository, where the installer looks; or -o)
#   TOOLCHAIN  arm-eabi-4.8 checkout (default ~/toolchain/arm-eabi-4.8)
#   REF     a recovery or boot image from the unit to compare the appended device trees against
#           (optional)
#
# --stock builds without the fragment (control build: should match the unit's kernel).
# Output: OUTDIR/zImage-dtb[-stock], the zImage with biscuit_evt.dtb and biscuit_min_evt.dtb
# appended, the same layout as the kernel in the recovery image. Use it with
#   python tools/linux/mkimage.py --kernel-image <unit>/recovery.img --kernel OUTDIR/zImage-dtb ...
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-$HOME/biscuit-build}
DL=${DL:-$WORK/dl}
OUTDIR=${OUTDIR:-$REPO/build/kernel}
TOOLCHAIN=${TOOLCHAIN:-$HOME/toolchain/arm-eabi-4.8}
REF=${REF:-}
STOCK=
while [ $# -gt 0 ]; do
	case "$1" in
	--stock) STOCK=1; shift;;
	-o) OUTDIR=$2; shift 2;;
	*) echo "unknown argument: $1" >&2; exit 1;;
	esac
done

AMZ_URL=https://fireos-audio-src.s3.amazonaws.com/dMUQiRDxI3hFuRDaF0WTumrp71/Echo_Dot_src-6.5.7.1-20251024.tar.bz2
AMZ_SHA=2f6b7eed8c09cecf7633f01909c6a4085bef691c29ed0d106c75e7b48c7b4721
LNX_URL=https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.18.19.tar.xz
LNX_SHA=3d80d3b8d98c3141d9e26f6c25d73575d688f1c1651b8076f0f2bfd76325b7c9
TC_REPO=https://android.googlesource.com/platform/prebuilts/gcc/linux-x86/arm/arm-eabi-4.8
CONFIG=$REPO/docs/dumps/biscuit-fireos6-echolocal-boot-3.18.19.config
FRAGMENT=$HERE/kernel-bt.fragment

fetch() { # url sha256
	local f=$DL/${1##*/}
	if [ ! -f "$f" ]; then
		curl -fL --retry 3 -o "$f.part" "$1"
		mv "$f.part" "$f"
	fi
	echo "$2  $f" | sha256sum -c --quiet - || { echo "checksum mismatch: $f" >&2; exit 1; }
}

mkdir -p "$DL" "$OUTDIR" "$WORK"
fetch "$AMZ_URL" "$AMZ_SHA"
fetch "$LNX_URL" "$LNX_SHA"
if [ ! -x "$TOOLCHAIN/bin/arm-eabi-gcc" ]; then
	git clone --depth 1 -b lollipop-release "$TC_REPO" "$TOOLCHAIN"
fi

# Fresh trees every run: Amazon's kernel, then the missing Bluetooth sources from upstream.
SRC=$WORK/src
rm -rf "$WORK/amz" "$WORK/linux-3.18.19" "$SRC"
mkdir -p "$WORK/amz"
tar -xjf "$DL/${AMZ_URL##*/}" -C "$WORK/amz" platform.tar prebuilt
tar -xf "$WORK/amz/platform.tar" -C "$WORK/amz" kernel/mediatek/mt8163/3.18_hl device/amazon/common/verity
mv "$WORK/amz/kernel/mediatek/mt8163/3.18_hl" "$SRC"
rm "$WORK/amz/platform.tar"
if [ -z "$STOCK" ]; then
	tar -xJf "$DL/${LNX_URL##*/}" -C "$WORK" linux-3.18.19/net/bluetooth linux-3.18.19/drivers/bluetooth \
		linux-3.18.19/include/net/bluetooth
	for d in net/bluetooth drivers/bluetooth include/net/bluetooth; do
		(cd "$WORK/linux-3.18.19" && find "$d" -type f) | while read -r f; do
			if [ -e "$SRC/$f" ]; then
				cmp -s "$SRC/$f" "$WORK/linux-3.18.19/$f" || echo "note: Amazon's $f differs from upstream; kept Amazon's"
			else
				mkdir -p "$SRC/$(dirname "$f")"
				cp "$WORK/linux-3.18.19/$f" "$SRC/$f"
			fi
		done
	done
	# Security fixes backported onto that Bluetooth code, in order; any patch that does not apply
	# exactly (offsets allowed, no fuzz) stops the build.
	n=0
	for p in "$HERE"/kernel-patches/[0-9][0-9][0-9][0-9]-*.patch; do
		[ -f "$p" ] || { echo "no patches found in $HERE/kernel-patches" >&2; exit 1; }
		if ! tr -d '\r' < "$p" | patch -d "$SRC" -p1 -F0 -N -s --dry-run --no-backup-if-mismatch; then
			echo "patch does not apply: ${p##*/}" >&2
			exit 1
		fi
		tr -d '\r' < "$p" | patch -d "$SRC" -p1 -F0 -N -s --no-backup-if-mismatch
		n=$((n + 1))
	done
	echo "applied $n Bluetooth patches from kernel-patches/"
fi

SUFFIX=-bt
[ -z "$STOCK" ] || SUFFIX=-stock
KOUT=$WORK/out$SUFFIX
rm -rf "$KOUT"
mkdir -p "$KOUT/include/generated"
# What Amazon's build_kernel.sh puts in its output directory before building: the trapz header
# and the certificate CONFIG_SYSTEM_TRUSTED_KEYRING compiles in.
cp "$WORK/amz/prebuilt/include/generated/trapz_generated_kernel.h" "$KOUT/include/generated/"
cp "$WORK/amz/device/amazon/common/verity/amazon_verity.x509.pem" "$KOUT/verity-keys"
tr -d '\r' < "$CONFIG" > "$KOUT/.config"
[ -n "$STOCK" ] || tr -d '\r' < "$FRAGMENT" >> "$KOUT/.config"

export ARCH=arm CROSS_COMPILE=$TOOLCHAIN/bin/arm-eabi-
# -fcommon: 3.18's dtc defines yylloc twice, which a current host gcc refuses to link.
MAKEARGS=(-C "$SRC" O="$KOUT" HOSTCFLAGS=-fcommon USE_TRAPZ=true
	KBUILD_BUILD_USER=techo5 KBUILD_BUILD_HOST=techo5-dot LOCALVERSION=$SUFFIX)
make -s "${MAKEARGS[@]}" olddefconfig
if [ -z "$STOCK" ]; then
	bad=0
	while IFS= read -r line; do
		line=${line%$'\r'}
		case "$line" in CONFIG_*) grep -qx "$line" "$KOUT/.config" || { echo "not applied: $line" >&2; bad=1; };; esac
	done < "$FRAGMENT"
	[ $bad = 0 ] || exit 1
fi
echo "config changes against the running kernel:"
diff <(tr -d '\r' < "$CONFIG" | grep -v '^#' | grep . | sort) <(grep -v '^#' "$KOUT/.config" | grep . | sort) || true

make "${MAKEARGS[@]}" -j"$(nproc)" zImage-dtb
echo "release: $(cat "$KOUT/include/config/kernel.release")"

name=zImage-dtb${STOCK:+-stock}
cp "$KOUT/arch/arm/boot/zImage-dtb" "$OUTDIR/$name"
cp "$KOUT/.config" "$OUTDIR/$name.config"
cp "$KOUT/System.map" "$OUTDIR/$name.System.map"
ls -l "$OUTDIR/$name"
sha256sum "$OUTDIR/$name"

if [ -n "$REF" ]; then
	python3 - "$REF" "$OUTDIR/$name" <<'EOF'
import struct, sys
def dtbs(b):  # device trees appended after the zImage, whose end offset is stored at 0x2c
    i = struct.unpack("<I", b[0x2c:0x30])[0]; out = []
    while b[i:i+4] == b"\xd0\x0d\xfe\xed":
        n = struct.unpack(">I", b[i+4:i+8])[0]; out.append(b[i:i+n]); i += n
    return out
img = open(sys.argv[1], "rb").read()
ks, ps = struct.unpack("<I", img[8:12])[0], struct.unpack("<I", img[36:40])[0]
ref, new = dtbs(img[ps:ps+ks]), dtbs(open(sys.argv[2], "rb").read())
same = len(ref) == len(new) and all(a == b for a, b in zip(ref, new))
print("appended device trees:", [len(x) for x in new], "identical to the reference" if same else "DIFFER from the reference")
sys.exit(0 if same else 1)
EOF
fi
