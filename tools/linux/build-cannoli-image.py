#!/usr/bin/env python3
"""Build the Echo Dot 5th gen's (cannoli) first Linux boot image: a boot_b FIT carrying the unit's own
signed kernel and device trees and our initramfs (tools/linux/init-cannoli), M2/M3 of the cannoli porting
plan.

    python3 tools/linux/build-cannoli-image.py --unit ../cannoli-<serial> -o build/cannoli-boot_b.img

--unit is the unit's working folder: bundle/ (the OS bundle cannoli-os-builder made for it: boot_a.img is
Fire OS's stock FIT, uboot_b.img the U-Boot that boots slot B) and m0/ (the backup: modules/ and
firmware/ from its system). --boot and --uboot take those two from elsewhere instead, such as the unit's
own boot_a and uboot_b volumes as tools/install-cannoli.py backs them up: a whole volume is cut to its
FIT, rounded up to 4 KiB as the bundle's images are. The inputs are techo5-dot's usual ones (docs/building.md): the Alpine armv7
minirootfs, busybox.static and apks-dot/ in TECHO5_INPUTS (default inputs/).

How the image is made. The FIT's configurations sign the kernel and the device tree (sign-images = fdt,
kernel) and leave the ramdisk out, so a ramdisk can be swapped for another with its hash-1 updated and
the signature still holds: TWRP on slot B is the same FIT made the same way. The configuration booted is
the -recovery one with the ramdisk (cannoli-pvt-aluminum-recovery, as TWRP's). The kernel's command line
is U-Boot's, not the FIT's (no configuration carries bootargs, and the configuration nodes are signed),
so it cannot be changed here; init-cannoli takes back what it can at run time.

Two checks before anything is written: every node and property of the result but the three changed
(the ramdisk's data, its hash, the default configuration) is byte-for-byte the stock FIT's, so whatever a
signature covers is unchanged; and the image is no longer than what the unit's U-Boot reads of boot_b
("ubi read 41000000 boot_b <length>" in uboot_b.img), since a longer one would be cut short and fail its
hash.

The image is the unit's own kernel, device trees, driver and firmware: never redistributable. Build it
from the backup of the unit it will run on. Windows, Linux and macOS; needs Python 3.
"""
import argparse
import glob
import hashlib
import importlib.util
import lzma
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# Fire OS NS6575/8349's boot.img, the FIT every cannoli slot A starts from.
STOCK_BOOT_SHA256 = "548dd73848cfcc76cb921ec5d8c68438c95886f4fc7187da9fa5f4b7efb8b201"
CONFIG = "cannoli-pvt-aluminum-recovery"

# The unit's Wi-Fi: the modules in the order Fire OS loads them (init.connectivity.rc, then the driver)
# and the MT7663's firmware, then its Bluetooth side's driver and settings (btmtksdio.ko, bt.cfg; the
# patch is the Wi-Fi one). Nothing of the DSP goes in: the ramdisk is small. A slot boot adopts all of
# them into the store on userdata (init-cannoli), so the root filesystem, which is published, carries
# none of the unit's own.
WIFI_MODULES = ["compat", "cfg80211", "mac80211", "wlan_mt76x3_sdio_prealloc", "wlan_mt76x3_sdio", "btmtksdio"]
WIFI_FIRMWARE = ["WIFI_RAM_CODE_MT7663.bin", "mt7663_patch_e2_hdr.bin", "EEPROM_MT7663.bin",
                 "TxPwrLimit_MT76x3.dat", "wifi.cfg", "regulatory.db", "bt.cfg"]

# The scripts of the Dot's root filesystem the rescue environment uses (slotctl: init-cannoli picks the
# slot with it).
SCRIPTS = ["techo5-net", "techo5-firewall", "wifi-set", "slotctl"]

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


