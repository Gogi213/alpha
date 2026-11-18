"""П-42 §13.22 (TK-596): ворота до чтения и счёт сделок через выплату фандинга. Печатают только совпадение / число — суммы и цифры `S_es15` не показывают.

  python3 tk596_gate.py g3 --new <каталог прогона августа TK-596> --ref <каталог прогона rt-aug TK-595> [--skip 0GUSDT,龙虾USDT] [--margin-min 75]
      G3 (§13.22, п. 3 критика): сделки L_base (лонг) и S_base (шорт) по (sym, entry_ms, exit_ms, net) у монет, кроме --skip. Различия допустимы, только если каждое объяснено
      пересечением во времени со сделкой пропущенных монет нового прогона (лимит позиций p = 3, пауза монеты cool 5 мин, общая пауза w 10 + ww 60 мин → окно [вход, выход + margin], margin = 75 мин);
      печатает: совпало, только в ref, только в new, объяснено, необъяснено; код 1, если есть необъяснённое.
  python3 tk596_gate.py cross --run <каталог прогона> --cells S_es15:short,L_base:long,S_base:short --t0 <P42_T0> [--funding /data/p42-metrics/funding] [--notional 500]
      D1 (§13.22): сколько сделок держали позицию через момент выплаты фандинга (00/08/16 UTC от полуночи P42_T0: строго позже входа, строго раньше выхода) и граница влияния
      Σ |ставка| · $500 (В-89) по этим выплатам — ставки из месячных архивов `fundingRate`; печатается рядом с итогом, вердикт не меняет.
"""
import argparse
import csv
import datetime
import io
import os
import sys
import zipfile

FUND_MS = 8 * 3600 * 1000


def trades(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def key(r):
    return (r["sym"], int(r["entry_ms"]), int(r["exit_ms"]), r["net"])


def g3(new, ref, skip, margin_min):
    ok = True
    m = margin_min * 60_000
    for cell, side in (("L_base", "long"), ("S_base", "short")):
        tn = trades(os.path.join(new, cell, side + "-trades.csv"))
        a = sorted(key(r) for r in tn if r["sym"] not in skip)
        b = sorted(key(r) for r in trades(os.path.join(ref, cell, side + "-trades.csv")) if r["sym"] not in skip)
        sa, sb = set(a), set(b)
        only_new, only_ref = sorted(sa - sb), sorted(sb - sa)
        sk = [(int(r["entry_ms"]), int(r["exit_ms"]) + m) for r in tn if r["sym"] in skip]
        expl = lambda k: any(k[1] <= x1 and k[2] >= x0 for x0, x1 in sk)   # окно сделки пропущенной монеты [вход, выход + margin] пересекает сделку
        bad = [k for k in only_new + only_ref if not expl(k)]
        ok &= not bad
        print("G3 %s (%s): совпало %d, только в ref %d, только в new %d; сделок пропущенных монет в new %d; объяснено %d, необъяснено %d — %s" % (
            cell, side, len(sa & sb), len(only_ref), len(only_new), len(sk), len(only_new) + len(only_ref) - len(bad), len(bad), "OK" if not bad else "СТОП"))
        for k in bad[:10]:
            print("  необъяснено:", k)
    return 0 if ok else 1


def crosses(r):
    e, x = int(r["entry_ms"]), int(r["exit_ms"])
    return ((e // FUND_MS) + 1) * FUND_MS < x


def rates(funding_dir, sym, year, month, cache):
    """{calc_time_ms: ставка} месячного архива `fundingRate` монеты."""
    k = (sym, year, month)
    if k not in cache:
        path = os.path.join(funding_dir, sym, "%s-fundingRate-%04d-%02d.zip" % (sym, year, month))
        d = {}
        if os.path.exists(path):
            with zipfile.ZipFile(path) as z:
                for r in csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")):
                    d[int(r["calc_time"])] = float(r["last_funding_rate"])
        cache[k] = d
    return cache[k]


def cross(run, cells, t0, funding_dir, notional):
    t0_ms = int(datetime.datetime.fromisoformat(t0).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    cache = {}
    for c in cells.split(","):
        cell, side = c.split(":")
        t = trades(os.path.join(run, cell, side + "-trades.csv"))
        n = bound = miss = pays = 0
        for r in t:
            e, x = int(r["entry_ms"]), int(r["exit_ms"])
            if not crosses(r):
                continue
            n += 1
            k = (e // FUND_MS + 1) * FUND_MS
            while k < x:
                pays += 1
                at = datetime.datetime.fromtimestamp((t0_ms + k) / 1000, datetime.timezone.utc)
                d = rates(funding_dir, r["sym"], at.year, at.month, cache)
                hit = [v for ct, v in d.items() if abs(ct - (t0_ms + k)) <= 60_000]
                if hit:
                    bound += abs(hit[0]) * notional
                else:
                    miss += 1
                k += FUND_MS
        print("D1 %s (%s): сделок %d, через момент выплаты %d (выплат %d, без ставки в архиве %d); граница влияния Σ|ставка|·$%d = %.4f $" % (cell, side, len(t), n, pays, miss, notional, bound))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    g = sub.add_parser("g3")
    g.add_argument("--new", required=True)
    g.add_argument("--ref", required=True)
    g.add_argument("--skip", default="0GUSDT,龙虾USDT")
    g.add_argument("--margin-min", type=int, default=75)
    c = sub.add_parser("cross")
    c.add_argument("--run", required=True)
    c.add_argument("--cells", required=True)
    c.add_argument("--t0", required=True)
    c.add_argument("--funding", default="/data/p42-metrics/funding")
    c.add_argument("--notional", type=float, default=500.0)
    a = ap.parse_args(argv)
    if a.mode == "g3":
        return g3(a.new, a.ref, set(a.skip.split(",")), a.margin_min)
    return cross(a.run, a.cells, a.t0, a.funding, a.notional)


if __name__ == "__main__":
    sys.exit(main())
