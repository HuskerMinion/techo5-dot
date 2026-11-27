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
import time
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
        self.assertEqual(ic.firebreak_cmd(None, 'unlock'), ['uvx', '--from', ic.FIREBREAK_PIN, 'firebreak', 'unlock'])
        self.assertRegex(ic.FIREBREAK_PIN, r'@[0-9a-f]{40}$')

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


    def test_bl2_image_is_checked_before_the_question(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 'brhgptlk_0.img')
        with self.assertRaises(techo5lib.Fail) as e:
            ic.check_bl2_image(p)
        self.assertIn('there is none', str(e.exception))
        with open(p, 'wb') as f:
            f.write(b'z' * ic.BRHGPTLK_LEN)
        with self.assertRaises(techo5lib.Fail) as e:
            ic.check_bl2_image(p)
        self.assertIn("not 8349's brhgptlk", str(e.exception))

    def test_another_device_is_refused(self):
        with self.assertRaises(techo5lib.Fail):
            ic.check_cannoli(SERIAL, {'ro.product.device': 'checkers', 'ro.build.product': 'checkers'})
        ic.check_cannoli(SERIAL, {'ro.product.device': 'cannoli'})


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

    def boot_b_unit(self, **env):
        self.sim_env()
        os.environ.update(env)
        unit = ic.Unit(techo5lib.Adb(SERIAL, SIM), '/data/local/tmp/techo5')
        unit.setup_busybox(os.path.join(self.inputs, 'busybox.static'))
        image = os.path.join(self.d, 'twrp-boot_b.img')
        with open(image, 'wb') as f:
            f.write(fit(b'B' * 200))
        return unit, image, os.path.join(self.env['TECHO5_SIM_DIR'], 'ubi0_8.bin')

    def test_boot_b_goes_through_memory_on_a_full_userdata(self):
        # A reinstall over TECHO5: its store leaves /data/local/tmp no room for TWRP's 13 MB.
        unit, image, written = self.boot_b_unit(TECHO5_SIM_NO_SPACE=r'/data/.*boot_b')
        ic.write_boot_b(unit, image, image)
        with open(image, 'rb') as a, open(written, 'rb') as b:
            self.assertEqual(a.read(), b.read())

    def test_boot_b_with_too_little_memory_writes_nothing(self):
        unit, image, written = self.boot_b_unit(TECHO5_SIM_RAM_FREE_KB='1024')
        with self.assertRaises(techo5lib.Fail) as e:
            ic.write_boot_b(unit, image, image)
        self.assertIn('nothing written', str(e.exception))
        self.assertFalse(os.path.exists(written))

    def test_a_push_with_no_room_is_not_retried(self):
        unit, image, written = self.boot_b_unit(TECHO5_SIM_NO_SPACE=r'/dev/.*boot_b')
        start = time.time()
        with self.assertRaises(techo5lib.Fail) as e:
            ic.write_boot_b(unit, image, image)
        self.assertIn('no room', str(e.exception))
        self.assertLess(time.time() - start, 8)  # a retry waits 10 s first
        self.assertFalse(os.path.exists(written))

    def bundle(self, **over):
        bundle = os.path.join(self.d, 'unit', 'bundle')
        os.makedirs(bundle, exist_ok=True)
        m = dict({'product': 'cannoli', 'serial': SERIAL, 'build': 'NS6575/8349', 'adb': 'keyless'}, **over)
        with open(os.path.join(bundle, 'bundle.json'), 'w') as f:
            json.dump({k: v for k, v in m.items() if v is not None}, f)

    def run_installer(self, state, *extra, build='NS6575/8349', name='Test'):
        env = dict(self.env, TECHO5_SIM_STATE=state, TECHO5_SIM_BUILD=build)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'install-cannoli.py'), '--adb', SIM, '--serial', SERIAL,
                            '--unit', os.path.join(self.d, 'unit'), '--inputs', self.inputs, '--firebreak', self.d,
                            '--dry-run'] + (['--name', name] if name else []) + list(extra),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        return r.returncode, r.stdout.decode('utf-8', 'replace')

    def test_newer_build_stops_with_nothing_written(self):
        code, out = self.run_installer('rooted', build='NS6575/8400')
        self.assertEqual(code, 1, out)
        self.assertIn('newer than NS6575/8349', out)
        self.assertIn('off Wi-Fi', out)
        self.assertFalse(os.path.exists(os.path.join(self.d, 'backups')), out)

    def test_stock_unit_is_unlocked_by_firebreak(self):
        self.bundle()
        code, out = self.run_installer('stock')
        self.assertEqual(code, 0, out)
        self.assertIn('stock Fire OS', out)
        self.assertRegex(out, r'firebreak: uv run --project \S+ firebreak unlock --device cannoli --bundle')
        self.assertIn('Nothing was written', out)

    def test_older_build_names_the_efuse(self):
        self.bundle()
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

    def test_bundle_from_before_keyless_adb_is_refused_up_front(self):
        self.bundle(adb=None)
        code, out = self.run_installer('stock')
        self.assertEqual(code, 1, out)
        self.assertIn('Rebuild the bundle with cannoli-os-builder (no --adb-key', out)
        self.assertNotIn('firebreak: ', out)  # said before firebreak is ever started

    def test_gift_on_a_stock_unit_with_no_saved_network(self):
        # A factory-reset unit: stock Fire OS in setup mode, no network saved, nothing named.
        self.bundle()
        code, out = self.run_installer('stock', '--for-someone-else', name=None)
        self.assertEqual(code, 0, out)
        self.assertIn('firebreak unlock --device cannoli', out)
        self.assertNotIn('--wifi', out)

    def test_gift_cannot_be_combined_with_name_wifi_or_key(self):
        for extra in (['--name', 'Kitchen'], ['--wifi', 'Net'], ['--wifi-passphrase-file', 'p.txt'],
                      ['--key-file', 'k.key'], ['--ssh-key', 'k.pub']):
            code, out = self.run_installer('stock', '--for-someone-else', *extra, name=None)
            self.assertEqual(code, 1, (extra, out))
            self.assertIn('--for-someone-else', out, extra)
            self.assertIn(extra[0], out, extra)

    def test_unauthorized_stock_unit_goes_to_the_unlock(self):
        # A factory-reset unit: stock adb is secure and nothing can accept this computer's key.
        self.bundle()
        for state in ('unauthorized', 'offline'):
            code, out = self.run_installer(state)
            self.assertEqual(code, 0, (state, out))
            self.assertIn('adb lists it as %s' % state, out)
            self.assertIn('cannot be read over adb', out)
            self.assertRegex(out, r'firebreak: uv run --project \S+ firebreak unlock --device cannoli')
            self.assertNotIn('adb does not see', out)
            self.assertIn('Nothing was written', out)

    def test_unauthorized_unit_needs_its_bundle_first(self):
        code, out = self.run_installer('unauthorized')
        self.assertEqual(code, 1, out)
        self.assertIn('no firebreak bundle', out)
        self.assertNotIn('firebreak: ', out)

    def test_gift_needs_the_root_filesystem(self):
        code, out = self.run_installer('stock', '--for-someone-else', '--rescue-only', name=None)
        self.assertEqual(code, 1, out)
        self.assertIn('needs the root filesystem', out)


