#!/bin/bash
# build-bt-modules.sh — the Echo Dot 3's Bluetooth stack as kernel modules, for its stock kernel.
#
#   tools/linux/build-bt-modules.sh [--source-tarball OUT.tar.xz]
#
# The Dot 3 keeps Amazon's 4.4.22 kernel (its device drivers are Amazon's modules), which has no
# Bluetooth stack: no CONFIG_BT, no /dev/vhci. Its kernel checks a module's vermagic only (no
# MODVERSIONS, no signing), so the stack is built here, apart from the kernel, and loaded at boot:
# cmac.ko, bluetooth.ko and hci_vhci.ko, left in /tmp/techo5-donut-kernel/modules.
#
# Amazon's source for the Dot 3 is not published; the Echo Input's (cupcake) is the same 4.4.22 MT8516
# tree. It lacks the Bluetooth sources Amazon left out, which come from mainline 4.4.22 unchanged
# (bt-modules/added-from-4.4.22.txt; no Amazon file is replaced).
#
# Runs in WSL or Linux. --source-tarball writes the complete corresponding source of the modules (the
# tree as built, its config, the list of what came from mainline, this script) for the release.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SRCTAR=
while [ $# -gt 0 ]; do
	case "$1" in
	--source-tarball) SRCTAR=$(realpath -m "$2"); shift 2;;
	*) echo "unknown argument: $1" >&2; exit 1;;
	esac
done

# Always this directory: the modules' warnings name their sources by the path they were built at, so
# a fixed one keeps the build machine out of them and makes every build of them the same.
W=/tmp/techo5-donut-kernel

CUPCAKE=https://github.com/bengris32/android_kernel_amazon_cupcake.git
CUPCAKE_COMMIT=d16134f046e32aee147e8faf59eeacf2a3903aed # "Import from Echo_Input_src-6.5.4.7-20210719"
MAINLINE=https://cdn.kernel.org/pub/linux/kernel/v4.x/linux-4.4.22.tar.xz
MAINLINE_SHA256=629d5661dc3b554fb0214b4635d80bb20f7977c6e814810945de31cfa86d1a0f
TOOLCHAIN=https://android.googlesource.com/platform/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9/+archive/refs/heads/android10-release.tar.gz

mkdir -p "$W"
cd "$W"

# GCC 4.9, the compiler the stock kernel was built with. The prebuilt's gcc is a python 2 wrapper;
# the real compiler sits beside it.
if [ ! -x tc/bin/aarch64-linux-android-gcc-4.9.x ]; then
	mkdir -p tc
	curl -fsSL "$TOOLCHAIN" | tar -xz -C tc
fi
for t in gcc gcc-4.9; do
	[ -L tc/bin/aarch64-linux-android-$t ] || ln -sf aarch64-linux-android-gcc-4.9.x tc/bin/aarch64-linux-android-$t
done

if [ ! -d src/.git ]; then
	git clone --filter=blob:none --no-checkout "$CUPCAKE" src
fi
git -C src checkout -q "$CUPCAKE_COMMIT"
# A clean tree each time (Amazon's is trimmed too far for make mrproper).
git -C src clean -fdxq

[ -f linux-4.4.22.tar.xz ] || curl -fsSLo linux-4.4.22.tar.xz "$MAINLINE"
echo "$MAINLINE_SHA256  linux-4.4.22.tar.xz" | sha256sum -c -
[ -d linux-4.4.22 ] || tar -xJf linux-4.4.22.tar.xz
# The missing sources, never over one of Amazon's.
while read -r f; do
	[ -n "$f" ] || continue
	[ -e "src/$f" ] && [ "$(git -C src ls-files -- "$f")" ] && { echo "refusing to replace Amazon's $f" >&2; exit 1; }
	mkdir -p "src/$(dirname "$f")"
	cp "linux-4.4.22/$f" "src/$f"
done < "$HERE/bt-modules/added-from-4.4.22.txt"
# The stock kernel's version is 4.4.22+, the plus for a git tree off a release tag; a module's must
# match it. .scmversion gives the plus to a tree from the source tarball too, which has no git.
echo "+" > src/.scmversion

export ARCH=arm64 CROSS_COMPILE=$W/tc/bin/aarch64-linux-android-
rm -rf out modules
mkdir -p out modules
# Amazon's MediaTek drivers build only with O=.
make -C src O=../out cupcake_defconfig
# Amazon's tree lacks scripts/config; mainline 4.4.22's is the same tool.
bash linux-4.4.22/scripts/config --file out/.config \
	-m BT -e BT_BREDR -e BT_LE -m BT_HCIVHCI -m BT_RFCOMM -m BT_BNEP -m BT_HIDP -m CRYPTO_CMAC \
	--set-str SYSTEM_TRUSTED_KEYS ""
make -C src O=../out olddefconfig

if [ -n "$SRCTAR" ]; then
	cp out/.config src/techo5-bt-modules.config
	cp "$0" src/techo5-build-bt-modules.sh
	cp "$HERE/bt-modules/added-from-4.4.22.txt" src/techo5-added-from-4.4.22.txt
	# The GPL travels with the source it covers: the kernel's own copy, from the 4.4.22 tree.
	cp linux-4.4.22/COPYING src/COPYING
	tar -C "$W" --exclude=.git --owner=0 --group=0 --numeric-owner -cJf "$SRCTAR" src
	rm src/techo5-bt-modules.config src/techo5-build-bt-modules.sh src/techo5-added-from-4.4.22.txt src/COPYING
	ls -l "$SRCTAR"
fi

# -fcommon: the 4.4 dtc's yylloc does not link with a newer host gcc.
make -C src O=../out -j"$(nproc)" HOSTCFLAGS=-fcommon Image modules

for m in crypto/cmac.ko net/bluetooth/bluetooth.ko drivers/bluetooth/hci_vhci.ko; do
	cp "out/$m" modules/
	tc/bin/aarch64-linux-android-strip --strip-debug "modules/${m##*/}"
done
for m in modules/*.ko; do
	v=$(tc/bin/aarch64-linux-android-objcopy -O binary --only-section=.modinfo "$m" /dev/stdout | tr '\0' '\n' | sed -n 's/^vermagic=//p')
	[ "$v" = "4.4.22+ SMP preempt mod_unload aarch64" ] || { echo "$m: vermagic '$v' is not the stock kernel's" >&2; exit 1; }
done
ls -l modules
md5sum modules/*.ko
