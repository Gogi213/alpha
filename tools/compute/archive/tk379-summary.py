#!/usr/bin/env python3
# TK-379: svodka vyqruzki Gate po log/dl.tsv + log/check.tsv (poslednyaya stroka na klyuch -- glavnaya), po spiskam monet.
# Zapusk na calc: python3 tk379-summary.py <root> --lists -d30[,-common,""] --days 2026-10-15,...,2026-10-22 [--csv out.csv]
import csv, os, sys, statistics, argparse

ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--lists", default="-d30"); ap.add_argument("--days", required=True)
ap.add_argument("--csv", default=""); ap.add_argument("--tag", default="")
a = ap.parse_args(); root = a.root; DAYS = a.days.split(","); D5 = {"2026-10-15", "2026-10-16"}
units, seen = [], set()
for mk in ("fut", "spot"):
    for L in a.lists.split(","):
        p = "%s/list-%s%s.csv" % (root, mk, L)
        if not os.path.exists(p): continue
        for r in csv.DictReader(open(p)):
            if mk == "spot" and r["gate_trade_status"] != "tradable": continue
            for d in DAYS:
                if (mk, r["gate"], d) not in seen: seen.add((mk, r["gate"], d)); units.append((mk, r["gate"], d))
dl, ck = {}, {}
for l in open(root + "/log/dl.tsv"):
    p = l.rstrip("\n").split("\t"); dl[(p[0], p[1], p[2])] = p
for l in open(root + "/log/check.tsv"):
    p = l.rstrip("\n").split("\t")
    if len(p) >= 6: ck[(p[0], p[1], p[2])] = p
gz_of = lambda x: "%s/raw/%s/%s/%s.csv.gz" % (root, *x)
for mk in ("fut", "spot"):
    u = [x for x in units if x[0] == mk]
    ok = [x for x in u if dl.get(x, [0, 0, 0, ""])[3] == "ok" and os.path.exists(gz_of(x))]
    rows = sum(int(dl[x][4]) for x in ok); gz = sum(os.path.getsize(gz_of(x)) for x in ok)
    gaps = [(int(dl[x][8] or 0), x) for x in ok if int(dl[x][8] or 0) > 0]
    fails = [x for x in u if x not in ok]
    print(mk, "coins", len({x[1] for x in u}), "units", len(u), "ok", len(ok), "fail/missing", len(fails), "rows", rows, "gz_MB %.1f" % (gz / 1e6),
          "gap_units", len(gaps), "gap_ids", sum(g for g, _ in gaps))
    print("  top gaps", sorted(gaps, reverse=True)[:5]); print("  fails", fails[:10])
    for iv, ds in (("1m", [d for d in DAYS if d not in D5]), ("5m", [d for d in DAYS if d in D5])):
        rel = []; nocheck = []; zero = 0
        for x in ok:
            if x[2] not in ds: continue
            c = ck.get(x)
            if not c or c[5].startswith("fail") or c[5] == "": nocheck.append((x, c[5][:60] if c else "no-row")); continue
            if float(c[4]) == 0 and float(c[3]) == 0: zero += 1
            rel.append((float(c[5]), x))
        s = sorted(abs(r) for r, _ in rel)
        pos = sum(1 for r, _ in rel if r > 0.001); neg = sum(1 for r, _ in rel if r < -0.001)
        q = lambda p: s[min(len(s) - 1, int(p * len(s)))] if s else float("nan")
        print("  %s %s: n %d no-check %d both-zero %d median|d| %.5f p95 %.5f max %.5f  >0.1%% %d (+%d/-%d) >1%% %d" % (
            mk, iv, len(rel), len(nocheck), zero, statistics.median(s) if s else float("nan"), q(0.95), s[-1] if s else float("nan"),
            sum(1 for v in s if v > 0.001), pos, neg, sum(1 for v in s if v > 0.01)))
        if nocheck: print("   no-check reasons", {r: sum(1 for _, y in nocheck if y == r) for r in {y for _, y in nocheck}})
        print("   worst", sorted(rel, key=lambda t: -abs(t[0]))[:4])
if a.csv:
    with open(a.csv, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["market", "symbol", "date", "rows", "id_gaps", "trades_vs_candles_rel"])
        for x in units:
            d = dl.get(x); c = ck.get(x)
            if not d or d[3] != "ok": w.writerow([*x, "", "", ""]); continue
            w.writerow([*x, d[4], d[8], c[5] if c and not c[5].startswith("fail") else ""])
