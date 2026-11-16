#!/usr/bin/env python3
"""Per-second host view around a full hour from the 1 Hz sampler logs (/data/indep-ab/host): vmstat (us sy id wa st), mpstat
(all + worst single CPU steal / softirq), PSI cpu 'some' stall delta, iostat vda, pidstat top CPU / disk writers.
Usage: hourwin.py 2026-11-16T17 [--before 5 --after 40]"""
import argparse, collections, datetime, re, sys

D = "/data/indep-ab/host"
SKIP = ("pidstat", "vmstat", "iostat", "mpstat", "hostsample", "perf record", "hourwin")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hour")
    ap.add_argument("--before", type=int, default=5)
    ap.add_argument("--after", type=int, default=40)
    a = ap.parse_args()
    H = datetime.datetime.strptime(a.hour, "%Y-%m-%dT%H").replace(tzinfo=datetime.timezone.utc)
    day = H.strftime("%Y-%m-%d")
    lo, hi = H - datetime.timedelta(seconds=a.before), H + datetime.timedelta(seconds=a.after)

    def hms(t):
        return t.strftime("%H:%M:%S")

    want = {hms(lo + datetime.timedelta(seconds=i)) for i in range(int((hi - lo).total_seconds()) + 1)}
    vm = {}
    for l in open(f"{D}/vmstat.log"):
        p = l.split()
        if len(p) >= 19 and p[-2] == day and p[-1] in want:
            vm[p[-1]] = dict(us=p[12], sy=p[13], id=p[14], wa=p[15], st=p[16], cs=p[11], intr=p[10], r=p[0])
    mp = collections.defaultdict(dict)
    for l in open(f"{D}/mpstat.log"):
        p = l.split()
        if len(p) == 12 and p[0] in want and p[1] != "CPU":
            mp[p[0]][p[1]] = dict(steal=float(p[8]), soft=float(p[7]), idle=float(p[11]))
    psi = {}
    prev = None
    for l in open(f"{D}/psi.log"):
        m = re.match(r"([\d.]+) [\d.]+ \| some avg10=\S+ avg60=\S+ avg300=\S+ total=(\d+)", l)
        if not m:
            continue
        t = datetime.datetime.fromtimestamp(float(m.group(1)), datetime.timezone.utc)
        tot = int(m.group(2))
        if prev is not None:
            psi[hms(t)] = round((tot - prev) / 1000, 1)  # ms of cpu 'some' stall since previous sample
        prev = tot
    io = {}
    cur = None
    for l in open(f"{D}/iostat.log"):
        m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\+0000", l)
        if m:
            cur = m.group(1)[11:]
        elif l.startswith("vda") and cur in want:
            p = l.split()
            io[cur] = dict(w_s=p[7], wkB=p[8], w_await=p[11], util=p[-1])
    pid = collections.defaultdict(list)
    for l in open(f"{D}/pidstat.log"):
        p = l.split(None, 12)
        if len(p) == 13 and p[0] in want and not any(s in p[12] for s in SKIP):
            pid[p[0]].append((float(p[7]), float(p[10]), p[12].strip()[:60], p[2]))
    print("time | vmstat us sy id wa st cs | mpstat all steal soft (worst cpu steal) | psi_cpu_some ms | vda w/s wkB/s w_await util | top cpu% procs | top disk wkB/s")
    for t in sorted(want):
        v, m = vm.get(t), mp.get(t, {})
        allv = m.get("all", {})
        worst = max((x["steal"] for c, x in m.items() if c != "all"), default=0)
        i = io.get(t)
        procs = sorted(pid.get(t, []), reverse=True)[:3]
        disk = sorted(pid.get(t, []), key=lambda x: -x[1])[:1]
        print(
            f"{t} | {v['us'] if v else '-':>3} {v['sy'] if v else '-':>3} {v['id'] if v else '-':>3} {v['wa'] if v else '-':>2} {v['st'] if v else '-':>3} {v['cs'] if v else '-':>6}"
            f" | {allv.get('steal', '-'):>6} {allv.get('soft', '-'):>6} ({worst:>5})"
            f" | {psi.get(t, '-'):>6}"
            f" | {(i['w_s'] + ' ' + i['wkB'] + ' ' + i['w_await'] + ' ' + i['util']) if i else '-'}"
            f" | {'; '.join(f'{c:.0f}% {n} [{p}]' for c, w, n, p in procs if c >= 2)}"
            f" | {'; '.join(f'{w:.0f} {n}' for c, w, n, p in disk if w >= 500)}"
        )


if __name__ == "__main__":
    main()
