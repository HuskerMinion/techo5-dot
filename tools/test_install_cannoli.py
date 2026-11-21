"""Tests for install-cannoli.py, with no Echo Dot 5: its parsing and gating, and runs against
sim-cannoli-adb.py on a small made-up unit. Run: python3 -m unittest tools/test_install_cannoli.py"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('install_cannoli', os.path.join(HERE, 'install-cannoli.py'))
ic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ic)
import techo5lib  # noqa: E402

SIM = os.path.join(HERE, 'sim-cannoli-adb.py')
SERIAL = 'G0000TEST0000001'


def fit(body, pad=b'\xff' * 5000):
    """A blob that starts like a FIT of len(body) + 8 bytes, then a volume's unused bytes."""
    total = 8 + len(body)
    return b'\xd0\x0d\xfe\xed' + total.to_bytes(4, 'big') + body + pad


class Build(unittest.TestCase):
    """The build gate: 8349 goes on, the older builds take the upgrade, a newer one stops."""

    def test_8349_from_the_display_id_or_the_number(self):
        self.assertEqual(ic.build_of({'ro.build.display.id': 'NS6575/8349'}), (8349, None))
        self.assertEqual(ic.build_of({'ro.build.version.number': '0013323246980'}), (None, 13323246980))
        self.assertEqual(ic.gate(8349, None), 'ok')
        self.assertEqual(ic.gate(None, 13323246980), 'ok')

    def test_older_builds_take_the_upgrade(self):
        for build in ic.OLDER_BUILDS:
            self.assertEqual(ic.gate(build, None), 'upgrade', build)
            self.assertEqual(ic.build_of({'ro.build.display.id': 'NS6574/%d' % build})[0], build)
        self.assertEqual(ic.gate(None, 12000000000), 'upgrade')

    def test_newer_or_unknown_stops(self):
        self.assertEqual(ic.gate(8350, None), 'newer')
        self.assertEqual(ic.gate(None, 13323246981), 'newer')
        self.assertEqual(ic.gate(None, None), 'unknown')
        self.assertEqual(ic.build_of({'ro.build.display.id': 'NS6575'}), (None, None))

    def test_the_id_wins_over_the_number(self):
        self.assertEqual(ic.gate(8344, 13323246980), 'upgrade')

    def test_fingerprint_and_incremental(self):
        self.assertEqual(ic.build_of({'ro.build.fingerprint': 'Amazon/cannoli/cannoli:7.1.2/NS6575_user_8349/x'})[0], 8349)
        self.assertEqual(ic.build_of({'ro.build.version.incremental': '8338'})[0], 8338)

    def test_the_pins(self):
        self.assertEqual(ic.OTA_MD5, '0bf96b3cd8fca30fdd12f61208e44d76')
        self.assertIn('8349', ic.OTA_NAME)
        self.assertEqual(ic.BRHGPTLK_LEN, 249856)


class State(unittest.TestCase):
    def test_states(self):
        self.assertEqual(ic.classify('recovery', {}, '0', False), 'twrp')
        self.assertEqual(ic.classify('device', {}, '0', True), 'rooted')
        self.assertEqual(ic.classify('device', {}, '2000', False), 'stock')
        self.assertEqual(ic.classify('device', {}, '2000', True), 'rooted-keyed')
        self.assertEqual(ic.classify('', {}, '', False), 'none')

    def test_ubi_and_mtd(self):
        self.assertEqual(ic.parse_ubi('ubi0_8 boot_b 13676544\nubi0_10 system_b 5\njunk\n'),
                         [(8, 'boot_b', 13676544), (10, 'system_b', 5)])
        mtd = ic.parse_mtd('dev:    size   erasesize  name\nmtd2: 00040000 00020000 "brhgptlk_2"\n')
        self.assertEqual(mtd, {'brhgptlk_2': (2, 0x40000)})


class Fit(unittest.TestCase):
    def test_cut_at_its_length_rounded_to_4k(self):
        cut = ic.fit_image(fit(b'x' * 100))
        self.assertEqual(len(cut), 4096)
        self.assertTrue(cut.startswith(b'\xd0\x0d\xfe\xed'))
        self.assertIsNone(ic.fit_image(b'ANDROID!'))

    def test_the_bundle_boot_is_the_volume_cut(self):
        # The same rule the builder uses: a whole boot_a volume cuts to the bundle's 8349 boot.img.
        img = ic._load('cannoli_image', os.path.join(HERE, 'linux', 'build-cannoli-image.py'))
        blob = fit(b'y' * 9000)
        self.assertEqual(img.fit_image(blob), ic.fit_image(blob))

    def test_not_ours(self):
        self.assertFalse(ic.is_techo5_fit(fit(b'z' * 64)))


