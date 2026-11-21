#!/usr/bin/env python3
"""Put a root filesystem into the Echo Dot 5th gen's (cannoli) store, over SSH, from slot-B Linux.

    python3 tools/linux/push-cannoli-rootfs.py --host <address> [-i <key>] build/techo5-cannoli-rootfs.tar.gz

The unit runs either the rescue ramdisk (init-cannoli) or a slot already; both have SSH with the keys
on userdata. The store is /data/techo5-store/techo5 on userdata, the Dot 3's layout (slotctl): the
tarball goes into the slot that is not running, on trial, and becomes the next to boot. The try count
init-cannoli keeps there goes back to zero, so a unit that gave up on an older slot tries the new one.
Then reboot holding Volume-Up (slot B); the ramdisk switches into the slot, or stays in rescue if it is
missing or never becomes healthy.

The tarball is build-dot-rootfs.py --board cannoli's: nothing of the unit's own is in it. The unit's
Wi-Fi and Bluetooth driver and firmware come from its slot-B ramdisk, which adopts them into the store
at boot. Needs Python 3 and an ssh client (OpenSSH, as Windows has).
"""
import argparse
import os
import subprocess
import sys

STOREDIR = '/data/techo5-store'
S = STOREDIR + '/techo5'
TMP = STOREDIR + '/incoming.tar.gz'

PREPARE = '''set -e
grep -q ' /data ' /proc/mounts || { echo "userdata is not mounted"; exit 1; }
mkdir -p %(S)s/slots
: > %(S)s/.techo5-store
rm -f %(TMP)s
''' % {'S': S, 'TMP': TMP}

# slotctl from the unit itself: the rescue ramdisk carries it in /usr/local/sbin, and so does a slot.
INSTALL = '''set -e
STORE=%(S)s /usr/local/sbin/slotctl install %(TMP)s
rm -f %(TMP)s
echo "0 install" > %(S)s/tries
rm -f %(S)s/rescue
sync
STORE=%(S)s /usr/local/sbin/slotctl status
''' % {'S': S, 'TMP': TMP}


def ssh(a, script, stdin=None):
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    if a.identity:
        cmd += ['-i', a.identity]
    cmd += ['root@' + a.host, 'sh -c ' + "'" + script.replace("'", "'\\''") + "'"]
    return subprocess.run(cmd, stdin=stdin).returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', required=True, help="the unit's address on the network")
    ap.add_argument('-i', '--identity', help='the SSH key the unit takes')
    ap.add_argument('rootfs', help="build-dot-rootfs.py --board cannoli's tarball")
    a = ap.parse_args()
    if not os.path.isfile(a.rootfs):
        sys.exit('no %s' % a.rootfs)
    if ssh(a, PREPARE) != 0:
        sys.exit('the unit would not make the store')
    print('copying %s (%d bytes)' % (a.rootfs, os.path.getsize(a.rootfs)))
    with open(a.rootfs, 'rb') as f:
        if ssh(a, 'cat > %s && sync' % TMP, stdin=f) != 0:
            sys.exit('copying failed')
    if ssh(a, INSTALL) != 0:
        sys.exit('slotctl install failed')
    print('\ninstalled. Reboot into slot B: power on holding Volume-Up (from Linux: reboot, then hold it).')


if __name__ == '__main__':
    main()
