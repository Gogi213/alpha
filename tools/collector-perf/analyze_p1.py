#!/usr/bin/env python3
"""Phase 1 (G-022 collector perf): reads `perf script` of a system-wide cpu-clock profile plus /proc snapshots; prints where the CPU goes.
usage: analyze_p1.py <dir with all.data, t0/t1 snapshots, <ex>.pid, <ex>.t0.threads ...> [--hz 499] [--secs 60]
"""
import collections, os, re, subprocess, sys, json

D = sys.argv[1]
HZ = 499
SECS = 60.0
EXS = ["binance", "bybit", "okx", "bingx"]
pid_of = {}
for e in EXS:
    try:
        pid_of[e] = int(open(f"{D}/{e}.pid").read().strip())
    except Exception:
        pass
ex_of_pid = {v: k for k, v in pid_of.items()}

def role(comm):
    for p in ("rx-", "wr-", "sk-", "ln-", "sh-"):
        if comm.startswith(p):
            return p[:-1]
    if comm.startswith("alpha-collect"):
        return "main"
    if comm.startswith("signals"):
        return "signals"
    return "other"

KSOFT = ("net_rx_action", "__do_softirq", "do_softirq", "run_ksoftirqd", "__softirqentry_text_start", "handle_softirqs")
cmd = ["perf", "script", "-i", f"{D}/all.data", "-F", "comm,pid,tid,cpu,ip,sym,dso", "--no-inline"]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace", bufsize=1 << 20)

def flush(h, frames, acc):
    comm, pid = h
    if not frames:
        return
    leaf_sym, leaf_dso = frames[0]
    kernel_leaf = "kernel" in leaf_dso
    syms = [s for s, _ in frames]
    if comm.startswith("swapper") or leaf_sym in ("native_safe_halt", "default_idle", "default_idle_call", "pv_native_safe_halt", "arch_cpu_idle"):
        cls = "idle"
    elif not kernel_leaf:
        cls = "user"
    elif any(any(k in s for k in KSOFT) for s in syms):
        cls = "softirq"
    elif any(s in ("entry_SYSCALL_64_after_hwframe", "do_syscall_64", "entry_SYSCALL_64") for s in syms):
        cls = "syscall"
    else:
        cls = "kernel-other"
    sysc = None
    if cls == "syscall":
        for s in syms:
            if s.startswith("__x64_sys_"):
                sysc = s[10:]
                break
    acc.append((comm, pid, cls, leaf_sym, leaf_dso, sysc, syms))

samples = []
hdr = None
frames = []
hre = re.compile(r"^\s*(\S.*?)\s+(\d+)/(\d+)\s+\[(\d+)\]")
for line in p.stdout:
    if not line.strip():
        if hdr:
            flush(hdr, frames, samples)
        hdr, frames = None, []
        continue
    if line[0] not in " \t":
        m = hre.match(line)
        hdr = (m.group(1), int(m.group(2))) if m else None
        frames = []
    else:
        parts = line.split()
        # "ip sym+off (dso)"
        if len(parts) >= 2:
            sym = parts[1].split("+0x")[0]
            dso = parts[-1].strip("()")
            frames.append((sym, dso))
if hdr:
    flush(hdr, frames, samples)
p.wait()
tot = len(samples)
cap = HZ * SECS  # samples per core-second window
print(f"samples {tot}; capacity per core {cap:.0f}; 8 cores = {8*cap:.0f}")

def cores(n):
    return n / cap

by_cls = collections.Counter(s[2] for s in samples)
print("\n## whole machine, cores by class")
for k, v in by_cls.most_common():
    print(f"  {k:14s} {cores(v):6.2f} cores  {100*v/tot:5.1f}%")

# per process
def owner(pid, comm):
    if pid in ex_of_pid:
        return ex_of_pid[pid]
    if comm.startswith("ksoftirqd"):
        return "ksoftirqd"
    if comm.startswith("swapper"):
        return "idle"
    return "other:" + comm.split("/")[0][:14]
by_owner = collections.defaultdict(collections.Counter)
for comm, pid, cls, *_ in samples:
    by_owner[owner(pid, comm)][cls] += 1
print("\n## per owner: cores total | user syscall softirq kernel-other")
rows = sorted(by_owner.items(), key=lambda kv: -sum(v for c, v in kv[1].items() if c != "idle"))
for o, c in rows[:14]:
    if o == "idle":
        continue
    t = sum(c.values())
    print(f"  {o:24s} {cores(t):6.2f} | " + " ".join(f"{k}={cores(c[k]):.2f}" for k in ("user", "syscall", "softirq", "kernel-other")))

# per collector x role
for e in EXS:
    pid = pid_of.get(e)
    if not pid:
        continue
    sub = [s for s in samples if s[1] == pid]
    n = len(sub)
    print(f"\n## {e} pid {pid}: {cores(n):.2f} cores total")
    br = collections.defaultdict(collections.Counter)
    for comm, _, cls, *_ in sub:
        br[role(comm)][cls] += 1
    for r, c in sorted(br.items(), key=lambda kv: -sum(kv[1].values())):
        t = sum(c.values())
        print(f"  {r:8s} {cores(t):5.2f} cores ({100*t/n:4.1f}%) | " + " ".join(f"{k}={cores(c[k]):.2f}" for k in ("user", "syscall", "softirq", "kernel-other")))
    # top user symbols per role (rx / wr / sk)
    for r in ("rx", "wr", "sk"):
        us = collections.Counter(s[3] for s in sub if role(s[0]) == r and s[2] == "user")
        ut = sum(us.values())
        if ut:
            print(f"  top user symbols in {r} (of {cores(ut):.2f} cores user):")
            for sy, v in us.most_common(14):
                print(f"     {100*v/ut:5.1f}%  {sy[:110]}")
    sc = collections.Counter(s[5] or "?" for s in sub if s[2] == "syscall")
    st = sum(sc.values())
    if st:
        print(f"  syscalls by kernel time ({cores(st):.2f} cores):", ", ".join(f"{k}={100*v/st:.0f}%" for k, v in sc.most_common(8)))
    kl = collections.Counter(s[3] for s in sub if s[2] in ("softirq", "syscall", "kernel-other"))
    kt = sum(kl.values())
    print(f"  top kernel leaf symbols ({cores(kt):.2f} cores):", ", ".join(f"{k}={100*v/kt:.0f}%" for k, v in kl.most_common(10)))

# ksoftirqd / idle / other kernel
ks = [s for s in samples if s[0].startswith("ksoftirqd")]
if ks:
    kl = collections.Counter(s[3] for s in ks)
    print(f"\n## ksoftirqd threads: {cores(len(ks)):.2f} cores; top leaf:", ", ".join(f"{k}={100*v/len(ks):.0f}%" for k, v in kl.most_common(10)))
sw = [s for s in samples if s[2] == "softirq"]
kl = collections.Counter(s[3] for s in sw)
print(f"\n## all softirq-context samples: {cores(len(sw)):.2f} cores; top leaf:", ", ".join(f"{k}={100*v/len(sw):.0f}%" for k, v in kl.most_common(14)))
