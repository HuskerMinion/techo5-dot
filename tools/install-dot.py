#!/usr/bin/env python3
"""Take an unlocked Echo Dot 2 (biscuit), Echo 2nd gen (radar) or Echo Dot 3rd gen (donut) from Fire OS or
TWRP to the TECHO5 Linux image, in one run.

    python3 tools/install-dot.py
    python3 tools/install-dot.py --serial <serial> --dry-run
    python3 tools/install-dot.py --serial <serial> --name Kitchen

Run with nothing, it finds the unit (asking which, when there are several) and asks for a name when
the unit has none. Every question has a switch, for running it from a script.

Windows, Linux and macOS alike; needs Python 3 and adb. Nothing is built: the signed release (root
filesystem, Bluetooth kernel, rescue packages) is downloaded and checked, and this unit's boot image is
built from it and the unit's own recovery backup. What it does, stopping at the first thing not right:

  1. checks the device: biscuit, radar or donut, Fire OS 6, adb as root, a saved Wi-Fi network (or asks)
  2. backs up every partition that boots the unit into backups/<serial>/, each checked by md5
  3. gets the release and builds this unit's boot image
  4. keeps the unit's Home Assistant identity if it has one (EchoLocal's name and key), or makes one
  5. writes the boot image to the recovery partition (swdl on the Dot 3) and reads it back
  6. unpacks the root filesystem into slot a of the store on the cache partition (userdata on the Dot 3)
  7. arms the Linux boot, reboots, and waits on the unit's USB console for a healthy boot

The Echo Dot 3rd gen differs in three places: it has no recovery partition, so the image goes where
kaeru boots recovery from (swdl, which held TWRP); the image carries the unit's own stock kernel, from
the boot slot Fire OS runs in, since TWRP's kernel is another build; and the store is on userdata, the
cache partition being 32 MB.

Prerequisites on the Dot: unlocked (amonet on the Dot 2 boards, kamakiri on the Dot 3), Fire OS 6574.1
in its system slots, and either in TWRP or in Fire OS with adb as root. Step by step from a stock Dot:
https://github.com/HuskerMinion/techo5/blob/main/docs/getting-started.md
"""
import argparse
import codecs
import glob
import json
import locale
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from techo5lib import wifi_conf as techo5_wifi_conf  # noqa: E402
from techo5lib import (CONSOLE_DOT, CONSOLE_TECHO5, Adb, Console, ask, ask_name, ask_wifi, check_serial_access,  # noqa: E402
                       console_hint, default_dir, download_checked, fail, head_is_android, interactive, md5,
                       need, new_api_key, note, pick_unit, repo_root, run_main, step, valid_api_key,
                       write_private)
from dotimage import BOARD_TYPE_IDS, KERNEL_61_VERSIONS, DotRelease, build_boot_image, kernel_board, kernel_is_61  # noqa: E402

# CABLE is what a backup that came over USB short or wrong usually means: the device reads the same
# every time, and the copy breaks off part way, with the Dot dropping off USB (techo5#86).
CABLE = ('This is usually the USB cable or port: try another cable, plugged straight into the computer '
         '(no hub or dock), and run the installer again.')

# The bytes Windows-1252 leaves undefined. Decoded as Latin-1 (U+0081 and so on), as the daemon's
# readableName reads them, so the installer and the device agree on the name.
CP1252_UNDEFINED = (0x81, 0x8D, 0x8F, 0x90, 0x9D)


def windows_code_page():
    """The code page an installer on this computer wrote a name in before names were UTF-8: the ANSI
    code page on Windows, Windows-1252 anywhere else, or where that cannot be told."""
    if os.name != 'nt':
        return 'cp1252'
    try:
        import ctypes
        enc = 'cp%d' % ctypes.windll.kernel32.GetACP()
    except (ImportError, AttributeError, OSError):
        enc = locale.getpreferredencoding(False)
    try:
        enc = codecs.lookup(enc).name
    except (LookupError, TypeError):
        return 'cp1252'
    # UTF-8 as the code page cannot be what wrote bytes that are not UTF-8.
    return 'cp1252' if enc in ('utf-8', 'cp65001') else enc


