"""Независимая сверка остатка arb-holding с биржами (TK-534 v6): 1-минутные свечи по REST (источник биржи, не наш сборщик).
Для каждой монеты и суток UTC: сколько минут из 1440 закрытие Bybit (или Gate) отличается от закрытия Binance futures на ≥ 50 bps (θ = 0,5 %), и по часам 16.11.
Запуск: python3 hold_rest_spot.py bybit|gate МОНЕТА ...  (нужен доступ к api.bybit.com / api.gateio.ws и fapi.binance.com; пишет таблицу в stdout)
"""
import datetime as dt
import json
import sys
import urllib.request

DAYS = ("2026-11-14", "2026-11-15", "2026-11-16")


def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha"}), timeout=20).read())


def ts(s):
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def klines(venue, coin, a, b):
    """{минута_мс: закрытие} для venue и Binance за [a, b) мс."""
    ve, bn = {}, {}
    for lo in range(a, b, 720 * 60_000):
        hi = min(lo + 720 * 60_000 - 1, b - 1)
        if venue == "bybit":
            for r in get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={coin}USDT&interval=1&start={lo}&end={hi}&limit=1000")["result"]["list"]:
                ve[int(r[0])] = float(r[4])
        else:
            for r in get(f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={coin}_USDT&interval=1m&from={lo // 1000}&to={hi // 1000}"):
                ve[int(r["t"]) * 1000] = float(r["c"])
        for r in get(f"https://fapi.binance.com/fapi/v1/klines?symbol={coin}USDT&interval=1m&startTime={lo}&endTime={hi}&limit=1000"):
            bn[r[0]] = float(r[4])
    return ve, bn


def main(venue, coins):
    print(f"| монета | " + " | ".join(f"{d[8:]}.11: минут ≥ 50 bps ({venue} против Binance)" for d in DAYS) + " | 16.11 по часам UTC (минут ≥ 50 bps) |")
    print("|---|" + "---|" * (len(DAYS) + 1))
    for c in coins:
        cells, hours = [], ""
        for d in DAYS:
            a = ts(d + "T00:00:00")
            try:
                ve, bn = klines(venue, c, a, a + 86_400_000)
            except Exception as e:  # noqa: BLE001
                cells.append(f"ошибка: {str(e)[:40]}")
                continue
            ks = sorted(set(ve) & set(bn))
            big = [k for k in ks if abs(ve[k] / bn[k] - 1) * 1e4 >= 50]
            cells.append(f"{len(big)} из {len(ks)}")
            if d == "2026-11-16":
                hours = " ".join(str(sum(1 for k in big if (k - a) // 3_600_000 == h)) for h in range(24))
        print(f"| {c} | " + " | ".join(cells) + f" | {hours} |")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
