#!/usr/bin/env python3
"""Take an Echo Dot 5th gen (cannoli), fresh from the box or already rooted, to TECHO5 Linux in one run.

    python3 tools/install-cannoli.py --serial <serial> --unit ../cannoli-<serial> --dry-run
    python3 tools/install-cannoli.py --serial <serial> --unit ../cannoli-<serial> --name Kitchen

The Dot 5 keeps Fire OS in slot A and boots slot B when Volume-Up is held at power-on. firebreak's cannoli
route (`firebreak unlock --device cannoli`) roots Fire OS in slot A and puts TWRP in slot B; this installer
then puts TECHO5's own boot image in slot B, built here from the unit's own kernel, drivers and firmware
(Amazon's, so never redistributed: every image is built on the host from the unit it runs on).

What it does, stopping at the first thing not right:

  1. finds the unit and what it runs: stock Fire OS in setup mode, rooted Fire OS (slot A), TWRP (slot B)
     or the RAM fastboot, and its Fire OS build
  2. gates on the build: NS6575/8349 goes on; an older one takes the one-way 8349 upgrade (firebreak
     cannoli-upgrade, then the four brhgptlk BL2 copies), asked for first; a newer one stops
  3. unlocks a stock unit with firebreak (`--firebreak`, `--bundle`)
  4. backs up every UBI volume but cache and userdata, the four brhgptlk copies, and the Wi-Fi and
     Bluetooth drivers and firmware into backups/<serial>/m0/, each checked against a hash the unit
     computes itself; a rooted 8349 unit whose brhgptlk copies still hold the launch BL2 is asked before
     they are written (8349's image checked first)
  5. builds slot B's image from that backup (tools/linux/build-cannoli-image.py) and the Bluetooth
     payload (tools/linux/build-cannoli-bt-payload.py)
  6. asks for the name, Wi-Fi and Home Assistant key as install-dot.py does
  7. writes any brhgptlk copies from TWRP, then boot_b from Fire OS, each read back, and unpacks the root
     filesystem and the Bluetooth payload onto userdata
  8. has the unit powered on with Volume-Up held and waits on its USB console for a healthy boot

--dry-run does 1, 2, 4, 5 and 6 and writes nothing to the unit (firebreak's commands are printed, not
run). The unit's folder (--unit) is the one the cannoli-os-builder and TWRP-builder work in: bundle/
(firebreak's bundle), stock-8349-parts/ (the unpacked 8349 OTA) and builds/ (cannoli_hwfixup.ko); each is
looked for there unless given.

Windows, Linux and macOS; Python 3 and adb (fastboot to see the RAM fastboot). firebreak's cannoli unlock
catches the preloader from native Windows, not from WSL: run this from a Windows Python for step 3.
The bundle itself (cannoli-os-builder, the TWRP builder) is built on an x86-64 Linux host (WSL is fine);
docs/install-cannoli.md has those commands.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from techo5lib import (CONSOLE_TECHO5, Adb, Console, Fastboot, adb_devices, ask, ask_name, ask_wifi, check_gift, confirm,  # noqa: E402
                       default_dir, download_checked, fail, hash_file, interactive, need, new_api_key, note,
                       repo_root, run_main, step, valid_api_key, write_private)
from techo5lib import wifi_conf as techo5_wifi_conf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIREBREAK_GUIDE = 'https://github.com/Gamer92000/firebreak/blob/main/docs/cannoli.md'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The Dot installer's wake words, mDNS lookup and slot unpacking, shared rather than copied.
dot = _load('install_dot', os.path.join(HERE, 'install-dot.py'))
# The board check on a root filesystem tarball, as push-cannoli-rootfs.py makes it.
push = _load('push_cannoli_rootfs', os.path.join(HERE, 'linux', 'push-cannoli-rootfs.py'))

# ---------------------------------------------------------------------------------------------- the build

# The one Fire OS build TECHO5's slot B is built for: the kernel modules' CRCs and the stock boot FIT
# are 8349's. Which builds firebreak's unlock works on is firebreak's to say (its cannoli guide).
# ro.build.version.number is Amazon's long build number.
TARGET_BUILD = 8349
TARGET_NUMBER = 13323246980
TARGET_NAME = 'NS6575/8349'
OTA_NAME = 'update-kindle-cannoli-NS6575_user_8349_0013323246980.bin'
OTA_MD5 = '0bf96b3cd8fca30fdd12f61208e44d76'
OTA_PAGE = ('https://ftvdb.com/echo/firmware/com.amazon.cannoli.android.os/'
            '0bf96b3cd8fca30fdd12f61208e44d76-13323246980-fire-os-6-5-7-5-ns6575-8349-2026-10-27/')
# Builds a unit can arrive on, older than 8349: each takes the one-way upgrade.
OLDER_BUILDS = {
    4936: '6.5.6.1',
    6562: '6.5.7.3',
    7577: '6.5.7.3',
    8149: '6574.1',
    8338: '6.5.7.5',
    8344: '6.5.7.5',
}

# The protected volumes 8349 brings, as firebreak checks them (firebreak/cannoli.py PREREQUISITES): a
# unit whose heads hash to these carries 8349's TEE, SCP and MCUPM, whatever its build props say.
PREREQUISITES = {
    'mcupm_a': (122880, '37857df6481383e0a2a69d1e4e27935cb64f5b1df17fa5f74b77ab647c8dcaed'),
    'scp_a': (1028096, 'e241c5c3faa563efd48dd6a2361d4a1b04ecf9faecf4fc73d6ba5d0f505d20ba'),
    'tee1': (647168, 'b3be918bb0e3dd74e5e7b436809bfd83b9667e9ce58c0081e29aa8d78e7199d6'),
    'tee2': (647168, 'b3be918bb0e3dd74e5e7b436809bfd83b9667e9ce58c0081e29aa8d78e7199d6'),
}
STOCK_BOOT_SHA256 = '548dd73848cfcc76cb921ec5d8c68438c95886f4fc7187da9fa5f4b7efb8b201'

# The 8349 BL2. The image is 249856 bytes in a 262144-byte partition, and flash_image fills the rest of
# the last erase block with zeros, so a copy is compared over the image's length only.
BRHGPTLK_SHA256 = '86e7da8927151fd5fd93ca00e03eeee6fe487233a8f1336db0d2713c6f19b13f'
BRHGPTLK_LEN = 249856
BRHGPTLK = ['brhgptlk_%d' % n for n in range(4)]
UPGRADE_PARTS = ('tee1.img', 'tee2.img', 'scp.img', 'mcupm.img', 'boot.img', 'uboot.img', 'system.img',
                 'brhgptlk_0.img')

# ---------------------------------------------------------------------------------------------- the unit

# Left out of the backup: cache and userdata are mounted and changing under Fire OS, and userdata is
# where everything this installer writes goes, beside Fire OS's own files.
SKIP_VOLUMES = ('cache', 'userdata')
# The unit's Wi-Fi and Bluetooth, as build-cannoli-image.py and build-cannoli-bt-payload.py take them,
# and the DSP's firmware the microphones will need.
MODULES = ('compat', 'cfg80211', 'mac80211', 'wlan_mt76x3_sdio_prealloc', 'wlan_mt76x3_sdio', 'btmtksdio')
FIRMWARE = ('WIFI_RAM_CODE_MT7663.bin', 'mt7663_patch_e2_hdr.bin', 'EEPROM_MT7663.bin', 'TxPwrLimit_MT76x3.dat',
            'wifi.cfg', 'regulatory.db', 'bt.cfg', 'dsp_cancheese.bin', 'hifi5dsp_load.bin')
FIRMWARE_DIRS = ('/vendor/firmware', '/system/vendor/firmware', '/system/etc/firmware')
MODULE_DIRS = ('/system/lib/modules', '/vendor/lib/modules')

# Where TECHO5 keeps its state on userdata, as on the other Dots.
STORE = '/data/techo5-store/techo5'
LINUX_DIR = '/data/techo5-linux'
STATE = '/data/misc/echolocal'
END = 'T5-END'
# Room left on userdata once the slot is in (the Bluetooth payload, the wake words, Fire OS's own), and
# the smallest part of the root filesystem worth pushing.
SPARE = 16 << 20
MIN_PART = 2 << 20
# A gift install's: the marker that starts first run, and a supplicant configuration with no network.
FIRSTRUN_MARKER = dot.FIRSTRUN_MARKER
EMPTY_WIFI_CONF = dot.EMPTY_WIFI_CONF

# After slot a is unpacked: what init-cannoli needs to boot it is there, and the store says to boot it
# (slotctl's layout: good, since it was checked here, and active). The count of slot boots that never
# became healthy starts again from zero, so a unit that gave up on an older slot tries this one.
SLOT_DONE = r'''S=$1
WANT=$2
BB=$3
for f in sbin/init bin/busybox.static etc/techo5/boot.sh usr/local/bin/echod; do
	[ -e $S/slots/a/$f ] || { echo "slot a has no /$f"; exit 1; }
done
[ -L $S/slots/a/bin/sh ] || { echo "slot a has no /bin/sh link: its symlinks were not made"; exit 1; }
HAVE=$(($($BB find $S/slots/a | $BB wc -l) - 1))
[ "$HAVE" -ge "$WANT" ] || { echo "slot a has $HAVE of the tarball's $WANT paths"; exit 1; }
: > $S/.techo5-store
echo good > $S/slots/a.state
echo a > $S/active
echo "0 install" > $S/tries
rm -f $S/rescue
sync
echo "slot a: $(cat $S/slots/a/etc/techo5-release)"
'''


def build_of(props):
    """The Fire OS build a unit runs, as (build id, long number), from its build props; either is None
    when it cannot be read. The id is in the display id (NS6575/8349) or the version name; the long
    number is ro.build.version.number (13323246980 for 8349)."""
    build = None
    for key in ('ro.build.display.id', 'ro.build.version.name', 'ro.build.version.incremental', 'ro.build.fingerprint'):
        m = re.search(r'NS\d{4}[/_](?:user_)?(\d{4,5})\b', props.get(key, ''))
        if m:
            build = int(m.group(1))
            break
    if build is None:
        inc = props.get('ro.build.version.incremental', '').strip()
        if re.fullmatch(r'\d{4,5}', inc):
            build = int(inc)
    number = None
    m = re.fullmatch(r'0*(\d{8,})', props.get('ro.build.version.number', '').strip())
    if m:
        number = int(m.group(1))
    return build, number


def gate(build, number):
    """What the build means for the install: 'ok' (8349), 'upgrade' (older, takes the one-way 8349
    upgrade), 'newer' (stop: TECHO5's slot B is built for 8349's kernel) or 'unknown' (stop: nothing to go by). The
    id decides when known; the long number otherwise, compared with 8349's."""
    if build is not None:
        if build == TARGET_BUILD:
            return 'ok'
        return 'upgrade' if build < TARGET_BUILD else 'newer'
    if number is not None:
        if number == TARGET_NUMBER:
            return 'ok'
        return 'upgrade' if number < TARGET_NUMBER else 'newer'
    return 'unknown'


