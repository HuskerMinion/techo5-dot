#!/usr/bin/env python3
"""Rebuild a Dot's boot image (kernel + initramfs) and put it in the recovery partition of a unit already
running TECHO5 Linux, over SSH.

    python3 tools/update-boot.py --serial <serial> --address <address>
    python3 tools/update-boot.py --serial <serial> --address <address> --kernel build/kernel/zImage-dtb

Home Assistant updates carry the root filesystem only; the boot image is built per unit from its own
recovery backup (kept by the installer) and is never published. This is how a new kernel or rescue
environment reaches a unit without going back through Fire OS or TWRP: for example, Bluetooth for a Dot
installed without the Bluetooth kernel. By default the kernel and rescue packages come from the signed
release, the one built for the unit's board. An Echo 2nd gen (radar) keeps the kernel from its recovery
backup when the release has none for it (dot-v1.0.18 and earlier), unless --kernel is given.

The Echo 2nd gen can run LibreEcho's Linux 6.1 kernel instead (--linux-61; tools/linux/build-kernel-61.sh),
from the same recovery partition. A unit already on it stays on it; --linux-318 takes it back to 3.18.

The previous image stays in the store as linux-prev.img, and linux.img (what back-to-linux.sh and to-twrp
use) is only replaced once the new image has booted healthy. Needs SSH to the unit: turn on its SSH switch
in Home Assistant, with a key set. Windows, Linux and macOS alike; needs Python 3 and ssh.

If the new image does not come up: when its kernel boots but the system does not, the unit stays in the
new image's rescue environment after five tries (USB console, SSH). When the kernel itself does not boot
there is no rescue, because the rescue environment is in the same image. The way back is then the unit's
own TWRP: hold Volume-Down while powering on for fastboot, `fastboot flash recovery
backups/<serial>/recovery.img`, let it boot TWRP, and `adb shell sh /cache/techo5/back-to-linux.sh` puts
back the last image that booted healthy.

On the Echo Dot 3rd gen the image is built on the unit's own stock kernel, from the backup of the boot slot
Fire OS runs in (backups/<serial>/boot_a.img or boot_b.img), and its TWRP backup is swdl.img.
"""
import argparse
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from techo5lib import default_dir, fail, md5, need, note, run_main, step  # noqa: E402
from dotimage import BOARD_TYPE_IDS, KERNEL_61_VERSIONS, DotRelease, build_boot_image, kernel_board, kernel_is_61  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--serial', required=True)
    ap.add_argument('--address', required=True, help="the unit's address on your network")
    ap.add_argument('--kernel', help="a kernel you built for this board instead of the release's")
    ap.add_argument('--no-bluetooth-kernel', action='store_true', help="keep the kernel from the unit's recovery backup")
    line = ap.add_mutually_exclusive_group()
    line.add_argument('--linux-61', action='store_true', help="Echo 2nd gen: the release's Linux 6.1 kernel (LibreEcho's)")
    line.add_argument('--linux-318', action='store_true', help="Echo 2nd gen: back to the release's 3.18 kernel from 6.1")
    ap.add_argument('--release', default='latest', help="a release tag (dot-vX.Y.Z), or latest")
    ap.add_argument('--backups', default=default_dir('TECHO5_BACKUPS', 'backups'))
    ap.add_argument('--work', default=default_dir('TECHO5_WORK', 'build'))
    ap.add_argument('--ssh', default='ssh')
    a = ap.parse_args()
    need(a.ssh, 'an OpenSSH client (built into Windows 10 and later, Linux and macOS)')

    unit = os.path.join(a.backups, a.serial)
    os.makedirs(a.work, exist_ok=True)
    image = os.path.join(unit, 'techo5-dot-linux-next.img')

    target = 'root@' + a.address
    ssh = [a.ssh, '-o', 'ConnectTimeout=10', target]

    def remote(command):
        r = subprocess.run(ssh + [command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode, r.stdout.decode('utf-8', 'replace').strip()

    # Each board has its own kernel, with its own device trees: a radar on biscuit's kernel would come up
    # on biscuit's device tree, if at all, and a kernel that does not boot leaves no rescue to reach. So
    # the board is read before anything is built, and a board the release has no kernel for keeps the
    # one from its recovery backup. An id that is empty or not one of the two is refused, not guessed.
    # The 3.18 kernels have the vendor idme in /proc; LibreEcho's 6.1 has it in the device tree.
    rc, out = remote("echo type=$({ cat /proc/idme/device_type_id || cat /proc/device-tree/idme/device_type_id/value; } "
                     "2>/dev/null | tr -d '\\000'); "
                     "echo slot=$(sed -n 's/.*androidboot.slot_suffix=\\([^ ]*\\).*/\\1/p' /proc/cmdline); "
                     "echo release=$(uname -r)")
    m = re.search(r'^type=(\S*)$', out, re.M)
    if rc != 0 or not m:
        fail('could not read the board over SSH from %s (%s); nothing was flashed' % (a.address, out or 'no output'))
    board = BOARD_TYPE_IDS.get(m.group(1))
    if not board:
        fail("%s reports device type id '%s', not an Echo Dot 2nd gen (biscuit), an Echo 2nd gen (radar) or an "
             "Echo Dot 3rd gen (donut); nothing was flashed" % (a.address, m.group(1)))
    donut = board == 'donut'
    if donut and (a.kernel or a.no_bluetooth_kernel):
        fail('the Echo Dot 3 always runs its own kernel; leave out --kernel and --no-bluetooth-kernel')
    if (a.linux_61 or a.linux_318) and board not in KERNEL_61_VERSIONS:
        fail('--linux-61 and --linux-318 are for the Echo 2nd gen (radar); this is %s' % board)
    if (a.linux_61 or a.linux_318) and (a.kernel or a.no_bluetooth_kernel):
        fail("--linux-61 and --linux-318 choose the release's kernel; leave out --kernel and --no-bluetooth-kernel")
    r = re.search(r'^release=(\S*)$', out, re.M)
    # uname -r: 6.1.178-techo5-radar, with a + after it when built from a tree without a release tag.
    on61 = bool(r and board in KERNEL_61_VERSIONS and KERNEL_61_VERSIONS[board] in r.group(1))
    linux61 = a.linux_61 or (on61 and not a.linux_318)
    # recovery is the unit's TWRP backup; donor the image the new one is built on (installer, step 2).
    recovery = os.path.join(unit, 'swdl.img' if donut else 'recovery.img')
    if not os.path.exists(recovery):
        fail('no TWRP backup for %s at %s (the installer keeps one; --backups points elsewhere)' % (a.serial, recovery))
    donor = recovery
    if donut:
        s = re.search(r'^slot=(_[ab])$', out, re.M)
        donor = os.path.join(unit, 'boot%s.img' % (s.group(1) if s else '_a'))
        if not os.path.exists(donor):
            fail('no backup of the boot slot at %s (the installer keeps one)' % donor)

    step('boot image')
    rel = DotRelease(a.work, a.release, board)
    note('TECHO5 Dot %s: %s and rescue packages checked' % (rel.version, 'Bluetooth kernel' if not donut else 'release'))
    kernel = None if a.no_bluetooth_kernel else (os.path.abspath(a.kernel) if a.kernel else rel.kernel_for(board, linux61))
    if kernel is None and linux61 and not a.kernel:
        # Never the 3.18 kernel in its place without being asked: that is --linux-318.
        fail('release %s has no Linux 6.1 kernel for %s; nothing was flashed' % (rel.version, board))
    if kernel is None and not a.no_bluetooth_kernel and not donut:
        note('release %s has no Bluetooth kernel for %s' % (rel.version, board))
    if kernel:
        # A kernel built for the other board would not boot this one, and on the Echo 2nd gen getting back
        # means opening the case: the board is read from the kernel's own configuration first.
        if not os.path.exists(kernel):
            fail('no kernel at %s' % kernel)
        kb = kernel_board(kernel)
        if kb != board:
            fail('%s is %s, not a kernel for this %s; nothing was flashed' % (
                kernel, 'built for %s' % kb if kb else 'a kernel whose board cannot be read', board))
    if kernel and not os.path.exists(kernel):
        fail('no kernel at %s' % kernel)
    if donut:
        note("kernel: the unit's own, from %s" % os.path.basename(donor))
    else:
        note('kernel: %s' % ('%s (%s)' % (os.path.basename(kernel), 'Linux 6.1' if kernel_is_61(kernel, board) else 'Bluetooth')
                             if kernel else 'from the recovery backup (no Bluetooth)'))
    build_boot_image(donor, image, rel.alpine, rel.busybox, rel.wmtup, rel.apks, kernel, board, rel.wpa_supplicant)
    want = md5(image)
    size = os.path.getsize(image)

    # A boot is told apart from the one before it by the kernel's boot id, so the healthy mark the old boot
    # left in MISC is never mistaken for the new one's. Read before anything is written: an empty id would
    # be "in" every later reply, and the wait below could never see the new boot as healthy.
    rc, out = remote('cat /proc/sys/kernel/random/boot_id')
    m = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', out)
    if rc != 0 or not m:
        fail("could not read the running boot's id over SSH from %s (%s); nothing was flashed" % (a.address, out or 'no output'))
    boot_id = m.group(0)

    step('flashing %s' % a.address)
    with open(image, 'rb') as f:
        if subprocess.run(ssh + ['cat > /store/techo5/linux-next.img'], stdin=f).returncode != 0:
            fail('sending the image failed')
    flash = ('set -e\nS=/store/techo5\n'
             '[ "$(md5sum < $S/linux-next.img | cut -d" " -f1)" = %(w)s ] || { echo "transfer mismatch"; exit 1; }\n'
             'cp -f $S/linux.img $S/linux-prev.img\n'
             'dd if=$S/linux-next.img of=/dev/mmcblk0p12 bs=1048576 2>/dev/null\nsync\n'
             'got=$(head -c %(n)d /dev/mmcblk0p12 | md5sum | cut -d" " -f1)\n'
             '[ "$got" = %(w)s ] || { dd if=$S/linux-prev.img of=/dev/mmcblk0p12 bs=1048576 2>/dev/null; sync; echo "read back $got; previous image restored"; exit 1; }\n'
             'echo "flashed $got"\n') % {'w': want, 'n': size}
    _, out = remote(flash)
    note(out)
    if 'flashed ' + want not in out:
        fail('flashing failed')

    remote('sync; (sleep 2; reboot) >/dev/null 2>&1 &')
    note('rebooting into the new image; waiting for a healthy boot')
    time.sleep(40)
    healthy = False
    deadline = time.time() + 6 * 60
    while time.time() < deadline:
        r = subprocess.run([a.ssh, '-o', 'ConnectTimeout=4', '-o', 'BatchMode=yes', target,
                            'cat /proc/sys/kernel/random/boot_id; dd if=/dev/mmcblk0p8 bs=512 skip=14 count=1 2>/dev/null | tr -d "\\000"'],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        text = r.stdout.decode('utf-8', 'replace')
        if 'healthy' in text and boot_id not in text:
            healthy = True
            break
        time.sleep(10)
    if not healthy and donut:
        fail('the unit did not report a healthy boot.\n'
             '   If the new image boots, the unit stays in its rescue environment after five tries: reach it on the\n'
             '   USB console or over SSH. If it does not boot at all, a power cycle starts Fire OS; from there\n'
             '   (root adb) put the last image that booted healthy back:\n'
             '     adb shell dd if=/data/techo5-store/techo5/linux.img of=/dev/block/mmcblk0p12\n'
             '     adb reboot recovery\n'
             '   (linux.img in the store was not replaced). The unit\'s TWRP backup is %s.' % recovery)
    if not healthy:
        fail('the unit did not report a healthy boot.\n'
             '   If the new kernel boots, the unit stays in its rescue environment after five tries: reach it on\n'
             '   the USB console or over SSH.\n'
             '   If the new kernel does not boot at all (no console, no SSH), there is no rescue to reach, since it\n'
             '   is in the same image. Go back through TWRP: hold Volume-Down while powering on for fastboot, then\n'
             '     fastboot flash recovery %s\n'
             '   let it boot TWRP, and run: adb shell sh /cache/techo5/back-to-linux.sh\n'
             '   That puts back the last Linux image that booted healthy (linux.img in the store was not replaced).'
             % recovery)
    _, out = remote('mv -f /store/techo5/linux-next.img /store/techo5/linux.img; sync; uname -r')
    note('kernel %s' % out)
    print("Done: %s booted the new image healthy; it is now the unit's linux.img." % a.serial)


if __name__ == '__main__':
    run_main(main)
