#!/usr/bin/env python3
"""TK-302: F2 П-15 по месяцам янв–сен, половина А — ΔSR_норм и Δ бп на сделку к B1 (позиция 500 $, как tk237-sieve --monthly).
Источники — те же closes с монетой, что у сита TK-303 (движок не гонялся, В-213): Г-102 A/B, Г-146, Г-72, Г-108 — /data/launch/out/L-P15F2{,B}/psim/closes-sym-p14f1-cap0-<мес>.json
(ключ 'набор@форма' из cell_line результатов), Г-134 R1 — /data/tk0303/sieveR1/psim/closes-sym-r1x-cap0-ММ.json (ключ = хеш, база 'B1' того же дерева).
База B1 и вырез монето-суток — как в tk303-sieve.py. ΔSR_норм месяца = SR(сутки месяца, клетка) − SR(сутки месяца, база); дни без сделок = 0.
  python tk302-monthly.py <каталог с add-cells/results/sieve.jsonl> <out.csv>"""
import csv, datetime, hashlib, json, os, sys
import numpy as np

dep, out = sys.argv[1], sys.argv[2]
B1 = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
EQ = "ladder3x0..0.0409sw10-pct2-tr1x1-14400-ttl1800"
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
load = lambda p: json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
rows_of = lambda d, k, mo: d.get(k, {}).get(mo, {}).get("0", [])
jl = lambda f: [json.loads(l) for l in open(os.path.join(dep, f), encoding="utf-8") if l.strip()]

cells = {c["id"]: c for c in jl("add-cells.jsonl")}
sieve = {s["cell_hash"]: s for s in jl("add-sieve.jsonl")}
line = {}   # (хеш, месяц) -> ключ 'набор@форма'
for r in jl("add-results.jsonl"):
    cl = r.get("ext", {}).get("cell_line")
    if cl: form, _, st = cl.partition(" "); line[(r["cell_id"], r["month"])] = f"{st}@{form}"
MO = [f"2026-{m:02d}" for m in range(1, 10)]


def agg(rows, excl):
    d = {}
    for ms, usd, sym in rows:
        if not isA(sym) or (excl and (sym, UTC(ms)) in excl): continue
        d.setdefault(UTC(ms), []).append(usd)
    return d   # дата -> [usd]


def month_stats(per_mo, days_in):   # per_mo: {дата: [usd]} за месяц -> (n, usd, SR по суткам месяца)
    n = sum(len(v) for v in per_mo.values()); usd = sum(sum(v) for v in per_mo.values())
    x = np.array([sum(per_mo.get(d, [0.0])) for d in days_in])
    sd = x.std(ddof=1)
    return n, usd, (x.mean() / max(sd, 1e-12))


base = {}; cell = {}
SYM = ["/data/launch/out/L-P15F2/psim", "/data/launch/out/L-P15F2B/psim"]
for mo in MO:
    mm = mo[-2:]; y, m = 2026, int(mm)
    days_in = [datetime.date(y, m, 1) + datetime.timedelta(i) for i in range((datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.date(y, m, 1)).days)]
    b1d = load(f"/data/p12r3a/closes-sym-b1-cap0-{mo}.json")
    f1 = load(f"/data/tk0196/psim/closes-sym-f1-cap0-{mo}.json")
    for k, v in load(f"/data/p12r3a/closes-sym-r2b-cap0-{mo}.json").items(): f1.setdefault(k, v)
    for d in SYM:
        for k, v in load(f"{d}/closes-sym-p14f1-cap0-{mo}.json").items(): f1.setdefault(k, v)
    r1 = load(f"/data/tk0303/sieveR1/psim/closes-sym-r1x-cap0-{mm}.json")
    cd = lambda c: {(sym, UTC(ms)) for ms, u, sym in c}
    eqk = next((k for k in f1 if k.startswith("t-bid-btc4h-q1@") and k.endswith(EQ)), None)
    excl = (cd(rows_of(f1, eqk, mo)) - cd(rows_of(b1d, B1, mo))) if eqk else set()
    base[("b1", mo)] = month_stats(agg(rows_of(b1d, B1, mo), excl), days_in)
    base[("r1", mo)] = month_stats(agg(rows_of(r1, "B1", mo), None), days_in)
    for h, c in cells.items():
        if c["set_name"].startswith("g134/"): cell[(h, mo)] = month_stats(agg(rows_of(r1, h, mo), None), days_in)
        elif (h, mo) in line: cell[(h, mo)] = month_stats(agg(rows_of(f1, line[(h, mo)], mo), excl), days_in)
        else: cell[(h, mo)] = (0, 0.0, 0.0)
    sys.stderr.write(f"{mo} ok\n")

hd = ["hyp", "hash", "label", "eligible", "plateau", "edge", "trades_A"]
for mo in MO: hd += [f"{mo}_n", f"{mo}_usd", f"{mo}_dsr", f"{mo}_dbp"]
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(hd)
    for h, c in cells.items():
        hyp = c["set_name"].split("/")[1]; s = sieve.get(h, {})
        bk = "r1" if c["set_name"].startswith("g134/") else "b1"
        lab = line.get((h, MO[0]), "R1").split("@")[-1] if bk == "b1" else "R1"
        r = [hyp, h, lab, s.get("eligible", ""), s.get("plateau", ""), s.get("edge", ""), sum(cell[(h, mo)][0] for mo in MO)]
        for mo in MO:
            n, usd, srx = cell[(h, mo)]; bn, bu, bsr = base[(bk, mo)]
            dbp = round((usd / n - bu / bn) / 500 * 1e4, 1) if n and bn else ""
            r += [n, round(usd, 2), round(srx - bsr, 4) if n else "", dbp]
        w.writerow(r)
    for bk in ("b1", "r1"):
        w.writerow(["BASE", bk, "", "", "", "", sum(base[(bk, mo)][0] for mo in MO)] + [v for mo in MO for v in (base[(bk, mo)][0], round(base[(bk, mo)][1], 2), round(base[(bk, mo)][2], 4), "")])
print("cells", len(cells))
