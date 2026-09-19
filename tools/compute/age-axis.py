#!/usr/bin/env python3
"""H2 шаг 2: уникален ли порог возраста по монете (handoff-2026-09-20.md, В-71).

Вопрос владельца: возраст выше жёсткого флора 15 мин — один порог для пула, свой для нескольких
пулов или свой у каждой монеты?

Проверка out-of-sample. На сутках A выбирается лучший порог `t` из набора, на сутках B он
оценивается той же мерой, что в `floors-balance.py`: **ход в день = сигналов/сутки × (mean m_1h −
круг комиссий 4.41 bps, В-63)**. Сравниваются три правила:
  * пуловый порог — один `t` на все монеты;
  * порог по кластеру — свой `t` на кластер монет (кластер из колонки пула, выбирается **до**
    взгляда на ответ, см. `--cluster-col`/`--clusters`);
  * свой порог у каждой монеты (`--min-signals` — сколько касаний монета обязана иметь на сутках A,
    чтобы выбирать её порог, иначе она берёт пуловый; это параметр, он печатается в шапке).

Если по-монетный порог на B не бьёт пуловый — возраст уникален не по монете, и решение в пользу
одного порога (или кластеров); если бьёт на большинстве разбиений — возраст надо выбирать по монете.

    python3 tools/compute/age-axis.py --touches-root study/touches \\
        --instruments root/instruments.csv --out study/age-axis.txt

Вход: `study/touches/<сутки>/touches-<SYM>.csv` (ночной H3, трекер `notional $10k`) или каталог с
`touches-*.csv` одного прогона. Ошибок в данных не терпит: строка без `m_1h`/`age_ms` пропускается.
"""
import argparse
import collections
import csv
import glob
import itertools
import os
import statistics
import sys

FEES = 4.41  # круг комиссий по рынку (В-63)
A = [15, 30, 45, 60, 90, 120]  # пороги возраста с постановки, мин


def load(root):
    """(монета, сутки, возраст мин, m_1h) по всем `touches-*.csv` (плоско или по суткам)."""
    paths = glob.glob(os.path.join(root, "touches-*.csv"))
    if not paths:
        paths = glob.glob(os.path.join(root, "*", "touches-*.csv"))
    rows = []
    for f in sorted(paths):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            try:
                rows.append((r["symbol"] if "symbol" in r else os.path.basename(f)[8:-4],
                             r["day_utc"], float(r["age_ms"]) / 60000.0, float(r["m_1h"])))
            except (ValueError, KeyError):
                pass
    return rows


def clusters(path, col, k):
    """Кластер монеты по колонке пула: `k` равных групп по возрастанию значения."""
    vals = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                vals[r["symbol"]] = float(r[col])
            except (ValueError, KeyError):
                pass
    if not vals:
        return {}
    order = sorted(vals, key=lambda s: vals[s])
    size = max(1, len(order) // k)
    out = {}
    for i, s in enumerate(order):
        out[s] = min(i // size, k - 1)
    return out


def mean(xs):
    xs = list(xs)
    return statistics.mean(xs) if xs else 0.0


def move(rows, days):
    """Ход в день по набору касаний: сигналов/сутки × (mean m_1h − комиссии)."""
    n = len(rows) / days
    return n * (mean(r[3] for r in rows) - FEES)


def best_threshold(rows, days):
    best, best_v = None, None
    for t in A:
        sel = [r for r in rows if r[2] >= t]
        v = move(sel, days, )
        if best_v is None or v > best_v:
            best, best_v = t, v
    return best, (best_v or 0.0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--touches-root", required=True, help="study/touches (по суткам) или каталог прогона")
    p.add_argument("--instruments", help="instruments.csv для кластеров (колонка --cluster-col)")
    p.add_argument("--cluster-col", default="median_trade_lots", help="колонка пула для кластеров")
    p.add_argument("--clusters", type=int, default=3, help="сколько кластеров")
    p.add_argument("--min-signals", type=int, default=10,
                   help="касаний монеты на сутках A, чтобы выбирать её порог (параметр, не константа)")
    p.add_argument("--out", help="куда продублировать вывод")
    a = p.parse_args()

    rows = load(a.touches_root)
    if not rows:
        print(f"touches: строк нет в {a.touches_root}")
        return 0
    days = sorted({r[1] for r in rows})
    if len(days) < 2:
        print(f"touches: суток {len(days)} ({days}) — для out-of-sample нужно ≥ 2")
        return 0
    cl = clusters(a.instruments, a.cluster_col, a.clusters) if a.instruments else {}
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    say(f"# age-axis: суток {len(days)} ({days[0]}…{days[-1]}), монет {len(set(r[0] for r in rows))}, "
        f"касаний {len(rows)}")
    say(f"# пороги возраста {A} мин; ход в день = сигналов/сутки × (mean m_1h − {FEES} bps); "
        f"min-signals={a.min_signals}; кластеры: "
        + (f"по {a.cluster_col}, {a.clusters} групп" if cl else "нет (--instruments не задан)"))

    diffs, per_coin = [], collections.Counter()
    for da, db in itertools.permutations(days, 2):
        A_rows = [r for r in rows if r[1] == da]
        B_rows = [r for r in rows if r[1] == db]
        t_pool, _ = best_threshold(A_rows, 1)
        pool_b = move([r for r in B_rows if r[2] >= t_pool], 1)

        # кластеры: порог выбирается на A внутри кластера, применяется к монетам этого кластера на B
        cl_t = {}
        for c in set(cl.values()):
            sel = [r for r in A_rows if cl.get(r[0]) == c]
            cl_t[c], _ = best_threshold(sel, 1) if sel else (t_pool, 0.0)
        cluster_b = 0.0
        for c in set(cl.values()):
            syms = {s for s, cc in cl.items() if cc == c}
            cluster_b += move([r for r in B_rows if r[0] in syms and r[2] >= cl_t.get(c, t_pool)], 1)

        # по монете: порог монеты на A (при достатке сигналов), иначе пуловый
        coin_b, chosen = 0.0, collections.Counter()
        by_coin_a = collections.defaultdict(list)
        for r in A_rows:
            by_coin_a[r[0]].append(r)
        for sym in {r[0] for r in B_rows}:
            ra = by_coin_a.get(sym, [])
            t = best_threshold(ra, 1)[0] if len(ra) >= a.min_signals else t_pool
            chosen[t] += 1
            per_coin[t] += 1
            coin_b += move([r for r in B_rows if r[0] == sym and r[2] >= t], 1)

        diffs.append(coin_b - pool_b)
        say(f"A={da} B={db}: пуловый порог {t_pool} мин → на B {pool_b:+.0f} bps/день; "
            f"кластеры {cluster_b:+.0f} ({cl_t}); по монете {coin_b:+.0f} "
            f"(разница {coin_b - pool_b:+.0f}); пороги монет: "
            + ", ".join(f"{t}мин×{n}" for t, n in sorted(chosen.items())))

    wins = sum(1 for d in diffs if d > 0)
    say("")
    say(f"# итог: по-монетный порог бьёт пуловый в {wins} разбиениях из {len(diffs)}; "
        f"медиана разницы {statistics.median(diffs):+.0f} bps/день, "
        f"средняя {statistics.mean(diffs):+.0f}")
    say("# порог, который монеты выбирали чаще всего: "
        + ", ".join(f"{t} мин — {n} раз" for t, n in per_coin.most_common()))
    say("# решение владельцу: если разница не устойчиво положительна — возраст не уникален по монете, "
        "брать один порог (или порог по кластеру); устойчиво положительна — выбирать по монете")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        print(f"out: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
