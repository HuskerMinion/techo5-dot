#!/bin/bash
# build-cannoli-hwfixup.sh — cannoli_hwfixup.ko for the Echo Dot 5th gen's stock 5.4.86 kernel (Fire OS
# NS6575/8349): the ring, amplifier and microphone converter U-Boot leaves disabled on a slot-B boot
# (cannoli-hwfixup/cannoli_hwfixup.c). Run inside WSL/Linux; no root.
#
#   tools/linux/build-cannoli-hwfixup.sh <platform.tar of Amazon's Echo Dot 5th gen GPL drop> [kernel-config]
#
# Headers: Amazon's own 5.4 tree (its struct module is the shipped kernel's: the module header matches the
# stock modules' byte for byte), configured with mt8519_defconfig plus trapz.config as Amazon's
# build_kernel.sh does, or with the unit's own /proc/config.gz when given, and the shipped kernel's
# release (5.4.86-ab0013323246848) so vermagic matches. No kernel is built: modules_prepare only.
# CRCs: cannoli-hwfixup/Module.symvers.8349, the shipped kernel's own, read out of the stock boot.img by
# echo-dot-assist's tools/kcrc.py and checked against the stock modules' __versions; modpost writes
# them into the module, so the kernel takes it. A new firmware needs that file read again.
#
# Compiler: build-wpa-supplicant.sh's musl.cc armv7 cross GCC (a kernel module needs no libc), or
# CROSS_COMPILE. Environment: WORK (default ~/cannoli-kernel), OUTDIR (default build/cannoli-hwfixup).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PLATFORM=${1:?usage: $0 <platform.tar> [kernel-config]}
CONFIG=${2:-}
WORK=${WORK:-$HOME/cannoli-kernel}
OUTDIR=${OUTDIR:-$(cd "$HERE/../.." && pwd)/build/cannoli-hwfixup}
CROSS_COMPILE=${CROSS_COMPILE:-$HOME/wpa-build/toolchain/bin/armv7l-linux-musleabihf-}
RELEASE=-ab0013323246848
export ARCH=arm CROSS_COMPILE

[ -x "${CROSS_COMPILE}gcc" ] || { echo "no ${CROSS_COMPILE}gcc: run build-wpa-supplicant.sh first, or set CROSS_COMPILE" >&2; exit 1; }
SRC=$WORK/kernel/mediatek/5.4
O=$WORK/out
M=$WORK/hwfixup
if [ ! -f "$SRC/Makefile" ]; then
	mkdir -p "$WORK"
	tar xf "$PLATFORM" -C "$WORK" kernel/mediatek/5.4
fi

mkdir -p "$O"
if [ -n "$CONFIG" ]; then
	cp "$CONFIG" "$O/.config"
else
	make -s -C "$SRC" O="$O" mt8519_defconfig
	[ -f "$SRC/arch/arm/configs/trapz.config" ] && cat "$SRC/arch/arm/configs/trapz.config" >> "$O/.config"
fi
sed -i "s/^CONFIG_LOCALVERSION=.*/CONFIG_LOCALVERSION=\"$RELEASE\"/; s/^CONFIG_LOCALVERSION_AUTO=y/# CONFIG_LOCALVERSION_AUTO is not set/" "$O/.config"
make -s -C "$SRC" O="$O" olddefconfig >/dev/null 2>&1
make -s -C "$SRC" O="$O" HOSTCFLAGS="-fcommon -w" LOCALVERSION= -j"$(nproc)" modules_prepare
[ "$(cat "$O/include/config/kernel.release")" = "5.4.86$RELEASE" ] || { echo "kernel release is not 5.4.86$RELEASE" >&2; exit 1; }

cp "$HERE/cannoli-hwfixup/Module.symvers.8349" "$O/Module.symvers"
rm -rf "$M"; mkdir -p "$M"
cp "$HERE/cannoli-hwfixup/cannoli_hwfixup.c" "$HERE/cannoli-hwfixup/Kbuild" "$M/"
make -s -C "$SRC" O="$O" M="$M" modules 2>&1 | grep -v 'from namespace , but does not import it' || true

KO=$M/cannoli_hwfixup.ko
[ -f "$KO" ] || { echo "no module built" >&2; exit 1; }
# Every symbol the module imports must have the shipped kernel's CRC: an unknown one would be 0.
"${CROSS_COMPILE}objcopy" -O binary --only-section=__versions "$KO" "$M/versions.bin"
python3 - "$M/versions.bin" "$HERE/cannoli-hwfixup/Module.symvers.8349" <<'EOF'
import struct, sys
want = {l.split()[1]: int(l.split()[0], 16) for l in open(sys.argv[2]) if l.strip()}
d = open(sys.argv[1], 'rb').read()
bad = []
for i in range(0, len(d), 64):
    crc = struct.unpack_from('<I', d, i)[0]
    name = d[i + 4:i + 64].split(b'\0')[0].decode()
    if want.get(name) != crc:
        bad.append('%s 0x%08x' % (name, crc))
if bad:
    sys.exit('symbols without the shipped kernel\'s CRC: ' + ', '.join(bad))
print('%d imports, every CRC the shipped kernel\'s' % (len(d) // 64))
EOF
mkdir -p "$OUTDIR"
cp "$KO" "$OUTDIR/"
"${CROSS_COMPILE}strings" "$KO" | grep '^vermagic='
sha256sum "$OUTDIR/cannoli_hwfixup.ko"
