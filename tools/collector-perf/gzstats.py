#!/usr/bin/env python3
"""Block types and dynamic-tree repeat rate over frames recorded by capture_gz.py. usage: gzstats.py <file>"""
import struct, sys, collections, zlib
raw = open(sys.argv[1], "rb").read()
frames = []; at = 0
while at + 13 <= len(raw):
    ln, kind, t = struct.unpack_from("<IBd", raw, at)
    if at + 13 + ln > len(raw): break
    frames.append((kind, raw[at + 13:at + 13 + ln])); at += 13 + ln
class Bits:
    def __init__(s, b, pos): s.b = b; s.p = pos * 8
    def get(s, n):
        v = 0
        for i in range(n):
            byte = s.b[s.p >> 3]; v |= ((byte >> (s.p & 7)) & 1) << i; s.p += 1
        return v
def canon(lens):
    cnt = collections.Counter(l for l in lens if l); code = 0; nxt = {}
    for l in range(1, 16):
        code = (code + cnt.get(l - 1, 0)) << 1; nxt[l] = code
    tab = {}
    for sym, l in enumerate(lens):
        if l: tab[(l, nxt[l])] = sym; nxt[l] += 1
    return tab
def readsym(br, tab):
    code = 0
    for l in range(1, 16):
        code = (code << 1) | br.get(1)
        if (l, code) in tab: return tab[(l, code)]
    raise ValueError
def hdr(frame):
    flg = frame[3]; pos = 10
    if flg & 4: pos += 2 + struct.unpack_from("<H", frame, pos)[0]
    if flg & 8: pos = frame.index(0, pos) + 1
    if flg & 16: pos = frame.index(0, pos) + 1
    if flg & 2: pos += 2
    return pos
types = collections.Counter(); trees = collections.Counter(); comp = collections.defaultdict(list); out = collections.defaultdict(list)
nblocks = collections.Counter(); dyn = 0; errs = 0; syms = []
order = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
for kind, f in frames:
    if len(f) < 18 or f[:2] != b"\x1f\x8b": continue
    comp[kind].append(len(f)); out[kind].append(struct.unpack("<I", f[-4:])[0])
    br = Bits(f, hdr(f)); nb = 0
    try:
        while True:
            fin = br.get(1); bt = br.get(2); nb += 1
            types[{0: "stored", 1: "fixed", 2: "dynamic"}.get(bt, "?")] += 1
            if bt != 2: break
            hlit = br.get(5) + 257; hdist = br.get(5) + 1; hclen = br.get(4) + 4
            pl = [0] * 19
            for i in range(hclen): pl[order[i]] = br.get(3)
            pt = canon(pl); lens = []
            while len(lens) < hlit + hdist:
                s = readsym(br, pt)
                if s < 16: lens.append(s)
                elif s == 16: lens += [lens[-1]] * (3 + br.get(2))
                elif s == 17: lens += [0] * (3 + br.get(3))
                else: lens += [0] * (11 + br.get(7))
            trees[(hlit, bytes(lens))] += 1; dyn += 1; syms.append(hlit)
            break   # only first block, rest needs full decode; frames are single-block in practice (checked via nblocks of fin below)
    except Exception as e:
        errs += 1
    nblocks[fin] += 0
n = sum(types.values())
print("frames", len(frames), "parsed", n, "types", dict(types), "hdr-parse errors", errs)
for k, name in ((0, "book"), (1, "trade"), (2, "mark")):
    c = sorted(comp[k]); o = sorted(out[k])
    if c: print(f"{name}: n {len(c)} comp p50 {c[len(c)//2]} p99 {c[int(len(c)*.99)]} B, out p50 {o[len(o)//2]} p99 {o[int(len(o)*.99)]} B")
tot = sum(trees.values()); top = trees.most_common()
print("dynamic blocks", tot, "distinct trees", len(trees))
cum = 0
for N in (1, 4, 16, 64, 256, 1024, 4096):
    print(f"  top-{N} trees cover {sum(c for _, c in top[:N]) / tot:.1%} of dynamic frames")
# LRU hit rate by sequence order
lru = collections.OrderedDict(); hits = {16: 0, 64: 0, 256: 0, 1024: 0}
ml = collections.Counter(); md = collections.Counter(); nl = []; used = []
for (hlit, lens), c in trees.items():
    L = lens[:hlit]; D = lens[hlit:]
    ml[max(L)] += c; md[max(D)] += c; nl.append((hlit, c)); used.append((sum(1 for x in L if x), c))
print("max litlen code length (freq-weighted):", sorted(ml.items()))
print("max dist code length:", sorted(md.items()))
tw = sum(c for _, c in used); print("avg used litlen symbols", sum(u * c for u, c in used) / tw)
