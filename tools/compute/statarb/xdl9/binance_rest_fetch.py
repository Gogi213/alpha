#!/usr/bin/env python3
"""TK-493 G1, запасная сверка Binance: независимая перекачка минутных свечей тех же символов живым REST `/fapi/v1/klines`.

Нужна потому, что сырьё TK-490 по Binance на Steam Deck (`~/alpha-pump/candles/binance-um`) на момент проверки отсутствует. Это НЕ сверка
с данными пампов, а проверка «файл xdl = то, что биржа отдаёт сейчас» на том же окне. stdlib. Символы выбираются тем же правилом, что и
`compare_tk490.py` (мажоры + равномерно по алфавиту среди символов с данными), список — из манифеста `manifest_binance.csv`.
Результат — `<out>/<SYMBOL>.csv.gz` (t_open_ms,o,h,l,c,v,quote_v,n_trades) для `compare_tk490.py --prev`.
Вес запроса limit=1500 равен 10; частота --rate (по умолчанию 3 запр/с = 1800 веса/мин при лимите биржи 2400); при заголовке
x-mbx-used-weight-1m > --weight-cap — пауза до следующей минуты; 418/429 — остановка.

usage: binance_rest_fetch.py --manifest manifest_binance.csv --out DIR [--n 30] [--start 2026-10-01] [--end 2026-11-05] [--rate 3]
"""
import argparse, csv, datetime, gzip, json, os, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_tk490 import pick, parse_day

BASE = "https://fapi.binance.com/fapi/v1/klines"
MIN_MS = 60_000


def get(url, retries=5):
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "alpha-xdl/1"}), timeout=25) as r:
                return json.loads(r.read()), int(r.headers.get("x-mbx-used-weight-1m", "0") or 0)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                raise SystemExit("лимит биржи %d — остановка (%s)" % (e.code, url[:90]))
            last = e
        except Exception as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("не удалось: %s | %s" % (url[:100], last))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--start", default="2026-10-01")
    ap.add_argument("--end", default="2026-11-05")
    ap.add_argument("--rate", type=float, default=3.0)
    ap.add_argument("--weight-cap", type=int, default=1800)
    ap.add_argument("--majors", default="BTCUSDT,ETHUSDT,SOLUSDT")
    a = ap.parse_args()
    start, end = parse_day(a.start), parse_day(a.end)
    with open(a.manifest, encoding="utf-8", newline="") as f:
        names = {r["symbol"]: int(r["rows"] or 0) for r in csv.DictReader(f) if r["has_candles"] == "1"}
    chosen = pick(list(names), [m for m in a.majors.split(",") if m], a.n, lambda n: names[n] > 0)
    os.makedirs(a.out, exist_ok=True)
    print("выбрано %d символов: %s" % (len(chosen), ",".join(chosen)), flush=True)
    dt = 1.0 / a.rate
    req = 0
    t0 = time.time()
    for sym in chosen:
        path = os.path.join(a.out, sym + ".csv.gz")
        if os.path.exists(path):
            continue
        rows = {}
        cur = start
        while cur < end:
            url = "%s?symbol=%s&interval=1m&startTime=%d&endTime=%d&limit=1500" % (BASE, urllib.parse.quote(sym), cur, end - 1)
            data, w = get(url)
            req += 1
            if not data:
                break
            for k in data:
                if start <= k[0] < end:
                    rows[k[0]] = [k[0], k[1], k[2], k[3], k[4], k[5], k[7], k[8]]
            cur = data[-1][0] + MIN_MS
            if len(data) < 1500:
                break
            if w > a.weight_cap:
                time.sleep(max(1.0, 61 - (time.time() % 60)))
            time.sleep(dt)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["t_open_ms", "o", "h", "l", "c", "v", "quote_v", "n_trades"])
            for t in sorted(rows):
                wr.writerow(rows[t])
        os.replace(tmp, path)
        print("%-20s строк %d, запросов всего %d, %.0f с" % (sym, len(rows), req, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