def readable_name(raw, enc=None):
    """The name file's bytes as text: UTF-8 as it is, anything else in the code page that most likely
    wrote it (windows_code_page). Windows-1252's undefined bytes come out as Latin-1, as on the device."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        pass
    enc = enc or windows_code_page()
    if codecs.lookup(enc).name == 'cp1252':
        return ''.join(chr(b) if b in CP1252_UNDEFINED else bytes([b]).decode('cp1252') for b in raw)
    return raw.decode(enc, 'replace')


PARTS = ['preloader', 'kb', 'dkb', 'lk_a', 'lk_b', 'tee1', 'tee2', 'expdb', 'misc', 'persist', 'boot_a', 'boot_b', 'recovery']
# The Echo Dot 3 has no recovery partition: kaeru boots swdl as recovery, and TWRP is there.
PARTS_DONUT = PARTS[:-1] + ['swdl']
# Where the store is: the cache partition on the Dot 2 boards, userdata on the Dot 3 (boot.sh binds either
# at /store).
STORE = '/cache/techo5'
STORE_DONUT = '/data/techo5-store/techo5'
# The consoles TECHO5 Linux brings up: the Dot 2 boards' Google ids, the Dot 3's Linux Foundation ones.
CONSOLES = (CONSOLE_DOT, CONSOLE_TECHO5)

# Wake words beyond esphome's built-in set, from the community collection at
# https://github.com/fwartner/home-assistant-wakewords-collection, which ships models with no manifest
# alongside them — the phrase is supplied here and written into one. Kept in step with TECHO5's own
# tools/fetch-inputs.py, which bundles the same set into the Show/Spot image.
EXTRA_MODELS = {
    'computer': ('en/computer/computer_v2.tflite', 'Computer'),
    'jarvis': ('en/jarvis/jarvis_v2.tflite', 'Jarvis'),
    'hey_friday': ('en/hey_friday/hey_Friday!.tflite', 'Hey Friday'),
    'glados': ('en/glados/glados.tflite', 'GLaDOS'),
    'hal': ('en/hal/hal_v2.tflite', 'HAL'),
    'terminator': ('en/terminator/Terminator.tflite', 'Terminator'),
    'marvin': ('en/marvin/marvin_v2.tflite', 'Marvin'),
    'home_assistant': ('en/home_assistant/Home_assistant.tflite', 'Home Assistant'),
}
EXTRA_MODELS_COMMIT = '8bcd2f20bb7b76c351b2eff871fa1ce873fe9be2'  # fwartner/home-assistant-wakewords-collection, 2026-01-13
EXTRA_MODELS_REPO = ('https://raw.githubusercontent.com/fwartner/home-assistant-wakewords-collection/%s'
                     % EXTRA_MODELS_COMMIT)
MODELS_COMMIT = '05b65922cc433c9df13e98e32a7fe520758c837e'  # esphome/micro-wake-word-models, 2025-03-21
MODELS_REPO = 'https://raw.githubusercontent.com/esphome/micro-wake-word-models/%s/models/v2' % MODELS_COMMIT

# Both repositories are other people's, and a branch points at whatever its owner pushed last. These
# models are written into /data/misc/echolocal/models, which the daemon parses as root on every boot,
# so they come from a commit that was looked at and have to hash to what that commit served. The pins
# and the sums are the same ones TECHO5's tools/fetch-inputs.py carries; keep the two in step.
#
# Moving a pin: put the new commit here, delete the downloaded models under the work directory and run
# the installer with --dry-run. It stops at the first file whose sha256 is not the one listed and
# prints what the file now hashes to; check the repository's history, then paste the new sums in.
MODEL_SHA256 = {
    'okay_nabu.tflite': '0689abe1912a95a3318a0d8cb2e67bad0cbcfe3e24dd6e050c75debddfb6f891',
    'okay_nabu.json': '6dd65604f70fe5ea9d1af73a7bf239529d1fbabc363807f45d2b22ce464ddbed',
    'hey_jarvis.tflite': '21a7976add39ee24ec96c63d96b7aaa18e24d1d9824b963e451da8feb4b78b77',
    'hey_jarvis.json': 'b153867d818675d8abcc9dace474afe7f83551ae0d5a9b1d71a98681320185af',
    'hey_mycroft.tflite': 'c2a9b6ed51182db72e014781d5a4ece1929dc232a40b5b4be384f0295f0e1571',
    'hey_mycroft.json': '57b2b06fe5fdbbe834a242fabc7af31e4194a550fc382b2c88636a6d62d0d57e',
    'alexa.tflite': '9011a8155b04de858c48038529235cbc0e42e9fca05a55bf588cb80a653a723b',
    'alexa.json': '1d999798b35b1fe2606465b75ab840be51c1811d2909d5e620cefb6e96f8abd0',
    'computer.tflite': '411db364955bf7b7a13a50d732a8b59c129e2fbe130a54f9eb3c20ca183bc4d0',
    'jarvis.tflite': 'cb2102fc9a76d4e02a740760d5ba2060978d766869489000b3565c8c4f8493a5',
    'hey_friday.tflite': 'eb127d82d884a1ef4167b455ec67682bf362b9de3e232c3f8d544a5e6ab4cb8b',
    'glados.tflite': '7564b95e5deed29cecfd55fd34cac70da5307c0d86108a7f87bfc610c9724dec',
    'hal.tflite': '8ddbdc859eed8fbd648f4b5fe137f82e5e8480c1756fd12257e3ef5645275117',
    'terminator.tflite': '7feb69397a56a6933d3248ecca3ca6aaca7b6bf2c36f5beb9b63bb3d17647686',
    'marvin.tflite': 'ed91c4d83e28bcc0af1cdebe3ab4f2a4f2d4c9908101ad8476d2e1b9b77f4e0c',
    'home_assistant.tflite': '9f54305884abde30d484f18dbff582ed6e2f1a141b93d6fa5f7feb505a0c94aa',
}

SLOT_SCRIPT = r'''set -e
T=$1
B=$T/busybox
S=$2
mkdir -p $S/slots
# A slot a from an earlier install is set aside until the new one has unpacked, then removed.
rm -rf $S/slots/a.old $S/slots/b.old
if [ -d $S/slots/a ]; then mv $S/slots/a $S/slots/a.old; fi
mkdir -p $S/slots/a
cd $S/slots/a
$B tar xzf $T/rootfs.tar.gz
[ -x $S/slots/a/sbin/init ] || { echo "unpacked slot has no /sbin/init"; exit 1; }
# The unit's firmware moves from the old slot into the store, where both slots find it.
if [ ! -e $S/firmware/WIFI_RAM_CODE_8163 ] && [ -e $S/slots/a.old/etc/firmware/WIFI_RAM_CODE_8163 ]; then
	mkdir -p $S/firmware; cp $S/slots/a.old/etc/firmware/* $S/firmware/
fi
rm -rf $S/slots/a.old
# Installed and checked from here, so it starts good rather than on trial. A slot b is left as it is.
: > $S/.techo5-store
echo good > $S/slots/a.state
echo a > $S/active
rm -f $S/rescue
sync
echo "slot a: $(cat $S/slots/a/etc/techo5-release), $(ls $S/slots/a/bin | wc -l) commands in /bin"
'''


def is_techo5_image(path):
    """A boot image TECHO5 built: every one carries techo5.* words in its header's command line."""
    with open(path, 'rb') as f:
        head = f.read(576)
    return head.startswith(b'ANDROID!') and b'techo5' in head[64:576]


