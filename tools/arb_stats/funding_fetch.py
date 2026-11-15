"""История выплат фандинга четырёх бирж (публичный REST, без ключей) по символам пулов — для «фандинг за время удержания» в cross_run.py.

Запуск: python3 funding_fetch.py <t0_мс> <t1_мс> <выход.json>
Пишет {"t0", "t1", "rates": {биржа: {символ: [[время_выплаты_мс, ставка], …]}}, "missing": {биржа: [символы без записей / с ошибкой]}}.
Берутся фактические отметки выплат (интервал у символа свой: 1/2/4/8 ч), не сетка; ставка — доля номинала за выплату (+ — лонг платит шорту).
Окно запроса — [t0 − 12 ч, t1 + 12 ч]. По одному потоку на биржу, темп ниже лимитов (Binance 500/5 мин на IP).
"""
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES  # noqa: E402

PAUSE = {"binance": 0.35, "bybit": 0.15, "okx": 0.25, "bingx": 0.3}
OKX_PAGE = 100


def get(url, tries=6):
    url = urllib.parse.quote(url, safe=":/?&=%")
    err = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "alpha-funding"}), timeout=20) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 — сеть/лимит: повтор с паузой
            err = e
            time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"{url}: {err}")


def binance(sym, a, b):
    out = {}
    cur = a
    while cur < b:
        r = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={cur}&endTime={b}&limit=1000")
        for x in r:
            out[int(x["fundingTime"])] = float(x["fundingRate"])
        if len(r) < 1000:
            break
        cur = max(out) + 1
        time.sleep(PAUSE["binance"])
    return out


def bybit(sym, a, b):
    out = {}
    end = b
    while True:
        d = get(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&startTime={a}&endTime={end}&limit=200")
        if d.get("retCode") != 0:
            raise RuntimeError(f"{sym}: {d.get('retMsg')}")
        r = d["result"]["list"]
        for x in r:
            out[int(x["fundingRateTimestamp"])] = float(x["fundingRate"])
        if len(r) < 200:
            break
        end = min(int(x["fundingRateTimestamp"]) for x in r) - 1
        time.sleep(PAUSE["bybit"])
    return out


def okx(sym, a, b):
    out = {}
    after = b + 1
    while True:
        d = get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={sym}&after={after}&limit={OKX_PAGE}")
        if d.get("code") != "0":
            raise RuntimeError(f"{sym}: {d.get('msg')}")
        r = d["data"]
        for x in r:
            t = int(x["fundingTime"])
            if t >= a:
                out[t] = float(x.get("realizedRate") or x["fundingRate"])
        if len(r) < OKX_PAGE or min(int(x["fundingTime"]) for x in r) < a:
            break
        after = min(int(x["fundingTime"]) for x in r)
        time.sleep(PAUSE["okx"])
    return out


def bingx(sym, a, b):
    out = {}
    end = b
    while True:
        d = get(f"https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate?symbol={sym}&startTime={a}&endTime={end}&limit=1000")
        if d.get("code") != 0:
            raise RuntimeError(f"{sym}: {d.get('msg')}")
        r = d["data"] or []
        for x in r:
            out[int(x["fundingTime"])] = float(x["fundingRate"])
        if len(r) < 1000:
            break
        end = min(int(x["fundingTime"]) for x in r) - 1
        time.sleep(PAUSE["bingx"])
    return out


FETCH = {"binance": binance, "bybit": bybit, "okx": okx, "bingx": bingx}


def work(ex, syms, a, b, rates, missing):
    for i, s in enumerate(syms):
        try:
            r = FETCH[ex](s, a, b)
        except Exception as e:  # noqa: BLE001
            print(ex, s, "ERR", e, flush=True)
            r = {}
        if r:
            rates[ex][s] = sorted([t, v] for t, v in r.items())
        else:
            missing[ex].append(s)
        if i % 100 == 0:
            print(ex, i, len(syms), flush=True)
        time.sleep(PAUSE[ex])


def main(t0, t1, out):
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    sm, _ = symmap_for(day)
    a, b = t0 - 12 * 3600_000, t1 + 12 * 3600_000
    rates = {ex: {} for ex in EXCHANGES}
    missing = {ex: [] for ex in EXCHANGES}
    th = [threading.Thread(target=work, args=(ex, sorted(sm[ex]), a, b, rates, missing)) for ex in EXCHANGES]
    for t in th:
        t.start()
    for t in th:
        t.join()
    Path(out).write_text(json.dumps({"t0": t0, "t1": t1, "rates": rates, "missing": missing}), encoding="utf-8")
    print({ex: {"symbols": len(rates[ex]), "missing": len(missing[ex])} for ex in EXCHANGES})


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
