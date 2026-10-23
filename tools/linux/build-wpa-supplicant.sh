#!/bin/bash
# build-wpa-supplicant.sh — wpa_supplicant 2.11 for the Echo Dot 3 (donut), built the way Alpine
# v3.24's wpa_supplicant-2.11-r4 is (its .config, its three patches, its compiler defaults and
# libraries) plus wpa-patches/0100-donut-no-wmm-replay-counters.patch. Run inside WSL/Linux; no root.
#
#   tools/linux/build-wpa-supplicant.sh [-o DIR]
#
# Why: Alpine's 2.11 advertises 16 PTKSA replay counters in its RSNE whenever the AP has WMM.
# Amazon's MT7668 driver puts its own RSNE (RSN Capabilities 0) into the association request, so
# the AP sees two different RSNEs and drops the station after EAPOL-Key msg 2/4: it never joins.
# 2.10 and Fire OS's 2.6 send 0 and join (docs/hardware-donut.md, Wi-Fi).
#
# The toolchain and sysroot steps are build-bluealsa.sh's (musl.cc cross GCC,
# Alpine -dev packages verified against APKINDEX and their datahash).
#
# Environment: WORK (default ~/wpa-build), DL (WORK/dl), OUTDIR (default build/wpa_supplicant in this
# repository; or -o), APKINDEX_DIR (WORK/apkindex), JOBS (default 4: keep it light).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$HOME/wpa-build}
DL=${DL:-$WORK/dl}
OUTDIR=${OUTDIR:-$(cd "$HERE/../.." && pwd)/build/wpa_supplicant}
APKINDEX_DIR=${APKINDEX_DIR:-$WORK/apkindex}
JOBS=${JOBS:-4}
while [ $# -gt 0 ]; do
	case "$1" in
	-o) OUTDIR=$2; shift 2;;
	*) echo "unknown argument: $1" >&2; exit 1;;
	esac
done

PKGVER=2.11
SRC_URL=https://w1.fi/releases/wpa_supplicant-$PKGVER.tar.gz
# The sha512 in Alpine's APKBUILD (3.24-stable, main/wpa_supplicant).
SRC_SHA512=9a0a3a9d6fa2235903c40aa57b5955f0c9dd1dccfd0e3825a3b6f92b3e32db8d464b3ea0aef3285ba3ee109e7b190560cedd744902e954f0003cdba543e277b2
TC_URL=https://musl.cc/armv7l-linux-musleabihf-cross.tgz
TC_SHA512=1bb399a61da425faac521df9b8d303e60ad101f6c7827469e0b4bc685ce1f3dedc606ac7b1e8e34d79f762a3bfe3e8ab479a97e97d9f36fbd9fc5dc9d7ed6fd1
ALPINE_MIRROR=https://dl-cdn.alpinelinux.org/alpine/v3.24
# Alpine's makedepends, plus musl-dev and fortify-headers from build-base.
SYSROOT_PKGS="musl-dev fortify-headers linux-headers openssl-dev dbus-dev libnl3-dev pcsc-lite-dev"
CROSS=armv7l-linux-musleabihf

