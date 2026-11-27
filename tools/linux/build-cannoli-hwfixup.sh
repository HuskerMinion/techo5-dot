#!/bin/bash
# build-cannoli-hwfixup.sh — cannoli_hwfixup.ko for the Echo Dot 5th gen's stock 5.4.86 kernel (Fire OS
# NS6575/8349): the ring, amplifier and microphone converter U-Boot leaves disabled on a slot-B boot
# (cannoli-hwfixup/cannoli_hwfixup.c). Run inside WSL/Linux; no root.
#
#   FILL=<6.5.5.5 drop>/kernel/mediatek/5.4 tools/linux/build-cannoli-hwfixup.sh \
#       <6.5.7.1 drop>/platform.tar <stock boot.img> <stock module>...
#
# Built against the checked tree build-cannoli-bt-modules.sh uses (cannoli-kernel.sh: Amazon's 6.5.7.1
# drop with the shipped kernel's own config, plain GCC with GCC 4.9's attributes): its CRCs are the ones
# that tree's types give, and each is compared with the shipped kernel's, so the module's structs are
# proven the kernel's own. Either script builds the tree; the other then takes seconds. Environment as
# cannoli-kernel.sh, and OUTDIR (default build/cannoli-hwfixup).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PLATFORM=${1:?usage: $0 <platform.tar> <stock boot.img> <stock module>...}
BOOTIMG=${2:?usage: $0 <platform.tar> <stock boot.img> <stock module>...}
shift 2
STOCK_MODULES=("$@")
OUTDIR=${OUTDIR:-$(cd "$HERE/../.." && pwd)/build/cannoli-hwfixup}
. "$HERE/cannoli-kernel.sh"
cannoli_init
cannoli_tree
cannoli_config
cannoli_build

M=$WORK/hwfixup
rm -rf "$M"; mkdir -p "$M"
cp "$HERE/cannoli-hwfixup/cannoli_hwfixup.c" "$HERE/cannoli-hwfixup/ring_id.h" "$HERE/cannoli-hwfixup/Kbuild" "$M/"
cannoli_ext "$M"
[ -f "$M/cannoli_hwfixup.ko" ] || { echo "no module built" >&2; exit 1; }

rm -rf "$OUTDIR"; mkdir -p "$OUTDIR"
cp "$M/cannoli_hwfixup.ko" "$OUTDIR/"
"${CROSS_COMPILE}strip" --strip-debug "$OUTDIR/cannoli_hwfixup.ko"
cannoli_check_imports "$OUTDIR"
cannoli_check_vermagic "$OUTDIR"
sha256sum "$OUTDIR/cannoli_hwfixup.ko"
