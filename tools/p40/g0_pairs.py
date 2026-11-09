"""П-40 G0 (TK-496 возврат 2, п.2): доля годного времени по паре бирж из validity.csv.gz.
Единица (биржа, контракт, час) годна = status ok. Bybit — единица «сутки»: статус действует на все 24 часа суток. Сетка часов = все (дата, час), где есть единицы Binance
(17.09 00:00 – 22.10 04:59 UTC, 845 ч). Пара: общие контракты (после нормализации имени: без -USDT-SWAP / USDT / _USDT); час годен для пары, если годны обе ноги.
Знаменатели: share_all — все часы сетки × общие контракты; share_exhole — без часов дыры Gate (часы, где у большинства контрактов Gate нет дифов: 29.09–08.10).
Основные 15 пар = шесть бирж с книгой-потоком (Binance, OKX, Bitget, Aster, Bybit, Gate); Hyperliquid (снимки) — отдельные 6 строк, flag=extra.
Запуск: g0_pairs.py <validity.csv.gz> <выход.csv>. Исходов не касается."""
import sys, gzip, csv, itertools, collections
import numpy as np

EXS = ["binance_futures", "okx_futures", "bitget_futures", "aster_futures", "bybit", "gate"]
EXTRA = "hyperliquid_futures"

def norm(c):
    for s in ("-USDT-SWAP", "_USDT", "USDT"):
        if c.endswith(s): return c[: -len(s)]
    return c

rows = list(csv.reader(gzip.open(sys.argv[1], "rt")))[1:]
slots = sorted({(r[2], r[3]) for r in rows if r[0] == "binance_futures"})
hidx = {s: i for i, s in enumerate(slots)}; H = len(slots)
dates = sorted({s[0] for s in slots}); day_idx = {d: [hidx[s] for s in slots if s[0] == d] for d in dates}
ok = collections.defaultdict(dict)      # ex -> asset -> bool[H]
seen = collections.defaultdict(set)     # (ex, asset) -> есть ли хоть одна единица
nodiff = collections.defaultdict(lambda: np.zeros(H))
gate_n = collections.Counter()
for ex, c, d, h, st, *_ in rows:
    if c == "*": continue
    a = norm(c)
    arr = ok[ex].setdefault(a, np.zeros(H, bool))
    if h == "day":
        if st == "ok":
            for i in day_idx.get(d, []): arr[i] = True
    else:
        i = hidx.get((d, h))
        if i is None: continue
        if st == "ok": arr[i] = True
        if ex == "gate":
            gate_n[i] += 1
            if st == "no_diffs": nodiff[ex][i] += 1
hole = np.array([nodiff["gate"][i] > 0.5 * gate_n[i] for i in range(H)]) if gate_n else np.zeros(H, bool)
keep = ~hole
out = [["flag", "ex_a", "ex_b", "common_contracts", "share_all", "share_exhole", "share_a_alone", "share_b_alone", "hours_grid", "hours_hole"]]
for flag, pairs in (("main", itertools.combinations(EXS, 2)), ("extra", [(x, EXTRA) for x in EXS])):
    for x, y in pairs:
        com = sorted(set(ok[x]) & set(ok[y]))
        if not com: out.append([flag, x, y, 0, "", "", "", "", H, int(hole.sum())]); continue
        both = np.array([ok[x][a] & ok[y][a] for a in com]); ax = np.array([ok[x][a] for a in com]); ay = np.array([ok[y][a] for a in com])
        out.append([flag, x, y, len(com), round(both.mean(), 4), round(both[:, keep].mean(), 4), round(ax.mean(), 4), round(ay.mean(), 4), H, int(hole.sum())])
csv.writer(open(sys.argv[2], "w", newline="")).writerows(out)
for r in out: print(*r, sep="\t")
