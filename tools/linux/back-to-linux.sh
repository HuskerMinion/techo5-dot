#!/sbin/sh
# back-to-linux.sh: run from TWRP after to-twrp, to put the unit's Linux boot image back in the
# recovery partition and boot it. The installer copies this to /cache/techo5/ on the unit.
#
#   adb shell sh /cache/techo5/back-to-linux.sh
S=/cache/techo5
grep -q ' /cache ' /proc/mounts || mount /cache 2>/dev/null || mount -t ext4 /dev/block/mmcblk0p15 /cache
REC=/dev/block/platform/bootdevice/by-name/recovery
[ -e $REC ] || REC=/dev/block/mmcblk0p12
[ -s $S/linux.img ] || { echo "back-to-linux: no $S/linux.img; run the installer instead"; exit 1; }

len=$(wc -c < $S/linux.img)
want=$(md5sum $S/linux.img | cut -d' ' -f1)
dd if=$S/linux.img of=$REC bs=1048576 2>/dev/null
sync
got=$(head -c $len $REC | md5sum | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "back-to-linux: recovery read back $got, wanted $want; not rebooting"; exit 1; }
echo "back-to-linux: Linux is in recovery (md5 $got); rebooting into it"
reboot recovery
