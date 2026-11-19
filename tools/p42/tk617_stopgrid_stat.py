"""TK-617 v3: статистика сетки жёсткого стопа против базы стороны — парная ΔΣ по суткам, Холм по всем клеткам (26), ворота g1–g4 как §13.30 п. 5 (порогов новых нет).
  python tk617_stopgrid_stat.py --out-root /data/p42-tk617/stopgrid/out --variants variants.txt --symbols /data/p42-t566/symbols.txt --funding /data/p42-metrics/funding --report stopgrid-stat.md
Ряды — `short_levers.ingest` (склейка окон, ворота G6/G3, фандинг границы B), оценка — `short_levers.evaluate`; сторона L читает `long-trades.csv` (`short_levers.SIDE`)."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402
import short_levers as sl  # noqa: E402

MON = rf.MONTH_NAMES


def neighbours(ids):
    out = {}
    for fam in ("s", "sn", "ls"):
        cells = [c for c in ids if c.split("_", 1)[1].startswith(fam) and c.split("_", 1)[1][len(fam):].replace(".", "").isdigit()]
        cells.sort(key=lambda c: float(c.split("_", 1)[1][len(fam):]))
        for i, c in enumerate(cells):
            out[c] = [cells[j] for j in (i - 1, i + 1) if 0 <= j < len(cells)]
    return out


def main(a):
    ids = [ln.split()[0] for ln in open(a.variants, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    coins = [x for x in open(a.symbols, encoding="utf-8").read().replace("\n", ",").split(",") if x]
    rates = rf.load_funding_rates(a.funding, coins)
    res, base_sum = {}, {}
    for side, pre in (("long", "L_"), ("short", "S_")):
        sl.SIDE = side
        base = sl.ingest(a.out_root, pre + "base", coins, rates)
        base_sum[pre] = (float(base.val.sum()), int(len(base.val)))
        for cid in [c for c in ids if c.startswith(pre) and c != pre + "base"]:
            res[cid] = sl.evaluate(sl.ingest(a.out_root, cid, coins, rates), base, len(coins), a.nboot)
    cells = list(res)
    rej = rf.holm_reject([res[c]["p"] for c in cells])
    nb = neighbours(cells)
    out = ["# TK-617 v3: сетка жёсткого стопа против базы стороны, парная ΔΣ по суткам (136), найдено на этих данных\n",
           "Холм по m = %d (все клетки обеих сторон, односторонне 5 %%), блоки 4 сут., %d повторов; ворота g1–g4 — §13.30 п. 5 без изменений (g1 Холм, g2 ≥ 4 из 5 месяцев ΔΣ > 0, g3 Δ$/сделку + SE ≥ 0, g4 без 2 суток и без 2 монет). Базы: L_base Σ %+.2f / %d сделок, S_base Σ %+.2f / %d.\n"
           % (len(cells), a.nboot, base_sum["L_"][0], base_sum["L_"][1], base_sum["S_"][0], base_sum["S_"][1]),
           "| клетка | сделок | Σ $ | ΔΣ ± SE | z | p | Холм | Δ$/сделку ± SE | " + " | ".join("Δ " + m for m in MON) + " | g1 g2 g3 g4 | без топ-2 суток / монет | плато (соседи ΔΣ>0) |",
           "|---|---:|---:|---|---:|---:|---|---|" + "---|" * len(MON) + "---|---|---|"]
    for c, ok in zip(cells, rej):
        x = res[c]
        g = sl.gates(x, bool(ok))
        npl = nb.get(c, [])
        plat = "/".join("%+.0f" % res[n]["dsum"] for n in npl) if npl else ""
        out.append("| %s | %d | %+.2f | %+.2f ± %.2f | %+.2f | %.4f | %s | %+.3f ± %.3f | %s | %s | %+.1f / %+.1f | %s |" % (
            c, x["n"], x["sum"], x["dsum"], x["se_sum"], x["z"], x["p"], "да" if ok else "нет", x["d_mean"], x["se_mean"],
            " | ".join("%+.1f" % m[0] for m in x["months"]), "".join("✓" if v else "✗" for v in g), x["no_top2_days"], x["no_top2_coins"], plat))
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    open(a.report, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("cells", len(cells), "holm_rejected", [c for c, ok in zip(cells, rej) if ok])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--funding", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--nboot", type=int, default=rf.NBOOT)
    main(ap.parse_args())
