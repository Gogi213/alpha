"""П-42 §13.27 (TK-610): файлы вариантов, строки обучения R0, признаки f12 кандидатов R1, счётчики. Метки и P&L августа и зоны не читаются (G6).

  python3 tk610_data.py variants --cstar <tk599-frozen.json> | --bases <tk599-grids.json> --out <каталог> [--q <каталог с q-<S>.json>]
      R0.txt / R1.txt (обе стороны), при `--q` — R2-<S>.txt по стороне (вариант оценки и клетки G5); ключи — `tk610_lib.variant_rows`.
  python3 tk610_data.py r0 --win wa|wb|wc --run <out/<окно>-R0> --side L|S --prod <копия tools/p42> --work <каталог> [--procs 2] [--root /data/p42-tk599]
      строки обучения окна: сделки `<S>_r0` в зачётном диапазоне окна и в блоках поиска (август не читается — отбрасывается при чтении кусками), метки, f12 → `<work>/train-<окно>-<S>.parquet`.
  python3 tk610_data.py cand --win … --run <out/<окно>-R1> --side L|S --prod … --work … [--procs 2]
      признаки f12 на ВСЕХ кандидатах потока R1 (`<S>_r1-sigsx`): `<work>/cand-<окно>-<S>.parquet` — ключ, сутки от 01.07, f12.
  f12: при `TK610_F12_BIN=<alpha-feat-f12d с md5 в имени>` — Rust TK-611 (md5 проверяется, склад `/data/p42-tk599/tick-store` на чтение), иначе производственный Python; реализация пишется в `.info.json`.
  python3 tk610_data.py count --win … --run <out/<окно>-R2-<S>> --variant <id> --side L|S
      счётчики сделок по суткам августа (читается ТОЛЬКО столбец `entry_ms` — метки и P&L не открываются).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_ftab as rfx  # noqa: E402
import retitr_full as rf  # noqa: E402
import tk598_lib as L598  # noqa: E402
import tk610_lib as T  # noqa: E402

ROOT = "/data/p42-tk599"
SIDE_NAME = rf.ENGINE_SIDE
WINS = {w.name: w for w in rf.WINDOWS}
CHUNK = 500_000


def win_env(win, root=ROOT):
    """(T0, кэш минуток, каталог aggTrades) окна TK-599 — только чтение."""
    return WINS[win].t0, os.path.join(root, win, "cache"), os.path.join(root, win, "aggtrades")


# ───────────────────────────── варианты ─────────────────────────────

def cmd_variants(a):
    if a.cstar:
        cstar = T.load_cstar(a.cstar)
    else:
        with open(a.bases, encoding="utf-8") as f:
            cstar = T.load_cstar({"sides": json.load(f)["bases"]})
    q = None
    if a.q:       # каталог с q-<S>.json (результат `tk610_fit.py score`)
        q = {s: json.load(open(os.path.join(a.q, "q-%s.json" % s), encoding="utf-8"))["q"] for s in rf.SIDES if os.path.exists(os.path.join(a.q, "q-%s.json" % s))}
    rows = T.variant_rows(cstar, q)
    os.makedirs(a.out, exist_ok=True)
    for r in ("R0", "R1"):
        with open(os.path.join(a.out, r + ".txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(T.variants_text(rows[r]))
    for s in rf.SIDES:
        r2 = [x for x in rows["R2"] if x[0].startswith(s + "_")]
        if r2:
            with open(os.path.join(a.out, "R2-%s.txt" % s), "w", encoding="utf-8", newline="\n") as f:
                f.write(T.variants_text(r2))
    print(json.dumps({r: [x[0] for x in v] for r, v in rows.items()}, ensure_ascii=False))


# ───────────────────────────── строки обучения R0 ─────────────────────────────

def search_keep(entry_abs_ms, win):
    """Строка обучения: сутки входа внутри зачётного диапазона окна и в блоках поиска (август и всё иное — вне)."""
    day = np.asarray(entry_abs_ms) // T.DAY
    lo, hi = T.credit_days(win)
    return (day >= lo) & (day <= hi) & (T.block_of_abs(day) >= 0)


def read_keys_dates(trades_csv, win):
    """Первый проход — только ключи и времена (метки не читаются): ключи сделок, которые идут в обучение."""
    off = T.win_offset_ms(win)
    k = pd.read_csv(trades_csv, usecols=T.KEY + ["entry_ms", "exit_ms"])
    keep = search_keep(k["entry_ms"].to_numpy(np.int64) + off, win)
    return k[keep].reset_index(drop=True), int(len(k)), int(keep.sum())


def read_rows(trades_csv, detail_jsonl, win, keys):
    """Второй проход — метки только у строк обучения: trades.csv читается кусками, строки вне `keys` сбрасываются сразу; detail.jsonl — номинал по ключу."""
    off = T.win_offset_ms(win)
    parts = []
    for ch in pd.read_csv(trades_csv, chunksize=CHUNK):
        keep = search_keep(ch["entry_ms"].to_numpy(np.int64) + off, win)
        if keep.any():
            parts.append(ch[keep])
    tr = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    kk = set(zip(keys["sym"], keys["level"].astype(np.int64), keys["sig_ms"].astype(np.int64)))
    nom = {}
    with open(detail_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            key = (r["sym"], int(r["level"]), int(r["sig_ms"]))
            if key in kk:
                nom[key] = L598.nominal_usd(r)
    tr["nominal"] = [nom.get((s, int(l), int(g))) for s, l, g in zip(tr["sym"], tr["level"], tr["sig_ms"])]
    if tr["nominal"].isna().any():
        raise T.GuardError("строки trades.csv без записи detail.jsonl")
    if tr.duplicated(T.KEY).any():
        raise T.GuardError("ключ (sym, level, sig_ms) не уникален")
    return tr


F12_RUST_MD5 = "65519d8ff44e5be924f662609f9d54f7"     # TK-611 `alpha-feat-f12d` (коммит 465874417): побитовое совпадение с CandWorker на 111 таблицах TK-599 и `ep_u0`
F12_REC = {"impl": "python", "bin_md5": None}        # чем посчитан f12 в этом процессе — пишется в `.info.json`, затем в заморозку
F12_STORE = "/data/p42-tk599/tick-store"


def _f12_rust(side, sigsx_csv, ep_csv, procs, ctx):
    """f12 Rust-бинарём TK-611 (`TK610_F12_BIN` — путь с md5 в имени); склад суток — только чтение. Возвращает таблицу как `build_episodes`."""
    binp = os.environ["TK610_F12_BIN"]
    md5 = T.md5_file(binp)
    if md5 != F12_RUST_MD5:
        raise T.GuardError("md5 f12-бинаря %s ≠ принятого %s" % (md5, F12_RUST_MD5))
    cache, trades, t0 = ctx
    cmd = [binp, "f12", "--sigsx", sigsx_csv, "--side", side, "--out", ep_csv, "--cache", cache, "--trades", trades, "--base-date", str(t0),
           "--days", "80", "--threads", str(procs), "--store", F12_STORE, "--store-cap-gb", "0"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise T.GuardError("f12-бинарь rc=%d: %s" % (r.returncode, r.stderr[-400:]))
    F12_REC.update({"impl": "rust", "bin_md5": md5, "days": 80, "store": F12_STORE, "summary": r.stdout.strip().splitlines()[-1][:400] if r.stdout.strip() else ""})
    return read_f12_csv(ep_csv)


def read_f12_csv(ep_csv):
    """CSV Rust-f12 → таблица; числа разбираются точно (быстрый разбор pandas даёт ±ULP — тест `test_read_f12_csv_exact`)."""
    ep = pd.read_csv(ep_csv, dtype={"sym": str}, float_precision="round_trip")
    if ep["tick_ok"].dtype != bool:
        ep["tick_ok"] = ep["tick_ok"].astype(str).str.lower().isin(["true", "1"])
    return ep


def episode_features(side, sigsx_csv, work, tag, procs, ctx=None):
    """f12 по сигналам `sigsx_csv`: Rust-бинарь TK-611 (`TK610_F12_BIN` задан и есть `ctx` = (кэш, aggTrades, T0)) или производственный Python (`retitr_ftab.build_episodes`, маршрут U0):
    таблица KEY + tick_ok + FEATS (lturn24 = log10(turn24h.clip(1)))."""
    os.makedirs(work, exist_ok=True)
    if os.environ.get("TK610_F12_BIN") and ctx is not None:
        ep = _f12_rust(side, sigsx_csv, os.path.join(work, "eps-%s.csv" % tag), procs, ctx)
    else:
        ep_path = os.path.join(work, "eps-%s.parquet" % tag)
        rfx.build_episodes(side, sigsx_csv, ep_path, procs)
        ep = pd.read_parquet(ep_path)
    ep["level"] = ep["level"].astype(np.int64)
    ep["sig_ms"] = ep["sig_ms"].astype(np.int64)
    if "tick_ok" not in ep:
        ep["tick_ok"] = True
    ep["tick_ok"] = ep["tick_ok"].fillna(False).astype(bool)
    ep["lturn24"] = np.log10(ep["turn24h"].astype(float).clip(lower=1)) if "turn24h" in ep else np.nan
    for c in ["dist_e8", "lp_e8"] + T.FEATS:      # dist_e8 / lp_e8 — личность уровня для ворот G4
        if c not in ep:
            ep[c] = np.nan
    out = ep[T.KEY + ["dist_e8", "lp_e8", "tick_ok"] + T.FEATS].copy()
    if out.duplicated(T.KEY).any():
        raise T.GuardError("дубль ключа в таблице признаков")
    return out


def cmd_r0(a):
    win = WINS[a.win]
    t0, cache, trades = win_env(a.win, a.root)
    side = SIDE_NAME[a.side]
    d = os.path.join(a.run, "eng", "%s_r0" % a.side)
    keys, n_all, n_keep = read_keys_dates(os.path.join(d, side + "-trades.csv"), win)
    print("R0 %s %s: сделок %d, строк обучения %d" % (a.win, a.side, n_all, n_keep), flush=True)
    rfx.load_prod(a.prod, cache, trades, days=80, base_date=t0)
    sig = rfx._SR.read_sigsx(os.path.join(d, side + "-sigsx.csv"))
    sig["level"] = sig["level"].astype(np.int64)
    sig["sig_ms"] = sig["sig_ms"].astype(np.int64)
    m = sig.merge(keys[T.KEY].assign(_k=1), on=T.KEY, how="inner", validate="many_to_one").drop(columns=["_k"])
    if m[T.KEY].drop_duplicates().shape[0] != len(keys):
        raise T.GuardError("%d ключей строк обучения не найдены в sigsx" % (len(keys) - m[T.KEY].drop_duplicates().shape[0]))
    os.makedirs(a.work, exist_ok=True)
    flt = os.path.join(a.work, "sigsx-%s-%s-r0.csv" % (a.win, a.side))
    m.to_csv(flt, index=False)
    ft = episode_features(side, flt, a.work, "%s-%s-r0" % (a.win, a.side), a.procs, (cache, trades, t0))
    tr = read_rows(os.path.join(d, side + "-trades.csv"), os.path.join(d, side + "-detail.jsonl"), win, keys)
    tr = L598.add_labels(tr)
    tab = tr.merge(ft, on=T.KEY, how="left", validate="one_to_one")
    n_nofeat = int(tab["tick_ok"].isna().sum())
    tab["tick_ok"] = tab["tick_ok"].fillna(False).astype(bool)
    off = T.win_offset_ms(win)
    tab["a_sig"] = tab["sig_ms"].astype(np.int64) + off
    tab["a_entry"] = tab["entry_ms"].astype(np.int64) + off
    tab["a_exit"] = tab["exit_ms"].astype(np.int64) + off
    tab["aday"] = tab["a_entry"] // T.DAY
    tab["win"] = a.win
    T.assert_search_days(tab["aday"].to_numpy(), "строки обучения")
    info = {"win": a.win, "side": a.side, "rows": int(len(tab)), "rows_without_feature_row": n_nofeat, "tick_ok": int(tab["tick_ok"].sum()),
            "zone_rows": int(T.zone_hit(tab["a_entry"], tab["a_exit"]).sum()), "age_censor": T.age_censor(tab["age_h"].to_numpy(float)),
            "rows_per_block": [int((T.block_of_abs(tab["aday"].to_numpy()) == k).sum()) for k in range(len(T.BLOCKS))],
            "nan_share": {c: float(tab[c].isna().mean()) for c in T.FEATS}, "f12": dict(F12_REC)}
    out = os.path.join(a.work, "train-%s-%s.parquet" % (a.win, a.side))
    tab.to_parquet(out)
    with open(out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


# ───────────────────────────── кандидаты потока R1 ─────────────────────────────

def cmd_cand(a):
    win = WINS[a.win]
    t0, cache, trades = win_env(a.win, a.root)
    side = SIDE_NAME[a.side]
    d = os.path.join(a.run, "eng", "%s_r1" % a.side)
    rfx.load_prod(a.prod, cache, trades, days=80, base_date=t0)
    sigsx = os.path.join(d, side + "-sigsx.csv")
    ft = episode_features(side, sigsx, a.work, "%s-%s-r1" % (a.win, a.side), a.procs, (cache, trades, t0))
    sig = rfx._SR.read_sigsx(sigsx)
    if sig.duplicated(["sym", "sig_ms"]).any():
        raise T.GuardError("в потоке sigsx дубль ключа (sym, sig_ms)")
    if len(ft) != len(sig):
        raise T.GuardError("строк признаков %d ≠ кандидатов потока %d" % (len(ft), len(sig)))
    off = T.win_offset_ms(win)
    ft["a_sig"] = ft["sig_ms"].astype(np.int64) + off
    ft["aday"] = ft["a_sig"] // T.DAY
    ft["win"] = a.win
    out = os.path.join(a.work, "cand-%s-%s.parquet" % (a.win, a.side))
    ft.to_parquet(out)
    lo, hi = T.credit_days(win)
    info = {"win": a.win, "side": a.side, "rows": int(len(ft)), "tick_ok": int(ft["tick_ok"].sum()),
            "days_min": int(ft["aday"].min()), "days_max": int(ft["aday"].max()), "credit_days": [lo, hi],
            "in_credit": int(((ft["aday"] >= lo) & (ft["aday"] <= hi)).sum()), "in_aug": int(T.is_aug(ft["aday"]).sum()),
            "age_censor": T.age_censor(ft["age_h"].to_numpy(float)), "nan_share": {c: float(ft[c].isna().mean()) for c in T.FEATS}, "f12": dict(F12_REC)}
    with open(out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


# ───────────────────────────── счётчики (без меток) ─────────────────────────────

def trades_per_day(trades_csv, win):
    """Число сделок по суткам от 01.07 (дата входа) — читается только `entry_ms`."""
    e = pd.read_csv(trades_csv, usecols=["entry_ms"])["entry_ms"].to_numpy(np.int64)
    return np.bincount((e + T.win_offset_ms(win)) // T.DAY, minlength=rf.NDAYS)


def cmd_count(a):
    win = WINS[a.win]
    c = trades_per_day(os.path.join(a.run, "eng", a.variant, SIDE_NAME[a.side] + "-trades.csv"), win)
    lo, hi = T.credit_days(win)
    n_aug = int(c[max(lo, T.AUG[0]):min(hi, T.AUG[1]) + 1].sum()) if hi >= T.AUG[0] and lo <= T.AUG[1] else 0
    print(json.dumps({"win": a.win, "variant": a.variant, "trades_total": int(c.sum()), "trades_aug_in_credit": n_aug, "credit_days": [lo, hi]}))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    v = sp.add_parser("variants")
    v.add_argument("--cstar")
    v.add_argument("--bases")
    v.add_argument("--out", required=True)
    v.add_argument("--q")
    for name in ("r0", "cand"):
        p = sp.add_parser(name)
        p.add_argument("--win", required=True, choices=list(WINS))
        p.add_argument("--run", required=True)
        p.add_argument("--side", required=True, choices=list(rf.SIDES))
        p.add_argument("--prod", required=True)
        p.add_argument("--work", required=True)
        p.add_argument("--procs", type=int, default=2)
        p.add_argument("--root", default=ROOT)
    c = sp.add_parser("count")
    c.add_argument("--win", required=True, choices=list(WINS))
    c.add_argument("--run", required=True)
    c.add_argument("--variant", required=True)
    c.add_argument("--side", required=True, choices=list(rf.SIDES))
    a = ap.parse_args(argv)
    if a.cmd == "variants" and bool(a.cstar) == bool(a.bases):
        raise SystemExit("variants: ровно одно из --cstar / --bases")
    {"variants": cmd_variants, "r0": cmd_r0, "cand": cmd_cand, "count": cmd_count}[a.cmd](a)


if __name__ == "__main__":
    main()