class Verdict(unittest.TestCase):
    def test_healthy_slot(self):
        self.assertEqual(ic.boot_verdict({'slot': 'a', 'tries': '0 healthy'}, True), 'healthy')
        self.assertEqual(ic.boot_verdict({'slot': 'a', 'tries': '1'}, True), 'booting')

    def test_rescue(self):
        self.assertEqual(ic.boot_verdict({'slot': '', 'release': 'techo5-cannoli rescue first'}, True), 'rescue')
        self.assertEqual(ic.boot_verdict({'slot': '', 'ip': '10.0.0.5', 'release': 'techo5-cannoli rescue'}, False), 'healthy')
        self.assertEqual(ic.boot_verdict({}, True), '')


class Firebreak(unittest.TestCase):
    def test_command(self):
        self.assertEqual(ic.firebreak_cmd('/fb', 'unlock', '--device', 'cannoli')[:5], ['uv', 'run', '--project', '/fb', 'firebreak'])
        self.assertEqual(ic.firebreak_cmd(None, 'unlock')[:2], ['uvx', 'firebreak@latest'])

    def test_bundle_for_another_unit_is_refused(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, 'bundle.json'), 'w') as f:
            json.dump({'product': 'cannoli', 'serial': 'OTHER', 'build': 'NS6575/8349', 'adb': 'keyless'}, f)
        with self.assertRaises(techo5lib.Fail):
            ic.check_bundle(d, SERIAL)
        with self.assertRaises(techo5lib.Fail):
            ic.check_bundle(os.path.join(d, 'none'), SERIAL)
        with open(os.path.join(d, 'bundle.json'), 'w') as f:
            json.dump({'product': 'cannoli', 'serial': SERIAL, 'build': 'NS6575/8349', 'adb': 'keyless'}, f)
        self.assertEqual(ic.check_bundle(d, SERIAL)['serial'], SERIAL)

    def test_ota_parts_must_be_8349s(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with self.assertRaises(techo5lib.Fail):
            ic.check_ota_parts(d)
        for p in ic.UPGRADE_PARTS:
            open(os.path.join(d, p), 'wb').close()
        with self.assertRaises(techo5lib.Fail):  # boot.img is not 8349's
            ic.check_ota_parts(d)


class Manifest(unittest.TestCase):
    def test_round_trip_in_volume_order(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 'ubi-sha256.txt')
        rows = {'u_10': ('system_b', 5, 'b' * 64), 'u_2': ('tee1', 3, 'a' * 64)}
        ic.write_manifest(p, rows)
        self.assertEqual(ic.read_manifest(p), rows)
        with open(p) as f:
            self.assertEqual([l.split('\t')[0] for l in f][1:], ['u_2', 'u_10'])


class Simulated(unittest.TestCase):
    """The installer against sim-cannoli-adb.py on a made-up unit: a few small volumes, the four BL2
    copies, the drivers and firmware."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.m0 = os.path.join(self.d, 'unit', 'm0')
        os.makedirs(os.path.join(self.m0, 'modules'))
        os.makedirs(os.path.join(self.m0, 'firmware'))
        for n in (1, 2, 7, 8):
            with open(os.path.join(self.m0, 'u_%d.bin' % n), 'wb') as f:
                f.write(fit(bytes([n]) * 300))
        for n in range(4):
            with open(os.path.join(self.m0, 'brhgptlk_%d.bin' % n), 'wb') as f:
                f.write(b'\x55' * 4096)
        for m in ic.MODULES:
            with open(os.path.join(self.m0, 'modules', m + '.ko'), 'wb') as f:
                f.write(m.encode())
        with open(os.path.join(self.m0, 'firmware', 'bt.cfg'), 'wb') as f:
            f.write(b'cfg')
        self.inputs = os.path.join(self.d, 'inputs')
        os.makedirs(self.inputs)
        with open(os.path.join(self.inputs, 'busybox.static'), 'wb') as f:
            f.write(b'\x7fELF busybox')
        self.env = dict(os.environ, TECHO5_SIM_M0=self.m0, TECHO5_SIM_SERIAL=SERIAL, TECHO5_SIM_DIR=os.path.join(self.d, 'sim'),
                        TECHO5_NO_PROMPT='1', TECHO5_BACKUPS=os.path.join(self.d, 'backups'))

    def sim_env(self):
        old = dict(os.environ)
        os.environ.update(self.env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))

    def test_backup_matches_the_unit(self):
        self.sim_env()
        unit = ic.Unit(techo5lib.Adb(SERIAL, SIM), '/data/local/tmp/techo5')
        unit.setup_busybox(os.path.join(self.inputs, 'busybox.static'))
        out = os.path.join(self.d, 'backup')
        files = ic.back_up(unit, out, True)
        self.assertEqual(set(files), {'uboot_b', 'tee1', 'boot_a', 'boot_b'} | set(ic.BRHGPTLK))
        for name, path in files.items():
            n = os.path.basename(path)[:-4]
            with open(os.path.join(self.m0, n + '.bin'), 'rb') as a, open(path, 'rb') as b:
                self.assertEqual(a.read(), b.read(), name)
        rows = ic.read_manifest(os.path.join(out, 'ubi-sha256.txt'))
        self.assertEqual(rows['u_7'][0], 'boot_a')
        with open(files['boot_a'], 'rb') as f:
            self.assertEqual(rows['u_7'][2], hashlib.sha256(f.read()).hexdigest())
        self.assertEqual(sorted(os.listdir(os.path.join(out, 'modules'))), sorted(m + '.ko' for m in ic.MODULES))
        self.assertEqual(os.listdir(os.path.join(out, 'firmware')), ['bt.cfg'])
        # A second run copies nothing and keeps the manifest.
        before = os.path.getmtime(files['boot_a'])
        ic.back_up(unit, out, True)
        self.assertEqual(os.path.getmtime(files['boot_a']), before)

    def test_volume_that_changed_is_kept_beside(self):
        self.sim_env()
        unit = ic.Unit(techo5lib.Adb(SERIAL, SIM), '/data/local/tmp/techo5')
        unit.setup_busybox(os.path.join(self.inputs, 'busybox.static'))
        out = os.path.join(self.d, 'backup')
        ic.back_up(unit, out, False)
        with open(os.path.join(self.m0, 'u_8.bin'), 'ab') as f:
            f.write(b'changed')
        files = ic.back_up(unit, out, False)
        self.assertRegex(os.path.basename(files['boot_b']), r'^u_8-\d{8}-\d{6}\.bin$')
        self.assertTrue(os.path.exists(os.path.join(out, 'u_8.bin')))

    def run_installer(self, state, *extra, build='NS6575/8349'):
        env = dict(self.env, TECHO5_SIM_STATE=state, TECHO5_SIM_BUILD=build)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'install-cannoli.py'), '--adb', SIM, '--serial', SERIAL,
                            '--unit', os.path.join(self.d, 'unit'), '--inputs', self.inputs, '--firebreak', self.d,
                            '--name', 'Test', '--dry-run'] + list(extra),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        return r.returncode, r.stdout.decode('utf-8', 'replace')

    def test_newer_build_stops_with_nothing_written(self):
        code, out = self.run_installer('rooted', build='NS6575/8400')
        self.assertEqual(code, 1, out)
        self.assertIn('newer than NS6575/8349', out)
        self.assertIn('off Wi-Fi', out)
        self.assertFalse(os.path.exists(os.path.join(self.d, 'backups')), out)

    def test_stock_unit_is_unlocked_by_firebreak(self):
        bundle = os.path.join(self.d, 'unit', 'bundle')
        os.makedirs(bundle)
        with open(os.path.join(bundle, 'bundle.json'), 'w') as f:
            json.dump({'product': 'cannoli', 'serial': SERIAL, 'build': 'NS6575/8349', 'adb': 'keyless'}, f)
        code, out = self.run_installer('stock')
        self.assertEqual(code, 0, out)
        self.assertIn('stock Fire OS', out)
        self.assertRegex(out, r'firebreak: uv run --project \S+ firebreak unlock --device cannoli --bundle')
        self.assertIn('Nothing was written', out)

    def test_older_build_names_the_efuse(self):
        code, out = self.run_installer('stock', build='NS6575/8344')
        self.assertEqual(code, 1, out)  # no unpacked 8349 OTA in the made-up unit
        self.assertIn('stock-8349-parts', out)
        self.assertIn('Fire OS 6.5.7.5 (8344)', out)

    def test_rooted_unit_without_8349_volumes_is_refused(self):
        # The made-up unit's tee1 is not 8349's, whatever its props say.
        code, out = self.run_installer('rooted')
        self.assertEqual(code, 1, out)
        self.assertIn("protected volumes are not 8349", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, 'backups')), out)


if __name__ == '__main__':
    unittest.main()
