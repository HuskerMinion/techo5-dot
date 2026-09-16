#!/bin/bash
# Build tools/aec/techo5-aec.cpp for x86_64 in an Alpine root under WSL, for offline replay on the host.
set -euo pipefail
SDK=$HOME/alpine-x86_64-sdk
APK=$HOME/apk/apk.static
SRC=${TECHO5_AEC_SRC:-/mnt/e/projects/techo5/tools/aec/techo5-aec.cpp}
OUT=${1:-$HOME/techo5-aec}
TARBALL=$HOME/alpine-minirootfs-3.24.1-x86_64.tar.gz

if [ -z "${TECHO5_IN_NS:-}" ]; then
	[ -f "$TARBALL" ] || wget -q -O "$TARBALL" https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-minirootfs-3.24.1-x86_64.tar.gz
	exec unshare -Ur --map-auto env TECHO5_IN_NS=1 bash "$0" "$OUT"
fi

if [ ! -x "$SDK/usr/bin/g++" ]; then
	echo "== making the x86_64 SDK root at $SDK"
	rm -rf "$SDK"; mkdir -p "$SDK"
	tar -xzf "$TARBALL" -C "$SDK"
	cp /etc/resolv.conf "$SDK/etc/resolv.conf"
	"$APK" --root "$SDK" --arch x86_64 --no-cache add build-base webrtc-audio-processing-1-dev pkgconf
fi

mkdir -p "$SDK/build"
cp "$SRC" "$SDK/build/"
chroot "$SDK" /bin/sh -c 'cd /build && g++ -O2 -std=c++17 -static -o techo5-aec techo5-aec.cpp $(pkg-config --cflags --libs webrtc-audio-processing-1) && strip techo5-aec && ls -la techo5-aec' || \
chroot "$SDK" /bin/sh -c 'cd /build && g++ -O2 -std=c++17 -o techo5-aec techo5-aec.cpp $(pkg-config --cflags --libs webrtc-audio-processing-1) && strip techo5-aec && ls -la techo5-aec'
cp "$SDK/build/techo5-aec" "$OUT"
echo "built: $OUT"
