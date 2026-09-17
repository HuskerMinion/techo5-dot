#!/usr/bin/env python3
"""Change the Wi-Fi network a Dot running TECHO5 Linux joins, over its USB serial console.

    python3 tools/set-wifi.py --serial <serial> --ssid "MyNetwork"
    python3 tools/set-wifi.py --serial <serial> --forget

The console needs no network, so this works exactly when it is needed: a new router, a changed password, a
unit moved to another house. The passphrase is asked for without echoing and turned into WPA's key on this
computer; the unit receives only the name and key as hex, runs wifi-set, and rejoins at once. --forget goes
back to the network Fire OS saved. Windows, Linux and macOS alike; needs Python 3.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from techo5lib import CONSOLE_DOT, Console, ask_wifi, fail, note, run_main  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--serial', required=True)
    ap.add_argument('--ssid')
    ap.add_argument('--forget', action='store_true')
    a = ap.parse_args()
    if not a.forget and not a.ssid:
        fail('give --ssid, or --forget')
    console = Console(a.serial, CONSOLE_DOT)
    if not console.find():
        fail('no TECHO5 Linux console for %s on USB (is it plugged in and booted into Linux?)' % a.serial)
    if a.forget:
        command = 'wifi-set --forget'
    else:
        conf = dict(re.findall(r'^(ssid|psk)=([0-9a-f]+)$', ask_wifi(a.ssid), re.M))
        command = 'wifi-set --hex %s %s' % (conf['ssid'], conf['psk'])
    # Joining takes up to a minute and a quarter (association, then a lease).
    out = console.run(command, 90) or ''
    lines = [l for l in out.split('\n') if re.match(r'^(wifi-set|techo5-net):|^\d+\.\d+\.\d+\.\d+$', l)]
    for l in lines:
        note(l)
    ips = [l for l in lines if re.match(r'^\d+\.\d+\.\d+\.\d+$', l)]
    if ips:
        print('Joined: %s is at %s' % (a.serial, ips[-1]))
    else:
        print('The unit did not report an address; the console (%s) shows why.' % console.port)
        sys.exit(1)


if __name__ == '__main__':
    run_main(main)
