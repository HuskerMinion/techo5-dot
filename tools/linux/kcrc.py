#!/usr/bin/env python3
# From echo-dot-assist, https://github.com/Gamer92000/echo-dot-assist, tools/kcrc.py at commit ae3a42d,
# unchanged below this header. Copyright (c) 2026 Julian Imhof, MIT License: the license text is in NOTICE.
"""The CRCs a 32-bit ARM kernel built with CONFIG_MODVERSIONS gives its exports, as Module.symvers lines, read out of
the firmware's boot image: what Wi-Fi motion's module (src/kmod/) needs to load there (devices/<codename>/Module.symvers;
modpost writes them into the module's __versions, and the kernel refuses a module whose CRC differs from its own).

    tools/kcrc.py [--check <stock .ko>...] <boot.img | zImage | Image> <symbol>...

The kernel: a FIT (cannoli's boot.img: its first "kernel" image), an Android boot image (athena's) or a bare kernel,
each with or without the zImage around it (the first gzip stream in it is the Image).  The kernel is not linked at
file offset 0, and with KALLSYMS_ALL off the tables have no symbols, so they are found by their shape: __ksymtab and
__ksymtab_gpl are runs of {value, name[, namespace]} (8 bytes before 5.4, 12 with symbol namespaces), name pointing
at a C identifier in __ksymtab_strings; the link address is the one that makes most entries point at names; each
run's __kcrctab is the array of words in which the CRCs stock modules carry for that run's symbols (their __versions,
--check, at least one module, at least 3 of a run's symbols) all sit at their symbol's index.  Every symbol asked
for must be found, else nothing is printed.
"""
import gzip, re, struct, sys, zlib

def fit_kernel(d):
    """the data of a FIT's first image of type kernel, else None"""
    if d[:4] != b'\xd0\x0d\xfe\xed': return None
    _, _, st, sp = struct.unpack('>4I', d[:16])
    p, depth, props, found = st, 0, {}, None
    def name(o): return d[sp + o:d.index(b'\0', sp + o)].decode()
    while True:
        tok, = struct.unpack('>I', d[p:p + 4]); p += 4
        if tok == 1: p = (d.index(b'\0', p) + 4) & ~3; depth += 1; props = {}
        elif tok == 2:
            if props.get('type') == b'kernel\0' and 'data' in props: return props['data']
            depth -= 1; props = {}
        elif tok == 3:
            ln, no = struct.unpack('>II', d[p:p + 8]); p += 8; props[name(no)] = d[p:p + ln]; p = (p + ln + 3) & ~3
        elif tok == 9: return None

def android_kernel(d):
    if d[:8] != b'ANDROID!': return None
    ksize, = struct.unpack('<I', d[8:12]); page, = struct.unpack('<I', d[36:40])
    return d[page:page + ksize]

def image(path):
    d = open(path, 'rb').read()
    k = fit_kernel(d) or android_kernel(d) or d
    # a zImage: the first gzip stream that inflates to a kernel; else a bare Image (its bytes may hold 1f 8b 08 by chance)
    for m in re.finditer(b'\x1f\x8b\x08', k):
        try: img = zlib.decompressobj(31).decompress(k[m.start():])
        except zlib.error: continue
        if b'Linux version' in img: return img
    if b'Linux version' in k: return k
    sys.exit(f'{path}: no kernel found')

def versions(ko):
    """{symbol: crc} of a module's __versions (struct modversion_info: unsigned long crc; char name[60])"""
    d = open(ko, 'rb').read()
    shoff, = struct.unpack('<I', d[32:36]); shnum, shstrndx = struct.unpack('<HH', d[48:52])
    sh = [struct.unpack('<10I', d[shoff + 40 * i:shoff + 40 * i + 40]) for i in range(shnum)]
    strs = sh[shstrndx][4]
    out = {}
    for s in sh:
        if d[strs + s[0]:d.index(b'\0', strs + s[0])] == b'__versions':
            v = d[s[4]:s[4] + s[5]]
            for i in range(0, len(v), 64):
                out[v[i + 4:i + 64].split(b'\0')[0].decode()] = struct.unpack('<I', v[i:i + 4])[0]
    return out

IDENT = re.compile(rb'[A-Za-z_][A-Za-z0-9_.]*\0')

