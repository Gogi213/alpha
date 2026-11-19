#!/usr/bin/env python3
"""TK-580 v2 report. usage: tk580v2_rep.py <session dir> [minute-table step, s; 0 = none]
One script over raw files for both copies, host and live collectors (one metric = one script, rule 23): queue_lag_p99 rows of stats-*.jsonl,
frames from health (sampler), host from samp.jsonl (/proc/stat, PSI, vmstat), reactor thread schedstat, counters from `perf stat -I 60000 -x,`."""
import bisect, datetime, glob, json, math, os, statistics, sys

D = sys.argv[1].rstrip("/")
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 60
LAB = os.path.basename(D)
TRIM = 20.0
LIVE = {"bingx": "/data/collect/bingx/data", "okx": "/data/collect/okx/data", "bybit": "/data/turbo-probe/soak-bybit/data"}
ph = []
for l in open(f"{D}/phases.tsv"):
    i, k, a, b, ab = l.split()
    ph.append((int(i), k, float(a), float(b), ab))
samp = [json.loads(l) for l in open(f"{D}/samp.jsonl") if l.strip()]
ST = [s["t"] for s in samp]
copies = [c for c in ("OLD", "NEW") if os.path.isdir(f"{D}/{c}")]
T_BEG, T_END = ph[0][2], ph[-1][3]


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * q))] if v else float("nan")


def med(v):
    return statistics.median(v) if v else float("nan")


def load_rows(root, lo, hi):
    out = {"book": [], "trade": [], "mark": []}
    for f in glob.glob(f"{root}/stats-*.jsonl"):
        kind = os.path.basename(f)[6:].rstrip("0123456789.jsonl")
        if kind not in out:
            continue
        for l in open(f):
            try:
                d = json.loads(l)
            except ValueError:
                continue
            t = d.get("ts_ms", 0) / 1000.0
            if lo <= t < hi and d.get("queue_lag_p99_ms") is not None:
                out[kind].append((t, d["queue_lag_p99_ms"]))
    for k in out:
        out[k].sort()
    return out


QL = {c: load_rows(f"{D}/{c}/data", T_BEG - 60, T_END + 60) for c in copies}
QL.update({ex: load_rows(root, T_BEG - 60, T_END + 60) for ex, root in LIVE.items()})


def qrows(src, t0, t1, kinds=("book", "trade", "mark")):
    r = []
    for k in kinds:
        v = QL[src][k]
        i = bisect.bisect_left(v, (t0, -1))
        while i < len(v) and v[i][0] < t1:
            r.append(v[i][1])
            i += 1
    return r


def srows(t0, t1):
    return samp[bisect.bisect_left(ST, t0):bisect.bisect_left(ST, t1)]


def host(t0, t1):
    s = srows(t0, t1)
    if len(s) < 2:
        return None
    a, b = s[0], s[-1]
    dt = b["t"] - a["t"]
    cs = [y - x for x, y in zip(map(int, a["stat"]), map(int, b["stat"]))]
    tot = sum(cs) or 1
    c = dict(zip(("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"), (100.0 * v / tot for v in cs)))
    p = {k: (b["psi"][k] - a["psi"][k]) / dt / 1e4 for k in a["psi"]}
    v = {k: (b["vm"][k] - a["vm"][k]) / dt for k in a["vm"] if k in b["vm"]}
    hold = max((sum(v2 for k2, v2 in x.items() if k2.endswith(("-scan.mem", "-marks.mem"))) for x in s), default=0) / 2 ** 30
    amb = 0.0                                       # biggest RSS of a process that is not ours and not a collector, GB (ambient memory-heavy jobs)
    for x in s:
        for r in x.get("top_rss", []):
            if not r[4].startswith(("/data/collect", "/data/tk580", "alpha")) and "hold_" not in r[4] and "tk580v2" not in r[4]:
                amb = max(amb, r[3] / 1024.0)
    return dict(dt=dt, c=c, p=p, v=v, mem_min=min(x["mem"]["MemAvailable"] for x in s) / 1048576, hold=hold, amb=amb,
                load=statistics.mean(float(x["load"]) for x in s))


def thread(copy, t0, t1):
    k = f"tk580v2-{LAB}-{copy}"
    s = [x for x in srows(t0, t1) if k in x]
    if len(s) < 2:
        return None
    a, b = s[0][k], s[-1][k]
    dt = s[-1]["t"] - s[0]["t"]
    return dict(run=(b["run"] - a["run"]) / dt / 1e7, wait=(b["wait"] - a["wait"]) / dt / 1e6, slices=(b["slices"] - a["slices"]) / dt,
                minflt=(b["minflt"] - a["minflt"]) / dt, majflt=(b["majflt"] - a["majflt"]) / dt)


