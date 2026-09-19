#!/usr/bin/env python3
"""Build the Echo Dot's root filesystem tarball: what a Dot running TECHO5 Linux installs into a slot.

    python3 tools/linux/build-dot-rootfs.py --daemon bin/echod-dot --release v0.0.0-test --out build/rootfs.tar.gz

Nothing of any one unit goes in: no firmware (each unit adopts its own into the store), no keys, no Wi-Fi,
no Home Assistant identity, so it can be published. The boot image is not in it: the installer builds that
per unit. Inputs (docs/building.md): the Alpine base, busybox.static, apks-dot/ and apks-bt-dot/ in
TECHO5_INPUTS (default inputs/), and bin/wmtup, bin/btbridge and build/bluealsa/bluealsa.
Windows, Linux and macOS alike; needs Python 3.
"""
import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def main():
    j = os.path.join
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--daemon', required=True, help='echod built with -tags dot')
    ap.add_argument('--release', required=True, help='the version this root filesystem is, e.g. v0.6.0')
    ap.add_argument('--wmtup', default=j(REPO, 'bin', 'wmtup'))
    ap.add_argument('--btbridge', default=j(REPO, 'bin', 'btbridge'))
    ap.add_argument('--bluealsa', default=os.environ.get('TECHO5_BLUEALSA') or j(REPO, 'build', 'bluealsa', 'bluealsa'))
    ap.add_argument('--inputs', default=os.environ.get('TECHO5_INPUTS') or j(REPO, 'inputs'))
    ap.add_argument('--out', default=j(REPO, 'bin', 'techo5-dot-rootfs.tar.gz'))
    a = ap.parse_args()

    busybox = j(a.inputs, 'busybox.static')
    for f in (a.daemon, a.wmtup, a.btbridge, a.bluealsa, busybox):
        if not os.path.exists(f):
            sys.exit('missing %s (docs/building.md says how to fetch or build it)' % f)
    # The wake word models every unit offers: boot.sh adds any a unit is missing.
    models = sorted(glob.glob(j(a.inputs, 'models', '*.tflite')) + glob.glob(j(a.inputs, 'models', '*.json')))
    if not any(m.endswith('.tflite') for m in models):
        sys.exit('no wake word models in %s (tools/fetch-inputs.py in TECHO5)' % j(a.inputs, 'models'))
    alpine = sorted(glob.glob(j(a.inputs, 'alpine-minirootfs-*-armv7.tar.gz')))
    if not alpine:
        sys.exit('no Alpine armv7 minirootfs in %s (tools/fetch-inputs.py in TECHO5)' % a.inputs)
    try:
        commit = subprocess.run(['git', '-C', REPO, 'rev-parse', '--short', 'HEAD'], stdout=subprocess.PIPE).stdout.decode().strip()
    except OSError:
        commit = 'unknown'
    cmd = [sys.executable, j(HERE, 'mkrootfs.py'), '--rootfs', alpine[0],
           '--apkdir', j(a.inputs, 'apks-dot'), '--apkdir', j(a.inputs, 'apks-bt-dot'),
           '--add', busybox + '=/bin/busybox.static', '--add', a.wmtup + '=/usr/local/bin/wmtup',
           '--add', a.daemon + '=/usr/local/bin/echod', '--add', a.btbridge + '=/usr/local/bin/btbridge',
           '--add', a.bluealsa + '=/usr/bin/bluealsa', '--overlay', j(HERE, 'rootfs'),
           '--release', 'techo5-dot %s (%s)' % (a.release, commit), '-o', a.out]
    for m in models:
        cmd += ['--data', '%s=/usr/share/techo5/models/%s' % (m, os.path.basename(m))]
    if subprocess.run(cmd).returncode != 0:
        sys.exit('building the root filesystem failed')
    print('rootfs for release %s -> %s' % (a.release, a.out))


if __name__ == '__main__':
    main()
