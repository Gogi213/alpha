#!/usr/bin/env python3
"""П-42 §13.34 (TK-634): сырые признаки «подход» и «поток» на кандидатах баз — готовым производственным сборщиком, без нового кода признаков. P&L НЕ читает.
  python3 tk634_feat.py build --uni h775|o566 --window wa|wb|wc --side L|S [--procs 4]
Поток кандидатов — `<база>/<сторона>-sigsx.csv` (все строки дампа, как у таблиц `--ftab` TK-625/633). Эпизоды (66 колонок: mv5/15/60, maxmv1, turn1h/24h, tpm…) — Rust `alpha-feat f12`,
поток сделок (16 колонок FLOW) — Rust `alpha-feat flow` (`retitr_ftab.build_episodes` / `run_tk594`, как TK-599, §13.24.7); бинарь v6c 992cde46, склад суток TK-607 на чтение.
Выход: /data/p42-tk634/feat/<uni>/<окно>-<сторона>.csv `sym,sig_ms,<EPS_COLS>,<FLOW_COLS>` (sig_ms — мс от T0 окна; пусто = NaN), порядок — по (sym, sig_ms)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = "/data/p42-tk634"
BIN = "/data/p42-tk609/bin/alpha-feat-v6c.992cde46"
BIN_MD5_PREFIX = "992cde46"
PROD = "/data/tk594/py"
STORE = "/data/p42-tk599/tick-store"
SIDE_NAME = {"L": "long", "S": "short"}
EPS_COLS = ["mv5", "mv15", "mv60", "maxmv1", "maxrng15", "turn1h", "turn24h", "tpm15", "tpm60", "natr_bp"]
FLOW_COLS = ["tfv10", "tfv60", "tfv300", "tfv1800", "tfn10", "tfn60", "tfn300", "tfn1800", "acc_v", "acc_n", "big300", "big1800", "tfp300", "lmsz300", "msz_r", "vpin"]
RAW_COLS = EPS_COLS + FLOW_COLS


def fmt(x):
    x = float(x)
    return "" if x != x else repr(x)


def write_table(path, sym, sig_ms, cols):
    n = len(sym)
    order = sorted(range(n), key=lambda i: (sym[i], int(sig_ms[i])))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(RAW_COLS) + "\n")
        for i in order:
            f.write("%s,%d,%s\n" % (sym[i], int(sig_ms[i]), ",".join(fmt(cols[c][i]) for c in RAW_COLS)))


def cmd_build(a):
    import pandas as pd
    import retitr_ftab as rfx
    import tk604_stats as st
    import tk625_run as r5
    u = r5.Uni(a.uni)
    w = next(x for x in st.TRADE_WINDOWS if x.name == a.window)
    side = SIDE_NAME[a.side]
    sigsx = os.path.join(u.base(w.name, a.side), r5.ENG[a.side] + "-sigsx.csv")
    work = os.path.join(ROOT, "feat", u.name, "%s-%s" % (w.name, a.side))
    os.makedirs(work, exist_ok=True)
    md5 = hashlib.md5(open(BIN, "rb").read()).hexdigest()
    if not md5.startswith(BIN_MD5_PREFIX):
        sys.exit("бинарь %s: md5 %s ≠ %s…" % (BIN, md5, BIN_MD5_PREFIX))
    os.environ.update(P42_FEAT_BIN=BIN, TK594_STORE=STORE, TK594_STORE_CAP_GB="0", TK594_STORE_MIN_FREE_GB="0", TK594_TRADES=os.path.join(u.wdir(w.name), "aggtrades"))
    os.environ.pop("P42_F12_BIN", None)
    rfx.load_prod(PROD, os.path.join(u.wdir(w.name), "cache"), os.path.join(u.wdir(w.name), "aggtrades"), base_date=w.t0)
    sig = rfx._SR.read_sigsx(sigsx)
    if sig.duplicated(["sym", "sig_ms"]).any():
        sys.exit("в потоке sigsx есть дубль ключа (sym, sig_ms)")
    eps_path = os.path.join(work, "eps-%s.parquet" % a.side)
    flow_path = os.path.join(work, "tk594-flow-%s.parquet" % a.side)
    if a.force or not os.path.exists(eps_path):
        rfx.build_episodes(side, sigsx, eps_path, a.procs)
    if a.force or not os.path.exists(flow_path):
        rfx.run_tk594(PROD, eps_path, flow_path, "flow", a.procs, w.t0)
    eps = pd.read_parquet(eps_path)
    flow = pd.read_parquet(flow_path)
    use = eps[["ep_id", "sym", "sig_ms"] + EPS_COLS].merge(flow[["ep_id"] + FLOW_COLS], on="ep_id", how="left", validate="one_to_one")
    tab = sig[["sym", "sig_ms"]].merge(use[["sym", "sig_ms"] + RAW_COLS], on=["sym", "sig_ms"], how="left", validate="one_to_one")
    assert len(tab) == len(sig), "число строк таблицы не равно числу кандидатов потока"
    out = os.path.join(ROOT, "feat", u.name, "%s-%s.csv" % (w.name, a.side))
    write_table(out, list(tab["sym"]), tab["sig_ms"].to_numpy(np.int64), {c: tab[c].to_numpy(np.float64) for c in RAW_COLS})
    info = dict(uni=u.name, window=w.name, side=a.side, rows=int(len(tab)), sigsx=sigsx, bin_md5=md5, nan_share={c: round(float(tab[c].isna().mean()), 4) for c in RAW_COLS},
                sha256=hashlib.sha256(open(out, "rb").read()).hexdigest())
    json.dump(info, open(out + ".info.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--uni", required=True, choices=("h775", "o566"))
    ap.add_argument("--window", required=True, choices=("wa", "wb", "wc"))
    ap.add_argument("--side", required=True, choices=("L", "S"))
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
