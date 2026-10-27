#!/bin/sh
# Read the breadcrumbs and boot log that tools/linux/init leaves in the spare area of MISC. Run on
# the device, from Android with root or from a TWRP shell:
#
#   adb -s <serial> push tools/linux/readmisc.sh /data/local/tmp/
#   adb -s <serial> shell sh /data/local/tmp/readmisc.sh
# (the same on Windows; pushing it, rather than piping it in, keeps Windows line endings out)
#
# The crumbs start at byte 8 KiB (block 16) and the snapshot at 64 KiB (block 128), both well past
# the bootloader message and the A/B slot metadata at the front of the partition.
MISC=/dev/block/mmcblk0p8
[ -e $MISC ] || MISC=/dev/mmcblk0p8
if [ ! -e $MISC ]; then
	echo "readmisc.sh: $MISC is missing, so this is not a TECHO5 Dot. It runs ON the Dot, through adb (unit in TWRP or Fire OS): adb push tools/linux/readmisc.sh /data/local/tmp/, then adb shell sh /data/local/tmp/readmisc.sh"
	exit 1
fi

echo "=== bootloader message (first 64 bytes)"
dd if=$MISC bs=64 count=1 2>/dev/null | od -c | head -4

echo "=== boot tries (the initramfs stays in rescue after five unhealthy boots)"
dd if=$MISC bs=512 skip=14 count=1 2>/dev/null | tr -d '\000'

echo "=== crumbs"
dd if=$MISC bs=512 skip=16 count=32 2>/dev/null | tr -d '\000' | grep TECHO5-DOT

echo "=== boot log"
dd if=$MISC bs=512 skip=128 count=240 2>/dev/null | tr -d '\000'
