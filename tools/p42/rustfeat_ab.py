#!/usr/bin/env python3
"""TK-609: A/B alpha-feat v3 04714685 против кандидата v4 на тёплом складе: wa/S flow (332 эп., 139 монет), 4 потока, порядок ABBA...; ЦП (user+sys) и стена на прогон, md5 выхода."""
import hashlib, json, os, resource, subprocess, sys, time
R = "/data/p42-tk609"
bins = {"v3": f"{R}/bin/alpha-feat-v3-04714685", sys.argv[1]: sys.argv[2]}
names = list(bins)
reps = int(sys.argv[3]) if len(sys.argv) > 3 else 3
env = dict(os.environ, TK594_STORE="/data/p42-tk599/tick-store", TK594_STORE_CAP_GB="0", TK594_STORE_MIN_FREE_GB="0",
           TK594_TRADES="/data/p42-tk599/wa/aggtrades", TK594_METRICS="/data/p42-tk599/metrics-all")
order = []
for k in range(reps):
    order += names if k % 2 == 0 else names[::-1]
out = f"{R}/perf-v3/ab-out.csv"
res = []
for n in order:
    c0 = resource.getrusage(resource.RUSAGE_CHILDREN); t0 = time.time(); l0 = os.getloadavg()[0]
    r = subprocess.run([bins[n], "flow", "--eps", f"{R}/perf-v3/eps.csv", "--out", out, "--base-date", "2026-06-17", "--threads", "4"],
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (c1.ru_utime + c1.ru_stime) - (c0.ru_utime + c0.ru_stime)
    md5 = hashlib.md5(open(out, "rb").read()).hexdigest()[:12]
    res.append({"bin": n, "rc": r.returncode, "wall": round(time.time() - t0, 1), "cpu": round(cpu, 1), "load0": round(l0, 1), "md5": md5})
    print(json.dumps(res[-1]), flush=True)
for n in names:
    c = [x["cpu"] for x in res if x["bin"] == n]; w = [x["wall"] for x in res if x["bin"] == n]
    print(n, "cpu mean", round(sum(c) / len(c), 1), "wall mean", round(sum(w) / len(w), 1), "md5", {x["md5"] for x in res if x["bin"] == n})
