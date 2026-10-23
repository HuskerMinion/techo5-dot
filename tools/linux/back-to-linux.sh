#!/sbin/sh
# back-to-linux.sh: run from TWRP after to-twrp, to put the unit's Linux boot image back in the
# recovery partition and boot it. The installer copies this into the unit's store:
#
#   adb shell sh /cache/techo5/back-to-linux.sh              (Echo Dot 2nd gen, Echo 2nd gen)
#   adb shell sh /data/techo5-store/techo5/back-to-linux.sh  (Echo Dot 3rd gen)
grep -q ' /cache ' /proc/mounts || mount /cache 2>/dev/null || mount -t ext4 /dev/block/mmcblk0p15 /cache
grep -q ' /data ' /proc/mounts || mount /data 2>/dev/null || mount -t ext4 /dev/block/mmcblk0p16 /data
# The store: the cache partition on the Dot 2 boards, userdata on the Dot 3.
S=/cache/techo5
[ -s $S/linux.img ] || S=/data/techo5-store/techo5
# The partition kaeru or amonet boots as recovery: recovery on the Dot 2 boards, swdl on the Dot 3, p12
# on both.
REC=/dev/block/platform/bootdevice/by-name/recovery
[ -e $REC ] || REC=/dev/block/mmcblk0p12
[ -s $S/linux.img ] || { echo "back-to-linux: no linux.img in the store; run the installer instead"; exit 1; }

len=$(wc -c < $S/linux.img)
want=$(md5sum $S/linux.img | cut -d' ' -f1)
dd if=$S/linux.img of=$REC bs=1048576 2>/dev/null
sync
got=$(head -c $len $REC | md5sum | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "back-to-linux: recovery read back $got, wanted $want; not rebooting"; exit 1; }
echo "back-to-linux: Linux is in recovery (md5 $got); rebooting into it"
reboot recovery