def api_port_open(address):
    try:
        with socket.create_connection((address, 6053), timeout=2):
            return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--serial', help="the unit's adb serial (adb devices); found, or asked for, when missing")
    ap.add_argument('--name', help='the name Home Assistant shows, for a unit with no identity yet (asked for when missing)')
    ap.add_argument('--key-file', help='where a new Home Assistant key is kept (default backups/<serial>/home-assistant.key)')
    ap.add_argument('--release', default='latest', help="a release tag (dot-vX.Y.Z), or latest")
    ap.add_argument('--kernel', help="a kernel you built for this board (tools/linux/build-kernel.sh, --board radar for "
                    "an Echo 2nd gen) instead of the release's")
    ap.add_argument('--no-bluetooth-kernel', action='store_true', help="keep the unit's own kernel, which has no Bluetooth")
    ap.add_argument('--linux-61', action='store_true', help="Echo 2nd gen: the release's Linux 6.1 kernel (LibreEcho's) "
                    "instead of its 3.18 one")
    ap.add_argument('--rootfs', help="a root filesystem you built (tools/linux/build-dot-rootfs.py) instead of the release's")
    ap.add_argument('--wake-words', default='okay_nabu,hey_jarvis,hey_mycroft,alexa,' + ','.join(EXTRA_MODELS), help='models for a unit that has none')
    ap.add_argument('--ssh-key', help='an SSH public key allowed to log in as root once SSH is switched on')
    ap.add_argument('--wifi', '--wifi-ssid', dest='wifi_ssid', help="a Wi-Fi network to join instead of the one Fire OS saved (asks for the passphrase)")
    ap.add_argument('--wifi-passphrase-file', help='a file holding the --wifi passphrase, for running from a script')
    ap.add_argument('--dry-run', action='store_true', help='checks, backups, download and boot image; write nothing')
    ap.add_argument('--backups', default=default_dir('TECHO5_BACKUPS', 'backups'))
    ap.add_argument('--work', default=default_dir('TECHO5_WORK', 'build'))
    ap.add_argument('--adb', default='adb')
    a = ap.parse_args()
    need(a.adb, 'install the Android platform tools (adb)')
    ssh_key = None
    if a.ssh_key:
        # Read and checked before anything touches the unit: a wrong file would otherwise surface only
        # at the end of the install.
        with open(os.path.expanduser(a.ssh_key)) as f:
            ssh_key = f.read().strip()
        if not ssh_key.startswith(('ssh-', 'ecdsa-', 'sk-')) or len(ssh_key.split()) < 2:
            fail('%s does not look like an SSH public key' % a.ssh_key)
    a.serial = pick_unit(a.serial, a.adb, ('device', 'recovery'), 'Echo Dot', consoles=CONSOLES)
    if a.name is not None:
        a.name = ask_name(a.name, '')
    adb = Adb(a.serial, a.adb)

    # ---------------------------------------------------------------------------------------- 1
    step('device %s' % a.serial)
    state = adb.state()
    if state not in ('device', 'recovery'):
        fail("adb does not see %s (state: '%s'). Boot it into Fire OS (root adb) or TWRP with USB connected.%s"
             % (a.serial, state, console_hint(CONSOLES)))
    twrp = state == 'recovery'
    product = adb.sh('getprop ro.product.device; getprop ro.build.product')
    # The Echo 2nd gen (radar) is a biscuit with a bigger speaker: the same SoC, partitions and boot chain,
    # but its own device trees, so its own Bluetooth kernel (dotimage.KERNEL_ASSETS). The Echo Dot 3rd gen
    # (donut_puffin) is another SoC (MT8516) with its own daemon and boot scripts.
    board = ('radar' if 'radar' in product else 'biscuit' if 'biscuit' in product
             else 'donut' if 'donut' in product else None)
    if not board:
        fail('%s is not an Echo Dot 2nd gen (biscuit), an Echo 2nd gen (radar) or an Echo Dot 3rd gen (donut)' % a.serial)
    donut = board == 'donut'
    if donut and (a.kernel or a.no_bluetooth_kernel):
        fail('the Echo Dot 3 always runs its own kernel (Bluetooth comes as modules in its root filesystem); '
             'leave out --kernel and --no-bluetooth-kernel')
    if a.linux_61 and board not in KERNEL_61_VERSIONS:
        fail('--linux-61 is for the Echo 2nd gen (radar); %s is %s' % (a.serial, board))
    if a.linux_61 and (a.kernel or a.no_bluetooth_kernel):
        fail("--linux-61 chooses the release's kernel; leave out --kernel and --no-bluetooth-kernel")
    # The partition the Linux image goes into (kaeru's "recovery" on the Dot 3), and the store.
    target = 'swdl' if donut else 'recovery'
    store = STORE_DONUT if donut else STORE
    ident = adb.sh('id')
    if not ident.startswith('uid=0'):
        fail("adb is not root on %s (%s). EchoLocal's install or boot-root.zip gives root adb; or boot TWRP." % (a.serial, ident))
    # The board's device type id (idme) is what the installed system and update-boot.py go by, so when it
    # can be read (TWRP and root adb in Fire OS both have /proc/idme) it has to agree with the build props.
    type_id = adb.sh('cat /proc/idme/device_type_id 2>/dev/null').replace('\0', '').strip()
    if type_id:
        if BOARD_TYPE_IDS.get(type_id) != board:
            fail("%s says %s in its build props but device type id '%s' in idme; nothing was written"
                 % (a.serial, board, type_id))
    else:
        note('could not read the device type id (/proc/idme); going by the build props: %s' % board)
    slot = adb.sh('getprop ro.boot.slot_suffix')
    bn = '/dev/block/platform/bootdevice/by-name'
    if adb.sh('test -e %s/%s && echo yes' % (bn, target)) != 'yes':
        bn = adb.sh('for d in /dev/block/platform/*/by-name /dev/block/platform/*/*/by-name /dev/block/by-name; '
                    'do [ -e $d/%s ] && { echo $d; break; }; done' % target)
        if not bn:
            fail('no by-name partition links on %s' % a.serial)
    if twrp:
        # TWRP runs from RAM, so writing the recovery partition under it is safe. userdata and cache are
        # mounted here if TWRP has not already, and the Fire OS release is read off the slot's system.
        stage = '/tmp/techo5'
        mounts = adb.sh("grep -q ' /data ' /proc/mounts || mount -t ext4 %s/userdata /data; "
                        "grep -q ' /cache ' /proc/mounts || mount -t ext4 %s/cache /cache; "
                        "grep -q ' /data ' /proc/mounts && echo data; grep -q ' /cache ' /proc/mounts && echo cache" % (bn, bn))
        if 'data' not in mounts or 'cache' not in mounts:
            fail('TWRP could not mount userdata and cache on %s (%s)' % (a.serial, mounts))
        fireos = adb.sh("mkdir -p /tmp/t5sys; mount -t ext4 -o ro %s/system%s /tmp/t5sys 2>/dev/null; "
                        "sed -n 's/^ro.build.version.name=//p' /tmp/t5sys/system/build.prop 2>/dev/null; umount /tmp/t5sys 2>/dev/null" % (bn, slot))
        if not fireos:
            # This TWRP (amonet's radar build) already has the slot's system mounted, at /system_root.
            fireos = adb.sh("sed -n 's/^ro.build.version.name=//p' /system_root/system/build.prop 2>/dev/null")
    else:
        stage = '/data/local/tmp/techo5'
        fireos = adb.sh('getprop ro.build.version.name')
    if 'Fire OS 6' not in fireos:
        fail("%s has '%s' in slot %s; this installer is for Fire OS 6" % (a.serial, fireos, slot))
    note('%s, %s, slot %s, %s' % (board, fireos, slot, 'in TWRP' if twrp else 'root adb in Fire OS'))

    # Wi-Fi: the network Fire OS saved, one set on the unit before (wifi-set), or one given here. Only the
    # derived key goes to the unit (techo5lib.wifi_conf).
    wifi = adb.sh('sed -n "s/^[ \\t]*ssid=//p" /data/misc/wifi/wpa_supplicant.conf 2>/dev/null | head -1')
    own_wifi = adb.sh('test -s /data/techo5-linux/wifi.conf && echo yes') == 'yes'
    wifi_conf = None
    if a.wifi_ssid or (not wifi and not own_wifi):
        ssid = a.wifi_ssid
        if not ssid:
            note('%s has no saved Wi-Fi network' % a.serial)
            if not interactive():
                fail('%s has no saved Wi-Fi network: pass --wifi (the passphrase is asked for, or read from --wifi-passphrase-file)' % a.serial)
            ssid = ask('Wi-Fi network name')
        if a.wifi_passphrase_file:
            # UTF-8, whatever this computer's code page, with a BOM (Notepad's) dropped. Only the line's
            # end goes: a passphrase may begin or end with spaces.
            with open(a.wifi_passphrase_file, encoding='utf-8-sig') as f:
                wifi_conf = techo5_wifi_conf(ssid, f.read().rstrip('\r\n'))
        else:
            wifi_conf = ask_wifi(ssid)
        note("will join '%s'" % ssid)
    elif own_wifi:
        note('Wi-Fi network set on the unit before (wifi-set); keeping it')
    else:
        note('saved Wi-Fi network %s' % wifi)
    ip = None
    if not twrp:
        ip = adb.sh('ifconfig wlan0 2>/dev/null | sed -n "s/.*inet addr:\\([0-9.]*\\).*/\\1/p" | head -1') or adb.sh('getprop dhcp.wlan0.ipaddress') or None
        if ip:
            note('address in Fire OS: %s' % ip)

    # ---------------------------------------------------------------------------------------- 2
    unit = os.path.join(a.backups, a.serial)
    os.makedirs(unit, exist_ok=True)
    step('backups to %s' % unit)
    for p in PARTS_DONUT if donut else PARTS:
        out = os.path.join(unit, p + '.img')
        # amonet's TWRP points preloader, lk and tee at decoy files in /tmp/ota-decoy, so an OTA flashed
        # from it cannot overwrite the unlock. The real partitions are the *_real links, and the preloader
        # is the eMMC boot area. A copy of a decoy would restore nothing.
        src = adb.sh('s=%s/%s; [ -e ${s}_real ] && s=${s}_real; case $(readlink $s) in /tmp/*) if [ %s = preloader ]; '
                     'then s=/dev/block/mmcblk0boot0; else s=; fi;; esac; echo $s' % (bn, p, p))
        if not src:
            fail('%s on %s points at a decoy with no real partition beside it' % (p, a.serial))
        dev = adb.sh('md5sum %s' % src).split(' ')[0]
        if os.path.exists(out) and md5(out) == dev:
            note('%s already backed up' % p)
            continue
        same = [f for f in glob.glob(os.path.join(unit, p + '-*.img')) if md5(f) == dev]
        if same:
            note('%s already backed up as %s' % (p, os.path.basename(same[0])))
            continue
        if os.path.exists(out) and p == target and head_is_android(out):
            # A recovery backup that no longer matches is TWRP from before a Linux image went in: the way back.
            note("%s: keeping the earlier backup (the device's %s has changed since)" % (p, p))
            continue
        dest = out if not os.path.exists(out) else os.path.join(unit, '%s-%s.img' % (p, time.strftime('%Y%m%d-%H%M%S')))
        tmp = dest + '.partial'
        # cat through exec-out, byte for byte; kept only once its md5 matches the device.
        if adb.exec_out_to_file('cat ' + src, tmp) != 0:
            fail('reading %s failed. %s' % (p, CABLE))
        got = md5(tmp)
        if got != dev:
            os.remove(tmp)
            fail('%s copy does not match the device (%s vs %s). %s' % (p, got, dev, CABLE))
        os.replace(tmp, dest)
        note('%s %d bytes, md5 ok%s' % (p, os.path.getsize(dest), '' if dest == out else ' (changed since %s.img; saved as %s)' % (p, os.path.basename(dest))))
    # recovery is the unit's TWRP, the way back; donor is the image whose header and kernel the Linux image
    # is built on: the same TWRP on the Dot 2 boards, the stock kernel of Fire OS's boot slot on the Dot 3.
    recovery = os.path.join(unit, target + '.img')
    if not head_is_android(recovery):
        fail('%s backup is not an Android boot image' % target)
    donor = recovery
    if donut:
        donor = os.path.join(unit, 'boot%s.img' % (slot or '_a'))
        if not head_is_android(donor):
            fail('%s is not an Android boot image' % donor)
    if is_techo5_image(recovery):
        # The recovery partition already held TECHO5 Linux when this backup was first taken. It is not a
        # way back to TWRP, and as the donor for the new image it would hand over a header already edited
        # for Linux (and perhaps the Bluetooth kernel) instead of the unit's own.
        fail("%s is a TECHO5 Linux boot image, not the unit's TWRP. Put this unit's TWRP image there "
             "(or boot TWRP, flash it to %s and move this file aside), then run this again." % (recovery, target))

    # ---------------------------------------------------------------------------------------- 3
    step("the release, and this unit's boot image")
    os.makedirs(a.work, exist_ok=True)
    rel = DotRelease(a.work, a.release, board)
    note('TECHO5 Dot %s: root filesystem, %s and rescue packages checked'
         % (rel.version, 'Bluetooth modules' if donut else 'Bluetooth kernel'))
    rootfs = os.path.abspath(a.rootfs) if a.rootfs else rel.rootfs
    kernel = None if a.no_bluetooth_kernel else (os.path.abspath(a.kernel) if a.kernel else rel.kernel_for(board, a.linux_61))
    if kernel is None and a.linux_61:
        fail('release %s has no Linux 6.1 kernel for %s; nothing was written' % (rel.version, board))
    if kernel is None and not a.no_bluetooth_kernel and not donut:
        note('release %s has no Bluetooth kernel for %s (--kernel takes one from tools/linux/build-kernel.sh '
             '--board %s)' % (rel.version, board, board))
    if kernel:
        # A kernel built for the other board would not boot this one, and on the Echo 2nd gen getting back
        # means opening the case: the board is read from the kernel's own configuration first.
        if not os.path.exists(kernel):
            fail('no kernel at %s' % kernel)
        kb = kernel_board(kernel)
        if kb != board:
            fail('%s is %s, not a kernel for this %s; nothing was written' % (
                kernel, 'built for %s' % kb if kb else 'a kernel whose board cannot be read', board))
    for f in (rootfs, kernel):
        if f and not os.path.exists(f):
            fail('no file at %s' % f)
    if donut:
        note("kernel: the unit's own, from %s (Bluetooth as modules)" % os.path.basename(donor))
    else:
        note('kernel: %s' % ('%s (%s)' % (os.path.basename(kernel), 'Linux 6.1' if kernel_is_61(kernel, board) else 'Bluetooth')
                             if kernel else "the unit's own (no Bluetooth)"))
    image = os.path.join(unit, 'techo5-dot-linux.img')
    build_boot_image(donor, image, rel.alpine, rel.busybox, rel.wmtup, rel.apks, kernel, board, rel.wpa_supplicant)
    note('boot image %d bytes, root filesystem %d bytes' % (os.path.getsize(image), os.path.getsize(rootfs)))

    # ---------------------------------------------------------------------------------------- 4
    step('Home Assistant identity')
    have_name = adb.sh('cat /data/misc/echolocal/name 2>/dev/null')
    have_key = adb.sh('test -s /data/misc/echolocal/psk && echo yes') == 'yes'
    name, psk, key_file = None, None, None
    shown = None  # the name and key printed at the end, for adding the Dot to Home Assistant
    if have_name and have_key:
        # Written by an installer before dot-v1.0.15 on Windows, in the Windows code page rather than
        # UTF-8: Home Assistant could not add the device. Read in that code page, and written again as
        # UTF-8 below, once the key is known to be good.
        rewrite = None
        if '�' in have_name:
            raw = adb.run('shell', 'cat /data/misc/echolocal/name').stdout.strip()
            try:
                raw.decode('utf-8')
            except UnicodeDecodeError:
                rewrite = readable_name(raw)
                have_name = rewrite
        note("keeping '%s' and its key: Home Assistant sees the same device" % have_name)
        key = adb.sh('cat /data/misc/echolocal/psk')
        if valid_api_key(key):
            key_file = a.key_file or os.path.join(unit, 'home-assistant.key')
            if not os.path.exists(key_file):
                write_private(key_file, key)
            shown = (have_name, key)
            if rewrite is not None:
                name, psk = rewrite, key
                note("the name was not saved as UTF-8; it is written again as '%s'" % name)
    else:
        note('%s has no Home Assistant identity yet' % a.serial)
        name = ask_name(a.name, 'Echo Dot')
        key_file = a.key_file or os.path.join(unit, 'home-assistant.key')
        if os.path.exists(key_file):
            with open(key_file) as f:
                psk = f.read().strip()
            if not valid_api_key(psk):
                fail('the key in %s is not 32 bytes of base64' % key_file)
        else:
            psk = new_api_key()
            write_private(key_file, psk)
            note('new key written to %s; Home Assistant asks for it when the device is added' % key_file)
        note("will provision '%s'" % name)
        shown = (name, psk)
    models = None
    have_models = adb.sh('ls /data/misc/echolocal/models/*.tflite 2>/dev/null | wc -l') or '0'
    if int(have_models or 0) == 0:
        models = os.path.join(a.work, 'models')
        os.makedirs(models, exist_ok=True)
        words = [w.strip() for w in a.wake_words.split(',') if w.strip()]
        for w in words:
            tf = os.path.join(models, '%s.tflite' % w)
            js = os.path.join(models, '%s.json' % w)
            # A name with no checksum recorded for it would be a download nothing could vouch for.
            if '%s.tflite' % w not in MODEL_SHA256:
                fail("no wake word model '%s' is pinned here; --wake-words takes any of: %s"
                     % (w, ', '.join(sorted(n[:-7] for n in MODEL_SHA256 if n.endswith('.tflite')))))
            if w in EXTRA_MODELS:
                path, phrase = EXTRA_MODELS[w]
                download_checked('%s/%s' % (EXTRA_MODELS_REPO, path), tf, MODEL_SHA256['%s.tflite' % w])
                if not os.path.exists(js):
                    with open(js, 'w') as f:
                        json.dump({'wake_word': phrase, 'model': '%s.tflite' % w, 'trained_languages': ['en']}, f, indent=2)
            else:
                for ext, out in (('json', js), ('tflite', tf)):
                    download_checked('%s/%s.%s' % (MODELS_REPO, w, ext), out, MODEL_SHA256['%s.%s' % (w, ext)])
        note('wake word models: %s' % ', '.join(words))
    else:
        note('%s wake word models already on the unit' % have_models)

    if a.dry_run:
        print('\nDry run: checks, backups and builds done; nothing written to %s.\n  boot image:      %s\n  root filesystem: %s' % (a.serial, image, rootfs))
        return
    check_serial_access(must=False)

    # ---------------------------------------------------------------------------------------- 5
    step('boot image into the %s partition' % target)
    # swdl on the Dot 3 is 32 MB, and system_a starts where it ends: an image that does not fit is never
    # written. The images are about 13 MB.
    if donut and os.path.getsize(image) > 32 * 1024 * 1024:
        fail('the boot image is %d bytes, more than the 32 MB swdl partition holds' % os.path.getsize(image))
    # The image goes into the store first and stays there: to-twrp and back-to-linux.sh use that copy.
    adb.sh('mkdir -p %s %s' % (stage, store))
    adb.push(image, store + '/linux.img')
    adb.push(rel.busybox, stage + '/busybox')
    want = md5(image)
    got = adb.sh('chmod 755 %s/busybox; dd if=%s/linux.img of=%s/%s bs=1048576 2>/dev/null; sync; %s/busybox head -c %d %s/%s | md5sum'
                 % (stage, store, bn, target, stage, os.path.getsize(image), bn, target)).split(' ')[0]
    if got != want:
        fail('%s partition reads back %s, wanted %s. The TWRP backup is at %s.' % (target, got, want, recovery))
    note('written and read back, md5 %s' % got)

    # ---------------------------------------------------------------------------------------- 6
    step('root filesystem into slot a')
    adb.push(rootfs, stage + '/rootfs.tar.gz')
    script = os.path.join(a.work, 'techo5-dot-slot.sh')
    with open(script, 'w', newline='\n') as f:
        f.write(SLOT_SCRIPT)
    adb.push(script, stage + '/slot.sh')
    os.remove(script)
    result = adb.sh('sh %s/slot.sh %s %s 2>&1' % (stage, stage, store))
    if 'slot a: ' not in result:
        fail('installing the slot failed: %s' % result)
    note(result)
    # The way back to TWRP without a PC: the unit's own TWRP (its recovery backup, checked in step 2 not to
    # be a Linux image) and the script that undoes it.
    adb.push(recovery, store + '/twrp.img')
    if adb.sh('md5sum %s/twrp.img' % store).split(' ')[0] != md5(recovery):
        fail('the TWRP copy on the unit does not match %s' % recovery)
    adb.push(os.path.join(repo_root(), 'tools', 'linux', 'back-to-linux.sh'), store + '/back-to-linux.sh')
    note('TWRP copy kept on the unit (to-twrp puts it back; %s/back-to-linux.sh returns)' % store)

    # SSH keys and Wi-Fi go to userdata, where they outlast slots and updates.
    tmp = os.path.join(a.work, 'provision-' + a.serial)
    os.makedirs(tmp, exist_ok=True)

    def push_text(text, remote, mode='600'):
        local = os.path.join(tmp, os.path.basename(remote))
        with open(local, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        adb.push(local, remote)
        os.remove(local)
        adb.sh('chmod %s %s' % (mode, remote))

    adb.sh('mkdir -p /data/misc/echolocal/ssh; chmod 700 /data/misc/echolocal/ssh')
    if ssh_key:
        have = adb.sh('cat /data/misc/echolocal/ssh/authorized_keys /data/techo5-linux/ssh/authorized_keys 2>/dev/null')
        if ssh_key.split()[1] not in have:
            push_text('\n'.join(x for x in (have, ssh_key) if x) + '\n', '/data/misc/echolocal/ssh/authorized_keys')
        note('SSH key %s set; turn on the SSH switch in Home Assistant to use it' % os.path.basename(a.ssh_key))
    if wifi_conf:
        adb.sh('mkdir -p /data/techo5-linux')
        push_text(wifi_conf, '/data/techo5-linux/wifi.conf')
        note('Wi-Fi network written to the unit')
    if name:
        step("provisioning '%s'" % name)
        adb.sh('mkdir -p /data/misc/echolocal/models; chmod 700 /data/misc/echolocal')
        push_text(name, '/data/misc/echolocal/name')
        push_text(psk, '/data/misc/echolocal/psk')
    if models:
        adb.sh('mkdir -p /data/misc/echolocal/models')
        for m in sorted(os.listdir(models)):
            adb.push(os.path.join(models, m), '/data/misc/echolocal/models/' + m)
        adb.sh('chmod 644 /data/misc/echolocal/models/*')
    os.rmdir(tmp)

    # ---------------------------------------------------------------------------------------- 7
    step('arming the Linux boot and rebooting')
    adb.sh("printf 'TECHO5-TRIES 0 installed\\n' | dd of=%s/misc bs=512 seek=14 count=1 conv=notrunc 2>/dev/null; "
           'rm -f %s/rootfs.tar.gz %s/busybox %s/slot.sh; sync' % (bn, stage, stage, stage))
    adb.reboot('recovery')

    # The unit is found on its USB serial console by its own serial number and asked how the boot went:
    # that works whatever address DHCP hands Linux, and from TWRP, where no address was known at all.
    note("waiting for the unit's Linux console on USB")
    console = Console(a.serial, CONSOLE_TECHO5 if donut else CONSOLE_DOT)
    query = ("echo slot=$(cat /run/techo5/slot 2>/dev/null); echo release=$(cat /etc/techo5-release 2>/dev/null); "
             "echo ip=$(ifconfig wlan0 2>/dev/null | sed -n 's/.*inet addr:\\([0-9.]*\\).*/\\1/p'); echo echod=$(pidof echod); "
             "echo tries=$(dd if=/dev/mmcblk0p8 bs=512 skip=14 count=1 2>/dev/null | tr -d '\\000')")
    healthy, said, told = False, '', ''
    start = time.time()
    deadline, ticked = start + 8 * 60, start
    while time.time() < deadline:
        out = console.run(query, 8)
        if not out and time.time() - start >= 30:
            # Nothing from the console yet: say so now and then, and why, so the wait never reads as a hang.
            if time.time() - ticked >= 30:
                note('still waiting for the console (%d s of %d)' % (time.time() - start, 8 * 60))
                ticked = time.time()
            h = console.waiting_hint()
            if h and h != told:
                note(h)
                told = h
        if out:
            kv = dict(re.findall(r'^(\w+)=(.*)$', out, re.M))
            now = 'slot %s, %s, address %s, echod %s' % (kv.get('slot'), kv.get('release'), kv.get('ip') or 'none yet',
                                                         'running' if kv.get('echod') else 'not running')
            if now != said:
                note(now)
                said = now
            if kv.get('ip'):
                ip = kv['ip'].strip()
            # Healthy is the boot's own verdict: echod up for 90 s, and the try count back to 0.
            if 'healthy' in kv.get('tries', ''):
                healthy = True
                break
        time.sleep(5)
    up = False
    if ip:
        deadline = time.time() + 60
        up = api_port_open(ip)
        while not up and time.time() < deadline:
            time.sleep(5)
            up = api_port_open(ip)

    print()
    if shown:
        print("Home Assistant will discover '%s' as an ESPHome device. When it asks for the encryption key, paste:\n\n    %s\n\n(also saved in %s)\n" % (shown + (key_file,)))
    if healthy and up:
        print("Done: %s is running TECHO5 Linux, healthy, and Home Assistant's API port answers at %s:6053." % (a.serial, ip))
        print('      SSH: switch it on in Home Assistant, then ssh root@%s (wifi-set, slotctl status, to-twrp)' % ip)
    elif up:
        print('%s answers at %s:6053, but the boot was not confirmed healthy (no console, or not within the wait).' % (a.serial, ip))
    else:
        print('Rebooted, but the unit was not confirmed up%s.' % (' (%s:6053 did not answer)' % ip if ip else ''))
        print('After five boots that never become healthy it stays in rescue (techo5-retry tries again). The USB console')
        print('shows the boot and the crumbs (tools/linux/readmisc.sh).')
        sys.exit(1)


if __name__ == '__main__':
    run_main(main)
