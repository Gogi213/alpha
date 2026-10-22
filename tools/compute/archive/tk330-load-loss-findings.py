#!/usr/bin/env python3
"""TK-330: находки убытка (признак × квартиль × рынок × месяц) из готовых tk327-features-{spot,fut}-*.csv -> docs/registry/loss_findings.jsonl.
Без нового прохода (В-213). Меры и тариф — как tk329-pareto.py (спот S0: Σ$ (в) В-47; фьюч B1 половина А: Σ$ (а)).
Идемпотентно: строки партии TK-330/1 переписываются целиком по id, чужие партии не трогаются; повтор = тот же файл.
Правило отбора LF-1 (предложение, назначает Судья): docs/registry/rules.jsonl (protocol TK-330), метка status = кандидат|наблюдение.
Использование: tk330-load-loss-findings.py [--dry]   (из корня репозитория)"""
import bisect, csv, json, os, sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FD = os.path.join(ROOT, "docs", "findings"); RD = os.path.join(ROOT, "docs", "registry")
DATE = "2026-10-20"; BATCH = "TK-330/1"
FE = [("ret1_bps", "ret1"), ("ret5_bps", "ret5"), ("ret30_bps", "ret30"), ("regime_btc4h", "regime_btc4h"),
      ("wall_usd", "wall_usd"), ("flow_pct", "flow_pct"), ("strength_w20_pct", "strength_w20"),
      ("strength_w50_pct", "strength_w50"), ("sigma240", "sigma240")]
# правило LF-1 (предложение): группа «минусовая» в рынке, если Σ<0, лифт ≥ LIFT_MIN, месяцев с Σ<0 ≥ MONTHS_NEG_MIN из месяцев с n>0;
# кандидат — минусовая в ≥ MARKETS_MIN рынках (признак × номер квартиля одинаковы)
RULE = {"lift_min": 1.5, "months_neg_min": 6, "markets_min": 2}
MKT = {"spot": dict(run_id="R-20261020-001", cell_id="920fc7fc4f93d27a", metric="v", tariff="В-47 (maker 5.625 / taker 6.75 bps на ногу)",
                    src="/data/tk0313/out (S0, спот Bybit v171-spot, янв–сен)"),
       "fut": dict(run_id=None, cell_id=None, metric="a", tariff="фьюч-модель (а): net_bps rounds как есть",
                   src="/data/tk0289/f6v/out p18a6vv2_10000, B1 ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800, половина А (прогон TK-289 в runs.jsonl не заведён)")}


def load(mode):
    rows = []
    with open(os.path.join(FD, f"tk327-features-{mode}-{DATE}.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            q = float(r["qty"]); px = float(r["entry_px"]); net = float(r["net_bps"])
            stop = r["reason"] in ("stop", "deadline")
            gross = net + 1.26 + (3.15 if stop else 1.26)
            k = q * px / 1e4
            r["a"] = net * k
            r["v"] = (gross - 5.625 - (6.75 if stop else 5.625)) * k
            for c, _ in FE:
                r[c] = float(r[c]) if r[c] != "" else None
            rows.append(r)
    return rows


def qbin(rows, key, nb=4):
    vs = sorted(r[key] for r in rows if r[key] is not None)
    cuts = sorted({vs[int(len(vs) * i / nb)] for i in range(1, nb)})
    def f(r):
        if r[key] is None:
            return None
        return bisect.bisect_right(cuts, r[key])
    return f, cuts


def findings(mode):
    rows = load(mode); N = len(rows); m = MKT[mode]; M = m["metric"]
    months = sorted({r["mon"] for r in rows}); out = []
    for key, name in FE:
        f, cuts = qbin(rows, key)
        g = defaultdict(list)
        for r in rows:
            g[f(r)].append(r)
        sums = {k: sum(r[M] for r in x) for k, x in g.items()}
        T = sum(r[M] for r in rows) if mode == "spot" else sum(v for v in sums.values() if v < 0)
        for k in sorted(g, key=lambda x: (x is None, x)):
            x = g[k]; s = sums[k]
            bm = defaultdict(lambda: [0, 0.0])
            for r in x:
                bm[r["mon"]][0] += 1; bm[r["mon"]][1] += r[M]
            mm = [[mo, bm[mo][0], round(bm[mo][1], 2)] for mo in months if bm[mo][0]]
            lo = "-inf" if k in (None, 0) else cuts[k - 1]; hi = "+inf" if k is None or k == len(cuts) else cuts[k]
            tshare = len(x) / N
            lshare = s / T if s < 0 else 0.0
            out.append({"id": f"LF-{mode}-{name}-{'none' if k is None else 'Q%d' % (k + 1)}", "batch": BATCH, "market": mode,
                        "run_id": m["run_id"], "cell_id": m["cell_id"], "feature": name,
                        "group": "нет" if k is None else f"Q{k + 1}", "bounds": None if k is None else [lo, hi],
                        "n": len(x), "pct_trades": round(100 * tshare, 2), "sum_usd": round(s, 2), "pct_loss": round(100 * lshare, 2),
                        "lift": round(lshare / tshare, 3) if tshare else 0.0,
                        "months_neg": sum(1 for _, _, v in mm if v < 0), "months_n": len(mm), "months": mm,
                        "metric": M, "tariff": m["tariff"], "pct_loss_base": "Σ группы / Σ всех (спот)" if mode == "spot" else "Σ группы / Σ групп с Σ<0 (итог фьюча +, чистого минуса нет)",
                        "raw": f"docs/findings/tk327-features-{mode}-{DATE}.csv", "src": m["src"], "report": "docs/findings/tk329-pareto-2026-10-20.md",
                        "n_trials": 36})
    return out


def label(allf):
    ok = defaultdict(set)
    for d in allf:
        if d["group"] != "нет" and d["sum_usd"] < 0 and d["lift"] >= RULE["lift_min"] and d["months_neg"] >= RULE["months_neg_min"]:
            ok[(d["feature"], d["group"])].add(d["market"])
    for d in allf:
        d["status"] = "кандидат" if len(ok.get((d["feature"], d["group"]), ())) >= RULE["markets_min"] else "наблюдение"
        d["note"] = "кандидат, не правило: срез выбран по тем же данным; проверка — сито/титровка вне подбора"
    return allf


allf = label(findings("spot") + findings("fut"))
new = {d["id"]: d for d in allf}
path = os.path.join(RD, "loss_findings.jsonl")
old = []
if os.path.exists(path):
    old = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
keep = [d for d in old if d["batch"] != BATCH]
lines = [json.dumps(d, ensure_ascii=False) for d in keep + allf]
print(f"находок {len(allf)} (спот {sum(d['market']=='spot' for d in allf)}, фьюч {sum(d['market']=='fut' for d in allf)}); чужих партий {len(keep)}")
for d in allf:
    if d["status"] == "кандидат":
        print(" кандидат", d["market"], d["feature"], d["group"], "лифт", d["lift"], "мес<0", f"{d['months_neg']}/{d['months_n']}", "Σ", d["sum_usd"])
if "--dry" not in sys.argv:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    rp = os.path.join(RD, "rules.jsonl")
    have = {(d["protocol"], d["version"], d["key"]) for d in map(json.loads, filter(str.strip, open(rp, encoding="utf-8")))}
    with open(rp, "a", encoding="utf-8", newline="\n") as f:
        for k, v in RULE.items():
            if ("TK-330", "LF-1@proposal", k) not in have:
                f.write(json.dumps({"protocol": "TK-330", "version": "LF-1@proposal", "key": k, "value": str(v)}, ensure_ascii=False) + "\n")