def build_text(build, number):
    if build is None and number is None:
        return 'an unknown build'
    if build is None:
        return 'build number %d' % number
    if build == TARGET_BUILD:
        return 'Fire OS 6.5.7.5 (%s)' % TARGET_NAME
    if build in OLDER_BUILDS:
        return 'Fire OS %s (%d)' % (OLDER_BUILDS[build], build)
    return 'build %d' % build


def classify(adb_state, props, uid, port_installed):
    """The unit's state, from what adb says. adb_state is get-state's answer ('' when adb does not see
    it), props its build props, uid the shell's after adb root, port_installed whether echo-dot-assist's
    port is on userdata. Fastboot is told apart by the caller (fastboot devices)."""
    if adb_state == 'recovery':
        return 'twrp'
    if adb_state != 'device':
        return 'none'
    if uid == '0':
        return 'rooted'
    if port_installed:
        # Rooted by firebreak, but this shell is not root: a bundle from before keyless adb.
        return 'rooted-keyed'
    return 'stock'


def parse_props(text):
    return dict(re.findall(r'^([\w.]+)=(.*)$', text, re.M))


def parse_ubi(text):
    """'ubi0_N name bytes' lines into [(N, name, bytes)]."""
    out = []
    for line in text.splitlines():
        m = re.fullmatch(r'ubi0_(\d+) (\S+) (\d+)', line.strip())
        if m:
            out.append((int(m.group(1)), m.group(2), int(m.group(3))))
    return sorted(out)


def parse_mtd(text):
    """/proc/mtd into {name: (N, size)}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r'mtd(\d+): ([0-9a-f]+) [0-9a-f]+ "([^"]+)"', line.strip())
        if m:
            out[m.group(3)] = (int(m.group(1)), int(m.group(2), 16))
    return out


def fit_image(data):
    """A FIT cut out of a whole UBI volume, at its own length rounded up to 4 KiB (the bundle's images
    are exactly that, and so is what U-Boot's hash covers)."""
    if data[:4] != b'\xd0\x0d\xfe\xed':
        return None
    total = int.from_bytes(data[4:8], 'big')
    return data[:total + (-total % 4096)]


def is_techo5_fit(data):
    """Whether a slot-B FIT is one this installer built: its ramdisk is ours (init-cannoli's
    techo5-release line), not TWRP's."""
    import lzma
    img = _load('cannoli_image', os.path.join(HERE, 'linux', 'build-cannoli-image.py'))
    try:
        rd = img.Fdt(data).get('/images/ramdisk-1', 'data')
        return b'techo5-cannoli' in lzma.decompress(rd)
    except Exception:
        return False


class Unit:
    """The unit over adb, as root: every read that decides something ends with a marker the check
    requires (adb shell's exit status cannot be trusted, and it can cut a tail)."""

    def __init__(self, adb, stage):
        self.adb, self.stage = adb, stage
        self.bb = stage + '/busybox'

    def sh(self, command):
        return self.adb.sh(command)

    def checked(self, command, what):
        out = self.adb.sh('%s; echo %s' % (command, END))
        if not out.endswith(END):
            fail('no complete answer from the unit for %s (%r)' % (what, out[-200:]))
        return out[:-len(END)].strip()

    def props(self):
        keys = ('ro.product.device', 'ro.build.product', 'ro.build.display.id', 'ro.build.version.number',
                'ro.build.version.name', 'ro.build.version.incremental', 'ro.build.fingerprint', 'ro.boot.slot_suffix')
        return parse_props(self.checked('for p in %s; do echo "$p=$(getprop $p)"; done' % ' '.join(keys), 'its build props'))

    def setup_busybox(self, busybox):
        self.sh('mkdir -p %s' % self.stage)
        self.adb.push(busybox, self.bb)
        self.sh('chmod 755 %s' % self.bb)
        if self.sha256(self.bb) != hash_file(busybox):
            fail('the busybox pushed to the unit does not match %s' % busybox)

    def sha256(self, path, length=None):
        if length is None:
            out = self.checked('%s sha256sum %s' % (self.bb, path), 'the hash of ' + path)
        else:
            out = self.checked('%s head -c %d %s | %s sha256sum' % (self.bb, length, path, self.bb), 'the hash of ' + path)
        h = out.split()[0] if out else ''
        if not re.fullmatch(r'[0-9a-f]{64}', h):
            fail('no hash of %s from the unit (%r)' % (path, out))
        return h

    def volumes(self):
        return parse_ubi(self.checked('for d in /sys/class/ubi/ubi0_*; do [ -e $d/name ] && '
                                      'echo "${d##*/} $(cat $d/name) $(cat $d/data_bytes)"; done', 'its UBI volumes'))

    def mtd(self):
        return parse_mtd(self.checked('cat /proc/mtd', '/proc/mtd'))

    def mtd_node(self, n):
        node = self.checked('for d in /dev/mtd%d /dev/mtd/mtd%d; do [ -c $d ] && { echo $d; break; }; done' % (n, n), 'mtd%d' % n)
        if not node:
            fail('no device node for mtd%d on the unit' % n)
        return node

    def find_file(self, name, dirs):
        return self.checked('for d in %s; do [ -f $d/%s ] && { echo $d/%s; break; }; done' % (' '.join(dirs), name, name), name)


# ---------------------------------------------------------------------------------------------- backups

def read_manifest(path):
    rows = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                p = line.rstrip('\n').split('\t')
                if len(p) == 4 and p[0] != 'file':
                    rows[p[0]] = (p[1], int(p[2]), p[3])
    return rows


def write_manifest(path, rows):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('file\tname\tbytes\tsha256\n')
        for k in sorted(rows, key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s)]):
            f.write('%s\t%s\t%d\t%s\n' % ((k,) + rows[k]))


def copy_checked(adb, command, out, want, size, what):
    """A raw read from the unit into out, kept only when its length and sha256 are the unit's."""
    tmp = out + '.partial'
    if adb.exec_out_to_file(command, tmp) != 0:
        fail('reading %s failed. %s' % (what, dot.CABLE))
    got_size, got = os.path.getsize(tmp), hash_file(tmp)
    if (size is not None and got_size != size) or got != want:
        os.remove(tmp)
        fail('the copy of %s does not match the unit (%d bytes, sha256 %s; the unit says %s bytes, %s). %s'
             % (what, got_size, got, size, want, dot.CABLE))
    os.replace(tmp, out)


