"""The Dot's signed release and the boot image built from it, shared by install-dot.py and update-boot.py."""
import gzip
import io
import os
import re
import subprocess
import sys
import tarfile
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
# The Echo 2nd gen's Linux 6.1 kernel (LibreEcho's, tools/linux/build-kernel-61.sh), taken instead of the
# 3.18 one only when asked for (--linux-61), or by update-boot.py for a unit already running it. Its
# local version is what tells it from the 3.18 kernel, whose config names the same board.
KERNEL_61_ASSETS = {'radar': 'techo5-dot-kernel-61-radar.zImage-dtb'}
KERNEL_61_VERSIONS = {'radar': '-techo5-radar'}
# Amazon's device type ids, in /proc/idme/device_type_id: the board each kernel is chosen by.
BOARD_TYPE_IDS = {
    'A3S5BH2HU6VAYF': 'biscuit',
    'A7WXQPH584YP': 'radar',
    'A32DOYMUN6DTXA': 'donut',
}
# Each board's root filesystem key in a release's manifest: the Echo Dot 3rd gen (donut) has its own
# daemon (echod built with -tags dot,donut) and boot scripts (tools/linux/rootfs-donut).
ROOTFS_KEYS = {'biscuit': 'arm-dot', 'radar': 'arm-dot', 'donut': 'arm-donut'}
# Where the Echo Dot 3's initramfs is loaded: TWRP's address. The stock header's 0x44000000 leaves under
# 4 MB below ram_console and pstore at 0x44400000, and an initramfs this size there never boots.
DONUT_RAMDISK_ADDR = '0x43478000'
APKS_ASSET = 'techo5-dot-rescue-apks.tar'


def kernel_config(path):
    """The kernel config a zImage-dtb carries (CONFIG_IKCONFIG), or None when the kernel is not
    gzip-compressed or carries none."""
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
        return zlib.decompressobj(31).decompress(image[at + 8:])
    except zlib.error:
        return None


def kernel_board(path):
    """The board a zImage-dtb was built for, read from the kernel config it carries: CONFIG_ARCH_MTK_PROJECT,
    'biscuit' or 'radar' (LibreEcho's 6.1 kernel keeps it too). The appended device trees cannot tell them
    apart: both boards' call themselves MT8163. None when the board cannot be read."""
    config = kernel_config(path)
    m = config and re.search(rb'^CONFIG_ARCH_MTK_PROJECT="([a-z0-9_]+)"$', config, re.M)
    return m.group(1).decode() if m else None


def kernel_is_61(path, board):
    """Whether a zImage-dtb is board's Linux 6.1 kernel (KERNEL_61_VERSIONS), not its 3.18 one."""
    want = KERNEL_61_VERSIONS.get(board)
    config = kernel_config(path)
    return bool(want and config and re.search(rb'^CONFIG_LOCALVERSION="%s"$' % re.escape(want.encode()), config, re.M))


class DotRelease:
    """The release's files, downloaded into workdir and checked against the release's signed manifest,
    which covers the root filesystem, the Bluetooth kernel and the rescue packages alike."""

    def __init__(self, workdir, tag='latest', board='biscuit'):
        rel = Release(REPO, tag, workdir)
        self.rel = rel
        self.version = rel.version
        self.board = board
        self.rootfs = rel.rootfs(ROOTFS_KEYS[board])
        self.kernel = rel.asset(KERNEL_ASSET) if board != 'donut' else None
        self.apks = os.path.join(rel.dir, 'apks-dot')
        tar_extract_all(rel.asset(APKS_ASSET), self.apks)
        # What the boot image needs from the root filesystem: busybox, and the Wi-Fi bring-up on the Dot
        # 2 boards (wmtup) or our wpa_supplicant on the Dot 3 (tools/linux/build-wpa-supplicant.sh).
        self.busybox = os.path.join(rel.dir, 'busybox.static')
        self.wmtup = os.path.join(rel.dir, 'wmtup') if board != 'donut' else None
        self.wpa_supplicant = os.path.join(rel.dir, 'wpa_supplicant-donut') if board == 'donut' else None
        wants = [('bin/busybox.static', self.busybox)]
        if self.wmtup:
            wants.append(('usr/local/bin/wmtup', self.wmtup))
        if self.wpa_supplicant:
            wants.append(('sbin/wpa_supplicant', self.wpa_supplicant))
        for member, out in wants:
            data = tar_read(self.rootfs, member)
            if data is None:
                fail('the root filesystem has no %s' % member)
            with open(out, 'wb') as f:
                f.write(data)
        self.alpine = alpine(workdir)

    def kernel_for(self, board, linux61=False):
        """The release's Bluetooth kernel for board (its Linux 6.1 kernel with linux61), downloaded and
        checked against the signed manifest like the rest, or None when the release has none for it (then
        the unit keeps its own)."""
        if linux61:
            name = KERNEL_61_ASSETS.get(board)
            if not name or not self.rel.signed(name):
                return None
            return self.rel.asset(name)
        if board == 'biscuit':
            return self.kernel
        if board == 'donut':
            return None  # the Echo Dot 3 runs its own kernel: the Bluetooth stack is modules in its rootfs
        name = KERNEL_ASSETS.get(board)
        if not name or not self.rel.signed(name):
            return None
        return self.rel.asset(name)


