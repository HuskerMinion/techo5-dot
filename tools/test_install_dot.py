"""Tests for the parts of install-dot.py that need no device. Run: python3 -m unittest tools/test_install_dot.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('install_dot', os.path.join(HERE, 'install-dot.py'))
install_dot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_dot)


class NodeSlug(unittest.TestCase):
    """The installer looks a unit up by the mDNS name the daemon makes of its name (layout.Slug)."""

    def test_like_the_daemon(self):
        self.assertEqual(install_dot.node_slug('Living Room'), 'living-room')
        self.assertEqual(install_dot.node_slug('  Extra Dot 2 '), 'extra-dot-2')
        self.assertEqual(install_dot.node_slug("Kid's  Room!"), 'kid-s-room')
        self.assertEqual(install_dot.node_slug('--Den--'), 'den')
        self.assertEqual(install_dot.node_slug('Küche'), 'k-che')
        self.assertEqual(install_dot.node_slug('Ελλάδα'), '')
        self.assertEqual(len(install_dot.node_slug('a' * 80)), 63)


class RescueDetection(unittest.TestCase):
    def test_rescue_when_no_slot_and_five_tries(self):
        self.assertTrue(install_dot.in_rescue({'slot': '', 'tries': 'TECHO5-TRIES 5 unhealthy'}))
        self.assertTrue(install_dot.in_rescue({'tries': 'TECHO5-TRIES 7 x'}))

    def test_not_rescue_with_a_slot(self):
        self.assertFalse(install_dot.in_rescue({'slot': 'a', 'tries': 'TECHO5-TRIES 5 x'}))

    def test_not_rescue_below_five_or_unknown(self):
        self.assertFalse(install_dot.in_rescue({'slot': '', 'tries': 'TECHO5-TRIES 2 booting'}))
        self.assertFalse(install_dot.in_rescue({'slot': '', 'tries': ''}))
        self.assertFalse(install_dot.in_rescue({}))
        self.assertFalse(install_dot.in_rescue({'slot': '', 'tries': 'TECHO5-TRIES 0 healthy'}))


class GiftInstall(unittest.TestCase):
    """--for-someone-else: nothing about Wi-Fi, name or key is asked or written; first run is marked."""

    def run_main(self, *argv):
        old = sys.argv
        sys.argv = ['install-dot.py'] + list(argv)
        try:
            install_dot.main()
        except sys.modules['techo5lib'].Fail as e:
            return str(e)
        finally:
            sys.argv = old
        return None

    def test_cannot_be_combined_with_name_wifi_or_key(self):
        for extra in (['--name', 'Kitchen'], ['--wifi', 'Net'], ['--wifi-passphrase-file', 'p.txt'],
                      ['--key-file', 'k.key'], ['--ssh-key', 'k.pub']):
            err = self.run_main('--for-someone-else', *extra)
            self.assertIn('--for-someone-else', err or '', extra)
            self.assertIn(extra[0], err, extra)

    def test_marker_is_in_the_state_directory(self):
        # echod's layout.StateDir on the Dot (echod/internal/layout/device_dot.go)
        self.assertEqual(install_dot.FIRSTRUN_MARKER, '/data/misc/echolocal/firstrun')

    def test_gift_branches_skip_wifi_name_and_key(self):
        with open(os.path.join(HERE, 'install-dot.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertRegex(src, r"if a\.for_someone_else:\n\s+note\('gift install: no Wi-Fi network")
        self.assertRegex(src, r"if a\.for_someone_else:\n\s+note\('gift install: no name or Home Assistant key")
        self.assertIn("push_text('', FIRSTRUN_MARKER)", src)
        # an earlier name, key and network are removed, and a configuration with no network is kept, so
        # techo5-net does not fall back to Fire OS's saved one
        self.assertIn("rm -f /data/misc/echolocal/name /data/misc/echolocal/psk /data/techo5-linux/wifi.conf", src)
        self.assertIn("push_text(EMPTY_WIFI_CONF, '/data/techo5-linux/wpa_supplicant.conf')", src)

    def test_empty_conf_has_no_network(self):
        self.assertNotIn('network', install_dot.EMPTY_WIFI_CONF)
        self.assertIn('ctrl_interface', install_dot.EMPTY_WIFI_CONF)


if __name__ == '__main__':
    unittest.main()