def frames_at(copy, t):
    k = f"tk580v2-{LAB}-{copy}.frames"
    best = None
    for x in samp[max(0, bisect.bisect_left(ST, t) - 12):bisect.bisect_left(ST, t) + 12]:
        if k in x and (best is None or abs(x["t"] - t) < abs(best[0] - t)):
            best = (x["t"], x[k][0], x[k][1])
    return best


def frames_between(copy, t0, t1):
    a, b = frames_at(copy, t0), frames_at(copy, t1)
    if not a or not b or b[0] <= a[0]:
        return None
    return (b[1] - a[1]) * (t1 - t0) / (b[0] - a[0])


PERF = {}
for c in copies:
    f, f0 = f"{D}/perf.{c}.csv", f"{D}/perf.{c}.t0"
    if not (os.path.exists(f) and os.path.exists(f0)):
        continue
    t0 = float(open(f0).read())
    iv = {}
    for l in open(f):
        x = l.strip().split(",")
        if len(x) >= 6 and not l.startswith("#"):
            try:
                tt, val = float(x[0]), float(x[1])
            except ValueError:
                continue
            iv.setdefault(round(tt), {})[x[3]] = (val, float(x[5]) if x[5] else 100.0)
    PERF[c] = [(t0 + tt - 60, t0 + tt, e) for tt, e in sorted(iv.items())]   # (start, end, events) 60 s each


def perf_agg(c, t0, t1):
    tot, pc = {}, {}
    for a, b, e in PERF.get(c, []):
        if a >= t0 and b <= t1:
            for k, (v, p) in e.items():
                tot[k] = tot.get(k, 0) + v
                pc[k] = min(pc.get(k, 100), p)
    return tot, pc


def signp(d):
    n = sum(1 for x in d if x != 0)
    k = sum(1 for x in d if x > 0)
    if not n:
        return 1.0
    m = min(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n)


