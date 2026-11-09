"""П-40 G0 (TK-496 возврат 3, п.1–2): статус `gap` в валидности. Единица (биржа, контракт, час) со статусом ok становится `gap`, если наибольший промежуток
без событий книги внутри часа > GAP_MAX_S = 300 с (порог назначен Судьёй, В-53; считая от начала часа до первого события и от последнего события до конца часа).
Bybit — единица «сутки», то же правило на сутки (от 00:00 UTC). Gate — по ts_first/ts_last/gap_max_s часовых файлов (часы no_diffs не трогаются).
Другие статусы (crossed>1% и т.п.) остаются: приоритет у прежнего статуса, gap — только из ok.
Вход: validity.csv.gz, gaps_*.jsonl (g0_gaps.py: Binance/OKX/Bitget/Aster), каталог vin с gate*.jsonl и bybit*.jsonl.
Выход: <out>/validity.csv.gz (+колонка gap_max_s), gap_summary.json. Запуск: g0_validity_gap.py <validity.csv.gz> <vin> <out> <gaps_a.jsonl> [gaps_b.jsonl ...]. Исходов не касается."""
import sys, os, json, glob, gzip, csv, collections, datetime as dt

GAP_MAX_S = 300.0
vpath, vin, outd, gpaths = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
os.makedirs(outd, exist_ok=True)

def start_s(date, hr=0):
    return dt.datetime.fromisoformat(date).replace(hour=int(hr), tzinfo=dt.timezone.utc).timestamp()

gap = {}                                   # (ex, contract, date, hour) -> gap_max_s
for p in gpaths:
    for l in open(p):
        d = json.loads(l)
        if d.get("missing") or "error" in d or "gap_max_s" not in d: continue
        ex, date, hr = d["unit"]; c = d["file"].replace("_orderbook.parquet", "")
        gap[(ex, c, date, hr)] = d["gap_max_s"]

seen = set()
for p in sorted(glob.glob(os.path.join(vin, "gate*.jsonl"))):
    for l in open(p):
        d = json.loads(l); c, date = d["unit"]
        if (c, date) in seen: continue
        seen.add((c, date))
        for hr, h in d.get("hours", {}).items():
            if h.get("ts_first") is None or h.get("gap_max_s") is None: continue
            t0 = start_s(date, hr)
            gap[("gate", c, date, hr)] = max(h["gap_max_s"], h["ts_first"] - t0, t0 + 3600 - h["ts_last"])

seen = set()
for p in sorted(glob.glob(os.path.join(vin, "bybit*.jsonl"))):
    for l in open(p):
        d = json.loads(l); date, c = d["unit"]
        if (date, c) in seen or "msgs" not in d: continue
        seen.add((date, c))
        t0 = start_s(date)
        gap[("bybit", c, date, "day")] = max(d["gap_max_ms"] / 1000, d["ts_first"] / 1000 - t0, t0 + 86400 - d["ts_last"] / 1000)

rows = list(csv.reader(gzip.open(vpath, "rt")))
head, rows = rows[0], rows[1:]
cnt = collections.defaultdict(collections.Counter); nog = collections.Counter(); out = []
for r in rows:
    ex, c, date, hr, st = r[:5]
    g = gap.get((ex, c, date, hr))
    if g is None and c != "*" and st == "ok": nog[ex] += 1
    if st == "ok" and g is not None and g > GAP_MAX_S: st = "gap"
    cnt[ex][r[4] + ("->gap" if st == "gap" else "")] += 1
    out.append(r[:4] + [st] + r[5:] + ["" if g is None else round(g, 3)])
with gzip.open(os.path.join(outd, "validity.csv.gz"), "wt", newline="") as f:
    w = csv.writer(f); w.writerow(head + ["gap_max_s"]); w.writerows(out)

summ = {}
for ex, c in cnt.items():
    n = sum(c.values()); ok_old = c["ok"] + c["ok->gap"]
    summ[ex] = {"units": n, "ok_before": ok_old, "ok_before_share": round(ok_old / n, 4), "to_gap": c["ok->gap"], "ok_after": c["ok"],
                "ok_after_share": round(c["ok"] / n, 4), "gap_share_of_ok_before": round(c["ok->gap"] / ok_old, 4) if ok_old else None,
                "ok_without_gap_value": nog[ex], "status_after": {k: v for k, v in c.items() if "->" not in k}}
    summ[ex]["status_after"]["gap"] = c["ok->gap"]
json.dump(summ, open(os.path.join(outd, "gap_summary.json"), "w"), indent=1, ensure_ascii=False)
print(json.dumps(summ, indent=1, ensure_ascii=False))
