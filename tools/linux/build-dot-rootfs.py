#!/usr/bin/env python3
"""Build the Echo Dot's root filesystem tarball: what a Dot running TECHO5 Linux installs into a slot.

    python3 tools/linux/build-dot-rootfs.py --daemon bin/echod-dot --release v0.0.0-test --out build/rootfs.tar.gz

Nothing of any one unit goes in: no firmware (each unit adopts its own into the store), no keys, no Wi-Fi,
no Home Assistant identity, so it can be published. The boot image is not in it: the installer builds that
per unit. Inputs (docs/building.md): the Alpine base, busybox.static, apks-dot/ and apks-bt-dot/ in
TECHO5_INPUTS (default inputs/), and bin/wmtup, bin/btbridge and build/bluealsa/bluealsa; bin/techo5-librespot
when it is there, and bin/techo5-shairport-sync and bin/techo5-nqptp (AirPlay 2) when both are.

--board donut builds the Echo Dot 3rd gen's instead: echod built with -tags dot,donut, the Dot 3's boot
scripts (rootfs-donut, over the Dot 2's), our wpa_supplicant (build-wpa-supplicant.sh) in place of
Alpine's, and the Bluetooth stack its stock kernel lacks as modules (build-bt-modules.sh); no wmtup.
The Echo Input (cupcake) is the Dot 3's board and runs this same root filesystem.

--board cannoli builds the Echo Dot 5th gen's: echod built with -tags dot,cannoli, its boot scripts
(rootfs-cannoli, over the Dot 2's), our wpa_supplicant, and the Bluetooth stack for its stock kernel
(build-cannoli-bt-modules.sh, with their load-order) and bin/ubiblock (cmd/ubiblock, through which boot.sh
adopts the unit's speaker tuning from system_a); no wmtup. Its Wi-Fi and Bluetooth driver and
firmware are the unit's own and stay out: the slot-B ramdisk, built from the unit's backup, adopts them
into the store at boot (init-cannoli). bluez-alsa is build-bluealsa.sh's when built, else Alpine's.
Windows, Linux and macOS alike; needs Python 3.
"""
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def btbridge_text(path):
    """Which btbridge a build takes: its path, sha256, and whether it knows -legacy-advertising (the
    Echo Dot 5th gen's techo5-bt passes it only to one that does)."""
    with open(path, 'rb') as f:
        data = f.read()
    return '%s (sha256 %s, %s -legacy-advertising)' % (path, hashlib.sha256(data).hexdigest(),
                                                       'with' if b'legacy-advertising' in data else 'without')


