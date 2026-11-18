"""П-42 §13.27 (TK-610): ворота по выходам движка и таблицам — до заморозки, без P&L августа (G6): печатаются счётчики, md5, булевы итоги.

  python3 tk610_gates.py g2 --win wa|wb|wc --run <out/<окно>-R1> --side L|S --cstar <tk599-frozen.json> [--root /data/p42-tk599] [--out <json>]
      G2: `<S>_r1` ≡ выход TK-599 для C* (md5 `*-trades|daily|coinday|entryrej.csv` и `meta.json`; содержимое P&L не читается).
  python3 tk610_gates.py g5 --win … --run <out/<окно>-R2> --r1 <out/<окно>-R1> --side L|S [--out <json>]
      G5: `<S>_g5pass` = R1 (4 csv байт в байт; `meta.json` — сверка счётчиков фильтра), `<S>_g5cut` — 0 сделок; `no_key = 0` у всех вариантов R2.
  python3 tk610_gates.py g1 --root /data/p42-tk599 --manifests <tk599-manifests.txt> --symbols <symbols.txt> --engine <alpha-master> [--allow <md5,…>] [--runs <out/…> …]
      G1: md5 движка ∈ допустимых (и равен записанному в манифестах), sha256 `symbols.txt`, манифесты окон `<root>/<окно>/MANIFEST` = записанным; `manifest-engine.txt` прогонов — тот же md5.
  python3 tk610_gates.py g3 --run-root <каталог out> --side L|S [--out <json>]
      G3: даты сделок R0 / R1 каждого окна (читается только `entry_ms`): ни одной позже 13.11 (сутки 135), в зачётном диапазоне окна есть сделки; печатаются числа.
  python3 tk610_gates.py g4 --cand <cand-….parquet> --ref <ep_u0.parquet | aug_u0.parquet> --ref-t0 <T0 эталона> --side long|short [--min-keys 200] [--out <json>]
      G4: f12 на общих ключах `(sym, абсолютный sig_ms, dist_e8, lp_e8)` равны эталону до 1e-9 (читаются только ключи и признаки эталона).
  python3 tk610_gates.py g9 --info <….parquet.info.json …> [--out <json>]
      G9: строк с `age_h` > 14 сут. в таблицах обучения и кандидатов — 0 (числа и доли печатаются).
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
        eq = bool(ma is not None and ma == mb)
        if n == "meta" and ma is not None and mb is not None and not eq:
            eq = _meta_sans_id(pa) == _meta_sans_id(pb)   # id варианта у сторон разный (`L_r1` против vid TK-599)
        out[n] = {"a": ma, "b": mb, "equal": eq}
    return out


def _meta_sans_id(path):
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    m.pop("id", None)
    return m


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


def parse_manifests(text):
    """`tk599-manifests.txt` → {секция: [строки]} (заголовок `== <имя>`)."""
    out, cur = {}, None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("== "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur is not None and line:
            out[cur].append(line)
    return out


def gate_g1(root, manifests, symbols, engine, allow=(), wins=("wa", "wb", "wc"), runs=()):
    with open(manifests, encoding="utf-8") as f:
        rec = parse_manifests(f.read())
    res = {"gate": "G1", "windows": {}, "runs": {}}
    ok = True
    md5 = T.md5_file(engine)
    recorded = {ln.split()[3] for sec in rec.values() for ln in sec if ln.startswith("binary ")}
    allowed = set(allow) | recorded
    res["engine_md5"], res["engine_allowed"] = md5, sorted(allowed)
    res["engine_ok"] = md5 in allowed
    ok &= res["engine_ok"]
    sha = T.sha256_file(symbols)
    res["symbols_sha256"] = sha
    res["symbols_ok"] = bool(rec.get("symbols")) and sha == rec["symbols"][0].split()[0]
    ok &= res["symbols_ok"]
    for w in wins:
        with open(os.path.join(root, w, "MANIFEST"), encoding="utf-8") as f:
            have = [ln.rstrip() for ln in f if ln.strip()]
        res["windows"][w] = {"equal": have == rec.get(w)}
        ok &= res["windows"][w]["equal"]
    for r in runs:
        p = os.path.join(r, "manifest-engine.txt")
        got = open(p, encoding="utf-8").read().split()[-1] if os.path.exists(p) else None
        res["runs"][r] = {"engine_md5": got, "ok": got in allowed}
        ok &= res["runs"][r]["ok"]
    res["ok"] = bool(ok)
    return res


def gate_g3(run_root, side):
    """Даты сделок R0 / R1 (только `entry_ms`): последние сутки ≤ 135, в зачётном диапазоне окна есть сделки."""
    name = rf.ENGINE_SIDE[side]
    res, ok = {"gate": "G3", "side": side, "runs": {}}, True
    for w in rf.WINDOWS:
        for tag in ("R0", "R1"):
            p = os.path.join(run_root, "%s-%s" % (w.name, tag), "eng", "%s_%s" % (side, tag.lower()), "%s-trades.csv" % name)
            if not os.path.exists(p):
                continue
            c = D.trades_per_day(p, w)
            days = np.nonzero(c)[0]
            lo, hi = T.credit_days(w)
            g = {"trades": int(c.sum()), "first_day": int(days.min()) if len(days) else None, "last_day": int(days.max()) if len(days) else None,
                 "in_credit": int(c[lo:hi + 1].sum()), "after_data_end": int(c[rf.NDAYS:].sum())}
            g["ok"] = bool(g["after_data_end"] == 0 and g["in_credit"] > 0)
            ok &= g["ok"]
            res["runs"]["%s-%s" % (w.name, tag)] = g
    res["ok"] = bool(ok and res["runs"])
    return res


def gate_g4(cand, ref, ref_t0, side, min_keys=200, tol=1e-9):
    """G4: f12 таблицы кандидатов (или обучения) = эталон на общих ключах; время эталона переводится в абсолютное (от 01.07) по его T0."""
    import datetime

    import pyarrow.parquet as pq
    import tk598_feat as TF
    mine = pd.read_parquet(cand)
    cols = ["sym", "sig_ms", "dist_e8", "lp_e8"] + [c for c in T.FEATS if c != "lturn24"] + ["turn24h"]
    r = pd.read_parquet(ref, columns=(["side"] if "side" in set(pq.ParquetFile(ref).schema.names) else []) + cols)
    if "side" in r:
        r = r[r["side"] == side]
    r = r.copy()
    r["lturn24"] = np.log10(r["turn24h"].astype(float).clip(lower=1))
    r["a_sig"] = r["sig_ms"].astype(np.int64) + (datetime.date.fromisoformat(ref_t0) - T.D0).days * T.DAY
    mine = mine[mine["tick_ok"]].copy()
    key = ["sym", "a_sig", "dist_e8", "lp_e8"]
    for df in (mine, r):
        df["a_sig"] = df["a_sig"].astype(np.int64)
        df["dist_e8"] = df["dist_e8"].astype(np.int64)
        df["lp_e8"] = df["lp_e8"].astype(np.int64)
    n = r.groupby(key).size().rename("n_ref").reset_index()
    r1 = r.merge(n, on=key)
    r1 = r1[r1["n_ref"] == 1].drop(columns=["n_ref", "sig_ms"])
    j = mine.merge(r1, on=key, suffixes=("", "_ref"), how="inner")
    res = {"gate": "G4", "side": side, "common_keys": int(len(j)), "min_keys": min_keys, "tol": tol, "features": {}}
    ok = len(j) >= min_keys
    for c in T.FEATS:
        v = TF.compare_values(j[c].to_numpy(), j[c + "_ref"].to_numpy(), tol)
        res["features"][c] = v
        ok &= v["n_mismatch"] == 0
    res["ok"] = bool(ok)
    return res


def gate_g9(infos):
    res, ok = {"gate": "G9", "tables": {}}, True
    for p in infos:
        with open(p, encoding="utf-8") as f:
            a = json.load(f)["age_censor"]
        res["tables"][os.path.basename(p)] = a
        ok &= a["gt_days"] == 0
    res["ok"] = bool(ok and infos)
    return res


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
    g1 = sp.add_parser("g1")
    g1.add_argument("--root", default=D.ROOT)
    g1.add_argument("--manifests", required=True)
    g1.add_argument("--symbols", required=True)
    g1.add_argument("--engine", required=True)
    g1.add_argument("--allow", default="")
    g1.add_argument("--runs", nargs="*", default=[])
    g1.add_argument("--out")
    g3 = sp.add_parser("g3")
    g3.add_argument("--run-root", required=True)
    g3.add_argument("--side", required=True, choices=list(rf.SIDES))
    g3.add_argument("--out")
    g4 = sp.add_parser("g4")
    g4.add_argument("--cand", required=True)
    g4.add_argument("--ref", required=True)
    g4.add_argument("--ref-t0", required=True)
    g4.add_argument("--side", required=True, choices=["long", "short"])
    g4.add_argument("--min-keys", type=int, default=200)
    g4.add_argument("--out")
    g9 = sp.add_parser("g9")
    g9.add_argument("--info", required=True, nargs="+")
    g9.add_argument("--out")
    s = sp.add_parser("sigma")
    s.add_argument("--run-root", required=True)
    s.add_argument("--side", required=True, choices=list(rf.SIDES))
    s.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "g2":
        r = gate_g2(a.win, a.run, a.side, a.cstar, a.root)
    elif a.cmd == "g5":
        r = gate_g5(a.win, a.run, a.r1, a.side)
    elif a.cmd == "g1":
        r = gate_g1(a.root, a.manifests, a.symbols, a.engine, [x for x in a.allow.split(",") if x], runs=a.runs)
    elif a.cmd == "g3":
        r = gate_g3(a.run_root, a.side)
    elif a.cmd == "g4":
        r = gate_g4(a.cand, a.ref, a.ref_t0, a.side, a.min_keys)
    elif a.cmd == "g9":
        r = gate_g9(a.info)
    else:
        r = sigma_search(a.run_root, a.side)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    sys.exit(0 if r.get("ok", True) else 1)


if __name__ == "__main__":
    main()
