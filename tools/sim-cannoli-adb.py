#!/usr/bin/env python3
"""A pretend adb for rehearsing tools/install-cannoli.py with no Echo Dot 5 attached: it answers the
installer's commands from a unit's m0/ backup (u_<N>.bin, brhgptlk_<n>.bin, modules/, firmware/), so a
dry run checks, backs up and builds exactly as it would on that unit.

    TECHO5_SIM_M0=../cannoli-<serial>/m0 TECHO5_SIM_SERIAL=<serial> \\
        python3 tools/install-cannoli.py --adb tools/sim-cannoli-adb.py --serial <serial> --dry-run

TECHO5_SIM_STATE is the unit's state: rooted (default; Fire OS 8349 in slot A, root adb), stock (not
rooted), recovery (TWRP), or none. TECHO5_SIM_BUILD sets ro.build.display.id (default NS6575/8349) and
TECHO5_SIM_NUMBER ro.build.version.number. TECHO5_SIM_DIR is where pushed files land (default a folder
in the system's temp directory). It only reads the backup; it writes nothing but its own folder.

It knows the commands install-cannoli.py sends and nothing else: anything it does not recognise prints
nothing, as a missing file would on the unit.
"""
import hashlib
import os
import re
import shutil
import sys
import tempfile

M0 = os.environ.get('TECHO5_SIM_M0', '')
SERIAL = os.environ.get('TECHO5_SIM_SERIAL', 'G0000SIMULATED00')
STATE = os.environ.get('TECHO5_SIM_STATE', 'rooted')
DIR = os.environ.get('TECHO5_SIM_DIR') or os.path.join(tempfile.gettempdir(), 'techo5-sim-cannoli')
END = 'T5-END'

# The unit's UBI volumes by number, as an Echo Dot 5 lays them out (ubi0_11 and ubi0_12 are not in m0/).
NAMES = {0: 'uboot_a', 1: 'uboot_b', 2: 'tee1', 3: 'tee2', 4: 'idme_nand', 5: 'persist', 6: 'misc', 7: 'boot_a',
         8: 'boot_b', 9: 'system_a', 10: 'system_b', 11: 'cache', 12: 'userdata', 13: 'mcupm_a', 14: 'mcupm_b',
         15: 'scp_a', 16: 'scp_b'}
SIZES = {11: 5160960, 12: 204193792}
PROPS = {
    'ro.product.device': 'cannoli',
    'ro.build.product': 'cannoli',
    'ro.build.display.id': os.environ.get('TECHO5_SIM_BUILD', 'NS6575/8349'),
    'ro.build.version.number': os.environ.get('TECHO5_SIM_NUMBER', '0013323246980'),
    'ro.build.version.name': 'Fire OS 6.5.7.5 (NS6575)',
    'ro.build.version.incremental': '',
    'ro.build.fingerprint': '',
    'ro.boot.slot_suffix': '_a',
}


def local(path):
    """Where a path on the unit is in the backup, or in the pushed files."""
    m = re.fullmatch(r'/dev/ubi0_(\d+)', path)
    if m:
        written = os.path.join(DIR, 'ubi0_%s.bin' % m.group(1))
        return written if os.path.exists(written) else os.path.join(M0, 'u_%s.bin' % m.group(1))
    m = re.fullmatch(r'/dev/mtd(?:/mtd)?(\d)', path)
    if m:
        return os.path.join(M0, 'brhgptlk_%s.bin' % m.group(1))
    m = re.fullmatch(r'/(?:system|vendor)/lib/modules/(.+\.ko)', path)
    if m:
        return os.path.join(M0, 'modules', m.group(1))
    m = re.fullmatch(r'/(?:system/)?vendor/firmware/(.+)', path)
    if m:
        return os.path.join(M0, 'firmware', m.group(1))
    return os.path.join(DIR, path.lstrip('/').replace('/', '__'))


def read(path, length=None):
    with open(local(path), 'rb') as f:
        return f.read() if length is None else f.read(length)