class Fdt:
    """A flattened device tree (a FIT is one), read into its structure tokens and written back."""

    def __init__(self, data):
        (magic, total, off_struct, off_strings, _rsv, version, _last, _boot, size_strings,
         size_struct) = struct.unpack_from(">10I", data)
        if magic != 0xD00DFEED or total > len(data):
            raise ValueError("not a FIT")
        self.head = bytearray(data[:off_struct])
        self.strings = data[off_strings:off_strings + size_strings]
        self.tail = data[off_strings:total]
        self.version = version
        s = data[off_struct:off_struct + size_struct]
        self.tokens = []  # (kind, path, name, value)
        path, i = [], 0
        while True:
            tag = struct.unpack_from(">I", s, i)[0]
            i += 4
            if tag == FDT_BEGIN_NODE:
                end = s.index(b"\0", i)
                name = s[i:end].decode()
                i = (end + 4) & ~3
                path.append(name)
                self.tokens.append(("node", self.pathname(path), None, None))
            elif tag == FDT_END_NODE:
                self.tokens.append(("end", self.pathname(path), None, None))
                path.pop()
            elif tag == FDT_PROP:
                size, nameoff = struct.unpack_from(">II", s, i)
                i += 8
                name = self.strings[nameoff:self.strings.index(b"\0", nameoff)].decode()
                self.tokens.append(("prop", self.pathname(path), (name, nameoff), s[i:i + size]))
                i = (i + size + 3) & ~3
            elif tag == FDT_NOP:
                # Kept where it is: a signature's hashed regions take NOPs inside its nodes too.
                self.tokens.append(("nop", self.pathname(path), None, None))
            elif tag == FDT_END:
                break
            else:
                raise ValueError("bad FDT token %d" % tag)

    @staticmethod
    def pathname(parts):
        return "/" + "/".join(parts[1:])

    def get(self, path, name):
        for kind, p, n, v in self.tokens:
            if kind == "prop" and p == path and n[0] == name:
                return v
        raise KeyError("%s:%s" % (path, name))

    def set(self, path, name, value):
        for i, (kind, p, n, _) in enumerate(self.tokens):
            if kind == "prop" and p == path and n[0] == name:
                self.tokens[i] = (kind, p, n, value)
                return
        raise KeyError("%s:%s" % (path, name))

    def props(self):
        return {(p, n[0]): v for kind, p, n, v in self.tokens if kind == "prop"}

    def nodes(self):
        return [p for kind, p, _, _ in self.tokens if kind == "node"]

    def blob(self):
        out = bytearray()
        for kind, path, n, v in self.tokens:
            if kind == "node":
                name = path.rsplit("/", 1)[1] if path != "/" else ""
                raw = name.encode() + b"\0"
                out += struct.pack(">I", FDT_BEGIN_NODE) + raw + b"\0" * (-len(raw) % 4)
            elif kind == "end":
                out += struct.pack(">I", FDT_END_NODE)
            elif kind == "nop":
                out += struct.pack(">I", FDT_NOP)
            else:
                out += struct.pack(">III", FDT_PROP, len(v), n[1]) + v + b"\0" * (-len(v) % 4)
        out += struct.pack(">I", FDT_END)
        head = bytearray(self.head)
        off_struct = len(head)
        off_strings = off_struct + len(out)
        total = off_strings + len(self.tail)
        struct.pack_into(">I", head, 4, total)
        struct.pack_into(">I", head, 12, off_strings)
        struct.pack_into(">I", head, 36, len(out))
        return bytes(head) + bytes(out) + self.tail


