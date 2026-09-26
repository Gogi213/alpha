"""Судья, П-02 Г-33 (2026-09-26): сосредоточенность корзины «цепочка ≥ 2» по монетам и разница без каждой из
крупнейших монет (чтение, не испытание). Компакт-сутки data/p02/g33/{e-aug,e-archive,root}/*.csv.gz, без TRX."""
import csv, glob, gzip, os
from collections import defaultdict

MONTHS = {"2026-08": ["e-aug"], "2026-09": ["e-archive", "root"]}
for month, dirs in MONTHS.items():
    s = defaultdict(lambda: [0, 0, 0, 0])  # symbol -> [base_b, base_n, ch_b, ch_n]
    for d in dirs:
        for f in sorted(glob.glob(os.path.join("data/p02/g33", d, "*.csv.gz"))):
            if not os.path.basename(f).startswith(month):
                continue
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r["symbol"] == "TRXUSDT":
                        continue
                    L = int(r["chain_len"])
                    b = r["ended_by_death"] == "false"
                    a = s[r["symbol"]]
                    if L == 0:
                        a[0] += b; a[1] += 1
                    elif L >= 2:
                        a[2] += b; a[3] += 1
    tot = [sum(v[i] for v in s.values()) for i in range(4)]
    diff = lambda t: (t[0] / t[1] - t[2] / t[3]) * 100
    top = sorted(s, key=lambda k: -s[k][3])
    print(f"== {month}: разница {diff(tot):+.2f} п.п.; касаний с цепочкой {tot[3]}, монет с цепочкой {sum(1 for v in s.values() if v[3])}")
    print("   доля цепочек у 1 / 3 / 5 крупнейших монет: " + " / ".join(
        f"{sum(s[k][3] for k in top[:m]) / tot[3] * 100:.0f} %" for m in (1, 3, 5)))
    for k in top[:6]:
        v = s[k]
        rest = [tot[i] - v[i] for i in range(4)]
        print(f"   {k:12s} цепочек {v[3]:5d}  bounced с цепочкой {v[2] / v[3] * 100:5.1f} % / база монеты {v[0] / v[1] * 100:5.1f} %  | без неё разница {diff(rest):+.2f}")
    rest = [tot[i] - sum(s[k][i] for k in top[:3]) for i in range(4)]
    print(f"   без 3 крупнейших: {diff(rest):+.2f} п.п. (цепочек {rest[3]})")
    # внутри монеты: средневзвешенная по цепочкам разница (база монеты − цепочка монеты)
    w = sum(v[3] for v in s.values() if v[1] and v[3])
    within = sum(v[3] * (v[0] / v[1] - v[2] / v[3]) for v in s.values() if v[1] and v[3]) / w * 100
    print(f"   внутри монеты (база монеты − цепочка, вес — цепочки): {within:+.2f} п.п.")
