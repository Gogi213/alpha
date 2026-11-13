#!/usr/bin/env python3
"""Phase 1: call tree below a syscall frame (collector samples only): where the kernel time of recvfrom/futex goes. usage: tree_p1.py <dir> <syscall frame> [depth] [min%]"""
import collections, re, subprocess, sys
D, SYS = sys.argv[1], sys.argv[2]
DEPTH = int(sys.argv[3]) if len(sys.argv) > 3 else 6
MIN = float(sys.argv[4]) if len(sys.argv) > 4 else 1.5
pids = {int(open(f"{D}/{e}.pid").read()) for e in ("binance", "bybit", "okx", "bingx")}
p = subprocess.Popen(["perf", "script", "-i", f"{D}/all.data", "-F", "comm,pid,tid,cpu,ip,sym,dso", "--no-inline"], stdout=subprocess.PIPE, text=True, errors="replace", bufsize=1 << 20)
hre = re.compile(r"^\s*(\S.*?)\s+(\d+)/(\d+)\s+\[(\d+)\]")
trie = {}
total = 0
hdr = None; syms = []
def flush():
    global total
    if not hdr or hdr[1] not in pids or SYS not in syms:
        return
    total += 1
    i = syms.index(SYS)
    path = syms[:i][::-1][:DEPTH]  # from syscall frame downward to leaf
    node = trie
    for s in path:
        node = node.setdefault(s, [0, {}])
        node[0] += 1
        node = node[1]
for line in p.stdout:
    if not line.strip():
        flush(); hdr = None; syms = []; continue
    if line[0] not in " \t":
        m = hre.match(line); hdr = (m.group(1), int(m.group(2))) if m else None; syms = []
    else:
        f = line.split()
        if len(f) >= 2: syms.append(f[1].split("+0x")[0])
flush()
print(f"{SYS}: {total} samples = {total/(499*60.0):.2f} cores")
def show(node, ind, lim):
    for k, (n, ch) in sorted(node.items(), key=lambda kv: -kv[1][0]):
        if 100 * n / total < MIN:
            continue
        print(f"{'  '*ind}{100*n/total:5.1f}%  {k}")
        show(ch, ind + 1, lim)
show(trie, 0, DEPTH)