def shell(cmd):
    out = []
    marked = cmd.endswith('; echo %s' % END)
    if marked:
        cmd = cmd[:-len('; echo %s' % END)]
    if cmd == 'id -u' or cmd == 'id':
        out.append('0' if STATE in ('rooted', 'recovery') else '2000')
    elif cmd.startswith('for p in ') and 'getprop' in cmd:
        out += ['%s=%s' % kv for kv in PROPS.items()]
    elif cmd.startswith('for d in /sys/class/ubi/'):
        for n, name in sorted(NAMES.items()):
            p = os.path.join(M0, 'u_%d.bin' % n)
            size = os.path.getsize(p) if os.path.exists(p) else SIZES.get(n)
            if size:
                out.append('ubi0_%d %s %d' % (n, name, size))
    elif cmd == 'cat /proc/mtd':
        out.append('dev:    size   erasesize  name')
        for n in range(4):
            p = os.path.join(M0, 'brhgptlk_%d.bin' % n)
            size = os.path.getsize(p) if os.path.exists(p) else 0x40000
            out.append('mtd%d: %08x 00020000 "brhgptlk_%d"' % (n, size, n))
    elif cmd.startswith('for d in /dev/mtd'):
        m = re.search(r'/dev/mtd(\d)', cmd)
        out.append('/dev/mtd' + m.group(1))
    elif cmd.startswith('for d in ') and '[ -f $d/' in cmd:
        m = re.match(r'for d in (.*?); do \[ -f \$d/(\S+) \]', cmd)
        for d in m.group(1).split():
            if os.path.exists(local(d + '/' + m.group(2))):
                out.append(d + '/' + m.group(2))
                break
    else:
        m = re.fullmatch(r'\S+ head -c (\d+) (\S+) \| \S+ sha256sum', cmd)
        h = re.fullmatch(r'\S+ sha256sum (\S+)', cmd)
        if m and os.path.exists(local(m.group(2))):
            out.append('%s  -' % hashlib.sha256(read(m.group(2), int(m.group(1)))).hexdigest())
        elif h and os.path.exists(local(h.group(1))):
            out.append('%s  %s' % (hashlib.sha256(read(h.group(1))).hexdigest(), h.group(1)))
        elif cmd.startswith('ls ') and 'wc -l' in cmd:
            out.append('0')
        elif re.search(r'ubiupdatevol\)? /dev/ubi0_\d+ ', cmd):
            # A volume update replaces the whole volume with the image.
            m = re.search(r'ubiupdatevol\)? /dev/ubi0_(\d+) (\S+)', cmd)
            shutil.copyfile(local(m.group(2)), os.path.join(DIR, 'ubi0_%s.bin' % m.group(1)))
        elif re.fullmatch(r'sh \S+/slot-done\.sh \S+ 2>&1', cmd):
            unpacked = os.path.join(DIR, 'slot-a.tar.gz')
            out.append('slot a: simulated' if os.path.exists(unpacked) else 'slot a has no /sbin/init')
        elif cmd.startswith('[ -e /data/local/hassmic ]'):
            out.append('yes' if STATE == 'rooted' else '')
    if marked:
        out.append(END)
    return '\n'.join(x for x in out if x != '')


def main():
    args = sys.argv[1:]
    if args[:1] == ['-s']:
        if args[1] != SERIAL:
            sys.stderr.write("error: device '%s' not found\n" % args[1])
            return 1
        args = args[2:]
    verb = args[0] if args else ''
    os.makedirs(DIR, exist_ok=True)
    if verb == 'devices':
        print('List of devices attached')
        if STATE != 'none':
            print('%s\t%s product:cannoli model:Echo_Dot device:cannoli' % (
                SERIAL, 'recovery' if STATE == 'recovery' else 'device'))
        return 0
    if STATE == 'none':
        sys.stderr.write("error: device '%s' not found\n" % SERIAL)
        return 1
    if verb == 'get-state':
        print('recovery' if STATE == 'recovery' else 'device')
    elif verb in ('root', 'wait-for-device', 'reboot'):
        pass
    elif verb == 'shell':
        text = shell(' '.join(args[1:]))
        if text:
            print(text)
    elif verb == 'exec-out':
        m = re.fullmatch(r'cat (\S+)', ' '.join(args[1:]))
        if not m or not os.path.exists(local(m.group(1))):
            return 1
        with open(local(m.group(1)), 'rb') as f:
            shutil.copyfileobj(f, sys.stdout.buffer, 1 << 20)
    elif verb == 'exec-in':
        # The root filesystem, unpacked as it arrives on the unit: kept here as it came.
        if not re.fullmatch(r'\S+ tar xzf - -C \S+/slots/a', ' '.join(args[1:])):
            return 1
        with open(os.path.join(DIR, 'slot-a.tar.gz'), 'wb') as f:
            shutil.copyfileobj(sys.stdin.buffer, f, 1 << 20)
    elif verb == 'push':
        shutil.copyfile(args[1], local(args[2]))
        print('%s: 1 file pushed.' % args[1])
    else:
        sys.stderr.write('sim-cannoli-adb: %s is not simulated\n' % verb)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
