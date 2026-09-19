#!/usr/bin/env python3
"""Справочные минутные свечи Bybit (BTC/ETH) по REST — задним числом, без ключей и без коллектора
(S3 плана по сторонам, владелец 20.09: «необязательно же стакан»). Режим «по битку» [D 10:17; S 54:00].

    python3 tools/compute/ref-klines.py --out-dir study/regime [--symbols BTCUSDT,ETHUSDT] \\
            [--since 2026-09-16] [--until 2026-09-20]

Пишет `<out-dir>/ref-<SYMBOL>-1m.csv` (`minute_ms,open,high,low,close,volume`; минуты подряд, по
возрастанию). Файл — источник истины: повторный запуск дописывает только недостающие минуты (от
последней в файле до `--until`), ничего не переписывает. Дыры в ответе биржи — в stderr и не
заполняются (следующий запуск попробует снова). Bybit `GET /v5/market/kline`, `category=linear`,
`interval=1`, окна по 1000 минут, `list` идёт от новых к старым; повтор при ошибке сети/`retCode`
— 3 попытки с паузой 2 с. Первый запуск — с `--since` (по умолчанию 2026-09-16, первые сутки корня).
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

API = "https://api.bybit.com/v5/market/kline"
MINUTE_MS = 60_000
WINDOW = 1000  # минут в одном запросе (лимит биржи)


def fetch(symbol, start_ms, end_ms, attempts=3):
    q = urllib.parse.urlencode({"category": "linear", "symbol": symbol, "interval": "1",
                                "start": start_ms, "end": end_ms, "limit": WINDOW})
    last = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"{API}?{q}", timeout=20) as r:
                d = json.load(r)
            if d.get("retCode") == 0:
                rows = [(int(x[0]), x[1], x[2], x[3], x[4], x[5]) for x in d["result"]["list"]]
                rows.sort()
                return rows
            last = f"retCode {d.get('retCode')} {d.get('retMsg')}"
        except Exception as e:  # noqa: BLE001 — сеть, JSON: повторить
            last = repr(e)
        time.sleep(2)
    raise SystemExit(f"{symbol}: {API} не ответил после {attempts} попыток: {last}")


def last_minute(path):
    if not os.path.exists(path):
        return None
    last = None
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            last = int(r["minute_ms"])
    return last


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", required=True)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    p.add_argument("--since", default="2026-09-16", help="первый день (UTC), если файла ещё нет")
    p.add_argument("--until", help="последний день (UTC), по умолчанию — сейчас")
    a = p.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    now_ms = int(time.time() * 1000) // MINUTE_MS * MINUTE_MS
    end_ms = now_ms - MINUTE_MS  # последняя закрытая минута
    if a.until:
        d = dt.datetime.strptime(a.until, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        end_ms = min(end_ms, int((d + dt.timedelta(days=1)).timestamp() * 1000) - MINUTE_MS)
    for symbol in a.symbols.split(","):
        path = os.path.join(a.out_dir, f"ref-{symbol}-1m.csv")
        last = last_minute(path)
        if last is None:
            d = dt.datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            start = int(d.timestamp() * 1000)
        else:
            start = last + MINUTE_MS
        if start > end_ms:
            print(f"{symbol}: свежий, последняя минута {last}")
            continue
        new_file = last is None
        added = 0
        gaps = 0
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["minute_ms", "open", "high", "low", "close", "volume"])
            cur = start
            prev = last
            while cur <= end_ms:
                chunk_end = min(cur + (WINDOW - 1) * MINUTE_MS, end_ms)
                rows = fetch(symbol, cur, chunk_end)
                for r in rows:
                    if r[0] < cur or r[0] > chunk_end:
                        continue
                    if prev is not None and r[0] != prev + MINUTE_MS:
                        gaps += 1
                        print(f"{symbol}: дыра {prev} → {r[0]} ({(r[0] - prev) // MINUTE_MS - 1} мин)",
                              file=sys.stderr)
                    w.writerow(r)
                    prev = r[0]
                    added += 1
                if not rows:
                    print(f"{symbol}: пустой ответ на окно {cur}..{chunk_end}", file=sys.stderr)
                    gaps += 1
                cur = chunk_end + MINUTE_MS
        print(f"{symbol}: дописано {added} минут → {path} (дыр {gaps})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
