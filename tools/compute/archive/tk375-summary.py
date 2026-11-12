#!/usr/bin/env python3
# TK-375: svodka vyqruzki Gate "obschie s Bybit" po log/dl.tsv + log/check.tsv (poslednyaya stroka na klyuch -- glavnaya).
# Zapusk na calc: python3 tk375-summary.py /srv/alpha-sda/tk372-gate [--csv out.csv]
import csv, os, sys, statistics, collections

root = sys.argv[1]
DAYS = ["2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21"]
units = []
for mk in ("fut", "spot"):
    for r in csv.DictReader(open("%s/list-%s-common.csv" % (root, mk))):
        if mk == "spot" and r["gate_trade_status"] != "tradable": continue
        units += [(mk, r["gate"], d) for d in DAYS]
dl, ck = {}, {}
for l in open(root + "/log/dl.tsv"):
    p = l.rstrip("\n").split("\t"); dl[(p[0], p[1], p[2])] = p
for l in open(root + "/log/check.tsv"):
    p = l.rstrip("\n").split("\t"); ck[(p[0], p[1], p[2])] = p
out = {}
for mk in ("fut", "spot"):
    u = [x for x in units if x[0] == mk]
    ok = [x for x in u if dl.get(x, [0, 0, 0, ""])[3] == "ok" and os.path.exists("%s/raw/%s/%s/%s.csv.gz" % (root, *x))]
    rows = sum(int(dl[x][4]) for x in ok); gz = sum(os.path.getsize("%s/raw/%s/%s/%s.csv.gz" % (root, *x)) for x in ok)
    gaps = [(int(dl[x][8] or 0), x) for x in ok if int(dl[x][8] or 0) > 0]
    fails = [x for x in u if x not in ok]
    print(mk, "coins", len({x[1] for x in u}), "units", len(u), "ok", len(ok), "fail/missing", len(fails), "rows", rows, "gz_MB %.1f" % (gz / 1e6),
          "gap_units", len(gaps), "gap_ids", sum(g for g, _ in gaps))
    print("  top gaps", sorted(gaps, reverse=True)[:5])
    print("  fails", fails[:10])
    for iv, ds in (("1m", DAYS[2:]), ("5m", DAYS[:2])):
        rel = []; nocheck = 0; zero = 0
        for x in ok:
            if x[2] not in ds: continue
            c = ck.get(x)
            if not c or c[5].startswith("fail") or c[5] == "": nocheck += 1; continue
            if float(c[4]) == 0 and float(c[3]) == 0: zero += 1
            rel.append((float(c[5]), x))
        a = sorted(abs(r) for r, _ in rel)
        pos = sum(1 for r, _ in rel if r > 0.001); neg = sum(1 for r, _ in rel if r < -0.001)
        q = lambda p: a[min(len(a) - 1, int(p * len(a)))] if a else float("nan")
        print("  %s %s: n %d no-check %d both-zero %d median|d| %.5f p95 %.5f max %.5f  >0.1%% %d (+%d/-%d) >1%% %d" % (
            mk, iv, len(rel), nocheck, zero, statistics.median(a) if a else float("nan"), q(0.95), a[-1] if a else float("nan"),
            sum(1 for v in a if v > 0.001), pos, neg, sum(1 for v in a if v > 0.01)))
        if mk == "fut" or neg: print("   worst", sorted(rel, key=lambda t: -abs(t[0]))[:4])
        out[(mk, iv)] = rel
if len(sys.argv) > 3 and sys.argv[2] == "--csv":
    with open(sys.argv[3], "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["market", "symbol", "date", "rows", "id_gaps", "trades_vs_candles_rel"])
        for x in units:
            d = dl.get(x); c = ck.get(x)
            if not d or d[3] != "ok": w.writerow([*x, "", "", ""]); continue
            w.writerow([*x, d[4], d[8], c[5] if c and not c[5].startswith("fail") else ""])