def load_mkimage():
    spec = importlib.util.spec_from_file_location("mkimage", os.path.join(HERE, "mkimage.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lf(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def read(path):
    with open(path, "rb") as f:
        return f.read()


def initramfs(a, unit_m0):
    mk = load_mkimage()
    c = mk.Cpio()
    c.dir("dev")
    c.chardev("dev/console", 5, 1)
    c.chardev("dev/null", 1, 3, 0o666)
    c.chardev("dev/kmsg", 1, 11)
    for d in ("proc", "sys", "run", "data", "config", "android"):
        c.dir(d)
    c.dir("tmp", 0o1777)
    alpine = sorted(glob.glob(os.path.join(a.inputs, "alpine-minirootfs-*-armv7.tar.gz")))
    if len(alpine) != 1:
        sys.exit("need exactly one Alpine armv7 minirootfs in %s (tools/fetch-inputs.py in TECHO5)" % a.inputs)
    c.add_tar(alpine[0])
    # The patched wpa_supplicant (build-wpa-supplicant.sh) before Alpine's packages: the first entry of a
    # name is the one the cpio keeps. Alpine's 2.11 advertises 16 PTKSA replay counters, Amazon's MediaTek
    # driver puts its own RSNE into the association request, and the AP drops the station after EAPOL
    # msg 2/4: the Dot 3's MT7668 does it (docs/hardware-donut.md) and so does the Dot 5's MT7663 (first
    # boot, 2026-11-21: deauth reason 2 right after msg 2/4, every try).
    if os.path.exists(a.wpa_supplicant):
        c.add_parents("sbin/wpa_supplicant")
        c.file("sbin/wpa_supplicant", read(a.wpa_supplicant), 0o755)
        print("wpa_supplicant: %s (patched)" % a.wpa_supplicant)
    else:
        print("note: no %s: Alpine's wpa_supplicant goes in, and it will not join on this driver\n"
              "      build it with tools/linux/build-wpa-supplicant.sh (WSL/Linux)" % a.wpa_supplicant)
    apks = sorted(glob.glob(os.path.join(a.inputs, "apks-dot", "*.apk")))
    if not apks:
        sys.exit("no %s: the rescue packages (wpa_supplicant, dropbear, iptables) come from there"
                 % os.path.join(a.inputs, "apks-dot"))
    for apk in apks:
        c.add_tar(apk, skip_dotfiles=True)
    busybox = os.path.join(a.inputs, "busybox.static")
    if not os.path.exists(busybox):
        sys.exit("missing %s" % busybox)
    c.add_parents("bin/busybox.static")
    c.file("bin/busybox.static", read(busybox), 0o755)
    for s in SCRIPTS:
        c.add_parents("usr/local/sbin/" + s)
        c.file("usr/local/sbin/" + s, lf(os.path.join(HERE, "rootfs", "usr", "local", "sbin", s)), 0o755)
    c.file("init", lf(os.path.join(HERE, "init-cannoli")), 0o755)
    c.file("etc/techo5-release", ("techo5-cannoli rescue %s\n" % a.release).encode())

    missing = []
    for m in WIFI_MODULES:
        src = os.path.join(unit_m0, "modules", m + ".ko")
        if not os.path.exists(src):
            missing.append(src)
            continue
        c.add_parents("lib/modules/techo5/x")
        c.file("lib/modules/techo5/%s.ko" % m, read(src))
    for fw in WIFI_FIRMWARE:
        src = os.path.join(unit_m0, "firmware", fw)
        if not os.path.exists(src):
            missing.append(src)
            continue
        c.add_parents("lib/firmware/x")
        c.file("lib/firmware/" + fw, read(src))
    # The I2C parts U-Boot leaves disabled on this boot (build-cannoli-hwfixup.sh).
    if os.path.exists(a.hwfixup):
        c.add_parents("lib/modules/techo5/cannoli_hwfixup.ko")
        c.file("lib/modules/techo5/cannoli_hwfixup.ko", read(a.hwfixup))
        print("hwfixup: %s" % a.hwfixup)
        if a.hwfixup_args:
            # Read by init-cannoli onto the module's insmod line, e.g. ring=<node> (#780).
            c.file("lib/modules/techo5/cannoli_hwfixup.args", (a.hwfixup_args + "\n").encode())
            print("hwfixup args: %s" % a.hwfixup_args)
    else:
        print("note: no %s: no ring, speaker or microphones on this boot (build-cannoli-hwfixup.sh)" % a.hwfixup)
    if missing:
        print("note: not in the unit's backup, so not in the image (Wi-Fi may not come up):\n  "
              + "\n  ".join(missing))

    raw = c.finish()
    names = mk.verify_cpio(raw)
    for need in ("init", "bin/busybox.static", "sbin/wpa_supplicant", "usr/sbin/dropbear"):
        if need not in names:
            print("note: %s is not in the initramfs" % need)
    # LZMA (the legacy .lzma format), as TWRP's ramdisk in the same slot: this kernel has RD_LZMA.
    return lzma.compress(raw, format=lzma.FORMAT_ALONE, preset=9 | lzma.PRESET_EXTREME), len(names), len(raw)


def fit_image(data):
    """A FIT as an image file: a whole UBI volume read from the unit carries its FIT and then the volume's
    unused bytes, so it is cut at the FIT's own length, rounded up to 4 KiB as the bundle's images are."""
    if data[:4] != b"\xd0\x0d\xfe\xed":
        return data
    total = struct.unpack_from(">I", data, 4)[0]
    return data[:total + (-total % 4096)]


def uboot_read_length(uboot):
    m = re.search(rb"ubi read 41000000 boot_b ([0-9a-f]+);", uboot)
    if not m:
        sys.exit("the unit's uboot_b.img has no 'ubi read 41000000 boot_b <length>': is it the bundle's?")
    return int(m.group(1), 16)


HWFIXUP_ARG = re.compile(r"^(nodes|ring)=[A-Za-z0-9_@,./-]+$")


def hwfixup_args(v):
    """cannoli_hwfixup's parameters, as name=value words: only its own, in characters a path has."""
    for w in v.split():
        if not HWFIXUP_ARG.match(w):
            raise argparse.ArgumentTypeError("not a cannoli_hwfixup parameter: %r" % w)
    return " ".join(v.split())


def main():
    j = os.path.join
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", required=True, help="the unit's folder, with bundle/ and m0/")
    ap.add_argument("--boot", help="Fire OS 8349's stock boot FIT (default <unit>/bundle/boot_a.img)")
    ap.add_argument("--uboot", help="the U-Boot that boots slot B (default <unit>/bundle/uboot_b.img)")
    ap.add_argument("--inputs", default=os.environ.get("TECHO5_INPUTS") or j(REPO, "inputs"))
    ap.add_argument("--release", default="first", help="what /etc/techo5-release says")
    ap.add_argument("--hwfixup", default=j(REPO, "build", "cannoli-hwfixup", "cannoli_hwfixup.ko"),
                    help="build-cannoli-hwfixup.sh's module")
    ap.add_argument("--hwfixup-args", type=hwfixup_args, default="",
                    help="the module's parameters at boot, e.g. ring=/soc/i2c@10019100/lp5036@31")
    ap.add_argument("--wpa-supplicant", default=j(REPO, "build", "wpa_supplicant", "wpa_supplicant"),
                    help="build-wpa-supplicant.sh's, in place of Alpine's")
    ap.add_argument("-o", "--output", default=None, help="default: <unit>/builds/techo5-cannoli-boot_b.img")
    a = ap.parse_args()

    bundle, m0 = j(a.unit, "bundle"), j(a.unit, "m0")
    boot = a.boot or j(bundle, "boot_a.img")
    stock = fit_image(read(boot))
    if hashlib.sha256(stock).hexdigest() != STOCK_BOOT_SHA256:
        sys.exit("%s is not Fire OS 8349's boot.img" % boot)
    limit = uboot_read_length(read(a.uboot or j(bundle, "uboot_b.img")))

    ramdisk, entries, raw_size = initramfs(a, m0)

    fit = Fdt(stock)
    if CONFIG not in [p.rsplit("/", 1)[1] for p in fit.nodes() if p.startswith("/configurations/")]:
        sys.exit("the stock FIT has no configuration %s" % CONFIG)
    if fit.get("/configurations/" + CONFIG, "ramdisk") != b"ramdisk-1\0":
        sys.exit("%s does not boot ramdisk-1" % CONFIG)
    for sig in [p for p in fit.nodes() if p.endswith("/signature-1")]:
        if b"ramdisk" in fit.get(sig, "sign-images"):
            sys.exit("%s signs the ramdisk: it cannot be replaced" % sig)
    if fit.get("/images/ramdisk-1/hash-1", "algo") != b"sha256\0":
        sys.exit("ramdisk-1's hash is not sha256")
    changed = {("/images/ramdisk-1", "data"): ramdisk,
               ("/images/ramdisk-1/hash-1", "value"): hashlib.sha256(ramdisk).digest(),
               ("/configurations", "default"): CONFIG.encode() + b"\0"}
    for (path, name), value in changed.items():
        fit.set(path, name, value)
    image = fit.blob()
    image += b"\0" * (-len(image) % 4096)

    # Everything but the three changed properties is the stock FIT's, byte for byte.
    before, after = Fdt(stock), Fdt(image)
    if [t[:3] for t in before.tokens] != [t[:3] for t in after.tokens] or before.strings != after.strings:
        sys.exit("the FIT's structure changed")
    if before.nodes() != after.nodes():
        sys.exit("the node list changed")
    b, c = before.props(), after.props()
    if b.keys() != c.keys():
        sys.exit("the property list changed")
    for key in b:
        if key not in changed and b[key] != c[key]:
            sys.exit("%s:%s changed" % key)
    if c[("/images/ramdisk-1", "data")] != ramdisk:
        sys.exit("the ramdisk did not go in")

    size = len(image)
    print("initramfs: %d entries, %d bytes raw, %d bytes lzma" % (entries, raw_size, len(ramdisk)))
    print("image: %d bytes of the %d the unit's U-Boot reads (%d to spare)" % (size, limit, limit - size))
    if size > limit:
        sys.exit("too large: U-Boot reads only %d bytes of boot_b; take something out of the ramdisk" % limit)

    out = a.output or j(a.unit, "builds", "techo5-cannoli-boot_b.img")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(image)
    print("\nbuilt: %s (sha256 %s)" % (out, hashlib.sha256(image).hexdigest()))
    print("write it to slot B from rooted Fire OS (slot A), keeping bundle/boot_b.img (TWRP) to go back:")
    print("  adb -s <serial> push %s /data/local/tmp/linux.img" % os.path.basename(out))
    print("  adb -s <serial> shell su -c 'ubiupdatevol /dev/ubi0_<boot_b> /data/local/tmp/linux.img'")
    print("where <boot_b> is the number of the volume named boot_b (8 on the first unit;")
    print("`cat /sys/class/ubi/ubi0_8/name` says).")
    print("then power on holding Volume-Up. Back to TWRP: the same ubiupdatevol with bundle/boot_b.img.")


if __name__ == "__main__":
    main()
