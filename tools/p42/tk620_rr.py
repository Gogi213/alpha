"""TK-620 (§13.31 п. 4): risk:reward клетки/базы по склеенным сделкам — печатается, не ворота (требование критика 19.11).
  python tk620_rr.py --root /data/p42-tk599/out --cell L_base=c53a116125b39:long --cell S_base=c10fbba7be522:short [--out <отчёт.md>]
Каталоги — `<root>/<окно>/<ячейка>/<side>-trades.csv`; сделки — зачётные сутки (`short_autopsy_base.spliced_rows`); P&L сделки = gross − fees ($ при позиции 500 $, маржа при плече 2 = 250 $).
`rr_stats(rows)` — функция для `stops_redesign.py`."""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import short_autopsy_base as sb  # noqa: E402

MARGIN = 250.0                       # 500 $ / плечо 2
STOP_KINDS = ("stop", "nt")          # выходы «стоп» (`HardStop`, в т.ч. стоп от уровня/за проторговкой) и «отмена сценария до касания» (`nt`)


def rr_stats(rows):
    """Словарь: n, Σ, средний выигрыш/проигрыш ($), R:R = средний выигрыш / |средний проигрыш|, стоп-выходы (n, доля, средний/максимальный убыток в $ и % маржи), доли видов выходов."""
    pnl = np.array([float(r["pnl"]) for r in rows], dtype=float)
    kind = np.array([r["kind"] for r in rows])
    n = len(pnl)
    win, loss = pnl[pnl > 0], pnl[pnl < 0]
    mw = float(win.mean()) if len(win) else float("nan")
    ml = float(loss.mean()) if len(loss) else float("nan")
    out = dict(n=n, sigma=float(pnl.sum()), mean_trade=float(pnl.mean()) if n else float("nan"), n_win=int(len(win)), n_loss=int(len(loss)),
               mean_win=mw, mean_loss=ml, rr=(mw / abs(ml)) if len(win) and len(loss) else float("nan"))
    kinds = {}
    for k in sorted(set(kind.tolist())):
        m = kind == k
        kinds[k] = dict(n=int(m.sum()), share=float(m.mean()), sigma=float(pnl[m].sum()), mean=float(pnl[m].mean()), worst=float(pnl[m].min()))
    out["kinds"] = kinds
    st = np.isin(kind, STOP_KINDS)
    sl = pnl[st]
    out["stops"] = dict(n=int(st.sum()), share=float(st.mean()) if n else float("nan"),
                        mean_loss=float(sl.mean()) if len(sl) else float("nan"), max_loss=float(sl.min()) if len(sl) else float("nan"),
                        mean_loss_pct=float(sl.mean()) / MARGIN * 100 if len(sl) else float("nan"), max_loss_pct=float(sl.min()) / MARGIN * 100 if len(sl) else float("nan"))
    return out


def fmt(name, r):
    s = r["stops"]
    lines = ["#### %s" % name, "",
             "n %d, Σ %+.2f $, $/сделку %+.3f; выигрышей %d (средний %+.3f $), убытков %d (средний %+.3f $), **R:R = %.2f**." % (
                 r["n"], r["sigma"], r["mean_trade"], r["n_win"], r["mean_win"], r["n_loss"], r["mean_loss"], r["rr"]),
             "Стоп-выходы (`stop` + `nt`): %d (%.1f %% сделок), убыток средний %+.2f $ (%.2f %% маржи), максимальный %+.2f $ (%.2f %% маржи)." % (
                 s["n"], s["share"] * 100, s["mean_loss"], s["mean_loss_pct"], s["max_loss"], s["max_loss_pct"]),
             "", "| выход | n | доля | Σ, $ | $/сделку | худший, $ |", "|---|---:|---:|---:|---:|---:|"]
    for k, v in r["kinds"].items():
        lines.append("| %s | %d | %.1f %% | %+.2f | %+.3f | %+.2f |" % (k, v["n"], v["share"] * 100, v["sigma"], v["mean"], v["worst"]))
    lines.append("")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--cell", action="append", required=True, help="имя=ячейка:сторона")
    ap.add_argument("--out")
    a = ap.parse_args()
    lines = ["# TK-620 — risk:reward баз (зачётные сутки, 566 монет)", ""]
    for spec in a.cell:
        name, rest = spec.split("=")
        cid, side = rest.split(":")
        rows = sb.spliced_rows(lambda w: os.path.join(a.root, w, cid, side + "-trades.csv"))
        lines += fmt(name, rr_stats(rows))
    text = "\n".join(lines)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