print(f"# session {D}")
print(open(f"{D}/driver.log").read().rstrip())
print()
for i, k, a, b, ab in ph:
    t0, t1 = a + TRIM, b
    h = host(t0, t1)
    if not h:
        continue
    c, p, v = h["c"], h["p"], h["v"]
    ended = {"1": " (load ABORTED: MemAvailable < 3 GB)", "2": " (hold jobs ended before the phase end)"}.get(ab, "")
    print(f"== phase {i} {k}{ended}: {b - a:.0f} s (cut {TRIM:.0f} s), load1 {h['load']:.1f}, hold RSS peak {h['hold']:.1f} GB, biggest other-thread process {h['amb']:.1f} GB, MemAvailable min {h['mem_min']:.1f} GB")
    print(f"   host: idle {c['idle']:.1f}% steal {c['steal']:.1f}% nice {c['nice']:.1f}% user {c['user']:.1f}% sys {c['system']:.1f}% iowait {c['iowait']:.1f}% soft {c['softirq']:.1f}% | "
          f"PSI cpu some {p['cpu.some']:.1f}% / mem some {p['memory.some']:.2f}% full {p['memory.full']:.2f}% / io some {p['io.some']:.1f}% full {p['io.full']:.1f}% | "
          f"pgfault {v.get('pgfault', 0) / 1e3:.0f}k/s majfault {v.get('pgmajfault', 0):.0f}/s pgscan {(v.get('pgscan_kswapd', 0) + v.get('pgscan_direct', 0)) / 1e3:.1f}k/s "
          f"direct {v.get('pgscan_direct', 0):.0f}/s compact_stall {v.get('compact_stall', 0):.1f}/s swap {v.get('pswpout', 0):.0f}/s")
    for cp in copies:
        q, qb = qrows(cp, t0, t1), qrows(cp, t0, t1, ("book",))
        fr = frames_between(cp, t0, t1)
        th = thread(cp, t0, t1)
        line = f"   {cp}: " + (f"msgs/s {fr / (t1 - t0):.0f}; " if fr else "")
        line += (f"queue_lag_p99 all-shards median {med(q):.1f} p90 {pct(q, .9):.1f} p99 {pct(q, .99):.1f} max {max(q) if q else float('nan'):.1f} (n {len(q)}), "
                 f"book median {med(qb):.1f} p90 {pct(qb, .9):.1f}, rows>30ms {100.0 * sum(1 for x in q if x > 30) / max(1, len(q)):.0f}%")
        print(line)
        if th:
            print(f"        thread: on-CPU {th['run']:.1f}%, runq wait {th['wait']:.1f} ms/s, wakeups {th['slices']:.0f}/s, minflt {th['minflt']:.0f}/s majflt {th['majflt']:.1f}/s")
        tot, pc = perf_agg(cp, t0, t1)
        if tot and fr:
            cyc, ins, tc = tot.get("cycles", 0), tot.get("instructions", 0), tot.get("task-clock", 0)
            full = sum(1 for aa, bb, _ in PERF[cp] if aa >= t0 and bb <= t1) * 60.0   # s covered by whole 60 s intervals
            fr_c = fr * full / (t1 - t0)
            print(f"        perf (covered {full:.0f} s): cycles/frame {cyc / fr_c:.0f}, instr/frame {ins / fr_c:.0f}, IPC {ins / max(cyc, 1):.2f}, GHz {cyc / max(tc * 1e6, 1):.2f}, "
                  f"LLC-ref/frame {tot.get('cache-references', 0) / fr_c:.1f}, LLC-miss/frame {tot.get('cache-misses', 0) / fr_c:.2f} ({100 * tot.get('cache-misses', 0) / max(tot.get('cache-references', 1), 1):.1f}% of refs; "
                  f"{1000 * tot.get('cache-misses', 0) / max(ins, 1):.3f}/k-instr), L1d-miss/frame {tot.get('L1-dcache-load-misses', 0) / fr_c:.1f}, "
                  f"page-faults {tot.get('page-faults', 0) / full:.0f}/s, ctx-sw {tot.get('context-switches', 0) / full:.0f}/s, migr {tot.get('cpu-migrations', 0) / full:.1f}/s; "
                  f"counted {min(pc.get(e, 100) for e in ('cycles', 'instructions', 'cache-references', 'cache-misses', 'L1-dcache-load-misses')):.0f}%")
    live = []
    for ex in LIVE:
        parts = []
        for sh in ("book", "trade", "mark"):
            r = qrows(ex, t0, t1, (sh,))
            if r:
                parts.append(f"{sh} {med(r):.1f}/{pct(r, .9):.1f}")
        live.append(f"{ex}: " + ", ".join(parts))
    print("   live queue_lag_p99 median/p90: " + " | ".join(live))
    print()

# ---- per-minute table and paired analysis
minutes = []
t = T_BEG
while STEP and t + STEP <= T_END + 1:
    e = t + STEP
    kind = next((k for i, k, a, b, ab in ph if a <= t and e <= b + 1), "-")
    h = host(t, e) or {}
    row = dict(t=t, k=kind, h=h, live=med(qrows("bingx", t, e, ("book",))),
               live_all=med(qrows("bingx", t, e)), okx=med(qrows("okx", t, e, ("book",))), bybit=med(qrows("bybit", t, e, ("book",))))
    for cp in copies:
        row[cp] = med(qrows(cp, t, e))
        fr = frames_between(cp, t, e)
        row[cp + ".fr"] = fr / STEP if fr else float("nan")
        pf = next((ev for aa, bb, ev in PERF.get(cp, []) if abs(aa - t) < 30 and abs(bb - e) < 30), None)
        if pf and fr:
            row[cp + ".cpf"] = pf["cycles"][0] / fr * STEP / 60.0 if "cycles" in pf else float("nan")
            row[cp + ".lmf"] = pf["cache-misses"][0] / fr * STEP / 60.0 if "cache-misses" in pf else float("nan")
        else:
            row[cp + ".cpf"] = row[cp + ".lmf"] = float("nan")
    minutes.append(row)
    t = e
if STEP:
    print(f"== per {STEP} s (queue_lag_p99 median of all shards, ms; cyc/frame in k; LLC-miss per frame)")
    print("  UTC   ph   " + "".join(f"{c + ' ql':>8}{c + ' kcpf':>9}{c + ' lmf':>8}{c + ' msg/s':>10}" for c in copies) + "  liveBXbook  OKXbook BYbook   idle  cpuP  memP pgf/k  holdG  ambG")
    for r in minutes:
        h = r["h"]
        if not h:
            continue
        print(f"  {datetime.datetime.fromtimestamp(r['t'], datetime.timezone.utc):%H:%M}  {r['k']:>3}  "
              + "".join(f"{r[c]:8.1f}{r[c + '.cpf'] / 1e3:9.1f}{r[c + '.lmf']:8.2f}{r[c + '.fr']:10.0f}" for c in copies)
              + f"  {r['live']:9.1f} {r['okx']:8.1f} {r['bybit']:6.1f}  {h['c']['idle']:5.1f} {h['p']['cpu.some']:5.1f} {h['p']['memory.some']:5.2f} {h['v'].get('pgfault', 0) / 1e3:5.0f} {h['hold']:6.1f} {h['amb']:5.1f}")
    print()
