"""П-42 §13.27 (TK-610): ворота по выходам движка и таблицам — до заморозки, без P&L августа (G6): печатаются счётчики, md5, булевы итоги.

  python3 tk610_gates.py g2 --win wa|wb|wc --run <out/<окно>-R1> --side L|S --cstar <tk599-frozen.json> [--root /data/p42-tk599] [--out <json>]
      G2: `<S>_r1` ≡ выход TK-599 для C* (md5 `*-trades|daily|coinday|entryrej.csv` и `meta.json`; содержимое P&L не читается).
  python3 tk610_gates.py g5 --win … --run <out/<окно>-R2> --r1 <out/<окно>-R1> --side L|S [--out <json>]
      G5: `<S>_g5pass` = R1 (4 csv байт в байт; `meta.json` — сверка счётчиков фильтра), `<S>_g5cut` — 0 сделок; `no_key = 0` у всех вариантов R2.
  python3 tk610_gates.py sigma --run-root <каталог out> --side L|S [--out <json>]
      σ, среднее и число сделок C* (R1) в окне поиска: строки с сутками входа в зачётном диапазоне окна и в блоках Б1…Б4 (август не читается).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402
import tk610_data as D  # noqa: E402
import tk610_lib as T  # noqa: E402

CSV4 = ("trades", "daily", "coinday", "entryrej")


def _files(d, side):
    return [(n, os.path.join(d, "%s-%s.csv" % (side, n))) for n in CSV4] + [("meta", os.path.join(d, "meta.json"))]


def compare_dirs(a, b, side):
    """md5 файлов выходов двух вариантов: {файл: (md5 a, md5 b, равны)}; нет файла — None."""
    out = {}
    for (n, pa), (_, pb) in zip(_files(a, side), _files(b, side)):
        ma = T.md5_file(pa) if os.path.exists(pa) else None
        mb = T.md5_file(pb) if os.path.exists(pb) else None
        out[n] = {"a": ma, "b": mb, "equal": bool(ma is not None and ma == mb)}
    return out


def meta_filter(path, side):
    """Счётчики фильтра признака `[cands, dropped, no_key, no_value]` из meta.json (нет — None)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)[side].get("filter_[cands,dropped,no_key,no_value]")


def csv_rows(path):
    with open(path, "rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def gate_g2(win, run, side, cstar, root):
    c = T.load_cstar(cstar)[side]
    vid = rf.Req(side, c["keys"], False).vid
    ref = os.path.join(root, "out", win, vid)
    name = rf.ENGINE_SIDE[side]
    cmp_ = compare_dirs(os.path.join(run, "eng", "%s_r1" % side), ref, name)
    return {"gate": "G2", "win": win, "side": side, "ref": ref, "files": cmp_, "ok": bool(all(v["equal"] for v in cmp_.values()))}


def gate_g5(win, run, r1, side):
    name = rf.ENGINE_SIDE[side]
    r1d = os.path.join(r1, "eng", "%s_r1" % side)
    pd_ = os.path.join(run, "eng", "%s_g5pass" % side)
    cd = os.path.join(run, "eng", "%s_g5cut" % side)
    r2d = os.path.join(run, "eng", "%s_r2" % side)
    cmp_ = compare_dirs(pd_, r1d, name)
    csv_ok = all(cmp_[n]["equal"] for n in CSV4)
    cut_rows = csv_rows(os.path.join(cd, "%s-trades.csv" % name))
    counters = {v: meta_filter(os.path.join(d, "meta.json"), name) for v, d in (("g5pass", pd_), ("g5cut", cd), ("r2", r2d), ("r1", r1d))}
    no_key_ok = all(c is not None and c[2] == 0 for k, c in counters.items() if k != "r1")
    # кандидатов столько же, сколько у R1; «режет всех» — все кандидаты, кроме NaN, отброшены
    cands_ok = all(counters[v] is not None and counters["r1"] is not None and counters[v][0] == counters["r1"][0] for v in ("g5pass", "g5cut", "r2"))
    return {"gate": "G5", "win": win, "side": side, "pass_vs_r1": cmp_, "pass_csv_equal": bool(csv_ok), "cut_trades": cut_rows, "filter_counters": counters,
            "no_key_zero": bool(no_key_ok), "cands_equal_r1": bool(cands_ok), "ok": bool(csv_ok and cut_rows == 0 and no_key_ok and cands_ok)}


def sigma_search(run_root, side):
    """σ, среднее и число сделок C* (R1) в окне поиска. Читаются метки только строк с сутками входа в зачётном диапазоне окна и в блоках поиска."""
    name = rf.ENGINE_SIDE[side]
    vals = []
    per = {}
    for w in rf.WINDOWS:
        p = os.path.join(run_root, "%s-R1" % w.name, "eng", "%s_r1" % side, "%s-trades.csv" % name)
        if not os.path.exists(p):
            continue
        off = T.win_offset_ms(w)
        got = []
        for ch in pd.read_csv(p, chunksize=D.CHUNK):
            keep = D.search_keep(ch["entry_ms"].to_numpy(np.int64) + off, w)
            if keep.any():
                got.append((ch["gross"] - ch["fees"]).to_numpy(float)[keep])
        v = np.concatenate(got) if got else np.zeros(0)
        per[w.name] = int(len(v))
        vals.append(v)
    v = np.concatenate(vals) if vals else np.zeros(0)
    return {"side": side, "n": int(len(v)), "mean": float(v.mean()) if len(v) else None, "sigma": float(v.std(ddof=1)) if len(v) > 1 else None, "n_by_win": per}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("g2", "g5"):
        p = sp.add_parser(name)
        p.add_argument("--win", required=True, choices=list(D.WINS))
        p.add_argument("--run", required=True)
        p.add_argument("--side", required=True, choices=list(rf.SIDES))
        p.add_argument("--out")
        if name == "g2":
            p.add_argument("--cstar", required=True)
            p.add_argument("--root", default=D.ROOT)
        else:
            p.add_argument("--r1", required=True)
    s = sp.add_parser("sigma")
    s.add_argument("--run-root", required=True)
    s.add_argument("--side", required=True, choices=list(rf.SIDES))
    s.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "g2":
        r = gate_g2(a.win, a.run, a.side, a.cstar, a.root)
    elif a.cmd == "g5":
        r = gate_g5(a.win, a.run, a.r1, a.side)
    else:
        r = sigma_search(a.run_root, a.side)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    sys.exit(0 if r.get("ok", True) else 1)


if __name__ == "__main__":
    main()