def with_member(apk, member, path, out):
    """out: the data of the Alpine package apk as a .tar.gz, with member's contents replaced by the file
    at path. mkimage takes the first copy of a path it meets and packages come before --add, so a
    replacement has to be made inside the package."""
    with open(apk, 'rb') as f:
        blob = f.read()
    parts, off = [], 0
    while off < len(blob):  # an apk is three gzip members: signature, control, data
        d = zlib.decompressobj(31)
        data = d.decompress(blob[off:]) + d.flush()
        parts.append(data)
        off = len(blob) - len(d.unused_data)
    src = tarfile.open(fileobj=io.BytesIO(parts[-1]))
    buf = io.BytesIO()
    found = False
    with tarfile.open(fileobj=buf, mode='w') as dst:
        for ti in src.getmembers():
            if ti.name == member:
                with open(path, 'rb') as f:
                    new = f.read()
                ti.size = len(new)
                dst.addfile(ti, io.BytesIO(new))
                found = True
            else:
                dst.addfile(ti, src.extractfile(ti) if ti.isfile() else None)
    if not found:
        fail('%s has no %s to replace' % (os.path.basename(apk), member))
    with open(out, 'wb') as f:
        f.write(gzip.compress(buf.getvalue()))


def build_boot_image(recovery, out, alpine_tgz, busybox, wmtup, apks, kernel=None, board='biscuit', wpa_supplicant=None):
    """A unit's boot image: its own backup's header and kernel (the recovery partition's on the Dot 2
    boards, without a kernel given; the booted slot's stock kernel on the Echo Dot 3), the rescue initramfs
    on Alpine's base, and the slot, network and TWRP tools."""
    j = os.path.join
    linux = j(repo_root(), 'tools', 'linux')
    donut = board == 'donut'
    mk = [sys.executable, j(linux, 'mkimage.py'), '--kernel-image', recovery, '--rootfs', alpine_tgz,
          '--init', j(linux, 'init-donut' if donut else 'init'), '--add', busybox + '=/bin/busybox.static',
          '--cmdline-drop', 'skip_initramfs', '--cmdline-drop', 'root=', '--cmdline-drop', 'dm=',
          '--cmdline-append', 'techo5.stay_minutes=15', '-o', out]
    if wmtup:
        mk += ['--add', wmtup + '=/usr/local/bin/wmtup']
    if donut:
        mk += ['--ramdisk-addr', DONUT_RAMDISK_ADDR]
    if kernel:
        mk += ['--kernel', kernel]
    for apk in sorted(os.listdir(apks)):
        if not apk.endswith('.apk'):
            continue
        if donut and apk.startswith('wpa_supplicant-'):
            # Alpine's 2.11 never completes a handshake with the Dot 3's Wi-Fi driver; ours does
            # (tools/linux/wpa-patches/0100-donut-no-wmm-replay-counters.patch).
            swapped = out + '.wpa_supplicant.tar.gz'
            with_member(j(apks, apk), 'sbin/wpa_supplicant', wpa_supplicant, swapped)
            mk += ['--apk', swapped]
            continue
        mk += ['--apk', j(apks, apk)]
    for t in ('slotctl', 'techo5-net', 'wifi-set', 'to-twrp', 'techo5-firewall'):
        mk += ['--script', j(linux, 'rootfs', 'usr', 'local', 'sbin', t) + '=/usr/local/sbin/' + t]
    if subprocess.run(mk).returncode != 0:
        fail('building the boot image failed')


if __name__ == '__main__':
    # release-dot.ps1 checks each kernel it publishes is the board's: python tools/dotimage.py board <file>
    # prints the board, with " 6.1" after it for a Linux 6.1 kernel.
    if len(sys.argv) != 3 or sys.argv[1] != 'board':
        sys.exit('usage: dotimage.py board <zImage-dtb>')
    b = kernel_board(sys.argv[2])
    print((b + (' 6.1' if kernel_is_61(sys.argv[2], b) else '')) if b else '')
