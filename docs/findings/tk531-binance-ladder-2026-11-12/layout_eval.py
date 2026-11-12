#!/usr/bin/env python3
"""TK-531 доработка: как выровнять нагрузку книги по соединениям. Сравнивает раскладки пула по шардам (по 64) по сумме сообщ/с на шард.
Вход: symcount.tsv (шард, 10-мин корзина от начала файла, символ, строк книги; из боевых файлов, symcount.sh), ticker.json (REST 24hr), pool-*.json.
Раскладки: alpha — как в боевом (символы по алфавиту, куски по 64); snake_qv / lpt_qv / lpt_count — по оценке активности из тикера (quoteVolume / count); lpt_fact_prev — по факту прошлого часа.
Мера — отношение max/min и max/среднее суммарной нагрузки шардов за час и за минуту всплеска; оценка на часе, которого раскладка не видела.
"""
import json
import sys
from collections import defaultdict

PER = 64


def alpha(pool, w, per=PER):
    return [pool[i : i + per] for i in range(0, len(pool), per)]


def lpt(pool, w, per=PER):
    """Жадно: по убыванию веса — в шард с наименьшей суммой, где ещё есть место (число шардов как у alpha)."""
    k = -(-len(pool) // per)
    sh, load = [[] for _ in range(k)], [0.0] * k
    for s in sorted(pool, key=lambda x: (-w.get(x, 0.0), x)):
        i = min((j for j in range(k) if len(sh[j]) < per), key=lambda j: load[j])
        sh[i].append(s)
        load[i] += w.get(s, 0.0)
    return sh


def snake(pool, w, per=PER):
    k = -(-len(pool) // per)
    sh = [[] for _ in range(k)]
    for r, s in enumerate(sorted(pool, key=lambda x: (-w.get(x, 0.0), x))):
        c, d = divmod(r, k)
        sh[d if c % 2 == 0 else k - 1 - d].append(s)
    return sh


def load_of(layout, rate):
    return [sum(rate.get(s, 0.0) for s in g) for g in layout]


def spread(loads):
    m = sum(loads) / len(loads)
    return {"max/min": round(max(loads) / min(loads), 3), "max/ср": round(max(loads) / m, 3)}


def windows(rows):
    """symcount: корзина 10 мин. Файлы 11.11 — корзины от старта файла (21:24:50 UTC), 12.11 — от эпохи (>1e6)."""
    w = {"train 11.11 21:25–22:25 (корз. 0–5)": defaultdict(float), "тест 11.11 22:25–23:05 (6–9)": defaultdict(float),
         "тест 11.11 23:05–23:15, с всплеском (10)": defaultdict(float), "тест 12.11 00:00–01:10": defaultdict(float)}
    names = list(w)
    for (sh, b), syms in rows.items():
        k = 3 if b > 10**6 else 0 if b <= 5 else 1 if b <= 9 else 2 if b == 10 else None
        if k is None:
            continue
        for s, n in syms.items():
            w[names[k]][s] += n
    return w


def main(d):
    pool = json.load(open(f"{d}/pool.json"))["pool"]
    tk = {t["symbol"]: t for t in json.load(open(f"{d}/ticker.json"))}
    qv = {s: float(tk[s]["quoteVolume"]) for s in pool if s in tk}
    cnt = {s: float(tk[s]["count"]) for s in pool if s in tk}
    rows = defaultdict(lambda: defaultdict(float))
    for line in open(f"{d}/symcount.tsv"):
        sh, b, s, n = line.split()
        rows[(sh, int(b))][s] += float(n)
    w = windows(rows)
    names = list(w)
    train = w[names[0]]
    layouts = {
        "alpha 64 (боевой)": alpha(pool, {}), "snake по quoteVolume": snake(pool, qv), "lpt по quoteVolume": lpt(pool, qv),
        "lpt по count сделок 24ч": lpt(pool, cnt), "lpt по факту часа 21:25–22:25": lpt(pool, train),
        "alpha 32 (кандидат А)": alpha(pool, {}, 32),
    }
    print(f"символов в пуле {len(pool)}, с фактом {sum(1 for s in pool if train.get(s))}")
    for name in names[1:]:
        print(f"\n== {name}: строк книги всего {sum(w[name].values()):.0f}")
        for ln, lay in layouts.items():
            sp = spread(load_of(lay, w[name]))
            print(f"  {ln:34s} шардов {len(lay):2d}  max/min {sp['max/min']:.3f}  max/ср {sp['max/ср']:.3f}")


if __name__ == "__main__":
    main(sys.argv[1])
