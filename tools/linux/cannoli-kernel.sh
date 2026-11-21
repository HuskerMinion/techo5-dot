# cannoli-kernel.sh — the Echo Dot 5th gen's kernel tree, built once and checked, for the modules made for
# its stock 5.4.86 kernel (Fire OS NS6575/8349): sourced by build-cannoli-bt-modules.sh and
# build-cannoli-hwfixup.sh. Bash, inside WSL/Linux; no root.
#
# The kernel has MODVERSIONS: every symbol a module imports carries a CRC, and the kernel refuses one that
# is not its own. The CRCs hash the types behind each symbol, so the whole tree is built (vmlinux and
# modules), and every CRC a module of ours imports is compared with the shipped kernel's, read out of the
# stock boot.img by echo-dot-assist's tools/kcrc.py (KCRC; checked against the stock modules given). One
# that differs is a struct that differs between the GPL tree and the shipped kernel: the build stops there
# rather than leave a module that the kernel refuses, or worse, takes.
#
# The tree: Amazon's Echo_Dot_5th_Gen_src-6.5.7.1-20251027 drop, with FILL set to the 5.4 tree of the
# complete 6.5.5.5 drop for what the newer one leaves out (cannoli_tree). With it every CRC matched the
# shipped kernel's (2026-11-21: Bluetooth's 265, the hwfixup's 16); 6.5.5.5's own tree differs in half.
# The config is the shipped kernel's own (IKCONFIG, out of the stock boot.img), or CONFIG, with the
# Bluetooth stack as modules on top: one tree serves both scripts, and modules change nothing built in.
# LOCALVERSION -ab0013323246848 as the shipped release.
#
# Compiler: plain GCC, build-wpa-supplicant.sh's musl.cc armv7 cross GCC 11, or CROSS_COMPILE. The shipped
# kernel was built with Linaro GCC 4.9.4, and genksyms hashes attributes: the attributes GCC 4.9 lacks are
# taken out the way the kernel's own GCC 4 table does (cannoli_gcc49). None of them changes code.
#
# Usage, after setting PLATFORM, BOOTIMG and STOCK_MODULES (an array):
#   cannoli_init; cannoli_tree; cannoli_config; cannoli_build
#   cannoli_check_imports <dir of our .ko>; cannoli_check_vermagic <dir of our .ko>
# Environment: WORK (default ~/cannoli-kernel-6571), FILL, CONFIG, KCRC (default
# ../echo-dot-assist-main/tools/kcrc.py beside techo5-dot), CROSS_COMPILE.

RELEASE=-ab0013323246848