def main():
    j = os.path.join
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--daemon', required=True, help='echod built with -tags dot (-tags dot,donut for the Dot 3)')
    ap.add_argument('--board', choices=('dot', 'donut', 'cannoli'), default='dot',
                    help='dot: the Echo Dot 2nd gen (biscuit, radar); donut: the Echo Dot 3rd gen and the Echo Input; '
                         'cannoli: the Echo Dot 5th gen')
    ap.add_argument('--wpa-supplicant', default=j(REPO, 'build', 'wpa_supplicant', 'wpa_supplicant'),
                    help="with --board donut or cannoli: build-wpa-supplicant.sh's")
    ap.add_argument('--bt-modules', default=j(REPO, 'build', 'bt-modules'),
                    help="with --board donut: build-bt-modules.sh's cmac.ko, bluetooth.ko and hci_vhci.ko; "
                         "with --board cannoli the default is build/cannoli-bt-modules, build-cannoli-bt-modules.sh's")
    ap.add_argument('--release', required=True, help='the version this root filesystem is, e.g. v0.6.0')
    ap.add_argument('--wmtup', default=j(REPO, 'bin', 'wmtup'))
    ap.add_argument('--ubiblock', default=j(REPO, 'bin', 'ubiblock'),
                    help="with --board cannoli: cmd/ubiblock's, which boot.sh mounts system_a through to adopt "
                         "the unit's speaker tuning")
    ap.add_argument('--btbridge', default=None, help='default: bin/btbridge, else build/btbridge/btbridge')
    ap.add_argument('--librespot', default=j(REPO, 'bin', 'techo5-librespot'),
                    help="Spotify Connect's receiver: techo5's tools/linux/build-librespot.sh writes it as "
                         "bin/techo5-librespot-arm there; left out when missing")
    ap.add_argument('--shairport', default=j(REPO, 'bin', 'techo5-shairport-sync'),
                    help="AirPlay 2's receiver: techo5's tools/linux/build-shairport-sync.sh writes it as "
                         "bin/techo5-shairport-sync-arm there; with --nqptp, left out when either is missing")
    ap.add_argument('--nqptp', default=j(REPO, 'bin', 'techo5-nqptp'),
                    help="the clock AirPlay 2 needs, bin/techo5-nqptp-arm from the same build")
    ap.add_argument('--bluealsa', default=os.environ.get('TECHO5_BLUEALSA') or j(REPO, 'build', 'bluealsa', 'bluealsa'))
    ap.add_argument('--inputs', default=os.environ.get('TECHO5_INPUTS') or j(REPO, 'inputs'))
    ap.add_argument('--out', default=j(REPO, 'bin', 'techo5-dot-rootfs.tar.gz'))
    a = ap.parse_args()

    donut = a.board == 'donut'
    cannoli = a.board == 'cannoli'
    if a.btbridge is None:
        a.btbridge = j(REPO, 'bin', 'btbridge')
        if not os.path.exists(a.btbridge) and os.path.exists(j(REPO, 'build', 'btbridge', 'btbridge')):
            a.btbridge = j(REPO, 'build', 'btbridge', 'btbridge')
    busybox = j(a.inputs, 'busybox.static')
    if cannoli:
        if a.bt_modules == j(REPO, 'build', 'bt-modules'):
            a.bt_modules = j(REPO, 'build', 'cannoli-bt-modules')
        order_file = j(a.bt_modules, 'load-order')
        if not os.path.exists(order_file):
            sys.exit('missing %s (tools/linux/build-cannoli-bt-modules.sh)' % order_file)
        with open(order_file) as f:
            order = [l.strip() for l in f if l.strip()]
        modules = [j(a.bt_modules, m + '.ko') for m in order] + [order_file]
    else:
        modules = [j(a.bt_modules, m + '.ko') for m in ('cmac', 'bluetooth', 'hci_vhci')]
    bluealsa = [] if cannoli and not os.path.exists(a.bluealsa) else [a.bluealsa]
    if not bluealsa:
        print("no %s: Alpine's bluez-alsa 4.3.1 goes in (build-bluealsa.sh has the AAC fix)" % a.bluealsa)
    needs = [a.daemon, a.btbridge, busybox] + bluealsa + ([a.wpa_supplicant] + modules if donut or cannoli else [a.wmtup])
    if cannoli:
        needs.append(a.ubiblock)
    for f in needs:
        if not os.path.exists(f):
            sys.exit('missing %s (docs/building.md says how to fetch or build it)' % f)
    print('btbridge: %s' % btbridge_text(a.btbridge))
    # The wake word models every unit offers: boot.sh adds any a unit is missing.
    models = sorted(glob.glob(j(a.inputs, 'models', '*.tflite')) + glob.glob(j(a.inputs, 'models', '*.json')))
    if not any(m.endswith('.tflite') for m in models):
        sys.exit('no wake word models in %s (tools/fetch-inputs.py in TECHO5)' % j(a.inputs, 'models'))
    alpine = sorted(glob.glob(j(a.inputs, 'alpine-minirootfs-*-armv7.tar.gz')))
    if not alpine:
        sys.exit('no Alpine armv7 minirootfs in %s (tools/fetch-inputs.py in TECHO5)' % a.inputs)
    # One base only: with an old one beside a new one, the first would be taken, and apk add does not
    # upgrade the packages a base already has.
    if len(alpine) > 1:
        sys.exit('more than one Alpine armv7 minirootfs in %s: keep only the one techo5lib.py names' % a.inputs)
    # Every package folder holds exactly its list: mkrootfs.py unpacks whatever is there, so a package
    # never fetched would be left out without a word, and a newer one fetched beside the old would be both.
    for lst, d in (('rescue', 'apks-dot'), ('bt', 'apks-bt-dot'), ('rootfs', 'apks-rootfs-dot')):
        with open(j(HERE, 'packages-%s.txt' % lst)) as f:
            want = set(l.strip() for l in f if l.strip().endswith('.apk') and not l.startswith('#'))
        have = set(n for n in os.listdir(j(a.inputs, d)) if n.endswith('.apk')) if os.path.isdir(j(a.inputs, d)) else set()
        if want != have:
            sys.exit('%s does not match packages-%s.txt (missing: %s; not listed: %s); tools/fetch-inputs.py in TECHO5'
                     % (j(a.inputs, d), lst, ' '.join(sorted(want - have)) or '-', ' '.join(sorted(have - want)) or '-'))
    try:
        commit = subprocess.run(['git', '-C', REPO, 'rev-parse', '--short', 'HEAD'], stdout=subprocess.PIPE).stdout.decode().strip()
    except OSError:
        commit = 'unknown'
    overlay = j(HERE, 'rootfs')
    tmp = None
    if donut or cannoli:
        # One overlay: the Dot 2's, with the board's own files over it.
        tmp = tempfile.mkdtemp()
        overlay = j(tmp, 'overlay')
        shutil.copytree(j(HERE, 'rootfs'), overlay, symlinks=True)
        shutil.copytree(j(HERE, 'rootfs-' + a.board), overlay, symlinks=True, dirs_exist_ok=True)
        if cannoli:
            # The Dot 2's way back is TWRP in recovery; the Dot 5's is a reboot (to-fireos).
            os.remove(j(overlay, 'usr', 'local', 'sbin', 'to-twrp'))
    cmd = [sys.executable, j(HERE, 'mkrootfs.py'), '--rootfs', alpine[0],
           '--apkdir', j(a.inputs, 'apks-dot'), '--apkdir', j(a.inputs, 'apks-bt-dot'),
           '--apkdir', j(a.inputs, 'apks-rootfs-dot'),
           '--add', busybox + '=/bin/busybox.static',
           '--add', a.daemon + '=/usr/local/bin/echod', '--add', a.btbridge + '=/usr/local/bin/btbridge',
           '--overlay', overlay,
           # avahi announces only what the AirPlay and Spotify Connect receivers register, not SSH.
           '--drop', 'etc/avahi/services/ssh.service', '--drop', 'etc/avahi/services/sftp-ssh.service',
           '--release', '%s %s (%s)' % ({'donut': 'techo5-donut', 'cannoli': 'techo5-cannoli'}.get(a.board, 'techo5-dot'),
                                        a.release, commit), '-o', a.out]
    for b in bluealsa:
        cmd += ['--add', b + '=/usr/bin/bluealsa']
    if donut or cannoli:
        cmd += ['--add', a.wpa_supplicant + '=/sbin/wpa_supplicant']
        for m in modules:
            cmd += ['--data', '%s=/usr/lib/techo5/modules/%s' % (m, os.path.basename(m))]
    else:
        cmd += ['--add', a.wmtup + '=/usr/local/bin/wmtup']
    if cannoli:
        cmd += ['--add', a.ubiblock + '=/usr/local/bin/ubiblock']
    # Each program built from others' sources carries its licenses (tools/linux/licenses) in /usr/share/licenses.
    def licenses(name):
        for f in sorted(glob.glob(j(HERE, 'licenses', name, '*'))):
            cmd.extend(['--data', '%s=/usr/share/licenses/%s/%s' % (f, name, os.path.basename(f))])
    if os.path.exists(a.librespot):
        cmd += ['--add', a.librespot + '=/usr/local/bin/techo5-librespot']
        licenses('librespot')
    else:
        print('no %s: the image will not offer Spotify Connect' % a.librespot)
    # AirPlay 2 on both boards: the daemon keeps the classic receiver where it can't run it (it also needs
    # the loopback and /dev/shm, from boot.sh, and the firewall's AirPlay 2 rules, techo5-firewall).
    if os.path.exists(a.shairport) and os.path.exists(a.nqptp):
        cmd += ['--add', a.shairport + '=/usr/local/bin/techo5-shairport-sync', '--add', a.nqptp + '=/usr/local/bin/techo5-nqptp']
        licenses('shairport-sync')
        licenses('ffmpeg')  # its decoders are linked into shairport-sync
        licenses('nqptp')
    else:
        print('no %s or %s: AirPlay will be the classic receiver' % (a.shairport, a.nqptp))
    for m in models:
        cmd += ['--data', '%s=/usr/share/techo5/models/%s' % (m, os.path.basename(m))]
    rc = subprocess.run(cmd).returncode
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    if rc != 0:
        sys.exit('building the root filesystem failed')
    print('rootfs for release %s -> %s' % (a.release, a.out))


if __name__ == '__main__':
    main()
