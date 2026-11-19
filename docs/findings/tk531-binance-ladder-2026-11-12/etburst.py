import sys, subprocess, json
# E-T (колонка 4) и rx-E (колонка 3) книги за минуту всплеска: 11.11 23:12:40–23:13:40 UTC
import calendar
t0 = calendar.timegm((2026, 11, 11, 23, 12, 40)) * 10**9
t1 = t0 + 60 * 10**9
def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p))]
res = {}
for sh in ["book02", "book00", "book04", "book05", "book07", "book01", "book03", "book06"]:
    f = f"/data/bnws/2026-11-11/{sh}-1794432290.tsv.zst"
    p = subprocess.Popen(["zstdcat", f], stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
    t = 0; lag = []; et = []; n = 0
    for line in p.stdout:
        c = line.split("\t", 4)
        t += int(c[1])
        if t < t0 or c[0] != "b":
            continue
        if t >= t1:
            break
        lag.append(int(c[2])); et.append(int(c[3])); n += 1
    p.kill()
    res[sh] = {"rows": n, "rxE_p50": pct(lag, .5), "rxE_p99": pct(lag, .99), "ET_p50": pct(et, .5), "ET_p99": pct(et, .99), "ET_max": max(et)}
json.dump(res, open("/root/tk531s/etburst.json", "w"), indent=0)
