"""Tests for the root filesystem's build (linux/mkrootfs.py, linux/build-dot-rootfs.py) and what goes in
it, with no inputs fetched. Run: python3 -m unittest tools/test_rootfs.py"""
import importlib.util
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mkrootfs = load('mkrootfs', os.path.join(HERE, 'linux', 'mkrootfs.py'))
build = load('build_dot_rootfs', os.path.join(HERE, 'linux', 'build-dot-rootfs.py'))


class PackageDirs(unittest.TestCase):
    """A package folder holds each package once, exactly as its list names it: two versions of one would
    both be unpacked, the later over the earlier, into an image no list describes."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def folder(self, name, *files):
        path = os.path.join(self.d, name)
        os.makedirs(path, exist_ok=True)
        for f in files:
            open(os.path.join(path, f), 'wb').close()
        return path

    def test_names(self):
        self.assertEqual(mkrootfs.apk_name('bluez-alsa-4.3.1-r0.apk'), 'bluez-alsa')
        self.assertEqual(mkrootfs.apk_name('libcrypto1.1-1.1.1w-r0.apk'), 'libcrypto1.1')
        self.assertEqual(mkrootfs.apk_name('alsa-lib-1.2.15.3-r0.apk'), 'alsa-lib')
        self.assertIsNone(mkrootfs.apk_name('notes.txt'))
        self.assertIsNone(mkrootfs.apk_name('odd.apk'))

    def test_each_package_once(self):
        a = self.folder('a', 'dbus-1.16.2-r2.apk', 'glib-2.88.4-r0.apk')
        b = self.folder('b', 'tzdata-2026a-r0.apk')
        self.assertEqual([os.path.basename(p) for p in mkrootfs.apk_files([a, b])],
                         ['dbus-1.16.2-r2.apk', 'glib-2.88.4-r0.apk', 'tzdata-2026a-r0.apk'])

    def test_two_versions_in_one_folder_stop_the_build(self):
        a = self.folder('a', 'dbus-1.16.2-r2.apk', 'dbus-1.16.4-r0.apk')
        with self.assertRaises(SystemExit) as e:
            mkrootfs.apk_files([a])
        self.assertIn('dbus is there twice', str(e.exception.code))

    def test_one_package_in_two_folders_stops_the_build(self):
        a = self.folder('a', 'dbus-1.16.2-r2.apk')
        b = self.folder('b', 'dbus-1.16.2-r2.apk')
        with self.assertRaises(SystemExit):
            mkrootfs.apk_files([a, b])

    def test_a_file_not_named_as_a_package_stops_the_build(self):
        with self.assertRaises(SystemExit):
            mkrootfs.apk_files([self.folder('a', 'dbus.apk')])

    def test_the_folder_against_its_list(self):
        lst = os.path.join(self.d, 'packages-x.txt')
        with open(lst, 'w') as f:
            f.write('# a comment\n\ndbus-1.16.2-r2.apk\nglib-2.88.4-r0.apk\n')
        self.assertEqual(build.apkdir_problem(lst, self.folder('ok', 'dbus-1.16.2-r2.apk', 'glib-2.88.4-r0.apk')), '')
        newer = build.apkdir_problem(lst, self.folder('newer', 'dbus-1.16.4-r0.apk', 'glib-2.88.4-r0.apk'))
        self.assertIn('missing: dbus-1.16.2-r2.apk', newer)
        self.assertIn('not listed: dbus-1.16.4-r0.apk', newer)
        self.assertIn('missing: dbus-1.16.2-r2.apk glib-2.88.4-r0.apk', build.apkdir_problem(lst, os.path.join(self.d, 'none')))
        with open(lst, 'a') as f:
            f.write('dbus-1.16.4-r0.apk\n')
        self.assertIn('names dbus twice', build.apkdir_problem(lst, self.folder('both', 'dbus-1.16.2-r2.apk',
                                                                                'dbus-1.16.4-r0.apk', 'glib-2.88.4-r0.apk')))

    def test_the_lists_in_the_repository(self):
        # Each list names each package once, and no package is in two lists.
        seen = {}
        for lst, _ in build.PACKAGE_DIRS:
            for f in build.apk_list(os.path.join(HERE, 'linux', 'packages-%s.txt' % lst)):
                name = mkrootfs.apk_name(f)
                self.assertIsNotNone(name, f)
                self.assertNotIn(name, seen, '%s in packages-%s.txt and packages-%s.txt' % (name, lst, seen.get(name)))
                seen[name] = lst


class Image(unittest.TestCase):
    def test_the_notice_goes_in(self):
        args = build.notice_data()
        self.assertEqual(args, ['--data', '%s=/usr/share/licenses/techo5/NOTICE' % os.path.join(REPO, 'NOTICE')])
        with open(os.path.join(HERE, 'linux', 'build-dot-rootfs.py'), encoding='utf-8') as f:
            self.assertIn('cmd += notice_data()', f.read())

    def test_every_license_text_in_the_notice_ends_with_a_blank_line(self):
        with open(os.path.join(REPO, 'NOTICE'), encoding='utf-8') as f:
            lines = f.read().split('\n')
        for i, line in enumerate(lines[:-1]):
            if line.strip() == 'SOFTWARE.':
                self.assertEqual(lines[i + 1], '', 'NOTICE line %d: no blank line after a license text' % (i + 2))

    def test_the_firewall_lets_igmp_in(self):
        # TECHO5 #779: a switch's IGMP queries, dropped, let the Dot's SSDP group membership time out.
        with open(os.path.join(HERE, 'linux', 'rootfs', 'usr', 'local', 'sbin', 'techo5-firewall'), encoding='utf-8') as f:
            script = f.read()
        for rule in ('iptables-legacy -A INPUT -p igmp -j ACCEPT', 'iptables-legacy -A INPUT -p udp --dport 1900 -j ACCEPT'):
            self.assertIn(rule, script)
            self.assertLess(script.index(rule), script.index('iptables-legacy -P INPUT DROP'), rule)


if __name__ == '__main__':
    unittest.main()
