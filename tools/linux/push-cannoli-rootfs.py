#!/usr/bin/env python3
"""Put a root filesystem into the Echo Dot 5th gen's (cannoli) store, over SSH, from slot-B Linux.

    python3 tools/linux/push-cannoli-rootfs.py --host <address> [-i <key>] [--only] build/techo5-cannoli-rootfs.tar.gz

The unit runs either the rescue ramdisk (init-cannoli) or a slot already; both have SSH with the keys
on userdata. The store is /data/techo5-store/techo5 on userdata, the Dot 3's layout (slotctl): the
tarball goes into the slot that is not running, on trial, and becomes the next to boot. The try count
init-cannoli keeps there goes back to zero, so a unit that gave up on an older slot tries the new one.
Then reboot holding Volume-Up (slot B); the ramdisk switches into the slot, or stays in rescue if it is
missing or never becomes healthy.

The tarball is unpacked as it arrives: userdata is Fire OS's own, about 180 MB with some 80 free, so
there is no room for the tarball beside the slot. --only (from the rescue, no slot running) empties
both slots first, for a unit with room for only one.

The tarball is build-dot-rootfs.py --board cannoli's: nothing of the unit's own is in it. The unit's
Wi-Fi and Bluetooth driver and firmware come from its slot-B ramdisk, which adopts them into the store
at boot. Needs Python 3 and an ssh client (OpenSSH, as Windows has).
"""
import argparse
import os
import subprocess
import sys

S = '/data/techo5-store/techo5'

# slotctl install's steps, with the tarball read from stdin. $1 is "only" or empty.
INSTALL = r'''set -e
grep -q ' /data ' /proc/mounts || { echo "userdata is not mounted"; exit 1; }
S=%s
mkdir -p $S/slots
: > $S/.techo5-store
booted=$(cat /run/techo5/slot 2>/dev/null || true)
if [ "$1" = only ]; then
	[ -z "$booted" ] || { echo "--only is for the rescue: slot $booted is running"; exit 1; }
	rm -rf $S/slots/a $S/slots/b $S/slots/a.state $S/slots/b.state $S/active
fi
case "$(cat $S/active 2>/dev/null)" in a) t=b ;; b) t=a ;; *) t=a ;; esac
if [ "$t" = "$booted" ]; then if [ "$t" = a ]; then t=b; else t=a; fi; fi
rm -rf $S/slots/$t $S/slots/$t.state
sync
mkdir -p $S/slots/$t
echo "unpacking into slot $t ($(df -h /data | awk 'NR==2 { print $4 }') free on userdata)"
if ! tar -xzf - -C $S/slots/$t; then rm -rf $S/slots/$t; sync; echo "unpacking failed"; exit 1; fi
for f in sbin/init bin/busybox.static usr/local/bin/echod etc/techo5/boot.sh etc/inittab; do
	[ -e $S/slots/$t/$f ] || { rm -rf $S/slots/$t; sync; echo "not a TECHO5 Dot rootfs: $f missing"; exit 1; }
done
echo "trial 2" > $S/slots/$t.state
echo $t > $S/active
echo "0 install" > $S/tries
rm -f $S/rescue
sync
echo "slot $t: $(cat $S/slots/$t/etc/techo5-release), $(cat $S/slots/$t.state); active: $(cat $S/active)"
df -h /data | awk 'NR==2 { print $4 " free on userdata" }'
''' % S


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', required=True, help="the unit's address on the network")
    ap.add_argument('-i', '--identity', help='the SSH key the unit takes')
    ap.add_argument('--only', action='store_true',
                    help='from the rescue: empty both slots first, for a unit whose userdata holds only one')
    ap.add_argument('rootfs', help="build-dot-rootfs.py --board cannoli's tarball")
    a = ap.parse_args()
    if not os.path.isfile(a.rootfs):
        sys.exit('no %s' % a.rootfs)
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    if a.identity:
        cmd += ['-i', a.identity, '-o', 'IdentitiesOnly=yes']
    quoted = "'" + INSTALL.replace("'", "'\\''") + "'"
    cmd += ['root@' + a.host, 'sh -c %s sh %s' % (quoted, 'only' if a.only else '')]
    print('sending %s (%d bytes)' % (a.rootfs, os.path.getsize(a.rootfs)))
    with open(a.rootfs, 'rb') as f:
        if subprocess.run(cmd, stdin=f).returncode != 0:
            sys.exit('installing failed')
    print('\ninstalled. Reboot into slot B: power on holding Volume-Up (from Linux: reboot, then hold it).')


if __name__ == '__main__':
    main()
