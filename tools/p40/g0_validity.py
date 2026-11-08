"""П-40 G0 (TK-496 возврат п.1,3,4): валидность (биржа, контракт, час) и S_max по контрактам из сырых выходов проходов.
Вход: каталог с full.jsonl (OKX/Bitget/Aster/HL), binance_full.jsonl (если есть), bybit*.jsonl, gate*.jsonl.
Выход: validity.csv.gz, smax.csv, summary.json, symbols.json. Правила: кросс-доля часа > 1 % (решение Судьи, Gate; по всем биржам одинаково),
prev_gaps > 0 (Binance/Aster) = час помечен «разрыв» целиком (без перевода во время — неполнота, в отчёте), файл пропущен/ошибка/checked = 0.
Исходов не касается. Запуск: python3 g0_validity.py <вход_каталог> <выход_каталог>"""
import sys, os, json, glob, gzip, csv, collections
import numpy as np

CROSS_MAX = 0.01
inp, outd = sys.argv[1], sys.argv[2]
os.makedirs(outd, exist_ok=True)
rows = []                        # ex, contract, date, hour, status, crossed_share, prev_gaps, checked
age = collections.defaultdict(list)   # (ex, contract) -> [p99 мс]
symbols = collections.defaultdict(set)

def contract(f):
    return f.replace("_orderbook.parquet", "")

def chft(path):
    seen = set()
    for l in open(path):
        d = json.loads(l)
        ex, date, hr = d["unit"]
        if d.get("missing") or "file" not in d:
            rows.append((ex, "*", date, hr, "missing_unit", "", "", "")); continue
        c = contract(d["file"]); key = (ex, c, date, hr)
        if key in seen: continue
        seen.add(key); symbols[ex].add(c)
        if "error" in d: st, cs, pg, ch = "read_error", "", "", ""
        else:
            ch = d.get("checked", 0); cr = d.get("crossed", 0); pg = d.get("prev_gaps")
            cs = cr / ch if ch else ""
            if not ch: st = "no_state"
            elif cr / ch > CROSS_MAX: st = "crossed>1%"
            elif pg: st = "gap_prev_id"
            else: st = "ok"
        rows.append((ex, c, date, hr, st, cs, "" if pg is None else pg, ch))
        if "best_age_ms_q" in d: age[(ex, c)].append(d["best_age_ms_q"][1])

for name in ("full.jsonl", "binance_full.jsonl"):
    if os.path.exists(os.path.join(inp, name)): chft(os.path.join(inp, name))

seenb = set()
for p in sorted(glob.glob(os.path.join(inp, "bybit*.jsonl"))):
    for l in open(p):
        d = json.loads(l); date, c = d["unit"]
        if (date, c) in seenb or "msgs" not in d: continue
        seenb.add((date, c)); symbols["bybit"].add(c)
        ch = d["checked"]; cr = d["crossed"]
        st = "ok" if ch and cr / ch <= CROSS_MAX and not d["u_gaps"] and not d["ts_nonmono"] else ("no_state" if not ch else "crossed>1%" if cr / ch > CROSS_MAX else "gap_prev_id")
        rows.append(("bybit", c, date, "day", st, cr / ch if ch else "", d["u_gaps"], ch))
        
        if d.get("best_age_ms_q"): age[("bybit", c)].append(d["best_age_ms_q"][1])

seeng = set()
for p in sorted(glob.glob(os.path.join(inp, "gate*.jsonl"))):
    for l in open(p):
        d = json.loads(l); c, date = d["unit"]
        if (c, date) in seeng: continue
        seeng.add((c, date)); symbols["gate"].add(c)
        for hr, h in sorted(d.get("hours", {}).items()):
            ch = h.get("checked", 0); cr = h.get("crossed", 0)
            if not ch: st = "no_diffs"            # часовые снимки 29.09–08.10: дифов нет
            elif cr / ch > CROSS_MAX: st = "crossed>1%"
            else: st = "ok"
            rows.append(("gate", c, date, hr, st, cr / ch if ch else "", "", ch))
            if ch and h.get("best_age_s_q"): age[("gate", c)].append(h["best_age_s_q"][1] * 1000)

with gzip.open(os.path.join(outd, "validity.csv.gz"), "wt", newline="") as f:
    w = csv.writer(f); w.writerow(["ex", "contract", "date", "hour", "status", "crossed_share", "prev_gaps", "checked"]); w.writerows(rows)

smax = {}
with open(os.path.join(outd, "smax.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["ex", "contract", "n_units", "p99_age_median_ms", "smax_ms"])
    for (ex, c), v in sorted(age.items()):
        m = float(np.median(v)); s = max(1000.0, m); smax.setdefault(ex, []).append(s)
        w.writerow([ex, c, len(v), round(m, 1), round(s, 1)])

summ = {}
by = collections.defaultdict(collections.Counter)
for r in rows: by[r[0]][r[4]] += 1
for ex, cnt in by.items():
    n = sum(cnt.values()); sm = smax.get(ex, [])
    summ[ex] = {"units": n, "status": dict(cnt), "good_share": cnt["ok"] / n,
                "n_contracts": len(symbols[ex]),
                "smax_s_q_0_10_25_50_75_90_100": [round(float(np.percentile(sm, q)) / 1000, 1) for q in (0, 10, 25, 50, 75, 90, 100)] if sm else None,
                "smax_gt60s": int(sum(x > 60000 for x in sm))}
json.dump(summ, open(os.path.join(outd, "summary.json"), "w"), indent=1, ensure_ascii=False)
json.dump({ex: sorted(s) for ex, s in symbols.items()}, open(os.path.join(outd, "symbols.json"), "w"), ensure_ascii=False)
print(json.dumps(summ, indent=1, ensure_ascii=False))
