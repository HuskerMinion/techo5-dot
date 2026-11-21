#!/usr/bin/env python3
"""Choose what an Echo Dot 5th gen (cannoli) boots when no button is held: TECHO5 or Fire OS.

    python3 tools/cannoli-boot-default.py --serial <serial> probe            # RAM test, writes nothing
    python3 tools/cannoli-boot-default.py --serial <serial> techo5           # TECHO5 by default
    python3 tools/cannoli-boot-default.py --serial <serial> fireos           # back to Fire OS by default

The unit keeps one boot selection, slot A, and slot A's U-Boot (firebreak's root-a, from the unit's
bundle) reads the CPLD's Volume-Up twice at power-on. As built by cannoli-os-builder it loads boot_b
(TECHO5) when Volume-Up is held on both reads and goes the stock way (Fire OS) otherwise. `techo5`
writes the same U-Boot with that decision turned round: boot_b unless Volume-Up is held on both reads.
A failed CPLD read and U-Boot's own boot request still go the stock way. Nothing else changes: misc's
slot record stays on A, and the code that boots boot_b is the code Volume-Up runs today.

The image is made here from the unit's own bundle/uboot_a.img: two branch instructions in the U-Boot
payload and the payload's hash in the FIT, which the signature does not cover (firebreak's
CVE-2026-46728 route). Input and output are pinned by sha256, so only firebreak's 8349 root-a goes in.

  probe   loads a test U-Boot into RAM through BL2 (firebreak's listener; the USB pads soldered) that
          only reports the decision: fastboot's product reads cannoli-keys-<read1>-<read2>-<U|N>, U for
          TECHO5, N for Fire OS. Run it once without a button and once with Volume-Up held.
  techo5  from rooted Fire OS (or TWRP): checks uboot_a holds a known image, pushes the new one, checks
  fireos  it on the unit, writes it with ubiupdatevol and reads it back. A readback that differs puts
          the previous image back before anything reboots.

Once TECHO5 is the default, Fire OS is Volume-Up at power-on; a reboot from Fire OS lands in TECHO5.
docs/install-cannoli.md has the details and the way back.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from techo5lib import CONSOLE_TECHO5, Adb, confirm, fail, interactive, list_consoles, note, run_main, step, wait_for  # noqa: E402

# firebreak's root-a uboot_a, as cannoli-os-builder builds it for 8349 (bundle.json's uboot_a; the same
# for every unit), and the U-Boot payload inside it.
ROOT_A_SHA256 = '05881ce6e338b1726c62c5665a4dfcb35a6c63bd513883acb190bc28cb0a2631'
IMAGE_SIZE = 1032192
PAYLOAD_AT = 0xCC
PAYLOAD_LEN = 988520
PAYLOAD_SHA256 = '2c33960c9970b143de7238fb8dff78aaadde95fdf8571ed1010ed600e73b6d43'
# The payload's /images/kernel-1/hash-1 value: what U-Boot checks the payload against.
HASH_AT = 0xF16BC

# What each image hashes to once made from ROOT_A (checked after making it).
TECHO5_SHA256 = 'f6103b5d95a6c885762795593411d77497c9e67a2823e79d426128644a03a246'
PROBE_SHA256 = '6ee1c9ff0db842973a2ccb02638385b3468d6b60e7514cd0c257c73b76daa2bf'

NOP = '1f2003d5'
# (payload offset, the stock-hook instruction there, the new one), little-endian AArch64.
# cannoli-os-builder's cannoli_os/uboot_buttons.S is the hook's source; its two "b.ne done" after the
# pressed test (bits 0x0c == 0x08) become "first read not pressed -> set boot_b" and "second read
# pressed too -> done", so only Volume-Up held on both reads leaves the stock (Fire OS) path.
TECHO5_PATCH = (
    (0xD5634, '01020054', 'a1000054'),  # b.ne done      -> b.ne set_boot_b (0xd5648)
    (0xD5644, '81010054', '80010054'),  # b.ne done      -> b.eq done (0xd5674)
)
# The probe on top: as uboot_buttons.S with PROBE=1 (no priority check, no env_set or recovery flags),
# and fastboot's getvar product answers with the hook's marker (product_hook at 0xd56c0).
PROBE_PATCH = TECHO5_PATCH + (
    (0xD55A8, '607640b9', NOP),  # ldr w0, [x19, #116]
    (0xD55AC, 'e0060035', NOP),  # cbnz w0, priority
    (0xD5650, 'e0040030', NOP),  # adr x0, boot_variable
    (0xD5654, '01050050', NOP),  # adr x1, recovery_command
    (0xD5658, '693cfd97', NOP),  # bl stock_env_set
    (0xD565C, 'c0010035', NOP),  # cbnz w0, env_failure
    (0xD5660, '34008052', NOP),  # mov w20, #1
    (0xD5664, '20008052', NOP),  # mov w0, #1
    (0xD5668, '607a00b9', NOP),  # str w0, [x19, #120]
    (0xD566C, '21ef1d10', NOP),  # adr x1, stock_recovery_cache
    (0xD5670, '20000039', NOP),  # strb w0, [x1]
    (0x3F2E4, 'fd7bbea9', 'f7580214'),  # getvar product handler -> b product_hook
)

STAGE = '/data/local/tmp/techo5-uboot_a.img'
END = 'T5-END'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def make(root, patch, pinned=True):
    """The root-a image with these payload instructions changed and its hash-1 value made to match.
    pinned=False skips the sha256 pins (the tests' made-up images)."""
    data = bytearray(root)
    if len(data) != IMAGE_SIZE:
        fail('uboot_a is %d bytes, not %d' % (len(data), IMAGE_SIZE))
    if pinned and sha256(data) != ROOT_A_SHA256:
        fail("uboot_a is not firebreak's 8349 root-a (sha256 %s); this only changes that one" % sha256(data))
    payload = bytes(data[PAYLOAD_AT:PAYLOAD_AT + PAYLOAD_LEN])
    if data[HASH_AT:HASH_AT + 32] != hashlib.sha256(payload).digest():
        fail("uboot_a's payload does not match its hash-1 value at 0x%x" % HASH_AT)
    if pinned and sha256(payload) != PAYLOAD_SHA256:
        fail("uboot_a's U-Boot payload is not root-a's")
    for offset, old, new in patch:
        at = PAYLOAD_AT + offset
        if data[at:at + 4] != bytes.fromhex(old):
            fail('the U-Boot payload differs at 0x%x (%s, expected %s)' % (offset, data[at:at + 4].hex(), old))
        data[at:at + 4] = bytes.fromhex(new)
    data[HASH_AT:HASH_AT + 32] = hashlib.sha256(data[PAYLOAD_AT:PAYLOAD_AT + PAYLOAD_LEN]).digest()
    return bytes(data)


def images(bundle):
    """{name: bytes} for root-a (Fire OS by default), techo5 and probe, each checked against its pin."""
    path = os.path.join(bundle, 'uboot_a.img')
    if not os.path.exists(path):
        fail('no %s: the unit\'s firebreak bundle has it (--bundle)' % path)
    with open(path, 'rb') as f:
        root = f.read()
    out = {'fireos': root, 'techo5': make(root, TECHO5_PATCH), 'probe': make(root, PROBE_PATCH)}
    for name, pin in (('techo5', TECHO5_SHA256), ('probe', PROBE_SHA256)):
        if sha256(out[name]) != pin:
            fail('the %s image came out as %s, not %s' % (name, sha256(out[name]), pin))
    return out


def check_bundle(bundle, serial):
    path = os.path.join(bundle, 'bundle.json')
    if not os.path.exists(path):
        fail('no firebreak bundle at %s (--bundle, or --unit with a bundle/ in it)' % bundle)
    with open(path) as f:
        m = json.load(f)
    if m.get('product') != 'cannoli' or m.get('serial') != serial or m.get('build') != 'NS6575/8349':
        fail('%s is for %s %s on %s, not this cannoli %s on NS6575/8349'
             % (path, m.get('product'), m.get('serial'), m.get('build'), serial))
    if m.get('images', {}).get('uboot_a', {}).get('sha256') != ROOT_A_SHA256:
        fail("%s's uboot_a is not firebreak's 8349 root-a" % path)


def decision(product):
    """What a probe's fastboot product says the hook would boot."""
    m = re.fullmatch(r'cannoli-keys-([0-9a-f?]{2})-([0-9a-f?]{2})-(.)', product or '')
    if not m:
        return None
    return {'U': 'TECHO5 (boot_b)', 'N': 'Fire OS (Volume-Up held)', 'E': 'Fire OS (the CPLD read failed)',
            '?': 'Fire OS (the hook did not run)'}.get(m[3], 'unknown state ' + m[3])


# ---------------------------------------------------------------------------------------------- probe

PROBE_LOADER = r'''
import sys
from firebreak.mediatek import bl2
data = open(sys.argv[1], 'rb').read()
print('listening for BL2: plug the power in now', flush=True)
with bl2.listen(float(sys.argv[2])) as port:
    bl2.upload(port, data)
print('uploaded', flush=True)
'''


def probe(a, image):
    if not a.firebreak or not os.path.isdir(a.firebreak):
        fail('the probe uses firebreak\'s BL2 listener: pass its checkout with --firebreak')
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'probe.img')
        with open(path, 'wb') as f:
            f.write(image)
        if interactive():
            input('   Unplug the Dot\'s power, then press Enter: ')
        note('then plug it back in within %d s: without a button for TECHO5, or holding Volume-Up about 5 s'
             % a.wait)
        r = subprocess.run(['uv', 'run', '--project', a.firebreak, 'python', '-c', PROBE_LOADER, path, str(a.wait)])
        if r.returncode != 0:
            fail('the BL2 upload did not go through (exit %d); nothing was written' % r.returncode)
    product = ['']

    def answered():
        try:
            r = subprocess.run([a.fastboot, '-s', a.serial, 'getvar', 'product'], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=10)
        except subprocess.TimeoutExpired:
            return False
        m = re.search(r'product:\s*(\S+)', r.stdout.decode('utf-8', 'replace'))
        product[0] = m[1] if m else ''
        return bool(product[0])

    wait_for('the probe on fastboot', 60, answered, 2)
    said = decision(product[0])
    if not said:
        fail('fastboot answered %r, not the probe\'s marker: this is not the probe U-Boot' % product[0])
    print('   %s: would boot %s' % (product[0], said))
    note('nothing was written; unplug the power to leave the RAM fastboot')


