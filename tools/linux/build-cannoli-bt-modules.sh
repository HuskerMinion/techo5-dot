#!/bin/bash
# build-cannoli-bt-modules.sh — the Bluetooth stack as modules for the Echo Dot 5th gen's stock 5.4.86
# kernel (Fire OS NS6575/8349), which is built without it (CONFIG_BT is off). Run inside WSL/Linux; no root.
#
#   FILL=<6.5.5.5 drop>/kernel/mediatek/5.4 tools/linux/build-cannoli-bt-modules.sh \
#       <6.5.7.1 drop>/platform.tar <stock boot.img> <stock module>...
#
# The radio's side stays Amazon's: the stock btmtksdio.ko (MediaTek's vendor driver, WMT inside, no
# dependency on a Bluetooth stack) makes /dev/stpbt, raw H4. btbridge turns that into hci0 through
# hci_vhci, as on the Echo Dot 3rd gen with its stock kernel (build-bt-modules.sh). Built here:
# bluetooth.ko, hci_vhci.ko, rfcomm.ko, and what BT selects that the stock kernel has as no built-in
# (ecdh_generic.ko and ecc.ko for LE Secure Connections).
#
# The tree, its config, the compiler and the CRC check: cannoli-kernel.sh. Environment as there, and
# OUTDIR (default build/cannoli-bt-modules).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PLATFORM=${1:?usage: $0 <platform.tar> <stock boot.img> <stock module>...}
BOOTIMG=${2:?usage: $0 <platform.tar> <stock boot.img> <stock module>...}
shift 2
STOCK_MODULES=("$@")
OUTDIR=${OUTDIR:-$(cd "$HERE/../.." && pwd)/build/cannoli-bt-modules}
. "$HERE/cannoli-kernel.sh"
cannoli_init
cannoli_tree
cannoli_config
cannoli_build
[ -f "$O/net/bluetooth/bluetooth.ko" ] || { echo "no bluetooth.ko: the build failed" >&2; exit 1; }

# What ours depend on, depth first, each module after its dependencies: take is the load order.
WANT="net/bluetooth/bluetooth.ko drivers/bluetooth/hci_vhci.ko net/bluetooth/rfcomm/rfcomm.ko"
declare -A KO
while read -r k; do KO[$(basename "$k" .ko)]=$k; done < <(cd "$O" && find . -name '*.ko' | sed 's|^\./||')
declare -A STATE
take=()
visit() {
	local m=$1 k deps d
	case "${STATE[$m]:-}" in done) return;; busy) echo "a dependency loop at $m" >&2; exit 1;; esac
	STATE[$m]=busy
	k=${KO[$m]:-${KO[${m//_/-}]:-}}
	[ -n "$k" ] || { echo "no module $m in the build" >&2; exit 1; }
	deps=$("${CROSS_COMPILE}objcopy" -O binary --only-section=.modinfo "$O/$k" /dev/stdout | tr '\0' '\n' | sed -n 's/^depends=//p')
	for d in ${deps//,/ }; do visit "$d"; done
	STATE[$m]=done
	take+=("$k")
}
for k in $WANT; do visit "$(basename "$k" .ko)"; done

rm -rf "$OUTDIR"; mkdir -p "$OUTDIR"
for k in "${take[@]}"; do
	cp "$O/$k" "$OUTDIR/"
	"${CROSS_COMPILE}strip" --strip-debug "$OUTDIR/$(basename "$k")"
done
cannoli_check_imports "$OUTDIR"
cannoli_check_vermagic "$OUTDIR"
for k in "${take[@]}"; do basename "$k" .ko; done > "$OUTDIR/load-order"
cat "$OUTDIR/load-order"
ls -l "$OUTDIR"
sha256sum "$OUTDIR"/*.ko