cannoli_init() {
	LIBHERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
	WORK=${WORK:-$HOME/cannoli-kernel-6571}
	KCRC=${KCRC:-$(cd "$LIBHERE/../../.." && pwd)/echo-dot-assist-main/tools/kcrc.py}
	CROSS_COMPILE=${CROSS_COMPILE:-$HOME/wpa-build/toolchain/bin/armv7l-linux-musleabihf-}
	CONFIG=${CONFIG:-}
	export ARCH=arm CROSS_COMPILE
	SRC=$WORK/kernel/mediatek/5.4
	O=$WORK/out
	GCC4=$WORK/gcc49-attributes.h
	[ -x "${CROSS_COMPILE}gcc" ] || { echo "no ${CROSS_COMPILE}gcc: run build-wpa-supplicant.sh first, or set CROSS_COMPILE" >&2; exit 1; }
	[ -f "$KCRC" ] || { echo "no kcrc.py at $KCRC: set KCRC" >&2; exit 1; }
	[ -f "$BOOTIMG" ] || { echo "no stock boot.img at $BOOTIMG" >&2; exit 1; }
	[ ${#STOCK_MODULES[@]} -gt 0 ] || { echo "give at least one stock module for kcrc.py --check" >&2; exit 1; }
	# CC soft-float: the musl.cc GCC defaults to hard float, and with it the arch Makefile's probe for
	# -march=armv7-a fails ("selected architecture lacks an FPU"), the fallback armv5t goes into every .s
	# as its .arch, and the assembler refuses isb/dmb/ldrex. The kernel is soft float anyway. The GCC 4.9
	# attributes go in through CC too, ahead of the kernel's own -include of compiler_types.h.
	KMAKE=(make -s -C "$SRC" O="$O" CC="${CROSS_COMPILE}gcc -mfloat-abi=soft -include $GCC4"
		HOSTCFLAGS="-fcommon -w" LOCALVERSION= KCFLAGS=-w)
}

# The 6.5.7.1 tree, and what it lacks from FILL. The newer drops are trimmed to what Amazon's build
# compiled, so they lack the Bluetooth sources (BT is off) and tools like scripts/config. Those come from
# FILL, never over a file the newer drop has: every type our modules share with the kernel is the newer
# drop's. $WORK/FILLED lists what was taken.
cannoli_tree() {
	if [ ! -f "$SRC/Makefile" ]; then
		mkdir -p "$WORK"
		tar xf "$PLATFORM" -C "$WORK" kernel/mediatek/5.4
	fi
	if [ -n "${FILL:-}" ] && [ ! -f "$WORK/FILLED" ]; then
		[ -f "$FILL/Makefile" ] || { echo "FILL=$FILL is not a kernel tree" >&2; exit 1; }
		(cd "$FILL" && find . -type f) | sort > "$WORK/fill.all"
		(cd "$SRC" && find . -type f) | sort > "$WORK/fill.have"
		comm -23 "$WORK/fill.all" "$WORK/fill.have" > "$WORK/FILLED.tmp"
		(cd "$FILL" && tar cf - -T "$WORK/FILLED.tmp") | (cd "$SRC" && tar xf -)
		mv "$WORK/FILLED.tmp" "$WORK/FILLED"
		echo "filled $(wc -l < "$WORK/FILLED") files from $FILL"
	fi
	[ -x "$SRC/scripts/config" ] || { echo "no scripts/config in $SRC: set FILL to the 6.5.5.5 drop's 5.4 tree" >&2; exit 1; }
}

# The shipped kernel's own config, from its IKCONFIG in the stock boot.img: mt8519_defconfig plus
# trapz.config is not it (no ext4, no binderfs in the shipped one, and more). Then Bluetooth as modules,
# and the GCC 4.9 attribute header.
cannoli_config() {
	mkdir -p "$O"
	if [ -n "$CONFIG" ]; then
		cp "$CONFIG" "$O/.config.new"
	else
		python3 - "$KCRC" "$BOOTIMG" "$O/.config.new" <<'EOF'
import gzip, importlib.util, sys
spec = importlib.util.spec_from_file_location("kcrc", sys.argv[1]); k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)
img = k.image(sys.argv[2])
i = img.find(b"IKCFG_ST")
if i < 0:
    sys.exit("the stock kernel has no IKCONFIG: give its config in CONFIG")
open(sys.argv[3], "wb").write(gzip.decompress(img[i + 8:img.index(b"IKCFG_ED", i)]))
EOF
	fi
	sed -i "s/^CONFIG_LOCALVERSION=.*/CONFIG_LOCALVERSION=\"$RELEASE\"/; s/^CONFIG_LOCALVERSION_AUTO=y/# CONFIG_LOCALVERSION_AUTO is not set/" "$O/.config.new"
	# Modules only: nothing built in changes. No LED triggers (BT_LEDS), HID, BNEP or 6LoWPAN: techo5-bt
	# runs bluetoothd without them. No trusted keys: Amazon's certificates are not in the drop, and
	# nothing here is signed (MODULE_SIG is off).
	"$SRC/scripts/config" --file "$O/.config.new" \
		-m BT -e BT_BREDR -e BT_LE -m BT_RFCOMM -e BT_RFCOMM_TTY -m BT_HCIVHCI \
		-d BT_HS -d BT_LEDS -d BT_HIDP -d BT_BNEP -d BT_6LOWPAN -d BT_SELFTEST -d BT_DEBUGFS -d BT_HCIBTMTKSDIO \
		--set-str SYSTEM_TRUSTED_KEYS "" --set-str MODULE_SIG_KEY ""
	# Only when it changed, so a second script's run keeps the first one's objects.
	if ! cmp -s "$O/.config.new" "$O/.config.src" 2>/dev/null; then
		cp "$O/.config.new" "$O/.config.src"
		cp "$O/.config.new" "$O/.config"
		make -s -C "$SRC" O="$O" olddefconfig >/dev/null 2>&1
	fi
	grep -q '^CONFIG_BT=m' "$O/.config" || { echo "CONFIG_BT=m did not stick" >&2; exit 1; }
	cannoli_gcc49
}

# The attributes as the shipped kernel's GCC (Linaro 4.9.4) saw them. genksyms hashes a struct's
# attributes with it, and a GCC with __has_attribute gives several structs __designated_init__ (and
# others __nonstring__) that 4.9 never had: every CRC reaching one differed, layout identical or not.
# compiler_attributes.h's own table for GCC 4 (4.9 for the minor), forced: none of these changes code but
# __assume_aligned__ (a hint) and __noclone__, which 4.9 has too. A system header, so the #undef's
# warning does not fail the Makefile's -Werror compiler probes.
cannoli_gcc49() {
	cat > "$GCC4.new" <<'EOF'
#pragma GCC system_header
#undef __has_attribute
#define __has_attribute(x) __techo5_gcc49_##x
#define __techo5_gcc49___assume_aligned__ 1
#define __techo5_gcc49___copy__ 0
#define __techo5_gcc49___designated_init__ 0
#define __techo5_gcc49___externally_visible__ 1
#define __techo5_gcc49___noclone__ 1
#define __techo5_gcc49___nonstring__ 0
#define __techo5_gcc49___no_sanitize_address__ 1
#define __techo5_gcc49___fallthrough__ 0
EOF
	cmp -s "$GCC4.new" "$GCC4" 2>/dev/null && rm "$GCC4.new" || mv "$GCC4.new" "$GCC4"
}

# The whole tree: vmlinux's CRCs come from compiling the objects that export them. Incremental: about 8
# minutes the first time, seconds after.
cannoli_build() {
	"${KMAKE[@]}" -j"$(nproc)" vmlinux modules 2>&1 | grep -v 'from namespace , but does not import it' || true
	[ "$(cat "$O/include/config/kernel.release")" = "5.4.86$RELEASE" ] || { echo "kernel release is not 5.4.86$RELEASE" >&2; exit 1; }
	[ -f "$O/vmlinux" ] && [ -f "$O/Module.symvers" ] || { echo "the tree did not build" >&2; exit 1; }
}

# cannoli_ext <dir>: an external module from <dir> (its Kbuild), against the built tree's CRCs.
cannoli_ext() {
	"${KMAKE[@]}" M="$1" modules 2>&1 | grep -v 'from namespace , but does not import it' || true
}

# cannoli_check_imports <dir>: every import of the .ko files in <dir> from outside them, with the CRC this
# build gave it, against the shipped kernel's. Leaves <dir>/imports.txt and <dir>/stock.symvers.
cannoli_check_imports() {
	local dir=$1 checks=() m
	python3 - "$O/Module.symvers" "$dir" > "$dir/imports.txt" <<'EOF'
import os, struct, sys
built = {}
for l in open(sys.argv[1]):
    f = l.rstrip('\n').split('\t')
    built[f[1]] = (int(f[0], 16), f[2])
ours = {os.path.splitext(k)[0].replace('-', '_') for k in os.listdir(sys.argv[2]) if k.endswith('.ko')}
def versions(ko):
    d = open(ko, 'rb').read()
    shoff, = struct.unpack('<I', d[32:36]); shnum, shstrndx = struct.unpack('<HH', d[48:52])
    sh = [struct.unpack('<10I', d[shoff + 40 * i:shoff + 40 * i + 40]) for i in range(shnum)]
    strs = sh[shstrndx][4]
    for s in sh:
        if d[strs + s[0]:d.index(b'\0', strs + s[0])] == b'__versions':
            v = d[s[4]:s[4] + s[5]]
            for i in range(0, len(v), 64):
                yield v[i + 4:i + 64].split(b'\0')[0].decode(), struct.unpack('<I', v[i:i + 4])[0]
seen = set()
for k in sorted(os.listdir(sys.argv[2])):
    if not k.endswith('.ko'): continue
    for name, crc in versions(os.path.join(sys.argv[2], k)):
        owner = built.get(name, (None, 'vmlinux'))[1]
        if os.path.basename(owner).replace('-', '_') in ours or name in seen: continue
        seen.add(name)
        print('%s %08x' % (name, crc))
EOF
	for m in "${STOCK_MODULES[@]}"; do checks+=(--check "$m"); done
	# module_layout is among them: the struct module the kernel expects.
	cut -d' ' -f1 "$dir/imports.txt" | xargs python3 -I "$KCRC" "${checks[@]}" "$BOOTIMG" > "$dir/stock.symvers"
	python3 - "$dir/imports.txt" "$dir/stock.symvers" <<'EOF'
import sys
ours = dict(l.split() for l in open(sys.argv[1]))
stock = {l.split()[1]: int(l.split()[0], 16) for l in open(sys.argv[2]) if l.strip() and not l.startswith('#')}
bad = ['%s: ours %s, stock %08x' % (n, c, stock.get(n, 0)) for n, c in sorted(ours.items()) if stock.get(n) != int(c, 16)]
if bad:
    sys.exit('CRCs that differ from the shipped kernel (a struct that differs between the GPL tree and it):\n  ' + '\n  '.join(bad))
print('%d imports, every CRC the shipped kernel\'s' % len(ours))
EOF
}

cannoli_vermagic() { "${CROSS_COMPILE}objcopy" -O binary --only-section=.modinfo "$1" /dev/stdout | tr '\0' '\n' | sed -n 's/^vermagic=//p'; }

# cannoli_check_vermagic <dir>: every .ko in <dir> has the first stock module's vermagic, byte for byte
# (it ends in a space).
cannoli_check_vermagic() {
	local want k
	want=$(cannoli_vermagic "${STOCK_MODULES[0]}")
	case "$want" in "5.4.86$RELEASE "*) ;; *) echo "${STOCK_MODULES[0]}: vermagic '$want' is not 5.4.86$RELEASE" >&2; exit 1;; esac
	for k in "$1"/*.ko; do
		[ "$(cannoli_vermagic "$k")" = "$want" ] || { echo "$k: vermagic '$(cannoli_vermagic "$k")' is not the stock kernel's '$want'" >&2; exit 1; }
	done
}
