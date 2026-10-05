#!/usr/bin/env python3
"""io-acct.py run <state.json> <own-cgroup-path>  — сэмплер (раз в 2 с): диски из /proc/diskstats, io.stat своего юнита, /proc/*/io по cgroup.
   io-acct.py report <state.json>                  — строки метрик: foreign_* по sda/sdb, disk_interference_pct, топ чужих cgroup."""
import json, os, sys, time, signal

DISKS = ("sda", "sdb")
T = 0.5


def diskstats():
    d = {}
    for l in open("/proc/diskstats"):
        f = l.split()
        if f[2] in DISKS:
            d[f[2]] = {"mm": f"{f[0]}:{f[1]}", "r": int(f[5]) * 512, "w": int(f[9]) * 512, "ticks": int(f[12]), "rc": int(f[3])}
    return d


def own_iostat(cg, mm):
    try:
        for l in open(f"/sys/fs/cgroup{cg}/io.stat"):
            f = l.split()
            if f[0] in mm.values():
                kv = dict(x.split("=") for x in f[1:])
                yield f[0], int(kv.get("rbytes", 0)), int(kv.get("wbytes", 0))
    except OSError:
        return


def pids():
    for p in os.listdir("/proc"):
        if p.isdigit():
            yield p


def foreign_d(own):
    """Чужие процессы в состоянии D (не ядро, не в замороженных cgroup, не в cgroup волны): {(cg, comm, args): 1}."""
    res = {}
    for p in pids():
        try:
            st = open(f"/proc/{p}/stat").read()
            if st.rsplit(")", 1)[1].split()[0] != "D":
                continue
            cg = open(f"/proc/{p}/cgroup").read().strip().split("::")[-1] or "?"
            if cg.startswith(own):
                continue
            args = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            if not args:
                continue
            try:
                if "frozen 1" in open(f"/sys/fs/cgroup{cg}/cgroup.events").read():
                    continue
            except OSError:
                pass
            comm = st[st.index("(") + 1:st.rindex(")")]
            res[(p, cg.rsplit("/", 1)[-1], comm, args[:90])] = 1
        except (OSError, ValueError, IndexError):
            continue
    return res


def main_run(state, own):
    ds0 = diskstats()
    mm = {k: v["mm"] for k, v in ds0.items()}
    own0 = {m: (r, w) for m, r, w in own_iostat(own, mm)}
    base, last, cg_of = {}, {}, {}
    t0 = time.time()
    first = True
    stop = []
    dsamp, dproc = 0, {}
    nsamp = 0
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    while True:
        for p in pids():
            try:
                st = open(f"/proc/{p}/stat").read()
                start = st.rsplit(")", 1)[1].split()[19]
                key = (p, start)
                io = {}
                for l in open(f"/proc/{p}/io"):
                    k, v = l.split(":")
                    io[k] = int(v)
                if key not in cg_of:
                    cg_of[key] = open(f"/proc/{p}/cgroup").read().strip().split("::")[-1] or "?"
                    base[key] = (io["read_bytes"], io["write_bytes"]) if first else (0, 0)
                last[key] = (io["read_bytes"], io["write_bytes"])
            except (OSError, ValueError, IndexError):
                continue
        first = False
        per = {}
        for key, (r, w) in last.items():
            b = base[key]
            c = cg_of[key]
            a = per.setdefault(c, [0, 0])
            a[0] += r - b[0]
            a[1] += w - b[1]
        fd = foreign_d(own)
        nsamp += 1
        if fd:
            dsamp += 1
        for k in fd:
            dproc[k] = dproc.get(k, 0) + 1
        ds = diskstats()
        o = {m: (r, w) for m, r, w in own_iostat(own, mm)}
        s = {"t0": t0, "t": time.time(), "own": own, "mm": mm, "ds0": ds0, "ds": ds, "own0": own0, "own_now": o, "per": per, "nsamp": nsamp, "dsamp": dsamp,
             "dproc": [[list(k), v] for k, v in sorted(dproc.items(), key=lambda x: -x[1])[:8]]}
        tmp = state + ".tmp"
        json.dump(s, open(tmp, "w"))
        os.replace(tmp, state)
        if stop:
            return
        time.sleep(T)


def report(state):
    s = json.load(open(state))
    mb = lambda x: round(x / 1e6, 1)
    own_ok = bool(s["own_now"])
    out = []
    tot_r = tot_w = 0
    foreign = 0
    own_r_all = 0
    for d in DISKS:
        m = s["mm"][d]
        tr = s["ds"][d]["r"] - s["ds0"][d]["r"]
        tw = s["ds"][d]["w"] - s["ds0"][d]["w"]
        orr, ow = 0, 0
        if own_ok:
            r1, w1 = s["own_now"].get(m, (0, 0))
            r0, w0 = s["own0"].get(m, (0, 0))
            orr, ow = r1 - r0, w1 - w0
        fr, fw = max(tr - orr, 0), max(tw - ow, 0)
        out.append(f"total_read_MB_{d} {mb(tr)} total_write_MB_{d} {mb(tw)}")
        if own_ok:
            out.append(f"own_read_MB_{d} {mb(orr)} own_write_MB_{d} {mb(ow)}")
            out.append(f"foreign_read_MB_{d} {mb(fr)} foreign_write_MB_{d} {mb(fw)}")
            foreign += fr + fw
            own_r_all += orr
    if own_ok:
        pct = round(100 * foreign / max(own_r_all, 1), 1)
        out.append(f"disk_interference_pct {pct}")
        out.append("INVALID_DISK_INTERFERENCE" if pct > 5 else "disk_interference_ok")
    else:
        out.append("disk_interference_unknown (io.stat своего юнита недоступен)")
    w = max(s["t"] - s["t0"], 1e-9)
    for d in DISKS:
        a, b = s["ds0"][d], s["ds"][d]
        rc = b.get("rc", 0) - a.get("rc", 0)
        rb = b["r"] - a["r"]
        out.append(f"disk_{d} r_per_s {round(rc / w, 1)} avg_req_KB {round(rb / max(rc, 1) / 1024, 1)} util_pct {round((b['ticks'] - a['ticks']) / (10 * w), 1)}")
    ns = max(s.get("nsamp", 0), 1)
    dpct = round(100 * s.get("dsamp", 0) / ns, 1)
    out.append(f"foreign_D_pct {dpct} (доля замеров раз в 2 с, когда чужой процесс вне юнита волны/замороженных висел в D)")
    out.append("INVALID_FOREIGN_IO" if dpct > 5 else "foreign_io_ok")
    for (k, v) in s.get("dproc", []):
        out.append(f"foreign_D_proc pid {k[0]} unit {k[1]} comm {k[2]} samples {v} cmd {k[3]}")
    own = s["own"]
    top = sorted(((c, v) for c, v in s["per"].items() if not c.startswith(own)), key=lambda x: -(x[1][0] + x[1][1]))[:6]
    for c, (r, w) in top:
        if r + w > 0:
            out.append(f"foreign_top {c.rsplit('/', 1)[-1]} read_MB {mb(r)} write_MB {mb(w)}")
    ownp = s["per"].get(own, [0, 0])
    out.append(f"own_pid_io read_MB {mb(ownp[0])} write_MB {mb(ownp[1])} window_s {round(s['t'] - s['t0'], 1)}")
    print("\n".join(out))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        main_run(sys.argv[2], sys.argv[3])
    else:
        report(sys.argv[2])
