#!/usr/bin/env python3
"""История ставок фандинга Bybit по пулу — для издержки «ставка × отметки внутри сделки × номинал» в
portfolio-sim (аудит комиссий В-135, `docs/research/reviews/fees-audit-2026-09-27.md` п. 3). REST вне горячего
пути, без ключей.

    python3 tools/compute/funding-history.py --symbols-from study/klines --since 2026-08-01 --until 2026-09-23 \\
            --out study/funding/funding-2026-08-01_2026-09-23.csv

Пишет CSV `symbol,funding_time_ms,funding_rate` (по монете, по возрастанию времени): отметка — момент
расчёта фандинга (`fundingRateTimestamp`), ставка — доля номинала (`fundingRate`; + — лонг платит, − —
лонг получает). Интервал у монет разный (1/2/4/8 ч) — берутся фактические отметки, не сетка. Bybit
`GET /v5/market/funding/history`, `category=linear`, окна по 200 записей от `--until` назад; повтор при ошибке
сети/`retCode` — 3 попытки с паузой 2 с. Монета без записей в периоде — строка в stderr, не отказ (итог печатает
их список). `--symbols-from` — каталог `ref-<SYMBOL>-1m.csv` (пул свечей) или `instruments.csv`.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.bybit.com/v5/market/funding/history"
LIMIT = 200


def get(symbol, start_ms, end_ms, attempts=3):
    q = urllib.parse.urlencode({"category": "linear", "symbol": symbol, "startTime": start_ms,
                                "endTime": end_ms, "limit": LIMIT})
    last = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"{API}?{q}", timeout=20) as r:
                d = json.load(r)
            if d.get("retCode") == 0:
                return [(int(x["fundingRateTimestamp"]), x["fundingRate"]) for x in d["result"]["list"]]
            last = f"retCode {d.get('retCode')} {d.get('retMsg')}"
        except Exception as e:  # noqa: BLE001 — сеть, JSON: повторить
            last = repr(e)
        time.sleep(2)
    raise SystemExit(f"{symbol}: {API} не ответил после {attempts} попыток: {last}")


def history(symbol, start_ms, end_ms):
    rows = {}
    end = end_ms
    while end >= start_ms:
        page = get(symbol, start_ms, end)
        if not page:
            break
        for t, r in page:
            if start_ms <= t <= end_ms:
                rows[t] = r
        oldest = min(t for t, _ in page)
        if len(page) < LIMIT or oldest <= start_ms:
            break
        end = oldest - 1
    return sorted(rows.items())


def symbols_from(path):
    if os.path.isdir(path):
        return sorted(f[4:-7] for f in os.listdir(path) if f.startswith("ref-") and f.endswith("-1m.csv"))
    with open(path, encoding="utf-8") as f:
        return sorted({r["symbol"] for r in csv.DictReader(f)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="через запятую")
    ap.add_argument("--symbols-from", help="каталог ref-<SYMBOL>-1m.csv или instruments.csv")
    ap.add_argument("--since", required=True, help="первый день (UTC)")
    ap.add_argument("--until", required=True, help="последний день (UTC), включительно")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.symbols:
        symbols = a.symbols.split(",")
    elif a.symbols_from:
        symbols = symbols_from(a.symbols_from)
    else:
        raise SystemExit("нужен --symbols или --symbols-from")
    day = lambda s: dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    start_ms = int(day(a.since).timestamp() * 1000)
    end_ms = int((day(a.until) + dt.timedelta(days=1)).timestamp() * 1000) - 1
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    tmp = a.out + ".tmp"
    empty = []
    total = 0
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "funding_time_ms", "funding_rate"])
        for s in symbols:
            rows = history(s, start_ms, end_ms)
            if not rows:
                empty.append(s)
                print(f"{s}: записей нет", file=sys.stderr)
            for t, r in rows:
                w.writerow([s, t, r])
            total += len(rows)
            gaps = sorted({rows[i + 1][0] - rows[i][0] for i in range(len(rows) - 1)})
            print(f"{s}: {len(rows)} отметок, интервалы {[g // 3_600_000 for g in gaps][:4]} ч")
    os.replace(tmp, a.out)
    print(f"монет {len(symbols)}, отметок {total}, без записей {len(empty)} {empty} → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