def runs(img, base, stride):
    """runs of ksymtab entries, [(offset of the first, [names])] in the Image's order"""
    def name_at(va):
        o = va - base
        if not 0 <= o < len(img) or (o and img[o - 1] != 0): return None
        m = IDENT.match(img, o)
        return m.group(0)[:-1].decode() if m else None
    out, cur, start = [], [], None
    o = 0
    while o + stride <= len(img):
        w = struct.unpack_from('<%dI' % (stride // 4), img, o)
        n = name_at(w[1]) if w[1] >= base else None     # any value: per-CPU and absolute exports are no addresses
        if n and (stride == 8 or w[2] == 0 or name_at(w[2]) is not None):     # namespace: NULL or a name
            if not cur: start = o
            cur.append(n); o += stride; continue
        if len(cur) > 16: out.append((start, cur))
        cur = []; o += 4
    if len(cur) > 16: out.append((start, cur))
    # one entry whose name is not in __ksymtab_strings (cannoli: loops_per_jiffy) splits a table: bridged, its name
    # unknown.  __ksymtab_gpl follows __ksymtab with nothing between: each is sorted by name (SORT(___ksymtab+*)), so
    # one ends where the order starts again.
    merged = []
    for st, names in out:
        if merged and merged[-1][0] + stride * (len(merged[-1][1]) + 1) == st:
            merged[-1][1].extend([None] + names)
        else:
            merged.append((st, list(names)))
    tables = []
    for st, names in merged:
        first, prev = 0, None
        for i, n in enumerate(names):
            if n is None: continue
            if prev is not None and n.encode() < prev.encode():
                tables.append((st + stride * first, names[first:i])); first = i
            prev = n
        tables.append((st + stride * first, names[first:]))
    return tables

def main():
    a = sys.argv[1:]; known = {}
    while a and a[0] == '--check':
        known.update(versions(a[1])); a = a[2:]
    if len(a) < 2 or not known: sys.exit(__doc__)
    img, want = image(a[0]), a[1:]
    # the link address: PAGE_OFFSET + TEXT_OFFSET as the 32-bit ARM kernels are linked (3G/1G split; 2G/2G as fallback)
    best = None
    for base in (0xc0008000, 0x80008000, 0xc0208000):
        for stride in (12, 8):
            r = runs(img, base, stride)
            n = sum(len(x[1]) for x in r)
            if not best or n > best[0]: best = (n, base, stride, r)
    _, base, stride, rs = best
    # each run's CRCs where every symbol of it that --check knows sits at its index: a run of something else that only
    # looks like ksymtab entries finds none.  Each run's CRCs are found on their own, so a run split by entries it could
    # not read keeps its indices.
    found = []
    for start, names in rs:
        idx = [(i, known[n]) for i, n in enumerate(names) if n in known]
        if len(idx) < 3: continue
        i0, c0 = idx[0]
        for m in re.finditer(re.escape(struct.pack('<I', c0)), img):
            t = m.start() - 4 * i0
            if t < 0 or t + 4 * len(names) > len(img): continue
            if all(struct.unpack_from('<I', img, t + 4 * i)[0] == c for i, c in idx):
                found.append((start, names, t, len(idx))); break
    # __ksymtab, then __ksymtab_gpl, then gpl_future (vmlinux.lds.h), each sorted by name: the kind changes where the
    # order starts again, in the Image's order
    crcs, kind, part, prev = {}, {}, 0, None
    for start, names, t, checked in sorted(found):
        real = [n for n in names if n]
        if prev is not None and real[0].encode() < prev.encode(): part += 1
        prev = real[-1]
        if part > 1: break
        exp = ('EXPORT_SYMBOL', 'EXPORT_SYMBOL_GPL')[part]
        for n, c in zip(names, struct.unpack_from('<%dI' % len(names), img, t)):
            if n: crcs[n] = c; kind[n] = exp
        print(f'# {exp}: {len(names)} symbols at {base + start:#x}, CRCs at {base + t:#x}, '
              f'{checked} of them checked against stock modules', file=sys.stderr)
    miss = [s for s in want if s not in crcs]
    if miss: sys.exit('not found: ' + ' '.join(miss))
    for s in want:
        # 5.4's modpost reads a fifth column, the symbol's namespace (empty for these), and refuses a line without it
        print(f'{crcs[s]:#010x}\t{s}\tvmlinux\t{kind[s]}' + ('\t' if stride == 12 else ''))

if __name__ == '__main__':
    main()
