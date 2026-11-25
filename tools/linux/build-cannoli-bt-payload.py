#!/usr/bin/env python3
"""Build the Echo Dot 5th gen's (cannoli) Bluetooth payload: a tarball that init-cannoli unpacks over its
root from /data/techo5-linux/bt.tar.gz at boot, since BlueZ does not fit in the boot_b ramdisk (U-Boot
reads 0xCA4000 bytes of it, and the ramdisk has about half a megabyte to spare).

    python3 tools/linux/build-cannoli-bt-payload.py --unit ../cannoli-<serial> [-o OUT]

What goes in:
  lib/modules/techo5/bt/      the unit's own btmtksdio.ko (MediaTek's vendor driver: WMT inside, it makes
                              /dev/stpbt), then build-cannoli-bt-modules.sh's Bluetooth stack and its
                              load-order
  lib/firmware/bt.cfg         the driver's settings, from the unit's backup (its patch,
                              mt7663_patch_e2_hdr.bin, is already in the ramdisk for Wi-Fi)
  usr/local/bin/btbridge      TECHO5's cmd/btbridge (/dev/stpbt to hci0 through /dev/vhci)
  usr/local/sbin/techo5-bt    the Dot's Bluetooth start script (bluetoothd, bluez-alsa)
  BlueZ, dbus, bluez-alsa     Alpine's packages, inputs/apks-bt-dot as for the Dot's root filesystem
  usr/bin/bluealsa            build-bluealsa.sh's, when built, in place of Alpine's 4.3.1

The unit's driver and firmware make the payload the unit's own: never redistributable.
"""
import argparse
import glob
import hashlib
import io
import os
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


class Out:
    """The payload, first entry of a name wins (as the ramdisk's cpio)."""

    def __init__(self, path):
        self.tar = tarfile.open(path, "w:gz", format=tarfile.GNU_FORMAT)
        self.seen = set()

    def parents(self, name):
        parts = name.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d not in self.seen:
                self.seen.add(d)
                t = tarfile.TarInfo(d)
                t.type, t.mode = tarfile.DIRTYPE, 0o755
                self.tar.addfile(t)

    def file(self, name, data, mode=0o644):
        if name in self.seen:
            return
        self.parents(name)
        self.seen.add(name)
        t = tarfile.TarInfo(name)
        t.size, t.mode = len(data), mode
        self.tar.addfile(t, io.BytesIO(data))

    def add_apk(self, path):
        # An apk is three gzip members (signature, control, data) read as one tar; the control and
        # signature entries are dotfiles at the top.
        with tarfile.open(path, "r:gz", ignore_zeros=True) as src:
            for m in src:
                name = m.name[2:] if m.name.startswith("./") else m.name
                name = name.rstrip("/")
                if not name or name.startswith("."):
                    continue
                if m.islnk() and m.linkname.startswith("./"):
                    m.linkname = m.linkname[2:]
                if name in self.seen:
                    continue
                self.parents(name)
                self.seen.add(name)
                m.name = name
                m.uid = m.gid = 0
                m.uname = m.gname = "root"
                m.pax_headers = {}
                if m.isfile():
                    self.tar.addfile(m, src.extractfile(m))
                elif m.isdir() or m.issym() or m.islnk():
                    self.tar.addfile(m)

    def close(self):
        self.tar.close()


def read(path):
    with open(path, "rb") as f:
        return f.read()


def lf(path):
    return read(path).replace(b"\r\n", b"\n")


def btbridge_text(path):
    """Which btbridge a build takes: its path, sha256, and whether it knows -legacy-advertising (the
    Echo Dot 5th gen's techo5-bt passes it only to one that does)."""
    with open(path, 'rb') as f:
        data = f.read()
    return '%s (sha256 %s, %s -legacy-advertising)' % (path, hashlib.sha256(data).hexdigest(),
                                                       'with' if b'legacy-advertising' in data else 'without')


def main():
    j = os.path.join
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", required=True, help="the unit's folder, with m0/")
    ap.add_argument("--inputs", default=os.environ.get("TECHO5_INPUTS") or j(REPO, "inputs"))
    ap.add_argument("--modules", default=j(REPO, "build", "cannoli-bt-modules"),
                    help="build-cannoli-bt-modules.sh's output")
    ap.add_argument("--btbridge", default=j(REPO, "build", "btbridge", "btbridge"),
                    help="TECHO5's cmd/btbridge for linux/arm (GOARM=7)")
    ap.add_argument("--bluealsa", default=os.environ.get("TECHO5_BLUEALSA") or j(REPO, "build", "bluealsa", "bluealsa"))
    ap.add_argument("-o", "--output", default=None, help="default: <unit>/builds/techo5-cannoli-bt.tar.gz")
    a = ap.parse_args()

    m0 = j(a.unit, "m0")
    need = [j(m0, "modules", "btmtksdio.ko"), j(m0, "firmware", "bt.cfg"), j(a.modules, "load-order"), a.btbridge]
    missing = [p for p in need if not os.path.exists(p)]
    if missing:
        sys.exit("missing:\n  " + "\n  ".join(missing))
    order = [l.strip() for l in open(j(a.modules, "load-order")) if l.strip()]
    for m in order:
        if not os.path.exists(j(a.modules, m + ".ko")):
            sys.exit("load-order names %s, not in %s" % (m, a.modules))
    apks = sorted(glob.glob(j(a.inputs, "apks-bt-dot", "*.apk")))
    if not apks:
        sys.exit("no %s (tools/fetch-inputs.py in TECHO5)" % j(a.inputs, "apks-bt-dot"))

    out = a.output or j(a.unit, "builds", "techo5-cannoli-bt.tar.gz")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    o = Out(out)
    mods = "lib/modules/techo5/bt/"
    o.file(mods + "btmtksdio.ko", read(j(m0, "modules", "btmtksdio.ko")))
    for m in order:
        o.file(mods + m + ".ko", read(j(a.modules, m + ".ko")))
    o.file(mods + "load-order", ("\n".join(order) + "\n").encode())
    o.file("lib/firmware/bt.cfg", read(j(m0, "firmware", "bt.cfg")))
    o.file("usr/local/bin/btbridge", read(a.btbridge), 0o755)
    print("btbridge: %s" % btbridge_text(a.btbridge))
    o.file("usr/local/sbin/techo5-bt", lf(j(HERE, "rootfs", "usr", "local", "sbin", "techo5-bt")), 0o755)
    conf = "etc/dbus-1/system.d/techo5-bluealsa.conf"
    o.file(conf, lf(j(HERE, "rootfs", conf)))
    if os.path.exists(a.bluealsa):
        o.file("usr/bin/bluealsa", read(a.bluealsa), 0o755)
        print("bluealsa: %s" % a.bluealsa)
    else:
        print("note: no %s: Alpine's bluez-alsa 4.3.1 goes in (build-bluealsa.sh has the AAC fix)" % a.bluealsa)
    for apk in apks:
        o.add_apk(apk)
    o.close()
    print("modules: btmtksdio " + " ".join(order))
    print("\nbuilt: %s (%d bytes)" % (out, os.path.getsize(out)))
    print("copy it to the unit's userdata from slot-B Linux, then reboot into slot B again (or run it now):")
    print('  cmd /c "ssh root@<ip> ""cat > /data/techo5-linux/bt.tar.gz"" < %s"' % os.path.basename(out))
    print("  ssh root@<ip> /sbin/techo5-bt-up")


if __name__ == "__main__":
    main()