def back_up(unit, m0, with_files):
    """Every UBI volume but cache and userdata as u_<N>.bin and the four brhgptlk copies as
    brhgptlk_<n>.bin, the m0/ layout, each checked against the unit's own sha256; and, from Fire OS
    (with_files), the drivers and firmware into modules/ and firmware/. A file already there and the
    same is kept; one that differs is kept too, and the new copy saved beside it with a time stamp.
    Returns {volume name: file}."""
    os.makedirs(m0, exist_ok=True)
    manifest = os.path.join(m0, 'ubi-sha256.txt')
    rows = read_manifest(manifest)
    by_name = {}

    def keep(key, name, size, want, command):
        out = os.path.join(m0, key + '.bin')
        if os.path.exists(out) and os.path.getsize(out) == size and hash_file(out) == want:
            note('%-10s %s already backed up' % (name, key + '.bin'))
            return out
        dest = out if not os.path.exists(out) else os.path.join(m0, '%s-%s.bin' % (key, time.strftime('%Y%m%d-%H%M%S')))
        copy_checked(unit.adb, command, dest, want, size, name)
        rows[os.path.basename(dest)[:-4]] = (name, size, want)
        write_manifest(manifest, rows)
        note('%-10s %d bytes, sha256 ok%s' % (name, size, '' if dest == out else ' (changed since %s; saved as %s)' % (key + '.bin', os.path.basename(dest))))
        return dest

    for n, name, size in unit.volumes():
        if name in SKIP_VOLUMES:
            continue
        dev = '/dev/ubi0_%d' % n
        by_name[name] = keep('u_%d' % n, name, size, unit.sha256(dev), 'cat ' + dev)
    mtd = unit.mtd()
    for name in BRHGPTLK:
        if name not in mtd:
            fail('the unit has no %s in /proc/mtd' % name)
        n, size = mtd[name]
        node = unit.mtd_node(n)
        by_name[name] = keep(name, name, size, unit.sha256(node), 'cat ' + node)
    if with_files:
        for sub, names, dirs, ext in (('modules', MODULES, MODULE_DIRS, '.ko'), ('firmware', FIRMWARE, FIRMWARE_DIRS, '')):
            os.makedirs(os.path.join(m0, sub), exist_ok=True)
            for base in names:
                src = unit.find_file(base + ext, dirs)
                if not src:
                    note('%s is not on the unit (%s)' % (base + ext, ', '.join(dirs)))
                    continue
                out = os.path.join(m0, sub, base + ext)
                want = unit.sha256(src)
                if os.path.exists(out) and hash_file(out) == want:
                    continue
                copy_checked(unit.adb, 'cat ' + src, out, want, None, src)
        note('drivers and firmware: %d modules, %d firmware files' % (
            len(os.listdir(os.path.join(m0, 'modules'))), len(os.listdir(os.path.join(m0, 'firmware')))))
    return by_name


# ---------------------------------------------------------------------------------------------- firebreak

# The firebreak the installer was tested with, when there is no checkout: no release on PyPI has the
# cannoli route yet, so it is the commit on Gamer92000/firebreak that added cannoli-upgrade and keyless adb.
FIREBREAK_PIN = 'git+https://github.com/Gamer92000/firebreak@c0ad855b412cc76a562033a5a1a32a0955dd36fe'


def firebreak_cmd(checkout, *args):
    """firebreak's command line: uv run in its checkout (the cannoli route), else uvx from the pinned commit."""
    if checkout:
        return ['uv', 'run', '--project', checkout, 'firebreak'] + list(args)
    return ['uvx', '--from', FIREBREAK_PIN, 'firebreak'] + list(args)


def on_wsl():
    try:
        with open('/proc/version') as f:
            return 'microsoft' in f.read().lower()
    except OSError:
        return False


