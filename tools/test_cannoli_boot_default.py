"""Tests for cannoli-boot-default.py, with no Echo Dot 5 and no Amazon image: the patch on a made-up
uboot_a laid out like root-a, the probe's answer, and the write against a fake unit.
Run: python3 -m unittest tools/test_cannoli_boot_default.py"""
import hashlib
import importlib.util
import os
import re
import sys
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('cannoli_boot_default', os.path.join(HERE, 'cannoli-boot-default.py'))
cbd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cbd)
import techo5lib  # noqa: E402


def fake_root():
    """IMAGE_SIZE bytes with every patched word's stock value in place and a matching hash-1."""
    data = bytearray(os.urandom(cbd.IMAGE_SIZE))
    for offset, old, _ in cbd.PROBE_PATCH:
        at = cbd.PAYLOAD_AT + offset
        data[at:at + 4] = bytes.fromhex(old)
    payload = data[cbd.PAYLOAD_AT:cbd.PAYLOAD_AT + cbd.PAYLOAD_LEN]
    data[cbd.HASH_AT:cbd.HASH_AT + 32] = hashlib.sha256(payload).digest()
    return bytes(data)


def made():
    root = fake_root()
    return {'fireos': root, 'techo5': cbd.make(root, cbd.TECHO5_PATCH, pinned=False),
            'probe': cbd.make(root, cbd.PROBE_PATCH, pinned=False)}


class Patch(unittest.TestCase):
    def test_only_the_two_branches_and_the_hash_change(self):
        imgs = made()
        root, new = imgs['fireos'], imgs['techo5']
        changed = {i for i in range(len(root)) if root[i] != new[i]}
        allowed = {cbd.PAYLOAD_AT + o + k for o, _, _ in cbd.TECHO5_PATCH for k in range(4)}
        allowed |= set(range(cbd.HASH_AT, cbd.HASH_AT + 32))
        self.assertTrue(changed <= allowed)
        payload = new[cbd.PAYLOAD_AT:cbd.PAYLOAD_AT + cbd.PAYLOAD_LEN]
        self.assertEqual(new[cbd.HASH_AT:cbd.HASH_AT + 32], hashlib.sha256(payload).digest())

    def test_branch_encodings(self):
        # b.ne +0x14 from 0xd5634 lands on set_boot_b; b.eq +0x30 from 0xd5644 on done.
        for (src, _, new), (dst, cond) in zip(cbd.TECHO5_PATCH, ((0xD5648, 1), (0xD5674, 0))):
            word = int.from_bytes(bytes.fromhex(new), 'little')
            self.assertEqual(word & 0xFF00001F, 0x54000000 | cond)
            self.assertEqual(src + (((word >> 5) & 0x7FFFF) << 2), dst)
        word = int.from_bytes(bytes.fromhex(cbd.PROBE_PATCH[-1][2]), 'little')
        self.assertEqual(word >> 26, 0x05)
        self.assertEqual(0x3F2E4 + ((word & 0x3FFFFFF) << 2), 0xD56C0)

    def test_a_different_payload_is_refused(self):
        root = bytearray(fake_root())
        at = cbd.PAYLOAD_AT + cbd.TECHO5_PATCH[0][0]
        root[at:at + 4] = b'\0\0\0\0'
        payload = root[cbd.PAYLOAD_AT:cbd.PAYLOAD_AT + cbd.PAYLOAD_LEN]
        root[cbd.HASH_AT:cbd.HASH_AT + 32] = hashlib.sha256(payload).digest()
        with self.assertRaises(techo5lib.Fail):
            cbd.make(bytes(root), cbd.TECHO5_PATCH, pinned=False)

    def test_a_stale_hash_is_refused(self):
        root = bytearray(fake_root())
        root[cbd.HASH_AT] ^= 1
        with self.assertRaises(techo5lib.Fail):
            cbd.make(bytes(root), cbd.TECHO5_PATCH, pinned=False)

    def test_the_pins_refuse_anything_but_root_a(self):
        with self.assertRaises(techo5lib.Fail):
            cbd.make(fake_root(), cbd.TECHO5_PATCH)


