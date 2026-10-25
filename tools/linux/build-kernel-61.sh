#!/bin/bash
# build-kernel-61.sh — the Echo 2nd gen's (radar) Linux 6.1 kernel: LibreEcho's (GPL-2.0,
# https://github.com/aslater3/LibreEcho-Linux) at a pinned commit, with kernel-61-radar.fragment on top
# of its mt8163_arm32_defconfig. Run in WSL/Linux; no root needed. Needs an ARM cross compiler: the
# Arm GNU Toolchain (arm-none-linux-gnueabihf) on PATH, or CROSS_COMPILE set.
#
#   tools/linux/build-kernel-61.sh [-o DIR]
#
# Output: DIR/techo5-dot-kernel-61-radar.zImage-dtb, the zImage with the Echo 2nd gen's (EVT) device
# tree appended in a 64 KiB slot, which is how the bootloader finds it. update-boot.py takes it with
# --kernel and packs it into the unit's own recovery header, as it does the 3.18 kernel: on the recovery
# partition the bootloader starts it from there only packed that way (no MediaTek header on the kernel,
# the ramdisk where the unit's recovery image has it), measured on a Dot 2 on 2026-10-25.
#
# The unit's startup needs the /lib/firmware links (init, boot.sh) for this kernel's Wi-Fi driver.
set -euo pipefail
ROOT=${TECHO5_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}
OUT=$ROOT/build/kernel-61
REPO=https://github.com/aslater3/LibreEcho-Linux
COMMIT=56a648f00166012bb1dce18ef9636adb5c8dcafc
WORK=${WORK:-$HOME/techo5-kernel-61}
J=${J:-$(nproc)}
while [ $# -gt 0 ]; do
	case "$1" in
	-o) OUT=$2; shift 2;;
	*) echo "unknown argument: $1" >&2; exit 1;;
	esac
done
CROSS_COMPILE=${CROSS_COMPILE:-arm-none-linux-gnueabihf-}
command -v ${CROSS_COMPILE}gcc >/dev/null || { echo "${CROSS_COMPILE}gcc is not on PATH" >&2; exit 1; }

if [ ! -d "$WORK/src/.git" ]; then
	git init -q "$WORK/src"
	git -C "$WORK/src" remote add origin "$REPO"
fi
git -C "$WORK/src" fetch -q --depth 1 origin "$COMMIT"
git -C "$WORK/src" checkout -q --detach FETCH_HEAD
[ "$(git -C "$WORK/src" rev-parse HEAD)" = "$COMMIT" ] || { echo "not $COMMIT" >&2; exit 1; }

K=$WORK/out
mk() { make -s -C "$WORK/src" O="$K" ARCH=arm CROSS_COMPILE="$CROSS_COMPILE" LD="${CROSS_COMPILE}ld.bfd" "$@"; }
mk mt8163_arm32_defconfig
"$WORK/src/scripts/kconfig/merge_config.sh" -m -O "$K" "$K/.config" "$ROOT/tools/linux/kernel-61-radar.fragment" >/dev/null
mk olddefconfig
# Every line of the fragment has to have survived olddefconfig: a dependency it misses would drop it.
while IFS= read -r line; do
	case "$line" in ''|'#'*) continue;; esac
	k=${line%%=*} v=${line#*=}
	if [ "$v" = n ]; then
		grep -q "^# $k is not set" "$K/.config" || { echo "$k is set, the fragment says n" >&2; exit 1; }
	else
		grep -qx "$k=$v" "$K/.config" || { echo "$line did not take" >&2; exit 1; }
	fi
done < "$ROOT/tools/linux/kernel-61-radar.fragment"
mk -j"$J" zImage dtbs

z=$K/arch/arm/boot/zImage dtb=$K/arch/arm/boot/dts/libreecho-radar-puffin.dtb
size=$(stat -c %s "$dtb")
[ "$size" -le 65536 ] || { echo "the device tree is $size bytes, more than its 64 KiB slot" >&2; exit 1; }
mkdir -p "$OUT"
{ cat "$z" "$dtb"; head -c $((65536 - size)) /dev/zero; } > "$OUT/techo5-dot-kernel-61-radar.zImage-dtb"
cp "$K/.config" "$OUT/techo5-dot-kernel-61-radar.config"
ls -la "$OUT"
sha256sum "$OUT/techo5-dot-kernel-61-radar.zImage-dtb"
