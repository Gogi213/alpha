"""П-42 §13.27 (TK-610): заморозка и единственное чтение августа.

  python3 tk610_read.py freeze --tk599-frozen <json> --tk599-commit <sha> --fit-dir <каталог fit> --score-dir <каталог score> --gates-dir <каталог json ворот>
                               --r2-root <каталог out> --out <tk610-frozen.json>
      собирает заморозку: sha256 `tk599-frozen.json` и коммит, md5 движка, C*, клетки и итерации, sha256 моделей, `q`, числа G8 (кандидаты августа и сделки R2 по счётчикам), σ C* (R1, окно поиска),
      sha256 выходов R2, sha256 кода, версия CatBoost, ворота. Требует G1…G5, G9 зелёными (файлы `<gates-dir>/<имя>.json`, поле `ok`), G8 — только числа. Метки и P&L R2 не открываются.
  python3 tk610_read.py read --frozen <tk610-frozen.json> --frozen-sha256 <hex> --run-root <каталог out> --symbols <symbols.txt> --out <каталог>
      единственное чтение августа: проверяет sha256 заморозки и кода, создаёт `READ-aug.started` (O_EXCL; повтор — отказ), склеивает A (R1) и B (R2) из `wa` 01–25.08 и `wb` 26–31.08,
      Δ, интервал, вердикт по п. 6, описательные меры (S0 / S1 по блокам на сделках R1, Спирмен) → `read-aug.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402
import tk598_lib as L598  # noqa: E402
import tk610_data as D  # noqa: E402
import tk610_lib as T  # noqa: E402

GATES_REQUIRED = ("G1", "G2", "G3", "G4", "G5", "G9")
VERSION = "tk610-frozen-1"


def _jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def read_symbols(path):
    with open(path, encoding="utf-8") as f:
        return [s for s in re.split(r"[,\s]+", f.read()) if s]


def r2_outputs_sha(r2_root, wins=("wa", "wb", "wc")):
    """sha256 выходов R2 (файлы не открываются как данные — только хэш): {окно: {вариант: {файл: sha256}}}."""
    out = {}
    for w in wins:
        base = os.path.join(r2_root, "%s-R2" % w, "eng")
        if not os.path.isdir(base):
            continue
        for var in sorted(os.listdir(base)):
            d = os.path.join(base, var)
            if os.path.isdir(d):
                out.setdefault(w, {})[var] = {fn: T.sha256_file(os.path.join(d, fn)) for fn in sorted(os.listdir(d)) if fn.endswith((".csv", ".json"))}
    return out


def load_gates(gates_dir):
    """{имя: json}; G1…G5, G9 обязаны быть и быть зелёными; G8 — числа без условия."""
    g = {}
    for fn in sorted(os.listdir(gates_dir)):
        if fn.endswith(".json"):
            g[fn[:-5]] = json.load(open(os.path.join(gates_dir, fn), encoding="utf-8"))
    for need in GATES_REQUIRED:
        hits = [k for k in g if k.upper().startswith(need)]
        if not hits:
            raise T.GuardError("нет файла ворот %s в %s" % (need, gates_dir))
        for k in hits:
            if g[k].get("ok") is not True:
                raise T.GuardError("ворота %s красные: %s — заморозка запрещена" % (k, json.dumps(g[k], ensure_ascii=False)[:300]))
    return g


def build_freeze(tk599_frozen, tk599_commit, fit_dir, score_dir, gates_dir, r2_root, extra=None):
    """Содержимое `tk610-frozen.json` (п. 4 §13.27)."""
    gates = load_gates(gates_dir)
    with open(tk599_frozen, encoding="utf-8") as f:
        fz = json.load(f)
    cstar = T.load_cstar(fz)
    fr = {"version": VERSION, "protocol": "docs/research/P-42-level-breakout.md §13.27", "created_utc": T.now_utc(),
          "tk599": {"frozen_sha256": T.sha256_file(tk599_frozen), "commit": tk599_commit, "bin_md5": fz.get("meta", {}).get("bin_md5"), "grids_sha256": fz.get("meta", {}).get("grids_sha256")},
          "feats": T.FEATS, "seed": T.SEED, "blocks": T.BLOCKS, "aug": T.AUG, "zone_ms": list(T.ZONE), "grid": [list(c) for c in T.GRID], "sides": {}}
    for s in rf.SIDES:
        fit = json.load(open(os.path.join(fit_dir, "fit-%s.json" % s), encoding="utf-8"))
        qj = json.load(open(os.path.join(score_dir, "q-%s.json" % s), encoding="utf-8"))
        if fit.get("dry") or qj.get("dry"):
            raise T.GuardError("заморозка из сухого прогона запрещена (сторона %s)" % s)
        sig = json.load(open(os.path.join(gates_dir, "sigma-%s.json" % s), encoding="utf-8")) if os.path.exists(os.path.join(gates_dir, "sigma-%s.json" % s)) else None
        cnt = json.load(open(os.path.join(gates_dir, "G8-%s.json" % s), encoding="utf-8")) if os.path.exists(os.path.join(gates_dir, "G8-%s.json" % s)) else None
        fr["sides"][s] = {"cstar_keys": cstar[s]["keys"], "cstar_decision": cstar[s]["decision"], "filter": cstar[s]["filter"],
                          "cell": fit["chosen"], "iterations": fit["iterations"], "catboost": fit["catboost"], "rows_per_block": fit["rows_per_block"],
                          "cv_loss_mean_chosen": min(r["loss_mean"] for r in fit["cv"]), "folds": [{"file": f["file"], "sha256": f["sha256"]} for f in fit["folds"]],
                          "q": qj["q"], "n_oof": qj["n_oof"], "by_win": qj["by_win"], "g8_candidates": {"aug_cands": qj["aug_cands"], "aug_ge_q": qj["aug_ge_q"], "aug_ge_q_passF": qj["aug_ge_q_passF"]},
                          "g8_r2_trades_aug": cnt, "sigma_cstar_search": sig}
    g1 = next((v for k, v in gates.items() if k.upper().startswith("G1")), {})
    fr["engine"] = {"md5": g1.get("engine_md5"), "bin": "/data/p42-tk607/bin/alpha-l1 (turbo TK-607, §13.24.6)", "tick_store": True, "sig_cache": True,
                    "allowed": g1.get("engine_allowed"), "deviation": "§13.27 п. 11.10: движок a112684a (TK-607) вместо a03c91df"}
    fr["r2_outputs_sha256"] = r2_outputs_sha(r2_root)
    fr["gates"] = gates
    fr["code_sha256"] = T.code_sha256()
    if extra:
        fr.update(extra)
    return fr


def cmd_freeze(a):
    fr = build_freeze(a.tk599_frozen, a.tk599_commit, a.fit_dir, a.score_dir, a.gates_dir, a.r2_root)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(fr, f, ensure_ascii=False, indent=1, default=_jd)
    print("заморозка записана:", a.out, "sha256", T.sha256_file(a.out))


# ───────────────────────────── чтение августа ─────────────────────────────

def verify_frozen(frozen_path, frozen_sha, here=T.HERE):
    """Чтение требует файла заморозки, его sha256 (из коммита заморозки) и неизменного кода."""
    if not os.path.exists(frozen_path):
        raise T.GuardError("G6: нет файла заморозки — чтение августа запрещено")
    if T.sha256_file(frozen_path) != frozen_sha:
        raise T.GuardError("G6: sha256 файла заморозки не совпал с записанным в коммите")
    fr = json.load(open(frozen_path, encoding="utf-8"))
    cur = T.code_sha256(here)
    for fn, h in fr["code_sha256"].items():
        if cur.get(fn) != h:
            raise T.GuardError("G6: код изменён после заморозки: %s" % fn)
    return fr


def take_marker(out_dir):
    """`READ-aug.started` создаётся O_EXCL; повторное чтение — отказ."""
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "READ-aug.started")
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise T.GuardError("G6: %s уже есть — повторное чтение августа запрещено" % p) from None
    os.write(fd, (T.now_utc() + "\n").encode())
    os.close(fd)
    return p


def aug_tables(run_root, tag, side):
    """Сделки и суточный срез `wa`, `wb` прогона `<окно>-<tag>` варианта `<S>_<вариант>`."""
    name = rf.ENGINE_SIDE[side]
    tables, daily = {}, {}
    for w in ("wa", "wb"):
        d = os.path.join(run_root, "%s-%s" % (w, tag[0]), "eng", "%s_%s" % (side, tag[1]))
        tables[w] = rf.read_trades_csv(os.path.join(d, name + "-trades.csv"))
        dp = os.path.join(d, name + "-daily.csv")
        daily[w] = rf.read_daily_csv(dp) if os.path.exists(dp) else None
    return tables, daily


def describe_search(run_root, side, shat_path):
    """Описательно: S0 / S1 по блокам Б1…Б4 на сделках R1 окна поиска (оценка — OOF кандидата с тем же `(win, sym, sig_ms)`), Спирмен `ŝ` с `net_nf`."""
    sh = pd.read_parquet(shat_path)
    name = rf.ENGINE_SIDE[side]
    parts = []
    for w in rf.WINDOWS:
        p = os.path.join(run_root, "%s-R1" % w.name, "eng", "%s_r1" % side, "%s-trades.csv" % name)
        if not os.path.exists(p):
            continue
        keys, _, _ = D.read_keys_dates(p, w)
        tr = D.read_rows(p, os.path.join(os.path.dirname(p), "%s-detail.jsonl" % name), w, keys)
        off = T.win_offset_ms(w)
        tr["aday"] = (tr["entry_ms"].astype(np.int64) + off) // T.DAY
        tr["win"] = w.name
        tr["net_nf"] = tr["gross"] - tr["fees"]
        parts.append(tr[["win", "sym", "sig_ms", "aday", "net_nf"]])
    tr = pd.concat(parts, ignore_index=True)
    j = tr.merge(sh[["win", "sym", "sig_ms", "shat"]].drop_duplicates(["win", "sym", "sig_ms"]), on=["win", "sym", "sig_ms"], how="left")
    return j


def cmd_read(a):
    fr = verify_frozen(a.frozen, a.frozen_sha256)
    marker = take_marker(a.out)
    coins = read_symbols(a.symbols)
    res = {"started_utc": open(marker, encoding="utf-8").read().strip(), "frozen_sha256": a.frozen_sha256, "sides": {}}
    for s in rf.SIDES:
        ta, da = aug_tables(a.run_root, ("R1", "r1"), s)
        tb, db = aug_tables(a.run_root, ("R2", "r2"), s)
        sa, sb = T.splice_aug(ta, coins, da), T.splice_aug(tb, coins, db)
        c = T.aug_compare(sa, sb, len(coins))
        st = c["stat"]
        r = {"verdict": c["verdict"], "delta": st.d, "se": st.se, "z": st.z, "lower5": st.lower, "upper95": c["upper"], "dsum": st.dsum, "se_sum": st.se_sum,
             "n_a": c["n_a"], "n_b": c["n_b"], "sum_a": c["sum_a"], "sum_b": c["sum_b"], "mean_a": c["mean_a"], "mean_b": c["mean_b"],
             "robust_ok": c["robust_ok"], "robust_days": c["robust_days"], "robust_coins": c["robust_coins"], "days_pos_a": c["days_pos_a"], "days_pos_b": c["days_pos_b"],
             "per_day": c["sum_b"] / 31.0, "per_day_a": c["sum_a"] / 31.0}
        sg = fr["sides"][s].get("sigma_cstar_search") or {}
        if sg.get("sigma"):
            r["mde_lower"] = T.mde_pair(sg["sigma"], c["n_b"], c["n_a"], False)
            r["mde_upper"] = T.mde_pair(sg["sigma"], c["n_b"], c["n_a"], True)
        shp = os.path.join(a.shat_dir, "shat-%s.parquet" % s) if a.shat_dir else None
        if shp and os.path.exists(shp):
            j = describe_search(a.run_root, s, shp)
            ok = ~j["shat"].isna()
            r["descriptive_blocks"] = T.describe_blocks(j["aday"].to_numpy()[ok], j["net_nf"].to_numpy()[ok], j["shat"].to_numpy()[ok], fr["sides"][s]["q"])
            r["spearman_shat_net"] = L598.spearman(j["shat"].to_numpy(float)[ok], j["net_nf"].to_numpy(float)[ok])
        res["sides"][s] = r
    res["finished_utc"] = T.now_utc()
    with open(os.path.join(a.out, "read-aug.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=_jd)
    print(json.dumps({s: {"verdict": v["verdict"], "delta": v["delta"], "lower5": v["lower5"], "n_a": v["n_a"], "n_b": v["n_b"]} for s, v in res["sides"].items()}, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    z = sp.add_parser("freeze")
    z.add_argument("--tk599-frozen", required=True)
    z.add_argument("--tk599-commit", required=True)
    z.add_argument("--fit-dir", required=True)
    z.add_argument("--score-dir", required=True)
    z.add_argument("--gates-dir", required=True)
    z.add_argument("--r2-root", required=True)
    z.add_argument("--out", required=True)
    r = sp.add_parser("read")
    r.add_argument("--frozen", required=True)
    r.add_argument("--frozen-sha256", required=True)
    r.add_argument("--run-root", required=True)
    r.add_argument("--symbols", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--shat-dir")
    a = ap.parse_args(argv)
    {"freeze": cmd_freeze, "read": cmd_read}[a.cmd](a)


if __name__ == "__main__":
    main()
