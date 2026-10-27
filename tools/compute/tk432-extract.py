"""TK-432: свечи 1 м окна [t_start-24ч, t_end+24ч] для списка кандидатов -> один TSV в stdout.
Использование: tk432-extract.py pick.csv  (читает /srv/alpha-sda/candles-1m и -aug)"""
import csv, gzip, os, sys
R = ["/srv/alpha-sda/candles-1m-aug", "/srv/alpha-sda/candles-1m"]
print("id\tt_ms\to\th\tl\tc\tq")
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    a = int(r["t_start_ms"]) - 86400000
    b = int(r["t_end_ms"]) + 86400000
    d = {}
    for root in R:
        fn = "%s/%s-%s/%s.csv.gz" % (root, r["биржа"], r["рынок"], r["монета"])
        if os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                next(f)
                for line in f:
                    p = line.split(",")
                    t = int(p[0])
                    if a <= t <= b:
                        d[t] = p
    for t in sorted(d):
        p = d[t]
        print("\t".join([r["id"], str(t), p[1], p[2], p[3], p[4], p[6]]))
    sys.stderr.write("%s %s %d\n" % (r["id"], r["монета"], len(d)))
