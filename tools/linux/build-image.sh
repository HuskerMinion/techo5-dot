#!/bin/bash
# build-image.sh — the Echo Dot's first Linux boot image (porting plan M2).
#
# Takes the kernel and header from the unit's own recovery image and replaces the ramdisk with an
# Alpine armv7 initramfs running tools/linux/init. The recovery partition, not a boot slot: the
# bootloader adds skip_initramfs when it boots a boot slot, and this kernel honours it, so a boot
# slot would ignore the ramdisk and boot Android's system instead. Recovery's ramdisk is booted as
# it is, which is how TWRP runs there.
#
#   tools/linux/build-image.sh [-o out.img] [--twrp recovery-twrp.img] [--stay-minutes 15]
#
# An image is the device's own kernel plus our ramdisk, so it is never redistributable: build it
# from a backup of the unit it will run on.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
UNIT=${TECHO5_DOT_UNIT:-/d/platform-tools/echodot/<serial>}
INPUTS=${TECHO5_INPUTS:-/d/platform-tools/echoshow/linux-image}
REF=$UNIT/recovery.img
OUT=$UNIT/techo5-dot-linux-first.img
STAY=15

while [ $# -gt 0 ]; do
	case "$1" in
	-o) OUT=$2; shift 2;;
	--twrp) REF=$2; shift 2;;
	--stay-minutes) STAY=$2; shift 2;;
	*) echo "unknown argument: $1" >&2; exit 1;;
	esac
done

ROOTFS=$(ls "$INPUTS"/alpine-minirootfs-*-armv7.tar.gz | head -1)
BUSYBOX=$INPUTS/busybox.static

for f in "$REF" "$ROOTFS" "$BUSYBOX"; do
	[ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

WMTUP=${TECHO5_WMTUP:-$HERE/../../bin/wmtup}
EXTRA=()
if [ -f "$WMTUP" ]; then
	EXTRA+=(--add "$WMTUP=/usr/local/bin/wmtup")
else
	echo "note: no $WMTUP, so this image has no Wi-Fi bring-up"
	echo "      build it with: GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -o bin/wmtup ./cmd/wmtup"
fi

# The supplicant and its libraries, unpacked into the initramfs. Alpine's own armv7 packages, so they
# match the rootfs; without them wlan0 comes up and joins nothing.
APKDIR=${TECHO5_APKS:-$INPUTS/apks-dot}
for a in "$APKDIR"/*.apk; do
	[ -f "$a" ] && EXTRA+=(--apk "$a")
done

python "$HERE/mkimage.py" \
	--kernel-image "$REF" \
	--rootfs "$ROOTFS" \
	--init "$HERE/init" \
	--add "$BUSYBOX=/bin/busybox.static" \
	"${EXTRA[@]}" \
	--cmdline-drop skip_initramfs --cmdline-drop root= --cmdline-drop dm= \
	--cmdline-append "techo5.stay_minutes=$STAY" \
	-o "$OUT"

echo
echo "built: $OUT"
echo "flash with the unit in TWRP (see docs/porting-plan.md, M2):"
echo "  adb -s <serial> push $(basename "$OUT") /tmp/linux.img"
echo "  adb -s <serial> shell 'dd if=/tmp/linux.img of=/dev/block/mmcblk0p12 && sync'"
echo "the way back is the same dd with $UNIT/recovery.img, from TWRP or from Android with root."
