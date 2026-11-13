#!/usr/bin/env python3
"""Phase 1: exclusive 'where the CPU goes' table for the 4 collectors from the system-wide perf profile (per exchange and total), plus top kernel leaves."""
import collections, re, subprocess, sys
D = sys.argv[1]
HZ, SECS = 499, 60.0
pids = {int(open(f"{D}/{e}.pid").read()): e for e in ("binance", "bybit", "okx", "bingx")}
KS = ("net_rx_action", "__do_softirq", "do_softirq", "run_ksoftirqd", "handle_softirqs")
def user_cat(sym, dso):
    s = sym
    if "miniz_oxide" in s: return "user: gunzip (miniz_oxide)"
    if "ZSTD" in s or "FSE_" in s or "HUF_" in s or "HIST_" in s or "zstd" in s: return "user: zstd compress"
    if "aes" in s.lower() or "ghash" in s.lower() or "chacha" in s.lower() or "poly1305" in s.lower() or "ring::" in s or "rustls" in s or "Tls13" in s: return "user: TLS (rustls/ring)"
    if "tungstenite" in s: return "user: WebSocket framing"
    if "collector::scan" in s or "Parser" in s or "okx::" in s or "bybit::" in s or "binance::" in s or "bingx::" in s: return "user: JSON parse/adapters"
    if "Encoder" in s or "codec::" in s: return "user: record encoder"
    if "writer::" in s or "Sink" in s or "Seen" in s or "hash" in s: return "user: writer (dedup, handle)"
    if "mpmc" in s or "mpsc" in s or "Channel" in s or "park" in s or "Waker" in s or "ring::" in s or "Thread" in s: return "user: channel/park (hand-off)"
    if "session::" in s or "net::" in s or "TcpStream" in s or "Instant" in s or "clock_gettime" in s: return "user: rx loop/stamps/sock wrappers"
    if s in ("malloc", "free", "realloc", "cfree", "_int_malloc", "_int_free", "malloc_consolidate", "calloc") or "alloc" in s: return "user: malloc/free/alloc"
    if s == "[unknown]": return "user: [unknown] (libc/vdso/unresolved)"
    if "memcpy" in s or "memmove" in s or "memset" in s or "memchr" in s: return "user: memcpy/memchr"
    return "user: other"
p = subprocess.Popen(["perf", "script", "-i", f"{D}/all.data", "-F", "comm,pid,tid,cpu,ip,sym,dso", "--no-inline"], stdout=subprocess.PIPE, text=True, errors="replace", bufsize=1 << 20)
hre = re.compile(r"^\s*(\S.*?)\s+(\d+)/(\d+)\s+\[(\d+)\]")
tab = collections.defaultdict(collections.Counter)
kleaf = collections.Counter()
hdr = None; frames = []
def flush():
    if not hdr or hdr[1] not in pids or not frames: return
    ex = pids[hdr[1]]
    leaf, dso = frames[0]
    syms = [s for s, _ in frames]
    ss = set(syms)
    if "kernel" not in dso:
        c = user_cat(leaf, dso)
    else:
        kleaf[leaf] += 1
        if any(k in ss for k in KS): c = "kernel: softirq (net rx, in collector thread ctx)"
        elif "__x64_sys_recvfrom" in ss: c = "kernel: syscall recvfrom"
        elif "__x64_sys_futex" in ss: c = "kernel: syscall futex (hand-off wake/wait)"
        elif "__x64_sys_ioctl" in ss: c = "kernel: syscall ioctl (FIONREAD)"
        elif "do_syscall_64" in ss or "entry_SYSCALL_64" in ss: c = "kernel: syscall other (clock_nanosleep/write/read/…)"
        else: c = "kernel: irq/timer/sched (not in syscall)"
    tab[ex][c] += 1; tab["ALL"][c] += 1
for line in p.stdout:
    if not line.strip():
        flush(); hdr = None; frames = []; continue
    if line[0] not in " \t":
        m = hre.match(line); hdr = (m.group(1), int(m.group(2))) if m else None; frames = []
    else:
        f = line.split()
        if len(f) >= 2: frames.append((f[1].split("+0x")[0], f[-1].strip("()")))
flush()
cap = HZ * SECS
order = ["ALL", "binance", "bybit", "okx", "bingx"]
tot = {k: sum(v.values()) for k, v in tab.items()}
print(f"{'category':58s}" + "".join(f"{k:>10s}" for k in order) + "   (cores; ALL also %)")
print(f"{'TOTAL cores':58s}" + "".join(f"{tot[k]/cap:10.2f}" for k in order))
cats = sorted(tab["ALL"], key=lambda c: -tab["ALL"][c])
for c in cats:
    print(f"{c:58s}" + "".join(f"{tab[k][c]/cap:10.3f}" for k in order) + f"   {100*tab['ALL'][c]/tot['ALL']:5.1f}%")
print("\ntop kernel leaf symbols in collector samples (all 4):")
kt = sum(kleaf.values())
for s, n in kleaf.most_common(25):
    print(f"  {100*n/kt:5.1f}%  {n/cap:5.3f} cores  {s}")
