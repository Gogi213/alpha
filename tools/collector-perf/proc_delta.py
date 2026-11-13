#!/usr/bin/env python3
"""Phase 1: /proc deltas between t0 and t1 snapshots (taken by p1.sh): per-collector CPU by thread role, context switches, frames/s, bytes/s; system counters."""
import collections, glob, json, os, sys

D = sys.argv[1]
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data", "bingx": "/data/collect/bingx/data"}
CLK = 100.0

def role(c):
    for p in ("rx-", "wr-", "sk-", "ln-", "sh-"):
        if c.startswith(p):
            return p[:-1]
    return "main/other"

def threads(path):
    out = {}
    for line in open(path):
        f = line.split()
        if len(f) < 6:
            continue
        tid, comm = f[0], f[1]
        ut, st = int(f[2]), int(f[3])
        out[tid] = (comm, ut, st, int(f[6]) if len(f) > 6 else 0, int(f[7]) if len(f) > 7 else 0)
    return out

res = {}
for e, root in ROOTS.items():
    a, b = f"{D}/{e}.t0.threads", f"{D}/{e}.t1.threads"
    ta, tb = threads(a), threads(b)
    dt = os.stat(b).st_mtime - os.stat(a).st_mtime
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0, 0, 0])
    for tid, (comm, ut, st, vol, inv) in tb.items():
        if tid in ta:
            c0 = ta[tid]
            r = role(comm)
            acc[r][0] += (ut - c0[1]) / CLK
            acc[r][1] += (st - c0[2]) / CLK
            acc[r][2] += vol - c0[3]
            acc[r][3] += inv - c0[4]
            acc[r][4] += 1
    # window frames/bytes from stats files
    t0ms, t1ms = int((os.stat(a).st_mtime) * 1000), int(os.stat(b).st_mtime * 1000)
    frames = bytes_ = 0
    per_kind = collections.Counter()
    for f in glob.glob(f"{root}/stats-*.jsonl"):
        try:
            lines = open(f).read().splitlines()[-30:]
        except Exception:
            continue
        for ln in lines:
            try:
                j = json.loads(ln)
            except Exception:
                continue
            if t0ms <= j["ts_ms"] <= t1ms:
                frames += j["msgs"]
                bytes_ += j["bytes"]
                per_kind[os.path.basename(f)[6:-6].rstrip("0123456789")] += j["msgs"]
    res[e] = dict(dt=dt, acc=acc, frames=frames, bytes=bytes_, per_kind=per_kind)
    tot_u = sum(v[0] for v in acc.values())
    tot_s = sum(v[1] for v in acc.values())
    sw = sum(v[2] + v[3] for v in acc.values())
    print(f"\n## {e}: window {dt:.0f} s; frames {frames/dt:,.0f}/s ({', '.join(f'{k}={v/dt:,.0f}' for k, v in per_kind.items())}); bytes {bytes_/dt/1e6:.2f} MB/s; avg frame {bytes_/max(frames,1):.0f} B")
    print(f"   CPU total {(tot_u+tot_s)/dt:.2f} cores = user {tot_u/dt:.2f} + sys {tot_s/dt:.2f}; ctx switches {sw/dt:,.0f}/s ({sw/max(frames,1):.2f}/frame); CPU/frame {(tot_u+tot_s)/max(frames,1)*1e6:.1f} us (user {tot_u/max(frames,1)*1e6:.1f}, sys {tot_s/max(frames,1)*1e6:.1f})")
    for r, v in sorted(acc.items(), key=lambda kv: -(kv[1][0] + kv[1][1])):
        print(f"   {r:10s} threads {v[4]:3d}  user {v[0]/dt:5.2f} sys {v[1]/dt:5.2f}  vol {v[2]/dt:8,.0f}/s invol {v[3]/dt:7,.0f}/s")

# system
def stat(p):
    for l in open(p):
        if l.startswith("cpu "):
            return list(map(int, l.split()[1:]))
s0, s1 = stat(f"{D}/t0.stat"), stat(f"{D}/t1.stat")
d = [b - a for a, b in zip(s0, s1)]
tot = sum(d[:8])
names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]
print("\n## system CPU (all 8 cores):", ", ".join(f"{n} {100*x/tot:.1f}%" for n, x in zip(names, d)))
def softirqs(p):
    out = {}
    for l in open(p):
        f = l.split()
        if f and f[0].endswith(":") and f[0][:-1] in ("NET_RX", "NET_TX", "TIMER", "RCU", "SCHED", "TASKLET", "HRTIMER", "BLOCK"):
            out[f[0][:-1]] = list(map(int, f[1:9]))
    return out
a, b = softirqs(f"{D}/t0.softirqs"), softirqs(f"{D}/t1.softirqs")
tt = float(open(f"{D}/t1.time").read()) - float(open(f"{D}/t0.time").read())
print(f"## softirqs per second per CPU (window {tt:.0f} s):")
for k in b:
    print(f"   {k:8s}", " ".join(f"{(y-x)/tt:7,.0f}" for x, y in zip(a[k], b[k])))
def nd(p):
    out = {}
    for l in open(p):
        if ":" in l and "Inter" not in l and "face" not in l:
            n, r = l.split(":", 1)
            f = r.split()
            out[n.strip()] = (int(f[0]), int(f[1]), int(f[8]), int(f[9]))
    return out
a, b = nd(f"{D}/t0.netdev"), nd(f"{D}/t1.netdev")
for i in ("eth0",):
    x, y = a[i], b[i]
    print(f"## eth0: rx {(y[0]-x[0])/tt/1e6*8:.1f} Mbit/s {(y[1]-x[1])/tt:,.0f} pkt/s (avg {(y[0]-x[0])/max(y[1]-x[1],1):.0f} B/pkt); tx {(y[2]-x[2])/tt/1e6*8:.1f} Mbit/s {(y[3]-x[3])/tt:,.0f} pkt/s")
def softnet(p):
    rows = []
    for l in open(p):
        rows.append([int(x, 16) for x in l.split()[:3]])
    return rows
a, b = softnet(f"{D}/t0.softnet"), softnet(f"{D}/t1.softnet")
print("## softnet per CPU processed/s, dropped, time_squeeze:", [(round((y[0]-x[0])/tt), y[1]-x[1], y[2]-x[2]) for x, y in zip(a, b)])
def interrupts(p):
    out = {}
    for l in open(p):
        f = l.split()
        if len(f) > 9 and "virtio3-input" in l:
            out[f[-1]] = list(map(int, f[1:9]))
    return out
a, b = interrupts(f"{D}/t0.interrupts"), interrupts(f"{D}/t1.interrupts")
print("## eth0 rx IRQs per queue per second:", {k: round(sum(y - x for x, y in zip(a[k], b[k])) / tt) for k in b})
