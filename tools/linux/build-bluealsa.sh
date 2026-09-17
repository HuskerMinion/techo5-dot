#!/bin/bash
# build-bluealsa.sh — bluez-alsa 4.3.1 for the Dot's Alpine 3.24 armv7 rootfs, built the way
# Alpine's bluez-alsa-4.3.1-r0 is (same configure options, compiler defaults and libraries) plus
# tools/linux/bluealsa-patches/NNNN-*.patch. Run inside WSL/Linux; no root needed.
#
#   tools/linux/build-bluealsa.sh [-o DIR]
#
# Why: Alpine's /usr/bin/bluealsa crashes at start-up on the Dot. 4.3.1 sizes the fdk-aac
# LIB_INFO array too short, and FDKlibInfo_getCapabilities() reads past it off the top of the
# stack (upstream 6ccf455, see bluealsa-patches/0001-*).
#
# What it does, from scratch every run apart from the download cache:
#   1. host tools the Ubuntu 24.04 host may lack (autoconf, automake, libtool, pkgconf,
#      docutils for rst2man): pinned .debs from archive.ubuntu.com, checked against the SHA512
#      apt publishes, unpacked with dpkg-deb into WORK/host and relocated there;
#   2. cross compiler: musl.cc armv7l-linux-musleabihf-cross (GCC 11.2.1), checked against the
#      SHA512 musl.cc publishes;
#   3. sysroot: Alpine v3.24 armv7 -dev packages from makedepends in Alpine's APKBUILD and their
#      library dependencies, resolved against the APKINDEX files in APKINDEX_DIR, each .apk
#      verified against its index entry (C: = Q1 + base64 sha1 of the control gzip member) and its
#      data against the control's datahash (sha256), then unpacked into WORK/sysroot;
#   4. bluez-alsa: the v4.3.1 GitHub tarball (the sha512 in Alpine's APKBUILD), patches,
#      autoreconf -fvi, Alpine's configure line, make;
#   5. OUTDIR/bluealsa (stripped) and OUTDIR/bluealsa.unstripped, plus a NEEDED comparison
#      against Alpine's own bluealsa from the verified bluez-alsa-4.3.1-r0.apk.
#
# Alpine's compiler defaults are applied by hand, since the cross compiler lacks Alpine's GCC
# patches: armv7-a/vfpv3-d16/hard-float/Thumb-2 (Alpine gcc --with-arch/--with-fpu/--with-mode),
# PIE, -fstack-protector-strong with ssp-buffer-size=4, -D_FORTIFY_SOURCE=2 with fortify-headers,
# -z now and new dtags (Alpine binutils); then abuild's default CFLAGS/LDFLAGS and the APKBUILD's -flto=auto.
#
# Environment:
#   WORK          scratch on a Linux filesystem (default ~/bluealsa-build)
#   DL            downloads (default WORK/dl)
#   OUTDIR        results (default build/bluealsa in this repository; or -o)
#   APKINDEX_DIR  Alpine v3.24 armv7 indexes, main/APKINDEX and community/APKINDEX (default
#                 WORK/apkindex, fetched from ALPINE_MIRROR over HTTPS when they are not there)
#   JOBS          make -j (default nproc)
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$HOME/bluealsa-build}
DL=${DL:-$WORK/dl}
OUTDIR=${OUTDIR:-$(cd "$HERE/../.." && pwd)/build/bluealsa}
APKINDEX_DIR=${APKINDEX_DIR:-$WORK/apkindex}
JOBS=${JOBS:-$(nproc)}
while [ $# -gt 0 ]; do
	case "$1" in
	-o) OUTDIR=$2; shift 2;;
	*) echo "unknown argument: $1" >&2; exit 1;;
	esac
done