def run_firebreak(a, args, dry_run, expect_fail=None):
    """Runs firebreak, its output shown as it goes. Returns (exit code, output). In a dry run the command
    is printed, not run."""
    cmd = firebreak_cmd(a.firebreak, *args)
    note('firebreak: ' + ' '.join('"%s"' % c if ' ' in c else c for c in cmd))
    if dry_run:
        return 0, ''
    if on_wsl() and args[0] == 'unlock':
        note("this runs in WSL, where firebreak's preloader catch has not worked: the BL2 USB window is "
             'shorter than usbipd passes it on. Run the installer from a Windows Python if it does not catch.')
    lines = []
    p = subprocess.Popen(cmd, cwd=a.firebreak or None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        line = raw.decode('utf-8', 'replace')
        sys.stdout.write('   | ' + line)
        sys.stdout.flush()
        lines.append(line)
    p.wait()
    out = ''.join(lines)
    if p.returncode != 0 and not (expect_fail and expect_fail in out):
        fail('firebreak %s stopped (exit %d); its output is above. Nothing more was written by this installer.'
             % (args[0], p.returncode))
    return p.returncode, out


def check_bundle(bundle, serial):
    """firebreak's per-unit bundle: built for this serial and for 8349."""
    path = os.path.join(bundle or '', 'bundle.json')
    if not bundle or not os.path.exists(path):
        fail('no firebreak bundle for %s%s. It comes from cannoli-os-builder and the TWRP builder on an x86-64 '
             'Linux host (WSL is fine), for this serial; docs/install-cannoli.md has the commands. Pass it with --bundle.'
             % (serial, ' at %s' % bundle if bundle else ''))
    with open(path) as f:
        m = json.load(f)
    if m.get('product') != 'cannoli' or m.get('serial') != serial or m.get('build') != TARGET_NAME:
        fail('%s is for %s %s on %s, not this cannoli %s on %s' % (path, m.get('product'), m.get('serial'), m.get('build'),
                                                                      serial, TARGET_NAME))
    if m.get('adb') != 'keyless':
        # firebreak refuses it before it writes anything, but only after the power prompts: said here first.
        fail('%s has no "adb": "keyless": it comes from a cannoli-os-builder before 74903ad, which baked the build '
             "host's adb key into slot A, and firebreak refuses it. Rebuild the bundle with cannoli-os-builder (no "
             '--adb-key, a fresh --output; docs/install-cannoli.md), then run this again. Nothing was written.' % path)
    return m


def check_ota_parts(parts):
    if not parts or not os.path.isdir(parts):
        fail('the 8349 upgrade needs the unpacked 8349 OTA (%s, MD5 %s, from %s), unpacked with payload_dump.py '
             'into a folder of %s; there is none at %s. Pass it with --ota-parts.'
             % (OTA_NAME, OTA_MD5, OTA_PAGE, ', '.join(UPGRADE_PARTS), parts))
    missing = [p for p in UPGRADE_PARTS if not os.path.exists(os.path.join(parts, p))]
    if missing:
        fail('%s lacks %s' % (parts, ', '.join(missing)))
    if hash_file(os.path.join(parts, 'boot.img')) != STOCK_BOOT_SHA256:
        fail('%s/boot.img is not 8349\'s boot image' % parts)
    check_bl2_image(os.path.join(parts, 'brhgptlk_0.img'))


def check_bl2_image(path):
    """8349's BL2 as the OTA unpacks it: there, and the right length and hash, before anyone is asked."""
    if not os.path.exists(path):
        fail("brhgptlk needs 8349's BL2 at %s, from the unpacked 8349 OTA (%s, MD5 %s, from %s, unpacked with "
             'payload_dump.py); there is none. Pass the folder with --ota-parts. Nothing was written.'
             % (path, OTA_NAME, OTA_MD5, OTA_PAGE))
    if os.path.getsize(path) != BRHGPTLK_LEN or hash_file(path) != BRHGPTLK_SHA256:
        fail("%s is not 8349's brhgptlk (sha256 %s expected). Nothing was written." % (path, BRHGPTLK_SHA256))


def check_cannoli(serial, props):
    if 'cannoli' not in (props.get('ro.product.device', ''), props.get('ro.build.product', '')):
        fail('%s is %r, not an Echo Dot 5th gen (cannoli); nothing more was written' % (serial, props.get('ro.product.device')))


def check_ota(path):
    if path:
        got = hash_file(path, 'md5')
        if got != OTA_MD5:
            fail('%s has MD5 %s, not 8349\'s %s' % (path, got, OTA_MD5))
        note('8349 OTA %s, MD5 ok' % os.path.basename(path))


# ---------------------------------------------------------------------------------------------- main

def wait_state(adb, states, seconds, what):
    end = time.time() + seconds
    said = time.time()
    while time.time() < end:
        if adb.state() in states:
            return adb.state()
        time.sleep(3)
        if time.time() - said >= 30:
            note('still waiting for %s' % what)
            said = time.time()
    fail('timed out after %d s waiting for %s' % (seconds, what))


def prompt(text):
    if not interactive():
        fail('nobody is at the terminal for this step: %s' % text)
    input('   %s, then press Enter: ' % text)


def to_twrp(adb):
    if adb.state() == 'recovery':
        return
    prompt('Unplug the Dot\'s power, hold Volume-Up, plug the power back in and keep holding about 5 s (TWRP, slot B)')
    wait_state(adb, ('recovery',), 120, 'TWRP on adb')


def to_fireos(adb):
    if adb.state() == 'device':
        return
    adb.reboot()
    note('rebooting into Fire OS (slot A)')
    wait_state(adb, ('device',), 240, 'Fire OS on adb')
    adb.root()


def root_shell(adb):
    """adb as root on the unit: keyless root adb is firebreak's slot A, and TWRP's shell is root."""
    if adb.sh('id -u') != '0':
        adb.root()
    return adb.sh('id -u') == '0'


def prerequisites_8349(unit):
    vols = {name: n for n, name, _ in unit.volumes()}
    out = {}
    for name, (length, want) in PREREQUISITES.items():
        out[name] = name in vols and unit.sha256('/dev/ubi0_%d' % vols[name], length) == want
    return out


def flash_brhgptlk(unit, image, dry_run):
    """The four copies of the 8349 BL2, from TWRP: each is skipped when it already holds it, written with
    flash_image otherwise, and read back over the image's length."""
    have = {}
    for name in BRHGPTLK:
        have[name] = unit.checked('dump_image %s %s/bl2 >/dev/null 2>&1 && %s head -c %d %s/bl2 | %s sha256sum; rm -f %s/bl2'
                                  % (name, unit.stage, unit.bb, BRHGPTLK_LEN, unit.stage, unit.bb, unit.stage), name).split(' ')[0]
    todo = [n for n in BRHGPTLK if have[n] != BRHGPTLK_SHA256]
    if not todo:
        note('all four brhgptlk copies hold 8349\'s BL2')
        return
    note('brhgptlk to write: %s' % ', '.join(todo))
    if dry_run:
        return
    unit.adb.push(image, unit.stage + '/brhgptlk.img')
    if unit.sha256(unit.stage + '/brhgptlk.img') != BRHGPTLK_SHA256:
        fail('the brhgptlk image pushed to the unit differs; nothing written')
    for name in todo:
        unit.sh('flash_image %s %s/brhgptlk.img' % (name, unit.stage))
        back = unit.checked('dump_image %s %s/bl2 >/dev/null 2>&1 && %s head -c %d %s/bl2 | %s sha256sum; rm -f %s/bl2'
                            % (name, unit.stage, unit.bb, BRHGPTLK_LEN, unit.stage, unit.bb, unit.stage), name).split(' ')[0]
        if back != BRHGPTLK_SHA256:
            fail('%s reads back %s, not 8349\'s BL2. Do not power the unit off; run the installer again from TWRP.' % (name, back))
        note('%s written and read back' % name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--serial', help="the unit's USB serial (adb devices, or fastboot devices with the action button held)")
    ap.add_argument('--unit', help="the unit's folder: bundle/, stock-8349-parts/, builds/ (default ../cannoli-<serial>)")
    ap.add_argument('--bundle', help="firebreak's bundle for this unit (default <unit>/bundle)")
    ap.add_argument('--firebreak', default=os.environ.get('FIREBREAK') or os.path.join(os.path.dirname(repo_root()), 'firebreak'),
                    help='a firebreak checkout with the cannoli route (default ../firebreak; uvx from a pinned commit when missing)')
    ap.add_argument('--ota-parts', help='the unpacked 8349 OTA, for a unit on an older build (default <unit>/stock-8349-parts)')
    ap.add_argument('--ota', help='the 8349 OTA itself (%s), to check its MD5' % OTA_NAME)
    ap.add_argument('--accept-efuse', action='store_true', help="answer the one-way 8349 upgrade's question with yes")
    ap.add_argument('--accept-bl2', action='store_true',
                    help="answer the question before a rooted 8349 unit's brhgptlk copies are written with yes")
    ap.add_argument('--rootfs', help="build-dot-rootfs.py --board cannoli's tarball (default build/techo5-cannoli-rootfs.tar.gz)")
    ap.add_argument('--rescue-only', action='store_true', help='no root filesystem: slot B boots the rescue environment')
    ap.add_argument('--hwfixup', help='cannoli_hwfixup.ko (default build/cannoli-hwfixup/, else <unit>/builds/)')
    ap.add_argument('--no-bluetooth', action='store_true', help='leave out the Bluetooth payload')
    ap.add_argument('--name', help='the name Home Assistant shows (asked for when missing)')
    ap.add_argument('--key-file', help='where the Home Assistant key is kept (default backups/<serial>/home-assistant.key)')
    ap.add_argument('--wifi', '--wifi-ssid', dest='wifi_ssid', help="a Wi-Fi network instead of the one Fire OS saved")
    ap.add_argument('--wifi-passphrase-file', help='a file holding the --wifi passphrase')
    ap.add_argument('--ssh-key', help='an SSH public key allowed to log in as root')
    ap.add_argument('--wake-words', default='okay_nabu,hey_jarvis,hey_mycroft,alexa,' + ','.join(dot.EXTRA_MODELS))
    ap.add_argument('--for-someone-else', action='store_true',
                    help='a gift install: no Wi-Fi, name or key is asked for or written; the Dot starts its own setup '
                    '(Improv over Bluetooth) when it first boots slot B')
    ap.add_argument('--dry-run', action='store_true', help='checks, backups and builds; write nothing to the unit')
    ap.add_argument('--backups', default=default_dir('TECHO5_BACKUPS', 'backups'))
    ap.add_argument('--work', default=default_dir('TECHO5_WORK', 'build'))
    ap.add_argument('--inputs', default=os.environ.get('TECHO5_INPUTS') or os.path.join(repo_root(), 'inputs'))
    ap.add_argument('--adb', default='adb')
    ap.add_argument('--fastboot', default='fastboot')
    a = ap.parse_args()
    # --ssh-key is refused here too: the daemon's first run wipes the state directory's ssh/ the first time
    # it sees the marker, so a key written with it would be gone by the first SSH login.
    check_gift(a, **{'--name': a.name, '--wifi': a.wifi_ssid, '--wifi-passphrase-file': a.wifi_passphrase_file,
                     '--key-file': a.key_file, '--ssh-key': a.ssh_key})
    if a.for_someone_else and a.rescue_only:
        fail("--for-someone-else needs the root filesystem: the rescue environment has no setup of its own")
    need(a.adb, 'install the Android platform tools (adb)')
    if not os.path.isdir(a.firebreak or ''):
        a.firebreak = None
    ssh_key = None
    if a.ssh_key:
        with open(os.path.expanduser(a.ssh_key)) as f:
            ssh_key = f.read().strip()
        if not ssh_key.startswith(('ssh-', 'ecdsa-', 'sk-')) or len(ssh_key.split()) < 2:
            fail('%s does not look like an SSH public key' % a.ssh_key)
    if a.name is not None:
        a.name = ask_name(a.name, '')
    busybox = os.path.join(a.inputs, 'busybox.static')
    if not os.path.exists(busybox):
        fail('no %s (tools/fetch-inputs.py in TECHO5): the unit\'s own shell has no sha256sum or head -c' % busybox)

    # ---------------------------------------------------------------------------------------- 1
    if not a.serial:
        units = [u for u in adb_devices(a.adb) if u[1] in ('device', 'recovery', 'unauthorized', 'offline')]
        if len(units) != 1:
            fail('pass --serial: %s' % ('no unit is on adb' if not units else 'several are: ' + ', '.join(u[0] for u in units)))
        a.serial = units[0][0]
    a.unit = os.path.abspath(a.unit or os.path.join(os.path.dirname(repo_root()), 'cannoli-' + a.serial))
    bundle = a.bundle or os.path.join(a.unit, 'bundle')
    ota_parts = a.ota_parts or os.path.join(a.unit, 'stock-8349-parts')
    adb = Adb(a.serial, a.adb)
    step('device %s' % a.serial)
    adb_state = adb.state()
    # Stock Fire OS's adb is secure, and with no screen there is no accepting this computer's key: a stock
    # unit in setup mode lists as unauthorized (or offline, while it decides), and get-state says nothing.
    listed = dict((u[0], u[1]) for u in adb_devices(a.adb)).get(a.serial, '')
    locked = adb_state not in ('device', 'recovery') and listed in ('unauthorized', 'offline')
    if locked:
        note('adb lists it as %s: stock Fire OS, whose adb takes no key without a screen to accept it. Its build '
             'cannot be read over adb, so firebreak unlocks it first (its BL2 and fastboot path needs no adb) and the '
             'build is read from the rooted slot A afterwards.' % listed)
    elif adb_state not in ('device', 'recovery'):
        fb = Fastboot(a.serial, a.fastboot)
        if os.path.exists(a.fastboot) or __import__('shutil').which(a.fastboot):
            if fb.present():
                fail('%s is in the RAM fastboot (action button held at power-on). Its Fire OS build cannot be read '
                     'there. Unplug the power and plug it back in without a button, let it start (stock: orange '
                     'ring; rooted: blue), and run this again.' % a.serial)
        fail('adb does not see %s (state %r). With D+, D- and GND soldered to USB and the 15 V adapter in, a stock '
             'or rooted unit shows on adb; none at all means the solder joints, or the preloader fallback (Volume-Up '
             'and action held for about 30 s).' % (a.serial, adb_state))
    twrp = adb_state == 'recovery'
    stage = '/tmp/techo5' if twrp else '/data/local/tmp/techo5'
    unit = Unit(adb, stage)
    if locked:
        props, state = {}, 'stock'
    else:
        if not twrp:
            root_shell(adb)
        uid = adb.sh('id -u')
        props = unit.props()
        check_cannoli(a.serial, props)
        port = adb.sh('[ -e /data/local/hassmic ] && echo yes') == 'yes'
        state = classify(adb_state, props, uid, port)
    if state == 'rooted-keyed':
        fail('%s runs the rooted port, but adb is not root: its bundle is from before keyless adb. Rebuild the bundle '
             '(cannoli-os-builder, no --adb-key) and run `firebreak unlock --device cannoli` again.' % a.serial)
    note({'stock': 'stock Fire OS, not rooted (setup mode)', 'rooted': 'rooted Fire OS in slot A (firebreak)',
          'twrp': 'TWRP in slot B'}[state])

    # ---------------------------------------------------------------------------------------- 2
    if locked:
        # 'unread': gated after the unlock, from the rooted slot A.
        verdict = 'unread'
    elif state == 'twrp':
        unit.setup_busybox(busybox)
        pre = prerequisites_8349(unit)
        verdict = 'ok' if all(pre.values()) else 'upgrade'
        note('8349 TEE, SCP and MCUPM: %s' % ', '.join('%s %s' % (k, 'yes' if v else 'no') for k, v in sorted(pre.items())))
    else:
        build, number = build_of(props)
        verdict = gate(build, number)
        note('%s, slot %s' % (build_text(build, number), props.get('ro.boot.slot_suffix') or '?'))
        if state == 'rooted':
            unit.setup_busybox(busybox)
            pre = prerequisites_8349(unit)
            if verdict == 'ok' and not all(pre.values()):
                fail('%s says 8349 but its protected volumes are not 8349\'s (%s); nothing was written'
                     % (a.serial, ', '.join(k for k, v in pre.items() if not v)))
    if verdict == 'newer':
        fail('%s runs a Fire OS newer than %s. TECHO5\'s slot B is built for %s: its kernel modules\' CRCs and '
             'the stock boot FIT are that build\'s. Nothing was written. For which builds firebreak\'s unlock works '
             'on, see its cannoli guide (%s). Keep the unit off Wi-Fi so it takes no further update.' % (a.serial, TARGET_NAME, TARGET_NAME, FIREBREAK_GUIDE))
    if verdict == 'unknown':
        fail('the Fire OS build of %s cannot be read (%s); nothing was written' % (a.serial, props))
    if state == 'stock' or verdict == 'upgrade':
        # firebreak needs the bundle for the unlock: a wrong or old one is said now, before any question.
        check_bundle(bundle, a.serial)
    if verdict == 'upgrade':
        check_ota(a.ota)
        check_ota_parts(ota_parts)
        print()
        lines = ['%s needs Fire OS %s first. firebreak cannoli-upgrade writes 8349\'s TEE, SCP, MCUPM, boot, U-Boot'
                 % (a.serial, TARGET_NAME),
                 'and system, then the four brhgptlk BL2 copies follow from TWRP.',
                 'This is ONE-WAY: the first 8349 TEE boot burns the anti-rollback eFuse, and the 2022 firmware',
                 'can never come back (an Amazon OTA to 8349 does the same).']
        if a.dry_run:
            for line in lines:
                note(line)
        else:
            confirm(a.accept_efuse, lines, 'UPGRADE')

    # ---------------------------------------------------------------------------------------- 3
    if state == 'stock' or verdict == 'upgrade':
        step('unlock with firebreak')
        check_bundle(bundle, a.serial)
        backup = os.path.join(a.backups, a.serial, 'firebreak-%s' % time.strftime('%Y%m%d-%H%M%S'))
        unlock = ['unlock', '--device', 'cannoli', '--bundle', bundle, '--serial', a.serial, '--backup', backup]
        upgrade = ['cannoli-upgrade', '--device', 'cannoli', '--bundle', bundle, '--serial', a.serial, '--ota-parts', ota_parts]
        if verdict == 'upgrade' and state == 'stock':
            # The unlock puts TWRP in slot B, then refuses the launch firmware's volumes: expected here.
            run_firebreak(a, unlock, a.dry_run, expect_fail='Unsupported firmware prerequisite')
        elif verdict == 'unread':
            # A launch-firmware unit shows itself only here: the unlock puts TWRP in slot B and then refuses
            # its volumes. From TWRP the installer reads them and takes the 8349 upgrade, after asking.
            code, out = run_firebreak(a, unlock, a.dry_run, expect_fail='Unsupported firmware prerequisite')
            if code != 0:
                fail('%s still runs launch firmware older than %s: firebreak put TWRP in slot B and stopped. Run the '
                     'installer again with the unit in TWRP (power on holding Volume-Up); it reads the volumes there and '
                     'takes the one-way 8349 upgrade, after asking.' % (a.serial, TARGET_NAME))
        elif state == 'stock':
            run_firebreak(a, unlock, a.dry_run)
        if a.dry_run:
            if verdict == 'upgrade':
                run_firebreak(a, upgrade, True)
                run_firebreak(a, unlock, True)
                note('then the four brhgptlk copies from TWRP')
            print('\nDry run: checks done; the unit is not rooted yet, so its volumes cannot be read for the backup '
                  'and the build. Nothing was written.')
            return
        if verdict == 'upgrade':
            to_twrp(adb)
            unit = Unit(adb, '/tmp/techo5')
            unit.setup_busybox(busybox)
            m0 = os.path.join(a.backups, a.serial, 'm0')
            step('backups before the upgrade, to %s' % m0)
            back_up(unit, m0, False)
            run_firebreak(a, upgrade, False)
            run_firebreak(a, unlock, False)
            to_twrp(adb)
            unit = Unit(adb, '/tmp/techo5')
            unit.setup_busybox(busybox)
            step('brhgptlk, the 8349 BL2')
            flash_brhgptlk(unit, os.path.join(ota_parts, 'brhgptlk_0.img'), False)
    if state != 'rooted' or verdict == 'upgrade':
        if a.dry_run:
            print('\nDry run from TWRP: checks done. Boot Fire OS (slot A, no button) and run it again for the '
                  'backups and the build. Nothing was written.')
            return
        to_fireos(adb)
        unit = Unit(adb, '/data/local/tmp/techo5')
        if not root_shell(adb):
            fail('Fire OS came up without root adb: slot A is not firebreak\'s rooted Fire OS. Run '
                 '`firebreak unlock --device cannoli` (docs/install-cannoli.md), then this again.')
        unit.setup_busybox(busybox)
        props = unit.props()
        # The unit firebreak unlocked is read again: what adb shows now is what slot B is written for.
        check_cannoli(a.serial, props)
        if gate(*build_of(props)) != 'ok' or not all(prerequisites_8349(unit).values()):
            fail('%s still does not run %s; nothing more was written' % (a.serial, TARGET_NAME))
        state = 'rooted'
    if props.get('ro.boot.slot_suffix') not in ('_a', ''):
        fail('Fire OS runs from slot %r; slot B is written from slot A' % props.get('ro.boot.slot_suffix'))
    flash_brhgptlk_needed = None

    # ---------------------------------------------------------------------------------------- 4
    root = os.path.join(a.backups, a.serial)
    m0 = os.path.join(root, 'm0')
    step('backups to %s' % m0)
    files = back_up(unit, m0, True)
    for name in ('boot_a', 'uboot_b', 'boot_b'):
        if name not in files:
            fail('the unit has no %s volume' % name)
    with open(files['boot_a'], 'rb') as f:
        boot_a = fit_image(f.read())
    if not boot_a or hashlib.sha256(boot_a).hexdigest() != STOCK_BOOT_SHA256:
        fail("boot_a is not Fire OS 8349's stock boot FIT: firebreak's slot A always is. Nothing was written.")
    bl2 = {}
    for name in BRHGPTLK:
        with open(files[name], 'rb') as f:
            bl2[name] = hashlib.sha256(f.read()[:BRHGPTLK_LEN]).hexdigest() == BRHGPTLK_SHA256
    # The way back to TWRP: the first boot_b backup that is not ours.
    twrp_copy = os.path.join(root, 'twrp-boot_b.img')
    with open(files['boot_b'], 'rb') as f:
        boot_b = fit_image(f.read())
    if not boot_b:
        fail('boot_b holds no FIT; nothing was written')
    if is_techo5_fit(boot_b):
        note('boot_b already holds a TECHO5 image')
        if not os.path.exists(twrp_copy) and os.path.exists(os.path.join(bundle, 'boot_b.img')):
            with open(os.path.join(bundle, 'boot_b.img'), 'rb') as f:
                write_bytes(twrp_copy, f.read())
    elif not os.path.exists(twrp_copy):
        write_bytes(twrp_copy, boot_b)
    if os.path.exists(twrp_copy):
        note('TWRP, the way back to slot B recovery: %s' % twrp_copy)
    else:
        note('no copy of TWRP to go back with: keep the bundle (%s)' % os.path.join(bundle, 'boot_b.img'))
    techo5_in_b = is_techo5_fit(boot_b)
    if not all(bl2.values()):
        flash_brhgptlk_needed = [n for n, ok in bl2.items() if not ok]
        # Everything the BL2 step needs is checked before the question: 8349's image, and TWRP to write it
        # from (a reinstall over TECHO5 puts the TWRP copy back in boot_b first).
        check_bl2_image(os.path.join(ota_parts, 'brhgptlk_0.img'))
        if techo5_in_b and not os.path.exists(twrp_copy):
            fail('brhgptlk is written from TWRP, but boot_b holds TECHO5 and there is no copy of TWRP (%s, or the '
                 "bundle's boot_b.img) to put back first. Nothing was written." % twrp_copy)
        lines = ['brhgptlk still holds the launch BL2 (%s): under Fire OS 8349 the ring stays dark.'
                 % ', '.join(flash_brhgptlk_needed),
                 "The install writes 8349's BL2 to them from TWRP before slot B is written, each read back.",
                 'A BL2 is the first thing the unit boots: a copy left bad by a power cut while it is written can',
                 'stop it starting.']
        if techo5_in_b:
            lines.append('boot_b holds TECHO5 now, so TWRP goes back into it first.')
        if a.dry_run:
            for line in lines:
                note(line)
        else:
            confirm(a.accept_bl2, lines, 'BL2')

    # ---------------------------------------------------------------------------------------- 5
    step("this unit's slot-B image")
    builds = os.path.join(root, 'builds')
    os.makedirs(builds, exist_ok=True)
    image = os.path.join(builds, 'techo5-cannoli-boot_b.img')
    hwfixup = a.hwfixup or first_file(os.path.join(repo_root(), 'build', 'cannoli-hwfixup', 'cannoli_hwfixup.ko'),
                                      os.path.join(a.unit, 'builds', 'cannoli_hwfixup.ko'))
    cmd = [sys.executable, os.path.join(HERE, 'linux', 'build-cannoli-image.py'), '--unit', root, '--inputs', a.inputs,
           '--boot', files['boot_a'], '--uboot', files['uboot_b'], '-o', image]
    if hwfixup:
        cmd += ['--hwfixup', hwfixup]
    run_tool(cmd, 'the slot-B image')
    with open(image, 'rb') as f:
        if not is_techo5_fit(f.read()):
            fail('%s is not a TECHO5 slot-B image' % image)
    bt = None
    if not a.no_bluetooth:
        bt = os.path.join(builds, 'techo5-cannoli-bt.tar.gz')
        need_bt = [os.path.join(repo_root(), 'build', 'cannoli-bt-modules', 'load-order'),
                   os.path.join(repo_root(), 'build', 'btbridge', 'btbridge'), os.path.join(a.inputs, 'apks-bt-dot')]
        missing = [p for p in need_bt if not os.path.exists(p)]
        if missing:
            note('no Bluetooth payload: %s missing (docs/building.md, Bluetooth); --no-bluetooth silences this'
                 % ', '.join(missing))
            bt = None
        else:
            run_tool([sys.executable, os.path.join(HERE, 'linux', 'build-cannoli-bt-payload.py'), '--unit', root,
                      '--inputs', a.inputs, '-o', bt], 'the Bluetooth payload')
    rootfs = None
    if not a.rescue_only:
        rootfs = os.path.abspath(a.rootfs or os.path.join(a.work, 'techo5-cannoli-rootfs.tar.gz'))
        if not os.path.exists(rootfs):
            if not a.dry_run:
                fail('no root filesystem at %s; build it, pass --rootfs, or --rescue-only for the rescue environment' % rootfs)
            note('no root filesystem at %s yet (a real run needs it, or --rescue-only)' % rootfs)
            rootfs = None
        else:
            err = push.board_error(rootfs)
            if err:
                fail(err + '. Nothing was written.')
            note('root filesystem %s, %d bytes: %s' % (rootfs, os.path.getsize(rootfs), push.rootfs_release(rootfs)))

    # ---------------------------------------------------------------------------------------- 6
    step('Wi-Fi and Home Assistant identity')
    wifi_conf, name, psk, shown, key_file = identity(a, adb, root)
    models = None
    have_models = int(adb.sh('ls %s/models/*.tflite 2>/dev/null | wc -l' % STATE) or 0)
    if have_models == 0:
        models = fetch_models(a.work, a.wake_words)
    else:
        note('%d wake word models already on the unit' % have_models)

    if a.dry_run:
        print('\nDry run: checks, backups and builds done; nothing written to %s.' % a.serial)
        print('  backup:          %s' % m0)
        print('  slot-B image:    %s' % image)
        print('  Bluetooth:       %s' % (bt or 'none'))
        print('  root filesystem: %s' % (rootfs or 'none'))
        if flash_brhgptlk_needed:
            print('  brhgptlk:        %s would be written from TWRP' % ', '.join(flash_brhgptlk_needed))
        return

    # ---------------------------------------------------------------------------------------- 7
    # brhgptlk first, from TWRP, which is in slot B only until boot_b is written; then back to Fire OS.
    if flash_brhgptlk_needed:
        if techo5_in_b:
            step('TWRP back into boot_b, for the BL2')
            write_boot_b(unit, twrp_copy, twrp_copy)
        to_twrp(adb)
        unit = Unit(adb, '/tmp/techo5')
        unit.setup_busybox(busybox)
        step('brhgptlk, the 8349 BL2')
        flash_brhgptlk(unit, os.path.join(ota_parts, 'brhgptlk_0.img'), False)
        to_fireos(adb)
        unit = Unit(adb, '/data/local/tmp/techo5')
        if not root_shell(adb):
            fail('Fire OS came up without root adb after the BL2; slot B was not written. Run the installer again.')
        unit.setup_busybox(busybox)
        props = unit.props()
        check_cannoli(a.serial, props)
        if props.get('ro.boot.slot_suffix') not in ('_a', ''):
            fail('Fire OS runs from slot %r after the BL2; slot B is written from slot A' % props.get('ro.boot.slot_suffix'))
    stage = unit.stage

    step('boot_b, slot B')
    write_boot_b(unit, image, twrp_copy)

    step('userdata: root filesystem, Bluetooth, identity')
    adb.sh('mkdir -p %s %s %s; chmod 700 %s' % (STORE, LINUX_DIR, STATE, STATE))
    if rootfs:
        unpack_rootfs(unit, rootfs, a.work, stage)
    if bt:
        adb.push(bt, LINUX_DIR + '/bt.tar.gz')
        if unit.sha256(LINUX_DIR + '/bt.tar.gz') != hash_file(bt):
            fail('the Bluetooth payload on the unit differs from %s' % bt)
        note('Bluetooth payload on userdata')
    tmp = os.path.join(a.work, 'provision-' + a.serial)
    os.makedirs(tmp, exist_ok=True)

    def push_text(text, remote, mode='600'):
        local = os.path.join(tmp, os.path.basename(remote))
        with open(local, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        adb.push(local, remote)
        os.remove(local)
        adb.sh('chmod %s %s' % (mode, remote))

    if ssh_key:
        adb.sh('mkdir -p %s/ssh; chmod 700 %s/ssh' % (STATE, STATE))
        have = adb.sh('cat %s/ssh/authorized_keys 2>/dev/null' % STATE)
        if ssh_key.split()[1] not in have:
            push_text('\n'.join(x for x in (have, ssh_key) if x) + '\n', STATE + '/ssh/authorized_keys')
        note('SSH key set')
    if wifi_conf:
        push_text(wifi_conf, LINUX_DIR + '/wifi.conf')
        note('Wi-Fi network written')
    if name:
        push_text(name, STATE + '/name')
        push_text(psk, STATE + '/psk')
        note("provisioned '%s'" % name)
    if a.for_someone_else:
        # An empty file in the state directory (echod's layout.StateDir): the Dot starts its own setup
        # (first run) and wipes what a giver left the first time it sees it. Nothing of the giver's stays:
        # techo5-net uses a kept wpa_supplicant.conf as it is and falls back to the network Fire OS saved
        # only when there is none, so a configuration with no network goes in.
        adb.sh('rm -f %s/name %s/psk %s/wifi.conf' % (STATE, STATE, LINUX_DIR))
        push_text('', FIRSTRUN_MARKER)
        push_text(EMPTY_WIFI_CONF, LINUX_DIR + '/wpa_supplicant.conf')
        note('first-run marker written; the name, key and any saved network are cleared')
    if models:
        adb.sh('mkdir -p %s/models' % STATE)
        for m in sorted(os.listdir(models)):
            adb.push(os.path.join(models, m), STATE + '/models/' + m)
        adb.sh('chmod 644 %s/models/*' % STATE)
    os.rmdir(tmp)
    adb.sh('rm -rf %s; sync' % stage)

    # ---------------------------------------------------------------------------------------- 8
    step('first boot of slot B')
    print('   Unplug the Dot\'s power, hold Volume-Up, plug it back in and keep holding about 5 s. Any power-on')
    print('   without Volume-Up starts Fire OS from slot A, as before.')
    wait_healthy(a, shown, key_file, rootfs is not None)


def write_boot_b(unit, image, twrp_copy):
    """An image into boot_b from Fire OS with ubiupdatevol, pushed and read back by sha256."""
    vols = dict((nm, (n, b)) for n, nm, b in unit.volumes())
    n, room = vols['boot_b']
    size = os.path.getsize(image)
    if size > room:
        fail('%s is %d bytes; boot_b holds %d' % (image, size, room))
    want = hash_file(image)
    remote = unit.stage + '/boot_b.img'
    unit.adb.push(image, remote)
    if unit.sha256(remote) != want:
        fail('the image pushed to the unit differs; nothing written')
    unit.checked('$(command -v ubiupdatevol || echo /system/vendor/bin/ubiupdatevol) /dev/ubi0_%d %s'
                 % (n, remote), 'ubiupdatevol')
    back = unit.sha256('/dev/ubi0_%d' % n, size)
    unit.sh('rm -f %s' % remote)
    if back != want:
        fail('boot_b reads back %s, not %s. Do not boot slot B. TWRP is at %s: ubiupdatevol /dev/ubi0_%d with it '
             'from Fire OS puts it back.' % (back, want, twrp_copy, n))
    note('written and read back, sha256 %s' % want)


def rootfs_names(rootfs):
    """The paths a root filesystem tarball makes, each once, and the bytes its files hold."""
    names, size = set(), 0
    with tarfile.open(rootfs, 'r:gz') as t:
        for m in t:
            n = m.name[2:] if m.name.startswith('./') else m.name
            n = n.rstrip('/')
            if n and n != '.':
                names.add(n)
                if m.isfile():
                    size += m.size
    return names, size


def split_rootfs(rootfs, out_dir, limit):
    """The tarball as parts that are each a whole tarball of their own, in order, each holding at most
    limit bytes of its files (so at most about that much compressed, as a part is never bigger than what
    it holds). A part goes onto the unit, is checked, unpacked and deleted before the next: userdata
    after a factory reset has about 155 MB free, and the slot alone takes 63 MiB. Hard links name a file
    of an earlier part or their own, which is on disk by then."""
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, old))
    parts, out, held = [], None, 0
    with tarfile.open(rootfs, 'r:gz') as src:
        for m in src:
            if out is None or (held and held + m.size > limit):
                if out:
                    out.close()
                path = os.path.join(out_dir, 'part-%03d.tar.gz' % len(parts))
                out = tarfile.open(path, 'w:gz', format=src.format)
                parts.append(path)
                held = 0
            out.addfile(m, src.extractfile(m) if m.isfile() else None)
            held += m.size if m.isfile() else 0
    if out:
        out.close()
    return parts


def unpack_rootfs(unit, rootfs, work, stage):
    """The root filesystem into the store's slot a, in parts that fit the room userdata has: each pushed,
    checked by sha256 on the unit, unpacked with tar's own exit status and errors read back, and deleted.
    Then SLOT_DONE checks the slot has every path the tarball has, the four init-cannoli boots, and a
    symlink (busybox's tar makes symlinks last, so a tar cut short loses them first)."""
    names, size = rootfs_names(rootfs)
    S = STORE
    # An earlier slot a goes first: the room is short. It is marked bad before anything of it goes, so a
    # run cut short here leaves no active slot a that init-cannoli would boot half-written: slotctl falls
    # back to slot b if it is good, or the ramdisk stays in rescue. SLOT_DONE marks it good at the end.
    unit.checked('mkdir -p %s/slots && echo bad > %s/slots/a.state && sync && rm -rf %s/slots/a %s/slots/a.old '
                 '%s/incoming-*.tar.gz && mkdir %s/slots/a' % ((S,) * 6), 'the slot')
    free = unit.checked("%s df -Pk /data | %s tail -n 1 | %s awk '{print $4}'" % (unit.bb, unit.bb, unit.bb), 'free space')
    if not free.isdigit():
        fail('could not read the free space on userdata (%r)' % free)
    free = int(free) * 1024
    # What the slot takes on UBIFS: its files, and a block for every path; then room for what comes after
    # it (the Bluetooth payload, the wake words).
    slot = size + 4096 * len(names)
    room = free - slot - SPARE
    if room < MIN_PART:
        fail('userdata has %d MB free; the slot needs about %d MB and %d MB besides. Nothing of the root filesystem '
             'was written. Free room in Fire OS (or factory-reset it) and run the installer again.'
             % (free >> 20, slot >> 20, (SPARE + MIN_PART) >> 20))
    whole = os.path.getsize(rootfs)
    parts = [rootfs] if whole <= room else split_rootfs(rootfs, os.path.join(work, 'cannoli-rootfs-parts'), room)
    note('unpacking the root filesystem into slot a: %d paths, %d MB, in %d part%s (userdata has %d MB free)'
         % (len(names), size >> 20, len(parts), '' if len(parts) == 1 else 's', free >> 20))
    for i, part in enumerate(parts):
        remote = '%s/incoming-%03d.tar.gz' % (S, i)
        unit.adb.push(part, remote)
        got = unit.sha256(remote)
        if got != hash_file(part):
            unit.sh('rm -f %s' % remote)
            fail('part %d of the root filesystem arrived as %s, not %s (cut short over USB). %s'
                 % (i + 1, got, hash_file(part), dot.CABLE))
        out = unit.checked('%s tar xzf %s -C %s/slots/a 2>%s/tar.err; echo "rc=$?"; %s head -c 2000 %s/tar.err; '
                           'rm -f %s %s/tar.err' % (unit.bb, remote, S, stage, unit.bb, stage, remote, stage), 'tar')
        if not out.startswith('rc=0'):
            fail('unpacking part %d of the root filesystem failed (%s). boot_b is written; run the installer again.'
                 % (i + 1, out))
    script = os.path.join(work, 'techo5-cannoli-slot.sh')
    with open(script, 'w', newline='\n') as f:
        f.write(SLOT_DONE)
    unit.adb.push(script, stage + '/slot-done.sh')
    os.remove(script)
    result = unit.sh('sh %s/slot-done.sh %s %d %s 2>&1' % (stage, S, len(names), unit.bb))
    if not result.startswith('slot a: '):
        fail('the root filesystem is not whole in slot a: %s\nboot_b is written; run the installer again.' % result)
    if len(parts) > 1:
        for part in parts:
            os.remove(part)
    note(result)


def identity(a, adb, root):
    """Step 6: the Wi-Fi network and the Home Assistant name and key, as install-dot.py has them. Returns
    (wifi_conf, name, psk, shown, key_file): what to write (None for nothing), and the name and key to
    print at the end. A gift install (--for-someone-else) asks for none of it and writes none of it."""
    if a.for_someone_else:
        note('gift install: no Wi-Fi network, name or Home Assistant key is asked for or written; the new '
             'owner gives it a network over Bluetooth (Improv) and their Home Assistant adds it')
        return None, None, None, None, None
    wifi_conf = None
    saved = adb.sh('sed -n "s/^[ \\t]*ssid=//p" /data/misc/wifi/wpa_supplicant.conf 2>/dev/null | head -1')
    own_wifi = adb.sh('test -s %s/wifi.conf && echo yes' % LINUX_DIR) == 'yes'
    if saved and not own_wifi and not a.wifi_ssid and interactive():
        if input('   Fire OS has the Wi-Fi network %s saved. Join it? [Y/n]: ' % saved).strip().lower() in ('n', 'no'):
            saved = ''
    if a.wifi_ssid or (not saved and not own_wifi):
        ssid = a.wifi_ssid
        if not ssid:
            if not interactive():
                fail('%s has no saved Wi-Fi network: pass --wifi, or --for-someone-else to leave the network to '
                     'Improv over Bluetooth' % a.serial)
            ssid = ask('Wi-Fi network name')
        if a.wifi_passphrase_file:
            with open(a.wifi_passphrase_file, encoding='utf-8-sig') as f:
                wifi_conf = techo5_wifi_conf(ssid, f.read().rstrip('\r\n'))
        else:
            wifi_conf = ask_wifi(ssid)
        note("will join '%s'" % ssid)
    elif own_wifi:
        note('Wi-Fi network set on the unit before; keeping it')
    else:
        note('saved Wi-Fi network %s (the one Fire OS saved)' % saved)
    have_name = adb.sh('cat %s/name 2>/dev/null' % STATE)
    have_key = adb.sh('cat %s/psk 2>/dev/null' % STATE)
    key_file = a.key_file or os.path.join(root, 'home-assistant.key')
    name, psk = None, None
    if have_name and valid_api_key(have_key):
        note("keeping '%s' and its key: Home Assistant sees the same device" % have_name)
        if not os.path.exists(key_file):
            write_private(key_file, have_key)
        shown = (have_name, have_key)
    else:
        name = ask_name(a.name, 'Echo Dot 5')
        if os.path.exists(key_file):
            with open(key_file) as f:
                psk = f.read().strip()
            if not valid_api_key(psk):
                fail('the key in %s is not 32 bytes of base64' % key_file)
        else:
            psk = new_api_key()
            write_private(key_file, psk)
            note('new key written to %s' % key_file)
        note("will provision '%s'" % name)
        shown = (name, psk)
    return wifi_conf, name, psk, shown, key_file


def write_bytes(path, data):
    tmp = path + '.partial'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def first_file(*paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def run_tool(cmd, what):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode('utf-8', 'replace').strip()
    for line in out.splitlines():
        if line.strip() and not line.startswith(('write it', '  adb', 'then power', 'copy it', '  cmd', '  ssh')):
            note(line)
    if r.returncode != 0:
        fail('building %s failed' % what)


def fetch_models(work, wake_words):
    models = os.path.join(work, 'models')
    os.makedirs(models, exist_ok=True)
    words = [w.strip() for w in wake_words.split(',') if w.strip()]
    for w in words:
        if '%s.tflite' % w not in dot.MODEL_SHA256:
            fail("no wake word model '%s' is pinned" % w)
        tf, js = os.path.join(models, w + '.tflite'), os.path.join(models, w + '.json')
        if w in dot.EXTRA_MODELS:
            path, phrase = dot.EXTRA_MODELS[w]
            download_checked('%s/%s' % (dot.EXTRA_MODELS_REPO, path), tf, dot.MODEL_SHA256[w + '.tflite'])
            if not os.path.exists(js):
                with open(js, 'w') as f:
                    json.dump({'wake_word': phrase, 'model': w + '.tflite', 'trained_languages': ['en']}, f, indent=2)
        else:
            for ext, out in (('json', js), ('tflite', tf)):
                download_checked('%s/%s.%s' % (dot.MODELS_REPO, w, ext), out, dot.MODEL_SHA256['%s.%s' % (w, ext)])
    note('wake word models: %s' % ', '.join(words))
    return models


def boot_verdict(kv, with_rootfs):
    """What the console's answers say: 'healthy' (a slot booted and its boot.sh reset the try count to
    "0 healthy" once echod stayed up), 'rescue' (the ramdisk, no slot), 'booting', or '' for no answer.
    Without a root filesystem, the rescue with an address is as far as it goes."""
    if not kv:
        return ''
    slot = kv.get('slot', '').strip()
    if slot:
        return 'healthy' if kv.get('tries', '').strip().startswith('0 healthy') else 'booting'
    if not with_rootfs and kv.get('ip', '').strip():
        return 'healthy'
    return 'rescue' if 'rescue' in kv.get('release', '') else 'booting'


def wait_healthy(a, shown, key_file, with_rootfs):
    """The unit on its USB console (the ACM gadget init-cannoli and the slot's boot.sh both make, under
    its serial): a slot booted and healthy, on Wi-Fi, and Home Assistant's API port answering."""
    console = Console(a.serial, CONSOLE_TECHO5)
    query = ('echo slot=$(cat /run/techo5/slot 2>/dev/null); echo release=$(cat /etc/techo5-release 2>/dev/null); '
             'echo echod=$(pidof echod); echo tries=$(cat /store/techo5/tries /data/techo5-store/techo5/tries 2>/dev/null | head -1); '
             "echo ip=$(ip -4 addr show wlan0 2>/dev/null | sed -n 's/.*inet \\([0-9.]*\\).*/\\1/p' | head -1)")
    start, said, kv, verdict = time.time(), '', {}, ''
    while time.time() < start + 10 * 60:
        out = console.run(query, 8)
        if out:
            kv = dict(re.findall(r'^(\w+)=(.*)$', out, re.M))
            verdict = boot_verdict(kv, with_rootfs)
            now = '%s, slot %s, address %s, echod %s' % (kv.get('release'), kv.get('slot') or 'none',
                                                         kv.get('ip') or 'none yet', 'running' if kv.get('echod') else 'not running')
            if now != said:
                note(now)
                said = now
            if verdict == 'healthy':
                break
        elif time.time() - start > 60 and int(time.time() - start) % 30 < 5:
            h = console.waiting_hint()
            if h:
                note(h)
        time.sleep(5)
    ip = (kv.get('ip') or '').strip()
    if not ip and shown and dot.node_slug(shown[0]):
        ip = dot.find_on_network(dot.node_slug(shown[0])) or ''
    up = bool(ip) and with_rootfs and dot.api_port_open(ip)
    print()
    if shown:
        print("Home Assistant will discover '%s' as an ESPHome device. When it asks for the encryption key, paste:\n\n"
              "    %s\n\n(also saved in %s)\n" % (shown[0], shown[1], key_file))
    if a.for_someone_else and verdict == 'healthy':
        print('Done: %s runs TECHO5 Linux from slot B (slot %s), healthy, and starts its own setup: about 45 s'
              % (a.serial, kv.get('slot')))
        print('after it starts, its ring breathes and it offers Improv over Bluetooth. Give it a network from Home')
        print("Assistant (or improv-wifi.com); its owner's Home Assistant then adds it with nothing typed. Every")
        print('power-on with Volume-Up held boots it; without, Fire OS starts from slot A.')
    elif verdict == 'healthy' and with_rootfs:
        print('Done: %s runs TECHO5 Linux from slot B (slot %s), healthy%s.' % (
            a.serial, kv.get('slot'), ", and Home Assistant's API port answers at %s:6053" % ip if up else ''))
        print('Every power-on with Volume-Up held boots it; without, Fire OS starts from slot A.')
    elif verdict == 'healthy':
        print('Done: %s runs the TECHO5 rescue environment from slot B at %s (SSH with your key).' % (a.serial, ip))
    elif kv:
        print('%s booted slot B but is not confirmed healthy (%s). /data/techo5-linux/first-boot.log says what the '
              'ramdisk did; techo5-retry on the console tries the slots again. A power-on without Volume-Up goes back '
              'to Fire OS.' % (a.serial, verdict))
        sys.exit(1)
    else:
        print("This PC never saw the unit's USB console. Check that Volume-Up was held at power-on (the console comes "
              'up within a minute); a power-on without it starts Fire OS.')
        sys.exit(1)


if __name__ == '__main__':
    run_main(main)
