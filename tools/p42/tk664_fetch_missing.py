#!/usr/bin/env python3
"""TK-664: докачка истории фандинга Binance USDT-M (публичный REST `fapi/v1/fundingRate`, без ключей) для монет сделок, которых нет ни в zip `/data/p42-metrics/funding`, ни в `/data/klines/binance/<SYM>/funding.csv`
(21 монета из сводки первого прогона), + контрольные BTCUSDT и ETHUSDT (сверка ставок и меток со склада). Окно 01.07 00:00 — 13.11 24:00 UTC (позже 13.11 не читается — изоляция).
Запуск на srv: python3 tk664_fetch_missing.py <summary.json первого прогона> <каталог выхода>
Выход: <каталог>/<SYM>/funding.csv (funding_time, funding_rate, mark_price, rate_type — как склад) и controls.json (расхождения контрольных монет со складом)."""
import csv
import json
import os
import sys
import time
import urllib.request

FROM_MS = 1782864000000                   # 2026-07-01 00:00 UTC
END_MS = 1794614400000                    # 2026-11-14 00:00 UTC (исключая)
PAUSE = 0.6                               # Binance 500 запросов / 5 мин / IP, общий лимит с коллекторами
CONTROLS = ["BTCUSDT", "ETHUSDT"]
STORE_ZIP = "/data/p42-metrics/funding"
STORE_API = "/data/klines/binance"


def get(url, tries=6):
    err = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "alpha-funding"}), timeout=20) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 — сеть / лимит: повтор с паузой
            err = e
            time.sleep(1.5 * (k + 1))
    raise RuntimeError("%s: %s" % (url, err))


def fetch(sym):
    rows, cur = {}, FROM_MS
    while cur < END_MS:
        r = get("https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&startTime=%d&endTime=%d&limit=1000" % (sym, cur, END_MS - 1))
        for x in r:
            t = int(x["fundingTime"])
            if t < END_MS:
                rows[t] = (x["fundingRate"], x.get("markPrice", ""))
        time.sleep(PAUSE)
        if len(r) < 1000:
            break
        cur = max(rows) + 1
    return rows


def store_rates(sym):
    out = {}
    import zipfile
    for m in ("2026-07", "2026-08", "2026-09"):
        p = os.path.join(STORE_ZIP, sym, "%s-fundingRate-%s.zip" % (sym, m))
        if os.path.exists(p):
            with zipfile.ZipFile(p) as z, z.open(z.namelist()[0]) as f:
                for row in csv.DictReader(line.decode("utf-8") for line in f):
                    out[int(row["calc_time"])] = float(row["last_funding_rate"])
    p = os.path.join(STORE_API, sym, "funding.csv")
    marks = {}
    if os.path.exists(p):
        for row in csv.DictReader(open(p, encoding="utf-8")):
            out[int(row["funding_time"])] = float(row["funding_rate"])
            marks[int(row["funding_time"])] = float(row["mark_price"])
    return out, marks


def main():
    summ, outdir = sys.argv[1:3]
    miss = json.load(open(summ, encoding="utf-8"))["meta"]["missing_syms"]
    ctl = {}
    for sym in miss + CONTROLS:
        rows = fetch(sym)
        d = os.path.join(outdir, sym)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "funding.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["funding_time", "funding_rate", "mark_price", "rate_type"])
            for t in sorted(rows):
                w.writerow([t, rows[t][0], rows[t][1], "Regular"])
        print("%-16s %4d выплат  %s … %s" % (sym, len(rows), min(rows) if rows else "-", max(rows) if rows else "-"), flush=True)
        if sym in CONTROLS:
            old, omarks = store_rates(sym)
            common = [t for t in rows if t in old]
            bad = [t for t in common if abs(float(rows[t][0]) - old[t]) > 1e-9]
            mk = [abs(float(rows[t][1]) / omarks[t] - 1) for t in common if t in omarks and rows[t][1]]
            ctl[sym] = {"api": len(rows), "store": len(old), "common": len(common), "rate_mismatch": len(bad), "only_api": len(set(rows) - set(old)), "only_store": len([t for t in old if t < END_MS and t not in rows]),
                        "mark_rel_max": max(mk) if mk else None, "mark_n": len(mk)}
    json.dump(ctl, open(os.path.join(outdir, "controls.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(ctl, indent=1))


if __name__ == "__main__":
    main()
