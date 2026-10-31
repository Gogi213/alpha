"""TK-461 Q-3: net при худшем/лучшем конце издержек по ячейке и году из q3-out (TK-471, --legs 1).
python tk461_q3.py <q3-out> <out.csv>
Строка входа: ... c,d,b,k,d2,bt,bm (суммы по сделкам за сутки). По варианту и году: net_worst=sum(bt)/sum(c),
net_best=sum(bm)/sum(c), base=sum(b)/sum(c); SE worst — кластеры по суткам (все монеты суток вместе), отношение сумм."""
import csv, glob, gzip, math, sys
from collections import defaultdict
from datetime import date, timedelta

root, out = sys.argv[1], sys.argv[2]
KEY = ("tf", "ev", "dir", "side", "stop", "take", "par", "be_r", "ot", "otp")
acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # (key,year,day) -> c,bt,bm,b
for f in sorted(glob.glob(root + "/*/*.csv.gz")):
    with gzip.open(f, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            day = int(r["day"])
            a = acc[(tuple(r[k] for k in KEY), (date(1970, 1, 1) + timedelta(days=day)).year, day)]
            a[0] += float(r["c"]); a[1] += float(r["bt"]); a[2] += float(r["bm"]); a[3] += float(r["b"])
yr = defaultdict(lambda: [0.0] * 4 + [0.0] * 4)  # c,bt,bm,b, Σbt², Σbt·c, Σc², ndays
for (key, y, day), (c, bt, bm, b) in acc.items():
    s = yr[(key, y)]
    s[0] += c; s[1] += bt; s[2] += bm; s[3] += b; s[4] += bt * bt; s[5] += bt * c; s[6] += c * c; s[7] += 1
with open(out, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(KEY + ("year", "n", "days", "net_worst", "net_best", "base", "se_worst", "lo_worst"))
    for (key, y), s in sorted(yr.items()):
        c = s[0]
        if c <= 0:
            continue
        r = s[1] / c
        var = max(s[4] - 2 * r * s[5] + r * r * s[6], 0.0)
        se = math.sqrt(var) / c
        w.writerow(key + (y, int(c), int(s[7]), f"{r:.4f}", f"{s[2]/c:.4f}", f"{s[3]/c:.4f}", f"{se:.4f}", f"{r-1.96*se:.4f}"))
