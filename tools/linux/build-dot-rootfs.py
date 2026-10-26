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
Windows, Linux and macOS alike; needs Python 3.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def main():
    j = os.path.join
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--daemon', required=True, help='echod built with -tags dot (-tags dot,donut for the Dot 3)')
    ap.add_argument('--board', choices=('dot', 'donut'), default='dot',
                    help='dot: the Echo Dot 2nd gen (biscuit, radar); donut: the Echo Dot 3rd gen')
    ap.add_argument('--wpa-supplicant', default=j(REPO, 'build', 'wpa_supplicant', 'wpa_supplicant'),
                    help="with --board donut: build-wpa-supplicant.sh's")
    ap.add_argument('--bt-modules', default=j(REPO, 'build', 'bt-modules'),
                    help="with --board donut: build-bt-modules.sh's cmac.ko, bluetooth.ko and hci_vhci.ko")
    ap.add_argument('--release', required=True, help='the version this root filesystem is, e.g. v0.6.0')
    ap.add_argument('--wmtup', default=j(REPO, 'bin', 'wmtup'))
    ap.add_argument('--btbridge', default=j(REPO, 'bin', 'btbridge'))
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
    busybox = j(a.inputs, 'busybox.static')
    modules = [j(a.bt_modules, m + '.ko') for m in ('cmac', 'bluetooth', 'hci_vhci')]
    needs = [a.daemon, a.btbridge, a.bluealsa, busybox] + ([a.wpa_supplicant] + modules if donut else [a.wmtup])
    for f in needs:
        if not os.path.exists(f):
            sys.exit('missing %s (docs/building.md says how to fetch or build it)' % f)
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
    if donut:
        # One overlay: the Dot 2's, with the Dot 3's files over it.
        tmp = tempfile.mkdtemp()
        overlay = j(tmp, 'overlay')
        shutil.copytree(j(HERE, 'rootfs'), overlay, symlinks=True)
        shutil.copytree(j(HERE, 'rootfs-donut'), overlay, symlinks=True, dirs_exist_ok=True)
    cmd = [sys.executable, j(HERE, 'mkrootfs.py'), '--rootfs', alpine[0],
           '--apkdir', j(a.inputs, 'apks-dot'), '--apkdir', j(a.inputs, 'apks-bt-dot'),
           '--apkdir', j(a.inputs, 'apks-rootfs-dot'),
           '--add', busybox + '=/bin/busybox.static',
           '--add', a.daemon + '=/usr/local/bin/echod', '--add', a.btbridge + '=/usr/local/bin/btbridge',
           '--add', a.bluealsa + '=/usr/bin/bluealsa', '--overlay', overlay,
           # avahi announces only what the AirPlay and Spotify Connect receivers register, not SSH.
           '--drop', 'etc/avahi/services/ssh.service', '--drop', 'etc/avahi/services/sftp-ssh.service',
           '--release', '%s %s (%s)' % ('techo5-donut' if donut else 'techo5-dot', a.release, commit), '-o', a.out]
    if donut:
        cmd += ['--add', a.wpa_supplicant + '=/sbin/wpa_supplicant']
        for m in modules:
            cmd += ['--data', '%s=/usr/lib/techo5/modules/%s' % (m, os.path.basename(m))]
    else:
        cmd += ['--add', a.wmtup + '=/usr/local/bin/wmtup']
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