fetch() { # url sha512 [name]
	local f=$DL/${3:-${1##*/}}
	if [ ! -f "$f" ]; then
		curl -fsSL --retry 3 -o "$f.part" "$1"
		mv "$f.part" "$f"
	fi
	echo "$2  $f" | sha512sum -c --quiet - || { echo "checksum mismatch: $f" >&2; exit 1; }
}

mkdir -p "$DL/apk" "$OUTDIR" "$WORK"
for repo in main community; do
	if [ ! -f "$APKINDEX_DIR/$repo/APKINDEX" ]; then
		mkdir -p "$APKINDEX_DIR/$repo"
		curl -fsSL "$ALPINE_MIRROR/$repo/armv7/APKINDEX.tar.gz" | tar -xzf - -C "$APKINDEX_DIR/$repo" APKINDEX
	fi
done
HOST=$WORK/host TC=$WORK/toolchain SYSROOT=$WORK/sysroot BUILD=$WORK/wpa_supplicant-$PKGVER
rm -rf "$HOST" "$TC" "$SYSROOT" "$BUILD"
mkdir -p "$HOST/wrap"
cat > "$HOST/wrap/pkg-config" <<EOF
#!/bin/sh
export PKG_CONFIG_SYSROOT_DIR=$SYSROOT
export PKG_CONFIG_LIBDIR=$SYSROOT/usr/lib/pkgconfig:$SYSROOT/usr/share/pkgconfig
unset PKG_CONFIG_PATH
# The host pkg-config by full path: this wrapper is first in PATH under the same name.
exec /usr/bin/pkg-config "\$@"
EOF
chmod +x "$HOST/wrap/pkg-config"
export PATH="$HOST/wrap:$PATH"

# 2. Cross compiler.
fetch "$TC_URL" "$TC_SHA512"
mkdir -p "$TC"
tar -xzf "$DL/${TC_URL##*/}" -C "$TC" --strip-components=1
export PATH="$TC/bin:$PATH"

# 3. Sysroot from verified Alpine packages.
python3 - "$APKINDEX_DIR" "$ALPINE_MIRROR" "$DL/apk" "$SYSROOT" $SYSROOT_PKGS <<'PY'
import base64, hashlib, io, os, re, socket, sys, tarfile, urllib.request, zlib

# urlretrieve has no timeout of its own, so a mirror that accepts the connection and then says nothing
# would hang the build for as long as the kernel lets it.
socket.setdefaulttimeout(60)

index_dir, mirror, apkdir, sysroot, roots = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
pkgs, provides = {}, {}
for repo in ("main", "community"):
    with open(os.path.join(index_dir, repo, "APKINDEX"), encoding="utf-8") as f:
        for block in f.read().split("\n\n"):
            e = {}
            for line in block.splitlines():
                if len(line) > 2 and line[1] == ":":
                    e.setdefault(line[0], line[2:])
            if "P" not in e or e["P"] in pkgs:
                continue
            e["repo"] = repo
            pkgs[e["P"]] = e
            for p in [e["P"]] + e.get("p", "").split():
                provides.setdefault(re.split(r"[<>=~]", p)[0], []).append(e)

def provider(dep):
    name = re.split(r"[<>=~]", dep)[0]
    if name in pkgs:
        return pkgs[name]
    cands = provides.get(name)
    if not cands:
        sys.exit("unresolved dependency: " + dep)
    return max(cands, key=lambda c: int(c.get("k", "0")))

# Build tools and documentation toolchains glib-dev and friends drag in; not needed to compile
# and link against the libraries.
SKIP = re.compile(r"^(cmd:.*|python3|py3-.*|docbook-.*|libxml2-utils|libxslt|pkgconf|pkgconfig|"
                  r"perl.*|bash|gettext|gettext-asprintf|gettext-envsubst|/bin/sh)$")
want, todo = {}, list(roots)
while todo:
    dep = todo.pop(0)
    if dep.startswith("!") or SKIP.match(re.split(r"[<>=~]", dep)[0]):
        continue
    e = provider(dep)
    if e["P"] in want:
        continue
    want[e["P"]] = e
    todo += e.get("D", "").split()

def members(blob):
    """Split concatenated gzip members; returns [(raw, decompressed)]."""
    out, off = [], 0
    while off < len(blob):
        d = zlib.decompressobj(31)
        data = d.decompress(blob[off:]) + d.flush()
        end = len(blob) - len(d.unused_data)
        out.append((blob[off:end], data))
        off = end
    return out

for name in sorted(want):
    e = want[name]
    fn = "%s-%s.apk" % (e["P"], e["V"])
    path = os.path.join(apkdir, fn)
    if not os.path.exists(path):
        urllib.request.urlretrieve("%s/%s/armv7/%s" % (mirror, e["repo"], fn), path + ".part")
        os.rename(path + ".part", path)
    blob = open(path, "rb").read()
    m = members(blob)
    if len(m) != 3:
        sys.exit("%s: expected signature, control and data gzip members, got %d" % (fn, len(m)))
    got = "Q1" + base64.b64encode(hashlib.sha1(m[1][0]).digest()).decode()
    if got != e["C"]:
        sys.exit("%s: control checksum %s does not match APKINDEX %s" % (fn, got, e["C"]))
    pkginfo = tarfile.open(fileobj=io.BytesIO(m[1][1])).extractfile(".PKGINFO").read().decode()
    datahash = re.search(r"^datahash = ([0-9a-f]+)$", pkginfo, re.M).group(1)
    if hashlib.sha256(m[2][0]).hexdigest() != datahash:
        sys.exit("%s: data sha256 does not match .PKGINFO datahash" % fn)
    print("verified %-40s %s" % (fn, e["C"]))
    t = tarfile.open(fileobj=io.BytesIO(m[2][1]))
    for ti in t.getmembers():
        if ti.name.startswith(".") and "/" not in ti.name:
            continue
        dest = os.path.join(sysroot, ti.name)
        if ti.isdir():
            os.makedirs(dest, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.lexists(dest):
            os.unlink(dest)
        if ti.issym():
            target = ti.linkname
            if target.startswith("/"):  # keep links inside the sysroot
                target = os.path.relpath(os.path.join(sysroot, target.lstrip("/")), os.path.dirname(dest))
            os.symlink(target, dest)
        elif ti.islnk():
            os.link(os.path.join(sysroot, ti.linkname), dest)
        elif ti.isfile():
            with open(dest, "wb") as out:
                out.write(t.extractfile(ti).read())
            os.chmod(dest, ti.mode & 0o7777)
PY

# 4. wpa_supplicant: Alpine's patches, ours, Alpine's .config, Alpine's make line.
fetch "$SRC_URL" "$SRC_SHA512"
tar -xzf "$DL/wpa_supplicant-$PKGVER.tar.gz" -C "$WORK"
n=0
for p in "$HERE"/wpa-patches/[0-9][0-9][0-9][0-9]-*.patch; do
	echo "applying ${p##*/}"
	patch -d "$BUILD" -p1 -F0 -N --no-backup-if-mismatch < "$p"
	n=$((n + 1))
done
cp "$HERE/wpa-patches/alpine-config" "$BUILD/wpa_supplicant/.config"

ARCH_FLAGS="-march=armv7-a -mtune=generic-armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard -mabi=aapcs-linux -mthumb"
ALPINE_GCC_DEFAULTS="-fPIE -fstack-protector-strong --param=ssp-buffer-size=4 -D_FORTIFY_SOURCE=2 -isystem $SYSROOT/usr/include/fortify"
export CC="$CROSS-gcc --sysroot=$SYSROOT"
export AR=$CROSS-gcc-ar RANLIB=$CROSS-gcc-ranlib
export CFLAGS="$ARCH_FLAGS $ALPINE_GCC_DEFAULTS -Os -fstack-clash-protection -Wformat -Werror=format-security -flto=auto"
# wpa_supplicant links with LDFLAGS only, not CFLAGS: the LTO code generation at link time needs the
# architecture and optimization flags too, or it emits ARM code the Thumb-2 objects cannot reach.
export LDFLAGS="$ARCH_FLAGS -Os -flto=auto -pie -Wl,-z,relro,-z,now,--enable-new-dtags -Wl,--as-needed,-O1,--sort-common"
export PKG_CONFIG=$HOST/wrap/pkg-config
cd "$BUILD/wpa_supplicant"
make -j"$JOBS" LIBDIR=/lib BINDIR=/sbin wpa_supplicant wpa_cli

# 5. Results, and the libraries they need against Alpine's own wpa_supplicant.
cp wpa_supplicant "$OUTDIR/wpa_supplicant.unstripped"
$CROSS-strip -o "$OUTDIR/wpa_supplicant" wpa_supplicant
$CROSS-strip -o "$OUTDIR/wpa_cli" wpa_cli
needed() { $CROSS-readelf -d "$1" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p' | sort; }
echo "NEEDED:"; needed "$OUTDIR/wpa_supplicant" | sed 's/^/  /'
{ grep -c "RSN_NUM_REPLAY_COUNTERS_16 << 2" "$BUILD/src/rsn_supp/wpa_ie.c" || true; } | sed 's/^/replay-counter lines left in wpa_ie.c (expect 0): /'
sha256sum "$OUTDIR/wpa_supplicant" "$OUTDIR/wpa_cli"
echo "done: $n patch(es) applied"