# ---------------------------------------------------------------------------------------------- write

def checked(adb, command, what):
    out = adb.sh('%s; echo %s' % (command, END))
    if not out.endswith(END):
        fail('no complete answer from the unit for %s (%r)' % (what, out[-200:]))
    return out[:-len(END)].strip()


def volume_sha1(adb, node):
    out = checked(adb, 'dd if=%s bs=4096 count=%d 2>/dev/null | sha1sum' % (node, IMAGE_SIZE // 4096), 'uboot_a')
    h = out.split()[0] if out else ''
    if not re.fullmatch(r'[0-9a-f]{40}', h):
        fail('no sha1 of uboot_a from the unit (%r)' % out)
    return h


def write(a, imgs, target):
    adb = Adb(a.serial, a.adb)
    state = adb.state()
    if state not in ('device', 'recovery'):
        fail('the unit is not on adb (%r): boot Fire OS (no button now that TECHO5 may be the default: '
             'hold Volume-Up) or TWRP, then run this again' % state)
    if adb.sh('id -u') != '0':
        adb.root()
        if adb.sh('id -u') != '0':
            fail('adb on the unit is not root: this needs firebreak\'s rooted slot A or TWRP')
    if state == 'device' and adb.sh('getprop ro.boot.slot_suffix') != '_a':
        fail('Fire OS is not running from slot A')
    found = checked(adb, 'for d in /sys/class/ubi/ubi0_*; do [ "$(cat $d/name 2>/dev/null)" = uboot_a ] && '
                         'echo "${d##*/} $(cat $d/data_bytes)"; done', 'the uboot_a volume').split()
    if len(found) != 2 or int(found[1]) != IMAGE_SIZE:
        fail('no uboot_a volume of %d bytes on the unit (%r)' % (IMAGE_SIZE, found))
    node = '/dev/' + found[0]
    if checked(adb, 'command -v ubiupdatevol', 'ubiupdatevol') == '':
        fail('the unit has no ubiupdatevol')

    known = {sha1(data): name for name, data in imgs.items() if name != 'probe'}
    now = volume_sha1(adb, node)
    if now not in known:
        fail('uboot_a on the unit (sha1 %s) is neither root-a nor TECHO5\'s: not touching it' % now)
    if known[now] == target:
        print('   uboot_a already boots %s by default; nothing to do' % ('TECHO5' if target == 'techo5' else 'Fire OS'))
        return
    previous, image = imgs[known[now]], imgs[target]

    step('Writing uboot_a (%s, %s)' % (node, 'TECHO5 by default' if target == 'techo5' else 'Fire OS by default'))
    lines = ['This rewrites slot A\'s U-Boot, which starts every boot of the unit.',
             'If it ever fails to start, only firebreak\'s BL2 route over the soldered USB pads gets it back.']
    if target == 'techo5':
        lines += ['Afterwards a power-on without a button boots TECHO5; Fire OS is Volume-Up held at power-on.']
    confirm(a.force, lines, word='WRITE')
    put(adb, node, image, 'the new uboot_a', previous=previous)
    print('   uboot_a written and read back (sha1 %s)' % sha1(image))
    if a.reboot:
        reboot(a, adb, target)
    else:
        note('the change takes effect at the next boot')


def put(adb, node, image, what, previous=None):
    """Writes image to uboot_a and reads it back. previous, when given, is what was there: a readback
    that differs puts it back (checked the same way) before failing."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'uboot_a.img')
        with open(path, 'wb') as f:
            f.write(image)
        adb.push(path, STAGE)
    staged = checked(adb, 'sha1sum %s' % STAGE, 'the staged image').split()[:1]
    if staged != [sha1(image)]:
        adb.sh('rm -f ' + STAGE)
        fail('the image pushed to the unit differs from %s; uboot_a was not touched' % what)
    out = checked(adb, 'ubiupdatevol %s %s; echo rc=$?' % (node, STAGE), 'ubiupdatevol')
    adb.sh('rm -f ' + STAGE)
    back = volume_sha1(adb, node)
    if 'rc=0' in out and back == sha1(image):
        return
    if previous is None:
        fail('uboot_a differs from %s after writing it (sha1 %s). Do not reboot: write the bundle\'s '
             'uboot_a.img back with ubiupdatevol by hand now.' % (what, back))
    note('the readback differs (%s, %s): putting the previous uboot_a back' % (out.strip(), back))
    put(adb, node, previous, 'the previous uboot_a')
    fail('writing uboot_a did not read back; the previous one is back in place and checked')


def reboot(a, adb, target):
    adb.reboot()
    if target == 'techo5':
        def up():
            return any(s == a.serial for _, s in list_consoles(CONSOLE_TECHO5))
        wait_for('TECHO5\'s console (%s:%s) with no button held' % CONSOLE_TECHO5, 180, up)
        print('   TECHO5 booted by default')
    else:
        wait_for('Fire OS on adb', 240, lambda: adb.state() == 'device')
        print('   Fire OS booted by default')


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('action', choices=('probe', 'techo5', 'fireos'))
    p.add_argument('--serial', required=True)
    p.add_argument('--unit', help='the unit\'s folder (default ../cannoli-<serial>)')
    p.add_argument('--bundle', help='firebreak\'s bundle (default <unit>/bundle)')
    p.add_argument('--firebreak', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                       '..', 'firebreak'), help='firebreak checkout, for probe')
    p.add_argument('--adb', default='adb')
    p.add_argument('--fastboot', default='fastboot')
    p.add_argument('--wait', type=int, default=90, help='seconds the probe listens for BL2')
    p.add_argument('--reboot', action='store_true', help='reboot afterwards and wait for the default to come up')
    p.add_argument('--force', action='store_true', help='do not ask before writing')
    p.add_argument('--out', help='also save the made image here')
    a = p.parse_args()
    unit = a.unit or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..',
                                  'cannoli-' + a.serial)
    bundle = a.bundle or os.path.join(unit, 'bundle')
    check_bundle(bundle, a.serial)
    imgs = images(bundle)
    if a.out:
        with open(a.out, 'wb') as f:
            f.write(imgs[a.action])
        note('%s image saved to %s' % (a.action, a.out))
    if a.action == 'probe':
        step('RAM probe of the TECHO5-default decision (writes nothing)')
        probe(a, imgs['probe'])
    else:
        write(a, imgs, a.action)


if __name__ == '__main__':
    run_main(main)
