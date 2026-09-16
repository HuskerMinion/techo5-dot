#!/bin/sh
# Read the breadcrumbs and boot log that tools/linux/init leaves in the spare area of MISC. Run on
# the device, from Android with root or from a TWRP shell:
#
#   adb -s <serial> shell < tools/linux/readmisc.sh
#
# The crumbs start at byte 8 KiB (block 16) and the snapshot at 64 KiB (block 128), both well past
# the bootloader message and the A/B slot metadata at the front of the partition.
MISC=/dev/block/mmcblk0p8
[ -e $MISC ] || MISC=/dev/mmcblk0p8

echo "=== bootloader message (first 64 bytes)"
dd if=$MISC bs=64 count=1 2>/dev/null | od -c | head -4

echo "=== boot tries (Linux falls back to Android after three unhealthy boots)"
dd if=$MISC bs=512 skip=14 count=1 2>/dev/null | tr -d '\000'

echo "=== crumbs"
dd if=$MISC bs=512 skip=16 count=32 2>/dev/null | tr -d '\000' | grep TECHO5-DOT

echo "=== boot log"
dd if=$MISC bs=512 skip=128 count=240 2>/dev/null | tr -d '\000'