PKGVER=4.3.1
SRC_URL=https://github.com/Arkq/bluez-alsa/archive/v$PKGVER.tar.gz
SRC_SHA512=db9ac0ce58c03062d65eab2a0ccaed3ddd23de6eda3572ac0d038557c6bb7f243a3551eebae70593c519bc4db070923b9997a6cc4e6546000a2279c56fe1f2e5
TC_URL=https://musl.cc/armv7l-linux-musleabihf-cross.tgz
TC_SHA512=1bb399a61da425faac521df9b8d303e60ad101f6c7827469e0b4bc685ce1f3dedc606ac7b1e8e34d79f762a3bfe3e8ab479a97e97d9f36fbd9fc5dc9d7ed6fd1
UBU=http://archive.ubuntu.com/ubuntu/pool/main
HOST_DEBS="
$UBU/a/autoconf/autoconf_2.71-3_all.deb 646396c70a4546de5a331b247c74ba4dda573c419298127cf6b06bc349832aaea923a26159466c947a0b15125313284f3d4f9ba99ab83e7e352aacc64060aa2b
$UBU/a/automake-1.16/automake_1.16.5-1.3ubuntu1_all.deb dd62fa468463de8d184a507d9ae2e83c55df2f4737e0de199cb309b44228b8ffd7c27280570731cd947551209f0e87c8113e8b457571264a506bb9b26f32baf0
$UBU/a/autotools-dev/autotools-dev_20220109.1_all.deb b3add366e8549028f8de8c1c21796ff1ed8831e3432dedac73e795e021bcd23fb0c4e5fcbaf6f512b21b2f5d1c747bc7bccad1719a3255988ff2d7f5ce81f3d4
$UBU/libt/libtool/libtool_2.4.7-7build1_all.deb 52b4057450b84c3da639026f947e44d57e561e5f631f4f92b3f2e4ac81b3963debbec2d6bcc472c61ca5b68cfc7799e08542bd515a8a94b465e93745d2c0dc86
$UBU/p/pkgconf/pkgconf-bin_1.8.1-2build1_amd64.deb be9cc5b9f3ab4007527640a2cb9059bdbc1d6f143b0af9f0fdcb1fddf9d33ed87082480e61fbd82e738584fc4ef1e59538dada7478ca4677cbdb6d24d1bea37f
$UBU/p/pkgconf/pkgconf_1.8.1-2build1_amd64.deb a07172ad0e7f0aa06fe6ef693fc96dc7f1bce74236324fbb2ab916938ae3a9060ed74d95e907469aef0049340af62cfe44c3c888018999903db11eb87c5cd173
$UBU/p/pkgconf/libpkgconf3_1.8.1-2build1_amd64.deb b4d3a5d5cd68bf166a52ceec03713fe538a2cbe44f16c8b0217c1d8f6108a5d25055832e1b604aa65f11faab57245a9e26a15c458bb011bec8bf6469bead9816
$UBU/p/python-docutils/python3-docutils_0.20.1%2bdfsg-3_all.deb a8dcffab8dfeb0dc66eea40c1b0ae4774c367438002e4e47522403c3ae6d759a3bcd9d04c169858e695cbaf0a22f3152775040743f58c1ab4ed28497fa92d5ad
$UBU/p/python-roman/python3-roman_3.3-3_all.deb f8abbb536339b1fa6a5483b1c14d1b82461e712c1cfbb071101680d3715fe4c77d9b15a49a701d9ae3ddbaff683c09a5f8728da7e4b4454df8594ded8493b5f0
"
ALPINE_MIRROR=https://dl-cdn.alpinelinux.org/alpine/v3.24
# makedepends of community/bluez-alsa 4.3.1-r0 (3.24-stable) that go into the sysroot (autoconf,
# automake, libtool and py3-docutils are host tools, above), plus musl-dev and fortify-headers
# from build-base.
SYSROOT_PKGS="musl-dev fortify-headers alsa-lib-dev bluez-dev dbus-dev fdk-aac-dev glib-dev
	libbsd-dev liblc3-dev libfreeaptx-dev ncurses-dev readline-dev sbc-dev spandsp-dev tiff-dev
	lame-libs mpg123-dev opus-dev libldac-dev"
CHOST=armv7-alpine-linux-musleabihf
CROSS=armv7l-linux-musleabihf