class Probe(unittest.TestCase):
    def test_the_markers_seen_on_the_first_unit(self):
        self.assertEqual(cbd.decision('cannoli-keys-1e-1e-U'), 'TECHO5 (boot_b)')
        self.assertEqual(cbd.decision('cannoli-keys-1a-1a-N'), 'Fire OS (Volume-Up held)')
        self.assertTrue(cbd.decision('cannoli-keys-??-??-?').startswith('Fire OS'))
        self.assertIsNone(cbd.decision('cannoli-bcb-1'))
        self.assertIsNone(cbd.decision('cannoli'))


class FakeUnit:
    """adb on a rooted slot-A Fire OS whose uboot_a is ubi0_0; bad_writes makes that many writes land
    corrupted."""

    def __init__(self, volume, bad_writes=0):
        self.volume, self.staged, self.bad_writes, self.writes = volume, None, bad_writes, 0

    def state(self):
        return 'device'

    def root(self):
        pass

    def reboot(self, target=None):
        pass

    def push(self, local, remote, **_):
        with open(local, 'rb') as f:
            self.staged = f.read()

    def sh(self, command):
        end = ' ' + cbd.END if command.endswith('echo ' + cbd.END) else ''
        if command == 'id -u':
            return '0'
        if command.startswith('getprop'):
            return '_a'
        if command.startswith('rm -f'):
            self.staged = None
            return ''
        if 'sys/class/ubi' in command:
            return 'ubi0_0 %d\n%s' % (cbd.IMAGE_SIZE, end)
        if command.startswith('command -v ubiupdatevol'):
            return '/vendor/bin/ubiupdatevol\n' + end
        if command.startswith('dd if=/dev/ubi0_0'):
            return '%s  -\n%s' % (cbd.sha1(self.volume), end)
        if command.startswith('sha1sum ' + cbd.STAGE):
            return '%s  %s\n%s' % (cbd.sha1(self.staged), cbd.STAGE, end)
        if command.startswith('ubiupdatevol /dev/ubi0_0'):
            self.writes += 1
            self.volume = self.staged
            if self.bad_writes:
                self.bad_writes -= 1
                self.volume = b'\xff' * cbd.IMAGE_SIZE
            return 'rc=0\n' + end
        raise AssertionError('unexpected command ' + command)


class Write(unittest.TestCase):
    def run_write(self, unit, imgs, target):
        cbd.Adb = lambda serial, exe: unit
        a = SimpleNamespace(serial='G0000TEST0000001', adb='adb', force=True, reboot=False)
        cbd.write(a, imgs, target)

    def test_root_a_to_techo5_and_back(self):
        imgs = made()
        unit = FakeUnit(imgs['fireos'])
        self.run_write(unit, imgs, 'techo5')
        self.assertEqual(unit.volume, imgs['techo5'])
        self.assertIsNone(unit.staged)
        self.run_write(unit, imgs, 'fireos')
        self.assertEqual(unit.volume, imgs['fireos'])

    def test_already_there_writes_nothing(self):
        imgs = made()
        unit = FakeUnit(imgs['techo5'])
        self.run_write(unit, imgs, 'techo5')
        self.assertEqual(unit.writes, 0)

    def test_an_unknown_uboot_a_is_left_alone(self):
        imgs = made()
        unit = FakeUnit(b'\0' * cbd.IMAGE_SIZE)
        with self.assertRaises(techo5lib.Fail):
            self.run_write(unit, imgs, 'techo5')
        self.assertEqual(unit.writes, 0)

    def test_a_bad_readback_puts_the_previous_one_back(self):
        imgs = made()
        unit = FakeUnit(imgs['fireos'], bad_writes=1)
        with self.assertRaises(techo5lib.Fail) as e:
            self.run_write(unit, imgs, 'techo5')
        self.assertTrue(re.search('previous one is back', str(e.exception)))
        self.assertEqual(unit.volume, imgs['fireos'])
        self.assertEqual(unit.writes, 2)


if __name__ == '__main__':
    unittest.main()