if len(copies) == 2:
    print("== paired OLD − NEW per minute (queue_lag median of all shards; positive = NEW lower), by phase kind and by ambient level (live BingX book median that minute)")
    ok = [r for r in minutes if r["k"] in ("Q", "L", "B", "C") and not math.isnan(r["OLD"]) and not math.isnan(r["NEW"])]

    def rep(lab, sel):
        d = [r["OLD"] - r["NEW"] for r in sel]
        if len(d) < 3:
            return
        print(f"  {lab}: n {len(d)} min, OLD median {med([r['OLD'] for r in sel]):.1f} NEW median {med([r['NEW'] for r in sel]):.1f}; "
              f"diff mean {statistics.mean(d):+.2f} median {med(d):+.2f} ms, NEW lower in {100.0 * sum(1 for x in d if x > 0) / len(d):.0f}% of minutes (sign test p {signp(d):.3f}); "
              f"OLD/NEW ratio of medians {med([r['OLD'] for r in sel]) / max(med([r['NEW'] for r in sel]), 1e-9):.2f}")

    for kk in ("Q", "L", "B", "C"):
        rep(f"phase {kk}", [r for r in ok if r["k"] == kk])
    rep("all phases", ok)
    lv = sorted(r["live"] for r in ok if not math.isnan(r["live"]))
    if lv:
        cuts = [lv[len(lv) // 3], lv[2 * len(lv) // 3]]
        for lab, lo, hi in (("ambient low (live BX book ≤ %.1f ms)" % cuts[0], -1, cuts[0]), ("ambient mid (%.1f–%.1f)" % tuple(cuts), cuts[0], cuts[1]), ("ambient high (> %.1f ms)" % cuts[1], cuts[1], 1e9)):
            rep(lab, [r for r in ok if lo < r["live"] <= hi])
    for lab, lo, hi in (("minutes with PSI cpu some < 20 %", -1, 20), ("PSI cpu some 20–40 %", 20, 40), ("PSI cpu some ≥ 40 %", 40, 1e9)):
        rep(lab, [r for r in ok if lo <= r["h"]["p"]["cpu.some"] < hi])
    print()
    print("== sensitivity: median of per-minute queue_lag by phase kind (same session; Q = nothing of mine, L = hold_scan+hold_marks, B = memhog bw, C = memhog chase)")
    for cp in copies + ["live"]:
        key = cp if cp != "live" else "live_all"
        qv = {kk: [r[key] for r in minutes if r["k"] == kk and not math.isnan(r[key])] for kk in ("Q", "L", "B", "C")}
        base = med(qv["Q"])
        print(f"  {cp}: " + "; ".join(f"{kk} {med(v):.1f} ms (n {len(v)}, ×{med(v) / max(base, 1e-9):.2f})" for kk, v in qv.items() if v))
    print()
    print("== per-frame counters by phase kind (median of per-minute values; minutes with msgs/s < 1500 or an incident (live BingX book ≥ 100 ms) left out)")
    for kk in ("Q", "L", "B", "C"):
        sel = [r for r in minutes if r["k"] == kk and r["live"] < 100 and all(r[c + ".fr"] >= 1500 for c in copies) and not any(math.isnan(r[c + ".cpf"]) for c in copies)]
        if not sel:
            continue
        line = f"  {kk} (n {len(sel)} min): "
        for c in copies:
            line += f"{c}: cycles/frame {med([r[c + '.cpf'] for r in sel]) / 1e3:.1f}k, LLC-miss/frame {med([r[c + '.lmf'] for r in sel]):.0f}; "
        if len(copies) == 2:
            line += f"NEW/OLD cycles ×{med([r['NEW.cpf'] / r['OLD.cpf'] for r in sel]):.3f}, LLC-miss ×{med([r['NEW.lmf'] / r['OLD.lmf'] for r in sel]):.3f}"
        print(line)
