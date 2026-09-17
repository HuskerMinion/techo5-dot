"""The Dot's signed release and the boot image built from it, shared by install-dot.py and update-boot.py."""
import os
import subprocess
import sys

from techo5lib import Release, alpine, fail, repo_root, tar_extract_all, tar_read

REPO = 'HuskerMinion/techo5-dot'
KERNEL_ASSET = 'techo5-dot-kernel-bt.zImage-dtb'
APKS_ASSET = 'techo5-dot-rescue-apks.tar'


class DotRelease:
    """The release's files, downloaded into workdir and checked: the signed manifest covers the root
    filesystem, SHA256SUMS the Bluetooth kernel and the rescue packages."""

    def __init__(self, workdir, tag='latest'):
        rel = Release(REPO, tag, workdir)
        self.version = rel.version
        self.rootfs = rel.rootfs('arm-dot')
        self.kernel = rel.asset(KERNEL_ASSET)
        self.apks = os.path.join(rel.dir, 'apks-dot')
        tar_extract_all(rel.asset(APKS_ASSET), self.apks)
        # What the boot image needs from the root filesystem: the Wi-Fi bring-up and busybox.
        self.wmtup = os.path.join(rel.dir, 'wmtup')
        self.busybox = os.path.join(rel.dir, 'busybox.static')
        for member, out in (('usr/local/bin/wmtup', self.wmtup), ('bin/busybox.static', self.busybox)):
            data = tar_read(self.rootfs, member)
            if data is None:
                fail('the root filesystem has no %s' % member)
            with open(out, 'wb') as f:
                f.write(data)
        self.alpine = alpine(workdir)


def build_boot_image(recovery, out, alpine_tgz, busybox, wmtup, apks, kernel=None):
    """A unit's boot image: its own recovery backup's header (and kernel, without one given), the rescue
    initramfs on Alpine's base, and the slot, network and TWRP tools."""
    j = os.path.join
    linux = j(repo_root(), 'tools', 'linux')
    mk = [sys.executable, j(linux, 'mkimage.py'), '--kernel-image', recovery, '--rootfs', alpine_tgz,
          '--init', j(linux, 'init'), '--add', busybox + '=/bin/busybox.static', '--add', wmtup + '=/usr/local/bin/wmtup',
          '--cmdline-drop', 'skip_initramfs', '--cmdline-drop', 'root=', '--cmdline-drop', 'dm=',
          '--cmdline-append', 'techo5.stay_minutes=15', '-o', out]
    if kernel:
        mk += ['--kernel', kernel]
    for apk in sorted(os.listdir(apks)):
        if apk.endswith('.apk'):
            mk += ['--apk', j(apks, apk)]
    for t in ('slotctl', 'techo5-net', 'wifi-set', 'to-twrp', 'techo5-firewall'):
        mk += ['--script', j(linux, 'rootfs', 'usr', 'local', 'sbin', t) + '=/usr/local/sbin/' + t]
    if subprocess.run(mk).returncode != 0:
        fail('building the boot image failed')
