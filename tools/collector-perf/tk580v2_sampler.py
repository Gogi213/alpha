#!/usr/bin/env python3
"""TK-580 v2: host sampler, 1 Hz -> JSONL. usage: tk580v2_sampler.py <out.jsonl> <seconds> <session dir> <unit> [unit ...]
Per second: /proc/stat cpu line, PSI totals (cpu/memory/io: some, full, us), selected /proc/vmstat counters, MemAvailable/Cached/AnonPages,
loadavg, and for each unit: cgroup memory.current; for the -OLD/-NEW copies also the reactor-thread schedstat (run ns, wait ns, slices), minflt/majflt
and (every 10 s) health reactor.frames. Every 10 s: per-process CPU % over the last 10 s (top 8) and RSS (top 4) of ALL processes (ambient load of other
threads), established sockets of the live BingX and of each copy."""
import json, os, subprocess, sys, time

OUT, SECS, SESS = sys.argv[1], float(sys.argv[2]), sys.argv[3]
UNITS = sys.argv[4:]
COPY = [u for u in UNITS if u.endswith(("-OLD", "-NEW"))]
VM = ("pgfault", "pgmajfault", "pgscan_kswapd", "pgscan_direct", "pgsteal_kswapd", "pgsteal_direct", "pswpin", "pswpout",
      "thp_fault_alloc", "compact_stall", "numa_hint_faults", "workingset_refault_file", "nr_dirty")
CLK = os.sysconf("SC_CLK_TCK")


def read(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return ""


def psi():
    d = {}
    for k in ("cpu", "memory", "io"):
        for l in read(f"/proc/pressure/{k}").splitlines():
            f = l.split()
            if f:
                d[f"{k}.{f[0]}"] = int(f[4].split("=")[1])
    return d


def vmstat():
    d = {}
    for l in read("/proc/vmstat").splitlines():
        a, b = l.split()
        if a in VM:
            d[a] = int(b)
    return d


def meminfo():
    d = {}
    for l in read("/proc/meminfo").splitlines():
        a, b = l.split(":")
        if a in ("MemAvailable", "Cached", "AnonPages", "MemFree"):
            d[a] = int(b.split()[0])
    return d


def main_pid(u):
    p = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", u + ".service"], capture_output=True, text=True).stdout.strip()
    return int(p) if p.isdigit() and int(p) > 0 else 0


def reactor_tid(pid):
    try:
        for t in os.listdir(f"/proc/{pid}/task"):
            if read(f"/proc/{pid}/task/{t}/comm").strip() == "rx-reactor":
                return int(t)
    except OSError:
        pass
    return 0


tid = {u: (0, 0) for u in COPY}   # unit -> (pid, tid)


def thread(u):
    pid, t = tid[u]
    if not t or not os.path.exists(f"/proc/{pid}/task/{t}"):
        pid = main_pid(u)
        t = reactor_tid(pid) if pid else 0
        tid[u] = (pid, t)
    if not t:
        return None
    ss = read(f"/proc/{pid}/task/{t}/schedstat").split()
    st = read(f"/proc/{pid}/task/{t}/stat")
    f = st[st.rfind(")") + 2:].split()
    if len(ss) < 3 or len(f) < 12:
        return None
    return {"tid": t, "run": int(ss[0]), "wait": int(ss[1]), "slices": int(ss[2]), "minflt": int(f[7]), "majflt": int(f[9])}


def cgmem(u):
    v = read(f"/sys/fs/cgroup/system.slice/{u}.service/memory.current").strip()
    return int(v) if v else None


def frames(u):
    try:
        h = json.load(open(f"{SESS}/{u.rsplit('-', 1)[1]}/health.json"))
        return h["reactor"]["frames"], h.get("gzip")
    except (OSError, ValueError, KeyError):
        return None


def socks(pid):
    if not pid:
        return 0
    r = subprocess.run(["bash", "-c", f"ss -tnpH state established 2>/dev/null | grep -c 'pid={pid},'"], capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


def procs():
    out = {}
    for p in os.listdir("/proc"):
        if p.isdigit():
            st = read(f"/proc/{p}/stat")
            i = st.rfind(")")
            if i < 0:
                continue
            f = st[i + 2:].split()
            try:
                out[int(p)] = (st[st.find("(") + 1:i], int(f[11]) + int(f[12]), int(f[16]), int(f[21]) * 4)   # comm, utime+stime ticks, nice, rss kB (pages*4)
            except (IndexError, ValueError):
                pass
    return out


def cmdline(pid):
    return read(f"/proc/{pid}/cmdline").replace("\0", " ")[:70]


t_end = time.time() + SECS
nxt = time.time()
n = 0
prev = procs()
prev_t = time.time()
with open(OUT, "a") as out:
    while time.time() < t_end:
        now = time.time()
        rec = {"t": round(now, 3), "stat": read("/proc/stat").splitlines()[0].split()[1:9], "psi": psi(), "vm": vmstat(), "mem": meminfo(),
               "load": read("/proc/loadavg").split()[0]}
        for u in UNITS:
            m = cgmem(u)
            if m is not None:
                rec[u + ".mem"] = m
        for u in COPY:
            th = thread(u)
            if th:
                rec[u] = th
        if n % 10 == 0:
            for u in COPY:
                fr = frames(u)
                if fr:
                    rec[u + ".frames"] = fr
            cur = procs()
            dt = max(now - prev_t, 1e-3)
            cpu = sorted(((cur[p][1] - prev[p][1]) / CLK / dt * 100 if p in prev else 0.0, p) for p in cur)[::-1][:8]
            rss = sorted(((cur[p][3], p) for p in cur))[::-1][:4]
            rec["top_cpu"] = [[p, cur[p][0], cur[p][2], round(c), cur[p][3] // 1024, cmdline(p)[:50]] for c, p in cpu]
            rec["top_rss"] = [[p, cur[p][0], cur[p][2], cur[p][3] // 1024, cmdline(p)[:50]] for _, p in rss]
            prev, prev_t = cur, now
            rec["socks"] = {"live": socks(main_pid("collect-bingx")), **{u: socks(tid[u][0]) for u in COPY}}
        out.write(json.dumps(rec, separators=(",", ":")) + "\n")
        out.flush()
        n += 1
        nxt += 1.0
        time.sleep(max(0.0, nxt - time.time()))