fetch() { # url sha512 [name]
	local f=$DL/${3:-${1##*/}}
	if [ ! -f "$f" ]; then
		curl -fsSL --retry 3 -o "$f.part" "$1"
		mv "$f.part" "$f"
	fi
	echo "$2  $f" | sha512sum -c --quiet - || { echo "checksum mismatch: $f" >&2; exit 1; }
}

mkdir -p "$DL/deb" "$DL/apk" "$OUTDIR" "$WORK"
for repo in main community; do
	if [ ! -f "$APKINDEX_DIR/$repo/APKINDEX" ]; then
		mkdir -p "$APKINDEX_DIR/$repo"
		curl -fsSL "$ALPINE_MIRROR/$repo/armv7/APKINDEX.tar.gz" | tar -xzf - -C "$APKINDEX_DIR/$repo" APKINDEX
	fi
done
HOST=$WORK/host TC=$WORK/toolchain SYSROOT=$WORK/sysroot BUILD=$WORK/bluez-alsa-$PKGVER
rm -rf "$HOST" "$TC" "$SYSROOT" "$BUILD"

# 1. Host tools, relocated from /usr to $HOST/usr.
mkdir -p "$HOST"
echo "$HOST_DEBS" | while read -r url sha; do
	[ -n "$url" ] || continue
	name=${url##*/}; name=${name//%2b/+}
	fetch "$url" "$sha" "deb/$name"
	dpkg-deb -x "$DL/deb/$name" "$HOST"
done
grep -rlIE '/usr/share/(autoconf|automake|aclocal|libtool)|/usr/bin/(auto|aclocal|libtoolize|ifnames)' \
	"$HOST/usr/bin" "$HOST/usr/share/autoconf" "$HOST/usr/share/automake-1.16" | while read -r f; do
	sed -i -e "s#/usr/share/\(autoconf\|automake-1\.16\|aclocal-1\.16\|aclocal\|libtool\)#$HOST/usr/share/\1#g" \
		-e "s#/usr/bin/\(auto[a-z0-9.-]*\|aclocal[a-z0-9.-]*\|libtoolize\|ifnames\)#$HOST/usr/bin/\1#g" "$f"
done
mkdir -p "$HOST/wrap"
# Debian installs these names through update-alternatives, which dpkg-deb -x does not run.
ln -s "$HOST/usr/bin/aclocal-1.16" "$HOST/wrap/aclocal"
ln -s "$HOST/usr/bin/automake-1.16" "$HOST/wrap/automake"
cat > "$HOST/wrap/pkg-config" <<EOF
#!/bin/sh
export LD_LIBRARY_PATH=$HOST/usr/lib/x86_64-linux-gnu
export PKG_CONFIG_SYSROOT_DIR=$SYSROOT
export PKG_CONFIG_LIBDIR=$SYSROOT/usr/lib/pkgconfig:$SYSROOT/usr/share/pkgconfig
unset PKG_CONFIG_PATH
exec $HOST/usr/bin/pkgconf "\$@"
EOF
cat > "$HOST/wrap/rst2man" <<EOF
#!/bin/sh
export PYTHONPATH=$HOST/usr/lib/python3/dist-packages
exec python3 -m docutils --writer=manpage "\$@"
EOF
chmod +x "$HOST/wrap/pkg-config" "$HOST/wrap/rst2man"
export PATH="$HOST/wrap:$HOST/usr/bin:$PATH"

# 2. Cross compiler.
fetch "$TC_URL" "$TC_SHA512"
mkdir -p "$TC"
tar -xzf "$DL/${TC_URL##*/}" -C "$TC" --strip-components=1
export PATH="$TC/bin:$PATH"

# 3. Sysroot from verified Alpine packages.
python3 - "$APKINDEX_DIR" "$ALPINE_MIRROR" "$DL/apk" "$SYSROOT" $SYSROOT_PKGS <<'PY'
import base64, hashlib, io, os, re, sys, tarfile, urllib.request, zlib

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
                  r"perl.*|bash|gettext|gettext-asprintf|gettext-envsubst|bluez|/bin/sh)$")
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

# 4. bluez-alsa.
fetch "$SRC_URL" "$SRC_SHA512" "bluez-alsa-$PKGVER.tar.gz"
tar -xzf "$DL/bluez-alsa-$PKGVER.tar.gz" -C "$WORK"
n=0
for p in "$HERE"/bluealsa-patches/[0-9][0-9][0-9][0-9]-*.patch; do
	[ -f "$p" ] || { echo "no patches found in $HERE/bluealsa-patches" >&2; exit 1; }
	echo "applying ${p##*/}"
	patch -d "$BUILD" -p1 -F0 -N --no-backup-if-mismatch < "$p"
	n=$((n + 1))
done
cd "$BUILD"
autoreconf -fvi

ARCH_FLAGS="-march=armv7-a -mtune=generic-armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard -mabi=aapcs-linux -mthumb"
ALPINE_GCC_DEFAULTS="-fPIE -fstack-protector-strong --param=ssp-buffer-size=4 -D_FORTIFY_SOURCE=2 -isystem $SYSROOT/usr/include/fortify"
export CC="$CROSS-gcc --sysroot=$SYSROOT"
export AR=$CROSS-gcc-ar RANLIB=$CROSS-gcc-ranlib NM=$CROSS-gcc-nm STRIP=$CROSS-strip
export CFLAGS="$ARCH_FLAGS $ALPINE_GCC_DEFAULTS -Os -fstack-clash-protection -Wformat -Werror=format-security -flto=auto"
export LDFLAGS="-pie -Wl,-z,relro,-z,now,--enable-new-dtags -Wl,--as-needed,-O1,--sort-common"
export PKG_CONFIG=$HOST/wrap/pkg-config
# src/dbus-codegen.py runs gdbus-codegen from PATH: glib-dev's own (pure Python, relocatable).
cat > "$HOST/wrap/gdbus-codegen" <<EOF
#!/bin/sh
exec python3 $SYSROOT/usr/bin/gdbus-codegen "\$@"
EOF
chmod +x "$HOST/wrap/gdbus-codegen"
export GDBUS_CODEGEN=$HOST/wrap/gdbus-codegen
./configure \
	--host=$CHOST \
	--build=x86_64-pc-linux-gnu \
	--prefix=/usr \
	--sysconfdir=/etc \
	--enable-a2dpconf \
	--enable-aac \
	--enable-aptx \
	--enable-aptx-hd \
	--enable-cli \
	--enable-hcitop \
	--enable-lc3-swb \
	--enable-ldac \
	--enable-manpages \
	--enable-mpg123 \
	--enable-msbc \
	--enable-ofono \
	--enable-opus \
	--enable-rfcomm \
	--enable-upower \
	--disable-static \
	--with-dbusconfdir=/usr/share/dbus-1/system.d \
	--with-libfreeaptx
make -j"$JOBS"

# 5. Results.
cp src/bluealsa "$OUTDIR/bluealsa.unstripped"
$CROSS-strip -o "$OUTDIR/bluealsa" src/bluealsa
REF=$WORK/alpine-ref
rm -rf "$REF" && mkdir -p "$REF"
python3 - "$APKINDEX_DIR" "$ALPINE_MIRROR" "$DL/apk" "$REF" <<'PY'
import base64, hashlib, io, os, sys, tarfile, urllib.request, zlib
index_dir, mirror, apkdir, ref = sys.argv[1:]
entry = None
with open(os.path.join(index_dir, "community", "APKINDEX"), encoding="utf-8") as f:
    for block in f.read().split("\n\n"):
        e = dict((l[0], l[2:]) for l in block.splitlines() if len(l) > 2 and l[1] == ":")
        if e.get("P") == "bluez-alsa":
            entry = e
fn = "bluez-alsa-%s.apk" % entry["V"]
path = os.path.join(apkdir, fn)
if not os.path.exists(path):
    urllib.request.urlretrieve("%s/community/armv7/%s" % (mirror, fn), path)
blob, parts, off = open(path, "rb").read(), [], 0
while off < len(blob):
    d = zlib.decompressobj(31); data = d.decompress(blob[off:]) + d.flush()
    end = len(blob) - len(d.unused_data); parts.append((blob[off:end], data)); off = end
assert "Q1" + base64.b64encode(hashlib.sha1(parts[1][0]).digest()).decode() == entry["C"], fn
open(os.path.join(ref, "bluealsa"), "wb").write(
    tarfile.open(fileobj=io.BytesIO(parts[2][1])).extractfile("usr/bin/bluealsa").read())
print("verified reference", fn)
PY
needed() { $CROSS-readelf -d "$1" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'; }
file "$OUTDIR/bluealsa"
if diff <(needed "$REF/bluealsa") <(needed "$OUTDIR/bluealsa"); then
	echo "NEEDED identical to Alpine's bluealsa:"; needed "$OUTDIR/bluealsa" | sed 's/^/  /'
else
	echo "NEEDED differs from Alpine's bluealsa (above: < Alpine, > this build)" >&2; exit 1
fi
sha256sum "$OUTDIR/bluealsa" "$OUTDIR/bluealsa.unstripped"
echo "done: $n patch(es) applied"