class Rootfs(unittest.TestCase):
    """The root filesystem onto the unit, in parts that fit, each checked: a copy cut short over USB, a
    tar that stops early and a slot without its symlinks are each caught (the first unit's exec-in stream
    lost its tail, and busybox's tar, which makes symlinks last, left 334 of 455 out)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.env = dict(TECHO5_SIM_SERIAL=SERIAL, TECHO5_SIM_DIR=os.path.join(self.d, 'sim'), TECHO5_SIM_M0=self.d)
        old = dict(os.environ)
        os.environ.update(self.env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        self.work = os.path.join(self.d, 'work')
        os.makedirs(self.work)
        self.unit = ic.Unit(techo5lib.Adb(SERIAL, SIM), '/data/local/tmp/techo5')

    def tarball(self, links=True, blobs=3):
        import io
        import tarfile
        path = os.path.join(self.d, 'rootfs.tar.gz')
        with tarfile.open(path, 'w:gz') as t:
            def add(name, data=b'', kind=None, target=''):
                m = tarfile.TarInfo(name)
                if kind:
                    m.type, m.linkname = kind, target
                    t.addfile(m)
                else:
                    m.size = len(data)
                    t.addfile(m, io.BytesIO(data))
            for d in ('bin', 'sbin', 'etc', 'etc/techo5', 'usr', 'usr/local', 'usr/local/bin', 'big'):
                add(d, kind=tarfile.DIRTYPE)
            add('bin/busybox.static', b'bb')
            add('etc/techo5/boot.sh', b'#!/bin/sh\n')
            add('usr/local/bin/echod', b'echod')
            add('etc/techo5-release', b'techo5-cannoli test\n')
            for i in range(blobs):
                add('big/blob%d' % i, os.urandom(1 << 20))  # incompressible, so the parts are real
            if links:
                add('bin/sh', kind=tarfile.SYMTYPE, target='/bin/busybox')
                add('sbin/init', kind=tarfile.SYMTYPE, target='/bin/busybox')
            else:
                add('sbin/init', b'init')
        return path

    def free_kb(self, rootfs, extra):
        names, size = ic.rootfs_names(rootfs)
        return str((size + 4096 * len(names) + ic.SPARE + extra) // 1024)

    def test_whole_tarball_when_it_fits(self):
        rootfs = self.tarball()
        ic.unpack_rootfs(self.unit, rootfs, self.work, '/data/local/tmp/techo5')

    def test_parts_when_room_is_short(self):
        rootfs = self.tarball()
        os.environ['TECHO5_SIM_FREE_KB'] = self.free_kb(rootfs, ic.MIN_PART + (512 << 10))
        ic.unpack_rootfs(self.unit, rootfs, self.work, '/data/local/tmp/techo5')

    def test_split_keeps_every_path_in_order(self):
        import tarfile
        rootfs = self.tarball()
        parts = ic.split_rootfs(rootfs, os.path.join(self.d, 'parts'), 1 << 20)
        self.assertGreater(len(parts), 2)
        got = []
        for p in parts:
            with tarfile.open(p, 'r:gz') as t:
                got += [m.name for m in t]
        with tarfile.open(rootfs, 'r:gz') as t:
            self.assertEqual(got, [m.name for m in t])

    def test_a_copy_cut_short_is_caught_before_tar(self):
        os.environ['TECHO5_SIM_TRUNCATE'] = '1'
        with self.assertRaises(techo5lib.Fail) as e:
            ic.unpack_rootfs(self.unit, self.tarball(), self.work, '/data/local/tmp/techo5')
        self.assertIn('cut short', str(e.exception))

    def test_tar_failing_is_said_with_its_errors(self):
        os.environ['TECHO5_SIM_TAR_FAIL'] = '1'
        with self.assertRaises(techo5lib.Fail) as e:
            ic.unpack_rootfs(self.unit, self.tarball(), self.work, '/data/local/tmp/techo5')
        self.assertIn('rc=1', str(e.exception))
        self.assertIn('tar: short read', str(e.exception))

    def slot_state(self):
        p = os.path.join(self.env['TECHO5_SIM_DIR'], 'slot-a.state')
        return open(p).read() if os.path.exists(p) else None

    def test_an_unpack_cut_short_leaves_slot_a_bad(self):
        os.environ['TECHO5_SIM_TAR_FAIL'] = '1'
        with self.assertRaises(techo5lib.Fail):
            ic.unpack_rootfs(self.unit, self.tarball(), self.work, '/data/local/tmp/techo5')
        self.assertEqual(self.slot_state(), 'bad')
        del os.environ['TECHO5_SIM_TAR_FAIL']
        ic.unpack_rootfs(self.unit, self.tarball(), self.work, '/data/local/tmp/techo5')
        self.assertEqual(self.slot_state(), 'good')

    def test_a_slot_without_its_symlinks_is_not_made_active(self):
        with self.assertRaises(techo5lib.Fail) as e:
            ic.unpack_rootfs(self.unit, self.tarball(links=False), self.work, '/data/local/tmp/techo5')
        self.assertIn('/bin/sh link', str(e.exception))

    def test_too_little_room_writes_nothing(self):
        rootfs = self.tarball()
        os.environ['TECHO5_SIM_FREE_KB'] = self.free_kb(rootfs, 0)
        with self.assertRaises(techo5lib.Fail) as e:
            ic.unpack_rootfs(self.unit, rootfs, self.work, '/data/local/tmp/techo5')
        self.assertIn('Nothing of the root filesystem was written', str(e.exception))

    def test_another_boards_rootfs_is_refused(self):
        import io
        import tarfile
        self.assertIsNone(ic.push.board_error(self.tarball(blobs=0)))
        path = os.path.join(self.d, 'donut.tar.gz')
        with tarfile.open(path, 'w:gz') as t:
            m = tarfile.TarInfo('./etc/techo5-release')
            data = b'techo5-donut v0.1.0 (abc)'
            m.size = len(data)
            t.addfile(m, io.BytesIO(data))
        self.assertIn("'techo5-donut v0.1.0 (abc)'", ic.push.board_error(path))
        with tarfile.open(path, 'w:gz') as t:
            t.addfile(tarfile.TarInfo('./etc'))
        self.assertIn('no etc/techo5-release', ic.push.board_error(path))

    def test_slot_done_checks_count_and_links(self):
        self.assertIn('[ -L $S/slots/a/bin/sh ]', ic.SLOT_DONE)
        self.assertIn('"$HAVE" -ge "$WANT"', ic.SLOT_DONE)


class FakeAdb:
    """What identity() asks the unit: here, a factory-reset one with no network saved and no name."""

    def __init__(self):
        self.asked = []

    def sh(self, command):
        self.asked.append(command)
        return ''


class Gift(unittest.TestCase):
    def args(self, **kw):
        import argparse
        d = dict(for_someone_else=False, wifi_ssid=None, wifi_passphrase_file=None, key_file=None, name=None,
                 serial=SERIAL)
        d.update(kw)
        return argparse.Namespace(**d)

    def test_gift_asks_and_writes_nothing(self):
        adb = FakeAdb()
        self.assertEqual(ic.identity(self.args(for_someone_else=True), adb, tempfile.gettempdir()),
                         (None, None, None, None, None))
        self.assertEqual(adb.asked, [])

    def test_no_saved_network_without_a_person_names_the_ways_out(self):
        os.environ['TECHO5_NO_PROMPT'] = '1'
        self.addCleanup(os.environ.pop, 'TECHO5_NO_PROMPT')
        with self.assertRaises(techo5lib.Fail) as e:
            ic.identity(self.args(), FakeAdb(), tempfile.gettempdir())
        self.assertIn('--wifi', str(e.exception))
        self.assertIn('--for-someone-else', str(e.exception))

    def test_marker_and_clearing(self):
        self.assertEqual(ic.FIRSTRUN_MARKER, '/data/misc/echolocal/firstrun')
        self.assertNotIn('network', ic.EMPTY_WIFI_CONF)
        with open(os.path.join(HERE, 'install-cannoli.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn("push_text('', FIRSTRUN_MARKER)", src)
        self.assertIn("push_text(EMPTY_WIFI_CONF, LINUX_DIR + '/wpa_supplicant.conf')", src)
        self.assertIn("rm -f %s/name %s/psk %s/wifi.conf", src)


if __name__ == '__main__':
    unittest.main()
