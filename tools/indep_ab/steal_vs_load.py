"""Is the hour-boundary %steal driven by our own CPU load? For every hour in /var/log/sysstat: steal in the 10-s bucket that contains :00:00,
mean busy% (user+nice+system) over the 5 minutes before, and the rank correlation. Also the same for the 5-min marks (:05, :10 ...)."""
import subprocess, re, os, datetime, statistics, collections
env = {"LC_ALL": "C"}
buckets = []  # (epoch, busy, steal)
for f in sorted(os.listdir("/var/log/sysstat")):
    if not re.match(r"sa\d\d$", f):
        continue
    out = subprocess.run(["sar", "-f", "/var/log/sysstat/" + f, "-u"], capture_output=True, text=True, env=env).stdout
    m = re.search(r"(\d\d)/(\d\d)/(\d\d)", out)
    if not m:
        continue
    mo, d, y = map(int, m.groups())
    for l in out.splitlines():
        p = l.split()
        if len(p) == 8 and p[1] == "all" and re.match(r"\d\d:\d\d:\d\d$", p[0]):
            h, mi, s = map(int, p[0].split(":"))
            t = int(datetime.datetime(2000 + y, mo, d, h, mi, s, tzinfo=datetime.timezone.utc).timestamp())
            buckets.append((t, float(p[2]) + float(p[3]) + float(p[4]), float(p[6])))
buckets.sort()
by_t = {t: (b, s) for t, b, s in buckets}
def rank(v):
    o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
    for k, i in enumerate(o):
        r[i] = k
    return r
def spearman(x, y):
    rx, ry = rank(x), rank(y); n = len(x); mx, my = sum(rx) / n, sum(ry) / n
    c = sum((a - mx) * (b - my) for a, b in zip(rx, ry)); sx = sum((a - mx) ** 2 for a in rx) ** .5; sy = sum((b - my) ** 2 for b in ry) ** .5
    return c / (sx * sy)
def at_mark(step):
    rows = []
    for t, busy, steal in buckets:
        # bucket label t covers (t-10, t]; contains the mark when 0 <= t % step <= 10 (first bucket after the mark)
        if 0 <= t % step <= 10 and (t - 300) in by_t or 0 <= t % step <= 10:
            before = [by_t[x][0] for x in range(t - 300, t - 10, 10) if x in by_t]
            if len(before) >= 20:
                rows.append((statistics.mean(before), steal))
    return rows
for name, step in (("hour :00", 3600), ("5-min marks excluding :00", 300)):
    rows = at_mark(step)
    if name.startswith("5-min"):
        rows = [r for r in rows]  # includes :00 too; reported separately below
    x = [a for a, b in rows]; y = [b for a, b in rows]
    print(f"{name}: n={len(rows)} busy_before mean {statistics.mean(x):.1f}% | steal at mark mean {statistics.mean(y):.1f}% median {statistics.median(y):.1f}% | spearman(busy_before, steal) = {spearman(x, y):.2f}")
allsteal = [s for t, b, s in buckets if not (0 <= t % 300 <= 10)]
print(f"steal in all other buckets: n={len(allsteal)} mean {statistics.mean(allsteal):.2f}% median {statistics.median(allsteal):.2f}% p99 {sorted(allsteal)[int(len(allsteal)*0.99)]:.1f}%")
hrs = sorted(set(int(t // 3600) for t, b, s in buckets))
low = [(a, b) for a, b in at_mark(3600)]
q = sorted(a for a, b in low)
lo_third = [b for a, b in low if a <= q[len(q) // 3]]; hi_third = [b for a, b in low if a >= q[2 * len(q) // 3]]
print(f"hour-mark steal when our busy% before is in the lowest third (<= {q[len(q)//3]:.0f}%): mean {statistics.mean(lo_third):.1f}% (n={len(lo_third)}); highest third (>= {q[2*len(q)//3]:.0f}%): mean {statistics.mean(hi_third):.1f}% (n={len(hi_third)})")
