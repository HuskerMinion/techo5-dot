"""The Dot's signed release and the boot image built from it, shared by install-dot.py and update-boot.py."""
import os
import re
import subprocess
import sys
import zlib

from techo5lib import Release, alpine, fail, repo_root, tar_extract_all, tar_read

REPO = 'HuskerMinion/techo5-dot'
# Each board's Bluetooth kernel in a release. dot-v1.0.18 and earlier carry biscuit's only, and a
# kernel carrying another board's device trees must never go onto a unit: a board whose kernel the
# release lacks runs its own.
KERNEL_ASSETS = {
    'biscuit': 'techo5-dot-kernel-bt.zImage-dtb',
    'radar': 'techo5-dot-kernel-bt-radar.zImage-dtb',
}
KERNEL_ASSET = KERNEL_ASSETS['biscuit']
# Amazon's device type ids, in /proc/idme/device_type_id: the board each kernel is chosen by.
BOARD_TYPE_IDS = {
    'A3S5BH2HU6VAYF': 'biscuit',
    'A7WXQPH584YP': 'radar',
}
APKS_ASSET = 'techo5-dot-rescue-apks.tar'


def kernel_board(path):
    """The board a zImage-dtb was built for, read from the kernel config it carries (CONFIG_IKCONFIG):
    CONFIG_ARCH_MTK_PROJECT, 'biscuit' or 'radar'. The appended device trees cannot tell them apart: both
    boards' call themselves MT8163. None when the kernel is not gzip-compressed or carries no config."""
    with open(path, 'rb') as f:
        data = f.read()
    at = data.find(b'\x1f\x8b\x08')
    if at < 0:
        return None
    try:
        image = zlib.decompressobj(31).decompress(data[at:])
        at = image.find(b'IKCFG_ST')
        if at < 0:
            return None
        config = zlib.decompressobj(31).decompress(image[at + 8:])
    except zlib.error:
        return None
    m = re.search(rb'^CONFIG_ARCH_MTK_PROJECT="([a-z0-9_]+)"$', config, re.M)
    return m.group(1).decode() if m else None


class DotRelease:
    """The release's files, downloaded into workdir and checked against the release's signed manifest,
    which covers the root filesystem, the Bluetooth kernel and the rescue packages alike."""

    def __init__(self, workdir, tag='latest'):
        rel = Release(REPO, tag, workdir)
        self.rel = rel
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

    def kernel_for(self, board):
        """The release's Bluetooth kernel for board, downloaded and checked against the signed manifest like
        the rest, or None when the release has none for it (then the unit keeps its own)."""
        if board == 'biscuit':
            return self.kernel
        name = KERNEL_ASSETS.get(board)
        if not name or not self.rel.signed(name):
            return None
        return self.rel.asset(name)


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


if __name__ == '__main__':
    # release-dot.ps1 checks each kernel it publishes is the board's: python tools/dotimage.py board <file>
    if len(sys.argv) != 3 or sys.argv[1] != 'board':
        sys.exit('usage: dotimage.py board <zImage-dtb>')
    print(kernel_board(sys.argv[2]) or '')
