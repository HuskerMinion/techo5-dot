#!/usr/bin/env python3
"""Build the Echo Dot's root filesystem as a tar.gz, for a slot in the store on the cache partition.

The initramfs in the recovery partition boots this; see tools/linux/init. The pieces are the same
ones the initramfs carries — Alpine armv7, the static busybox, the supplicant, cmd/wmtup — plus what
only a real root filesystem has room for: the daemon itself, which is 20 MB and will never fit in a
16 MB partition beside a kernel.

    python3 mkrootfs.py --rootfs alpine-minirootfs-armv7.tar.gz --apkdir apks-dot \
        --add busybox.static=/bin/busybox.static --add wmtup=/usr/local/bin/wmtup \
        --add echod=/usr/local/bin/echod --overlay rootfs -o techo5-dot-rootfs.tar.gz

No root and no device nodes: the rootfs makes those at boot, the way the initramfs does, because
this is unpacked by an unprivileged-looking untar on the device rather than restored as an image.
"""

import argparse
import io
import os
import sys
import tarfile
import time


class Root:
    """The tree being assembled, written out as a tar."""

    def __init__(self):
        # Entries are kept by name and written once, at the end, so a later source replaces an earlier
        # one rather than following it into the archive. That matters on the device: busybox tar
        # creates symlinks last, and one it cannot create — Alpine's sbin/init symlink after our own
        # sbin/init file — abandons every symlink still waiting, which is how the first slot arrived
        # with nothing in /bin.
        self.entries = {}
        self.seen = set()

    def _info(self, name, mode, kind=tarfile.REGTYPE, size=0, link=""):
        ti = tarfile.TarInfo(name)
        ti.mode, ti.type, ti.size, ti.linkname = mode, kind, size, link
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        ti.mtime = int(time.time())
        return ti

    def dir(self, name, mode=0o755):
        name = name.strip("/")
        if not name or name in self.seen:
            return
        self.parents(name)
        self.entries[name] = (self._info(name + "/", mode, tarfile.DIRTYPE), None)
        self.seen.add(name)

    def parents(self, name):
        parts = name.strip("/").split("/")[:-1]
        for i in range(1, len(parts) + 1):
            self.dir("/".join(parts[:i]))

    def file(self, name, data, mode=0o644):
        name = name.strip("/")
        self.parents(name)
        self.entries.pop(name, None)
        self.entries[name] = (self._info(name, mode, size=len(data)), data)
        self.seen.add(name)

    def symlink(self, name, target):
        name = name.strip("/")
        self.parents(name)
        self.entries.pop(name, None)
        self.entries[name] = (self._info(name, 0o777, tarfile.SYMTYPE, link=target), None)
        self.seen.add(name)

    def add_tar(self, path, skip_dotfiles=False):
        with tarfile.open(path, "r:*") as tf:
            for m in tf:
                name = m.name
                if name.startswith("./"):
                    name = name[2:]
                # The check has to see the leading dot, so it comes before the strip rather than
                # after: an apk's .PKGINFO becomes "PKGINFO" and lands in / if it does not.
                if skip_dotfiles and name.startswith("."):
                    continue  # apk metadata: .PKGINFO, .SIGN.*, .post-install
                name = name.strip("/")
                if not name:
                    continue
                if m.isdir():
                    self.dir(name, m.mode & 0o7777)
                elif m.issym():
                    self.symlink(name, m.linkname)
                elif m.isfile():
                    self.file(name, tf.extractfile(m).read(), m.mode & 0o7777)
                elif m.islnk():
                    src = tf.getmember(m.linkname)
                    self.file(name, tf.extractfile(src).read(), src.mode & 0o7777)

    def add_dir(self, path, mode_exec=0o755):
        """An overlay directory, copied in as it stands. Scripts come out executable."""
        for dirpath, _, files in os.walk(path):
            for f in files:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, path).replace("\\", "/")
                data = open(full, "rb").read()
                # Everything that lands on the device runs there, so normalise line endings: a
                # script with CRLF fails with "not found" naming its own interpreter.
                if data[:2] == b"#!":
                    data = data.replace(b"\r\n", b"\n")
                    self.file(rel, data, mode_exec)
                else:
                    self.file(rel, data)

    def finish(self, path):
        import gzip

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for info, data in self.entries.values():
                tf.addfile(info, io.BytesIO(data) if data is not None else None)
        with gzip.open(path, "wb", compresslevel=6) as out:
            out.write(buf.getvalue())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rootfs", required=True, help="Alpine minirootfs .tar.gz")
    ap.add_argument("--apkdir", action="append", default=[],
                    help="directory of .apk packages to unpack (repeatable)")
    ap.add_argument("--add", action="append", default=[], metavar="SRC=DEST", help="an executable")
    ap.add_argument("--data", action="append", default=[], metavar="SRC=DEST", help="a data file (0644)")
    ap.add_argument("--overlay", help="directory copied in as it stands")
    ap.add_argument("--release", default="", help="what to write in /etc/techo5-release")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()

    r = Root()
    r.add_tar(a.rootfs, skip_dotfiles=True)

    for d in a.apkdir:
        if not os.path.isdir(d):
            sys.exit(f"no package directory at {d} (fetch-inputs.py fills it)")
        for f in sorted(os.listdir(d)):
            if f.endswith(".apk"):
                r.add_tar(os.path.join(d, f), skip_dotfiles=True)

    for spec, mode in [(s, 0o755) for s in a.add] + [(s, 0o644) for s in a.data]:
        src, _, dest = spec.partition("=")
        if not dest:
            sys.exit(f"--add and --data want SRC=DEST, got {spec!r}")
        # A shell that rewrites unix paths for Windows turns /usr/local/bin/x into
        # C:/Program Files/Git/usr/local/bin/x, and the tree then grows a C: directory. Take the
        # destination back rather than trusting what arrived.
        if ":" in dest:
            dest = "/" + dest.split(":", 1)[1].split("/", 3)[-1]
        r.file(dest, open(src, "rb").read(), mode)

    if a.overlay:
        r.add_dir(a.overlay)

    # Directories the boot script expects to exist, since it mounts into them.
    for d in ("proc", "sys", "dev", "dev/pts", "run", "tmp", "data", "android", "store", "mnt"):
        r.dir(d)

    release = a.release or time.strftime("techo5-dot %Y-%m-%d %H:%M")
    r.file("etc/techo5-release", (release + "\n").encode())

    r.finish(a.output)
    size = os.path.getsize(a.output)
    print(f"rootfs: {len(r.entries)} entries, {size} bytes gzip -> {a.output}")
    print(f"release: {release}")


if __name__ == "__main__":
    main()
