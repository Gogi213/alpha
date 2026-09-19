#!/usr/bin/env python3
"""Разбор сетки форм по монетам (H1 в docs/plan/handoff-2026-09-19.md).

Сетка `lob bounce-grid` считает пул целиком, вердикт `lob bounce-verdict` даёт интервал по пулу
(кластер — сутки, а при суток < 7 — час UTC). Вопрос владельца — «на каких монетах» (В-67),
поэтому нужен дешёвый срез: `b5/<метка>/forms.csv` — сводка symbol × сутки × форма
(сигналы, круги, «занято», sum_net_bps, причины выхода, n_eaten/n_partial у E7).

    python3 tools/compute/grid-coins.py b5/v68lat-base-any/forms.csv \
            [--vs b5/v69e7-base-any/forms.csv] [--top 6] [--min-fills 5] [--csv out.csv]

Печатает: формы по пулу, монеты лучших форм (лучшие/худшие), монету через формы, монеты с
n_fills >= порога и sum_net > 0, сверку «дробная доля против одного лота» (--vs).
Интервалов не считает: по монете 1–7 кругов, интервал не построить — это срез, не вердикт.
"""
import argparse
import collections
import csv
import sys

FIELDS = ("n_signals", "n_fills", "n_busy", "sum_net_bps", "n_stop", "n_take", "n_deadline",
          "n_eaten", "n_partial", "n_horizon")


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            for k in FIELDS:
                r[k] = float(r.get(k) or 0.0)
            rows.append(r)
    return rows


def split_form(form):
    """`pct0.5-eat50x80-3600` -> (стоп, тейк, дедлайн)."""
    parts = form.split("-")
    return "-".join(parts[:-2]), parts[-2], parts[-1]


def per_form(rows):
    """(форма) -> суммы по пулу."""
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    coins, days = collections.defaultdict(set), collections.defaultdict(set)
    for r in rows:
        a = agg[r["form"]]
        for k in FIELDS:
            a[k] += r[k]
        if r["n_fills"]:
            coins[r["form"]].add(r["symbol"])
            days[r["form"]].add(r["day_utc"])
    return agg, coins, days


def per_coin(rows, form):
    """(символ) -> суммы по суткам для одной формы."""
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    days = collections.defaultdict(set)
    for r in rows:
        if r["form"] != form:
            continue
        a = agg[r["symbol"]]
        for k in FIELDS:
            a[k] += r[k]
        if r["n_fills"]:
            days[r["symbol"]].add(r["day_utc"])
    return agg, days


def net_per_fill(a):
    return a["sum_net_bps"] / a["n_fills"] if a["n_fills"] else 0.0


def print_pool(rows, top, min_fills):
    agg, coins, days = per_form(rows)
    by_coin = collections.defaultdict(list)
    for (sym, form), a in per_coin_rows(rows).items():
        if a["n_fills"]:
            by_coin[form].append((sym, a))
    ranked = sorted(agg.items(), key=lambda kv: -net_per_fill(kv[1]))
    print(f"== формы по пулу ({len(ranked)} форм, {len({r['symbol'] for r in rows})} монет, "
          f"{len({r['day_utc'] for r in rows})} суток)")
    print(f"{'форма':<22}{'сигн':>6}{'круги':>6}{'занято':>7}{'sum_net':>9}{'на круг':>8}"
          f"{'монет':>6}{'+:':>4}{'лучшая (круги)':>22}{'худшая (круги)':>22}")
    for form, a in ranked[:top] + ranked[-2:]:
        pairs = by_coin[form]
        for floor in (min_fills, 2, 1):
            thick = [p for p in pairs if p[1]["n_fills"] >= floor]
            if len(thick) >= 2:
                break
        name = lambda p: f"{p[0][:-4]} {p[1]['sum_net_bps']:+.0f} ({p[1]['n_fills']:.0f})"
        best = name(max(thick, key=lambda p: p[1]["sum_net_bps"]))
        worst = name(min(thick, key=lambda p: p[1]["sum_net_bps"]))
        pos = sum(1 for _, c in pairs if c["sum_net_bps"] > 0)
        print(f"{form:<22}{a['n_signals']:>6.0f}{a['n_fills']:>6.0f}{a['n_busy']:>7.0f}"
              f"{a['sum_net_bps']:>9.1f}{net_per_fill(a):>8.2f}{len(coins[form]):>6}{pos:>4}"
              f"{best:>22}{worst:>22}")


def print_form_coins(rows, form, min_fills, width=8):
    agg, days = per_coin(rows, form)
    ranked = sorted(agg.items(), key=lambda kv: -kv[1]["sum_net_bps"])
    with_fills = [kv for kv in ranked if kv[1]["n_fills"]]
    print(f"== монеты формы {form}: кругов {sum(a['n_fills'] for _, a in with_fills):.0f} "
          f"у {len(with_fills)} монет")
    print(f"{'монета':<16}{'круги':>7}{'sum_net':>10}{'на круг':>9}{'занято':>8}{'суток':>6}")
    for sym, a in with_fills[:width] + with_fills[-3:]:
        print(f"{sym:<16}{a['n_fills']:>7.0f}{a['sum_net_bps']:>10.1f}{net_per_fill(a):>9.2f}"
              f"{a['n_busy']:>8.0f}{len(days[sym]):>6}")
    thick = [(s, a) for s, a in with_fills if a["n_fills"] >= min_fills]
    print(f"   монет с >= {min_fills} кругами: {len(thick)}; из них sum_net > 0: "
          f"{sum(1 for _, a in thick if a['sum_net_bps'] > 0)}")


