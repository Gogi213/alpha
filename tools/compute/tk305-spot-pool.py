#!/usr/bin/env python3
"""TK-305: пул В-171/В-174 для СПОТА Bybit (USDT-пары) и размеры ob200 у quote-saver.
  tk305-spot-pool.py OUTDIR [--heads]
Шаги: (1) символы = каталог public.bybit.com/spot/ ∪ instruments-info(spot) только *USDT; (2) дневные свечи category=spot
(оборот в USDT) → OUTDIR/turnover-daily.csv, OUTDIR/first-day.csv; (3) пул по правилу pool-v171.py фьючерса
(вход 7 сут. ранг ≤100, выход 7 сут. >110, возраст ≥30 сут. по первой свече, без BTC/ETH и xstocks), D+1 для переноса круга;
(4) --heads: HEAD ob200 + сделок на каждую монето-сутки → OUTDIR/sizes.csv."""
import csv, re, sys, os, json, time, collections, datetime as dt, urllib.request
from concurrent.futures import ThreadPoolExecutor

OUT = sys.argv[1]; HEADS = "--heads" in sys.argv
os.makedirs(OUT, exist_ok=True)
DA, D0, D1 = dt.date(2025, 12, 1), dt.date(2026, 1, 1), dt.date(2026, 9, 30)   # разогрев рангов / окно пула
STABLE = {"USDC", "USDE", "USD1", "FDUSD", "DAI", "TUSD", "USDP", "PYUSD", "EURI", "USDD", "BUSD"}   # справочно (вариант B)
def get(u, n=5):
    for i in range(n):
        try:
            return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha"}), timeout=30).read()
        except Exception:
            time.sleep(1 + i)
    raise RuntimeError(u)
def head(u):
    for i in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers={"User-Agent": "alpha"}), timeout=30)
            return int(r.headers["Content-Length"])
        except urllib.error.HTTPError as e:
            if e.code == 404: return -1
            time.sleep(1 + i)
        except Exception:
            time.sleep(1 + i)
    return -2
ms = lambda d: int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
day = lambda t: dt.datetime.fromtimestamp(int(t) / 1000, dt.timezone.utc).date()

# (1) символы + метаданные
inst = {x["symbol"]: x for x in json.loads(get("https://api.bybit.com/v5/market/instruments-info?category=spot&limit=1000"))["result"]["list"]}
cat = set(re.findall(r'href="([A-Z0-9]+USDT)"', get("https://public.bybit.com/spot/").decode()))
syms = sorted(cat | {s for s, x in inst.items() if x["quoteCoin"] == "USDT"})
print("symbols", len(syms), "catalog", len(cat), "instruments-usdt", sum(1 for x in inst.values() if x["quoteCoin"] == "USDT"), file=sys.stderr)

# (2) свечи: последние 1000 суток (с 01.2024); если вернулось 1000 строк — первая свеча усечена (возраст ≥ 1000 сут.)
tdy = dt.datetime.now(dt.timezone.utc).date()
def kl(s):
    j = json.loads(get(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={s}&interval=D&end={ms(tdy)-1}&limit=1000"))
    return s, (j["result"]["list"] if j["retCode"] == 0 else [])
T = collections.defaultdict(dict); first = {}
with ThreadPoolExecutor(6) as ex:
    for s, rows in ex.map(kl, syms):
        if not rows: continue
        rows.sort(key=lambda r: int(r[0]))
        first[s] = (day(rows[0][0]), len(rows))
        for r in rows:
            d = day(r[0])
            if d >= DA: T[s][d] = float(r[6])
with open(f"{OUT}/turnover-daily.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["symbol", "day", "turnover_usdt"])
    for s in sorted(T):
        for d in sorted(T[s]): w.writerow([s, d, T[s][d]])
with open(f"{OUT}/first-day.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["symbol", "first_candle", "n_candles", "in_instruments", "symbolType"])
    for s in sorted(first): w.writerow([s, first[s][0], first[s][1], int(s in inst), inst.get(s, {}).get("symbolType", "")])

# (3) пул
alld = [DA + dt.timedelta(n) for n in range((D1 + dt.timedelta(1) - DA).days + 1)]
def base(s): return s[:-4]
def excluded(s, variant):
    b = base(s)
    if b in ("BTC", "ETH"): return True
    if inst.get(s, {}).get("symbolType") == "xstocks": return True
    if variant == "B" and b in STABLE: return True
    return False
def rank_of_day(d, variant):
    c = []
    for s in T:
        if excluded(s, variant) or d not in T[s] or T[s][d] <= 0: continue
        fd, n = first[s]
        if n < 1000 and (d - fd).days < 30: continue
        c.append((T[s][d], s))
    c.sort(reverse=True)
    return {s: i + 1 for i, (_, s) in enumerate(c)}
def pool_seq(variant, IN=100, OUT_=110, N=7):
    up = collections.Counter(); dn = collections.Counter(); pool = set(); res = {}
    for d in alld:
        cur = set(pool)
        for s in list(cur):
            if dn[s] >= N or d not in T[s]: cur.discard(s)
        for s in list(up):
            if up[s] >= N and s not in cur and d in T[s]: cur.add(s)
        pool = cur; res[d] = set(pool)
        r = rank_of_day(d, variant)
        for s in T:
            k = r.get(s)
            up[s] = up[s] + 1 if (k is not None and k <= IN) else 0
            dn[s] = dn[s] + 1 if (k is None or k > OUT_) else 0
    return res
rows_all = {}
for variant in ("A", "B"):
    res = pool_seq(variant)
    ds = [d for d in alld if D0 <= d <= D1]
    need = {(s, d) for d in ds for s in res[d]}
    carry = {(s, d + dt.timedelta(1)) for (s, d) in need if (s, d + dt.timedelta(1)) not in need}
    rows_all[variant] = (need, carry)
    sz = [len(res[d]) for d in ds]
    print(f"variant {variant}: coins {len({s for s, _ in need})} symdays {len(need)} carry {len(carry)} size min/med/max {min(sz)}/{sorted(sz)[len(sz)//2]}/{max(sz)}", file=sys.stderr)
    with open(f"{OUT}/pool-spot-{variant}.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["sym", "day", "kind"])
        for s, d in sorted(need): w.writerow([s, d, "pool"])
        for s, d in sorted(carry): w.writerow([s, d, "carry"])

# (4) размеры: ob200 — HEAD по всем (32 потока), сделки — выборка 600 случайных (HEAD сделок на многих потоках душится CDN, TK-037)
if HEADS:
    import random
    want = sorted(set().union(*[a | b for a, b in rows_all.values()]))
    def ob(sd): return sd[0], sd[1], head(f"https://quote-saver.bycsi.com/orderbook/spot/{sd[0]}/{sd[1]}_{sd[0]}_ob200.data.zip")
    def tr(sd): return sd[0], sd[1], head(f"https://public.bybit.com/spot/{sd[0]}/{sd[0]}_{sd[1]}.csv.gz")
    for name, fn, sel, nt in (("sizes-tr-sample", tr, random.Random(305).sample(want, min(600, len(want))), 8), ("sizes", ob, want, 32)):
        with ThreadPoolExecutor(nt) as ex, open(f"{OUT}/{name}.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["sym", "day", "bytes"])
            for r in ex.map(fn, sel): w.writerow(r); f.flush() if r[0][0] == "Z" else None
        print(name, len(sel), file=sys.stderr)