def print_coins_all_forms(rows, min_fills):
    """Монета через все формы: где вообще были круги и где сумма плюс."""
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    forms = collections.defaultdict(set)
    for r in rows:
        if not r["n_fills"]:
            continue
        a = agg[r["symbol"]]
        for k in FIELDS:
            a[k] += r[k]
        forms[r["symbol"]].add(r["form"])
    ranked = sorted(agg.items(), key=lambda kv: -kv[1]["sum_net_bps"])
    print(f"== монеты через все формы ({len(ranked)} монет с кругами)")
    print(f"{'монета':<16}{'круги':>7}{'sum_net':>10}{'форм':>6}")
    for sym, a in ranked[:10] + ranked[-6:]:
        print(f"{sym:<16}{a['n_fills']:>7.0f}{a['sum_net_bps']:>10.1f}{len(forms[sym]):>6}")
    thick = [(s, a) for s, a in ranked if a["n_fills"] >= min_fills]
    print(f"   монет с >= {min_fills} кругами: {len(thick)}; из них sum_net > 0: "
          f"{sum(1 for _, a in thick if a['sum_net_bps'] > 0)}")


def print_vs(frac_rows, base_rows):
    """Дробный выход (E7) против одного лота (база): пара по стопу и дедлайну."""
    frac = per_form(frac_rows)[0]
    base = per_form(base_rows)[0]
    pairs, missing = [], []
    for form, a in frac.items():
        stop, take, dl = split_form(form)
        ctrl = f"{stop}-1to1-{dl}"
        if ctrl in base:
            pairs.append((stop, take, dl, a, base[ctrl]))
        else:
            missing.append(form)
    pairs.sort(key=lambda p: (p[0], p[2]))
    print(f"== дробный выход против одного лота (--vs): пар {len(pairs)}"
          + (f", без контроля {missing}" if missing else ""))
    print(f"{'стоп':<8}{'тейк':<10}{'дедл':>6}{'кр.дроб':>8}{'дроб net':>9}{'дроб/круг':>10}"
          f"{'eat':>6}{'part':>6}{'кр.1to1':>8}{'1to1 net':>9}{'1to1/круг':>10}")
    for stop, take, dl, a, b in pairs:
        print(f"{stop:<8}{take:<10}{dl:>6}{a['n_fills']:>8.0f}{a['sum_net_bps']:>9.1f}"
              f"{net_per_fill(a):>10.2f}{a['n_eaten']:>6.0f}{a['n_partial']:>6.0f}"
              f"{b['n_fills']:>8.0f}{b['sum_net_bps']:>9.1f}{net_per_fill(b):>10.2f}")


def print_by_day(rows, forms):
    """По суткам: ровно то, что вердикт берёт кластером (В-60)."""
    print("== по суткам (кластер вердикта)")
    for form in forms:
        per_day = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
        for r in rows:
            if r["form"] != form:
                continue
            per_day[r["day_utc"]][0] += r["n_fills"]
            per_day[r["day_utc"]][1] += r["sum_net_bps"]
            per_day[r["day_utc"]][2] += r["n_signals"]
        cells = ", ".join(f"{d}: {v[2]:.0f} сигн {v[0]:.0f} кр {v[1]:+.0f}"
                          for d, v in sorted(per_day.items()))
        print(f"{form:<22}{cells}")


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "form", "n_fills", "n_busy", "sum_net_bps", "net_per_fill_bps",
                    "n_eaten", "n_partial"])
        for (sym, form), a in sorted(per_coin_rows(rows).items()):
            if not (a["n_fills"] or a["n_busy"] or a["n_eaten"]):
                continue
            w.writerow([sym, form, f"{a['n_fills']:.0f}", f"{a['n_busy']:.0f}",
                        f"{a['sum_net_bps']:.1f}", f"{net_per_fill(a):.2f}",
                        f"{a['n_eaten']:.0f}", f"{a['n_partial']:.0f}"])


def per_coin_rows(rows):
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in rows:
        a = agg[(r["symbol"], r["form"])]
        for k in FIELDS:
            a[k] += r[k]
    return agg


def main():
    p = argparse.ArgumentParser()
    p.add_argument("forms", help="b5/<метка>/forms.csv")
    p.add_argument("--vs", help="forms.csv контрольной сетки (дробный выход против одного лота)")
    p.add_argument("--top", type=int, default=6, help="сколько форм печатать (умолчание 6)")
    p.add_argument("--min-fills", type=int, default=5, help="порог «толстой» монеты (умолчание 5)")
    p.add_argument("--csv", help="куда выгрузить symbol × form")
    a = p.parse_args()
    rows = load(a.forms)
    print(f"# {a.forms}: строк {len(rows)}")
    print_pool(rows, a.top, a.min_fills)
    agg = per_form(rows)[0]
    best = [f for f, _ in sorted(agg.items(), key=lambda kv: -net_per_fill(kv[1]))[:2]]
    for form in best:
        print_form_coins(rows, form, a.min_fills)
    print_coins_all_forms(rows, a.min_fills)
    print_by_day(rows, [f for f, _ in sorted(agg.items(), key=lambda kv: -net_per_fill(kv[1]))[:4]])
    if a.vs:
        print_vs(rows, load(a.vs))
        for form in best:
            print_form_coins(rows, form, a.min_fills)
    if a.csv:
        write_csv(a.csv, rows)
        print(f"# csv: {a.csv}")


if __name__ == "__main__":
    sys.exit(main())
