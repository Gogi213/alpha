#!/usr/bin/env python3
"""П-42 §13.21 (TK-595), стадия F: таблицы признаков кандидатов для `breakout tick --ftab` и границы срезов 10 / 20 / 25 %.

  python3 retitr_ftab.py build <L|S> --sigsx <side-sigsx.csv> --work <каталог> --out <ftab.csv> [--prod /data/tk594/py] [--procs 6] [--base-date 2026-09-01] [--force]
      поток кандидатов `sigsx` базы стороны (после стадии P) → признаки каждого кандидата → CSV `sym,sig_ms,<признаки>` (пусто = NaN).
      Признаки считаются ПРОИЗВОДСТВЕННЫМ кодом TK-588/592/594, не переписанным: каталог `--prod` (srv: /data/tk594/py) = копия
      sigfeat_run.py / sigfeat_lib.py / tk594_feat.py ветки TK-594. Эпизодный класс `Worker.process` вызывается как есть, переопределена
      только группировка сигналов в эпизоды (`do_universe`): каждый кандидат считается как свой «первый сигнал» — признаки на его
      собственном сигнале (то же, что делает таблица `ep_u0` для первого сигнала эпизода). Поток и производные TK-594 — его же CLI
      `tk594_feat.py` (`--mode deriv` у лонга: oi_c24, loi_turn; `--mode flow` у шорта: big1800).
  python3 retitr_ftab.py cuts <L|S> <ftab.csv> <feat.json>
      границы срезов: квантили признака по кандидатам половины A (сутки 14…43 от 01.09), NaN не считаются; `lo` отбрасывает нижнюю долю p
      (fv = q(p)), `hi` — верхнюю (fv = q(1 − p)); квантиль — `numpy.quantile`, линейная (как стадия меток). Формат для
      `retitr_cells.py emit --stage F --features`: [{"name","fo","cuts":{"10","20","25"}}]. Печатает реально отбрасываемую долю (ничьи).
  python3 retitr_ftab.py gate <L|S> <ftab.csv> <gate.json> --eps <work/eps-X.parquet> [--ep0 …] [--ep1 …] [--flow …] [--deriv …]
      ворота: на общих кандидатах (ключ движка (sym, sig_ms) и тот же уровень: dist_e8, lp_e8) значения равны таблицам TK-594 / TK-588/592 до 1e-9.
  python3 retitr_ftab.py ctrl <L|S> <control-sigsx.csv> --old-sigsx /data/tk592/out/eng/b0p0/<side>-sigsx.csv [--exclude <ftab.csv>]
      контрольный поток для ворот (общих ключей кандидатов базы с таблицами TK-594 меньше 200): строки, бывшие первыми сигналами эпизодов ep_u0.
  python3 retitr_ftab.py gatecells <L|S> <файл вариантов> --accepted <json>
      ворота движка: база + «пропускает всех» (lo −1e18, hi +1e18) и «режет всех» (lo +1e18) по каждому признаку стороны.
  python3 retitr_ftab.py gatecheck <каталог прогона> <L|S> --ftab <ftab.csv>
      ворота движка, проверка: клетки «пропускает всех» байт в байт с базой (и счётчики фильтра: все кандидаты нашли строку таблицы);
      «режет всех» — сделки только у кандидатов без значения (NaN проходит), у признака без NaN — 0 сделок.
Протокол: docs/research/P-42-level-breakout.md §13.21 «Стадия меток», «Ворота и тесты v21»; тест — tests/test_retitr_ftab.py.
"""
import argparse
import csv
import json
import math
import os
import subprocess
import sys

import numpy as np

DAY_MS = 86_400_000
A_DAYS = (14, 43)                # половина A: сутки 14…43 от 01.09 (15.09–14.10); B (44…73) не используется
CUT_PCTS = (10, 20, 25)          # срезы F: отбросить худшие 10 / 20 / 25 % кандидатов
GATE_TOL = 1e-9
GATE_MIN_KEYS = 200
# признаки F (топ-5 стадии меток, список зафиксирован до денег): (имя, направление отбрасывания хвоста)
FEATURES = {
    "L": [("oi_c24", "hi"), ("maxmv1", "lo"), ("depth_mean_bp", "hi"), ("depth_min_bp", "hi"), ("loi_turn", "lo")],
    "S": [("tpm15", "lo"), ("cons60", "hi"), ("tpm60", "lo"), ("big1800", "lo"), ("since_born_h", "lo")],
}
TK594_MODE = {"oi_c24": "deriv", "loi_turn": "deriv", "big1800": "flow"}   # прочие признаки — эпизодная таблица TK-588/592
SIDE_NAME = {"L": "long", "S": "short"}
SIDE_SIGN = {"long": 1, "short": -1}
DAYS_TOTAL = 80                  # сетка движка v21 (cache80)
BASE_DATE = "2026-09-01"         # T0 сетки TK-595 (`P42_T0`); TK-599 передаёт T0 своего окна (`--base-date`): sig_ms окна отсчитывается от него
PASS_LO, PASS_HI = "-1e18", "1e18"


# ───────────────────────── формат таблицы ─────────────────────────

def fmt_val(x):
    """Значение ячейки: NaN / None — пусто; число — кратчайшая запись, читаемая без потерь (repr float)."""
    if x is None:
        return ""
    x = float(x)
    return "" if math.isnan(x) else repr(x)


def write_ftab(path, syms, sig_ms, names, cols):
    """CSV `sym,sig_ms,<names>`; cols — список массивов по столбцам. Ключ (sym, sig_ms) обязан быть уникальным (в движке последний выигрывает)."""
    n = len(syms)
    assert len(sig_ms) == n and all(len(c) == n for c in cols)
    keys = set(zip(syms, (int(v) for v in sig_ms)))
    if len(keys) != n:
        raise SystemExit("ключ (sym, sig_ms) не уникален: %d строк, %d ключей" % (n, len(keys)))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(["sym", "sig_ms"] + list(names)) + "\n")
        for i in range(n):
            f.write(",".join([syms[i], str(int(sig_ms[i]))] + [fmt_val(c[i]) for c in cols]) + "\n")


def read_ftab(path):
    """(список монет, массив sig_ms int64, {признак: массив float64 с NaN}); разбор как у движка: пусто / nan / na / none / null — NaN."""
    syms, ms, cols = [], [], None
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        head = next(rd)
        if head[:2] != ["sym", "sig_ms"]:
            raise ValueError("заголовок должен начинаться с `sym,sig_ms`")
        names = head[2:]
        cols = {n: [] for n in names}
        for row in rd:
            if not row:
                continue
            syms.append(row[0])
            ms.append(int(row[1]))
            for n, v in zip(names, row[2:]):
                t = v.strip()
                cols[n].append(float("nan") if t == "" or t.lower() in ("nan", "na", "none", "null") else float(t))
    return syms, np.array(ms, dtype=np.int64), {n: np.array(v, dtype=np.float64) for n, v in cols.items()}


# ───────────────────────── границы срезов ─────────────────────────

def a_mask(sig_ms):
    """Кандидаты половины A: сутки sig_ms // DAY_MS в [14, 43]."""
    d = np.asarray(sig_ms, dtype=np.int64) // DAY_MS
    return (d >= A_DAYS[0]) & (d <= A_DAYS[1])


def quantile_cut(x, fo, pct):
    """fv среза «отбросить худшие pct %»: lo — q(p), hi — q(1 − p) по значениям без NaN (линейная квантиль numpy, как в retitr_labels.py)."""
    v = np.asarray(x, dtype=np.float64)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        raise ValueError("нет значений признака в A")
    q = pct / 100.0 if fo == "lo" else (100 - pct) / 100.0
    return float(np.quantile(v, q))


def dropped_share(x, fo, fv):
    """Доля значений без NaN, которую срез реально отбросит (правило движка: lo — x < fv, hi — x > fv); при ничьих на границе меньше p."""
    v = np.asarray(x, dtype=np.float64)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return float("nan")
    return float(((v > fv) if fo == "hi" else (v < fv)).mean())


def build_feat_json(side, ftab_path):
    """([{"name","fo","cuts"}], диагностика) по таблице стороны; популяция — все строки таблицы (кандидаты потока sigsx) с сутками A."""
    syms, ms, cols = read_ftab(ftab_path)
    am = a_mask(ms)
    out, diag = [], []
    for name, fo in FEATURES[side]:
        xa = cols[name][am]
        cuts = {str(p): quantile_cut(xa, fo, p) for p in CUT_PCTS}
        out.append({"name": name, "fo": fo, "cuts": cuts})
        diag.append({"name": name, "fo": fo, "n_cand_A": int(am.sum()), "n_nan_A": int(np.isnan(xa).sum()),
                     "cuts": cuts, "dropped": {p: dropped_share(xa, fo, c) for p, c in cuts.items()},
                     "distinct_cuts": len(set(cuts.values()))})
    return out, diag


# ───────────────────────── ворота: сравнение с таблицами ─────────────────────────

def compare_values(a, b, tol=GATE_TOL):
    """Сравнение значений на общих ключах: NaN = NaN — равно; NaN против числа — расхождение; число против числа — |a − b| ≤ tol."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    na, nb = np.isnan(a), np.isnan(b)
    both = ~na & ~nb
    diff = np.abs(a[both] - b[both])
    bad = int((diff > tol).sum()) + int((na != nb).sum())
    return {"n_common": int(len(a)), "n_both_finite": int(both.sum()), "n_both_nan": int((na & nb).sum()),
            "n_nan_mismatch": int((na != nb).sum()), "n_mismatch": bad,
            "max_abs_diff": float(diff.max()) if len(diff) else 0.0}


def gate_values_ok(r):
    """Значения на общих кандидатах равны (|Δ| ≤ 1e-9, NaN = NaN)."""
    return r["n_mismatch"] == 0


def gate_enough(r, min_keys=GATE_MIN_KEYS):
    """Достаточно общих кандидатов с конечными значениями с обеих сторон (≥ 200)."""
    return r["n_both_finite"] >= min_keys


def gate_pass(r, min_keys=GATE_MIN_KEYS):
    return gate_values_ok(r) and gate_enough(r, min_keys)


# ───────────────────────── производственный код (srv) ─────────────────────────

_SR = None          # модуль sigfeat_run (производственный)
_CAND = None        # класс CandWorker


def load_prod(prod, cache, trades, days=DAYS_TOTAL, base_date=BASE_DATE):
    """Подключает производственные модули TK-588/592: окружение задаётся ДО импорта (sigfeat_lib читает его при импорте)."""
    global _SR, _CAND
    os.environ["SIGFEAT_DAYS"] = str(days)
    os.environ["SIGFEAT_CACHE"] = cache
    os.environ["SIGFEAT_TRADES"] = trades
    os.environ["SIGFEAT_BASE_DATE"] = base_date
    os.environ.pop("SIGFEAT_MIN_DAY", None)
    sys.path.insert(0, prod)
    import sigfeat_run as sr
    _SR = sr

    class CandWorker(sr.Worker):
        """Производственный Worker; единственная правка — `do_universe`: каждый кандидат потока = свой «эпизод» из одного сигнала."""

        def do_universe(self, sym, univ, side, s, df, m, cf, natr, pool, t1h, r5, r15, r1, tk, first_valid, emp_coin, sig_rows, ep_rows, st):
            rng_base = [588, sum((i + 1) * ord(ch) for i, ch in enumerate(sym)), 0 if s > 0 else 1, 0 if univ == "U1" else 1]
            i = 0
            for level, g in df.groupby("level", sort=True):
                for _, row in g.sort_values("sig_ms").iterrows():
                    sig_ms = int(row.sig_ms)
                    ep = self.episode(sym, univ, side, s, row, i, 1, sig_ms, m, cf, natr, pool, t1h, r5, r15, r1, tk, first_valid, emp_coin, st,
                                      rng_base)
                    if ep is None:   # сигнальная сделка не найдена в лентах — признаки пусты (NaN → кандидат проходит)
                        ep = {"univ": univ, "side": side, "sym": sym, "level": int(level), "ep_no": i, "sig_ms": sig_ms, "day": sig_ms // DAY_MS,
                              "ep_id": f"{univ}|{side}|{sym}|{int(level)}|{i}", "tick_ok": False}
                    ep_rows.append(ep)
                    i += 1
    _CAND = CandWorker
    return sr


def _init(cfg):
    _SR._W = _CAND(cfg)


def build_episodes_rust(side, sigsx, out, procs, feat_bin):
    """TK-609: те же эпизоды Rust-бинарём `alpha-feat f12` (порт TK-611: признаки и служебные столбцы, 66 колонок; метки/контроли не портированы
    и ниже по `build` не читаются). Окно — из окружения `load_prod` (`SIGFEAT_*`); склад суток — `TK594_STORE` (только чтение), как у `run_tk594`."""
    import pandas as pd
    e = os.environ
    csv_path = out + ".f12.csv"
    cmd = [feat_bin, "f12", "--sigsx", sigsx, "--side", side, "--out", csv_path, "--cache", e["SIGFEAT_CACHE"],
           "--trades", e["SIGFEAT_TRADES"], "--base-date", e["SIGFEAT_BASE_DATE"], "--days", e["SIGFEAT_DAYS"], "--threads", str(procs)]
    if e.get("TK594_STORE"):
        cmd += ["--store", e["TK594_STORE"], "--store-cap-gb", "0"]
    print("запуск:", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, text=True)
    st = json.loads(r.stdout.strip().splitlines()[-1])
    ep = pd.read_csv(csv_path, dtype={"sym": str}, float_precision="round_trip")   # точный разбор: быстрый даёт ±ULP
    if ep["tick_ok"].dtype != bool:
        ep["tick_ok"] = ep["tick_ok"].astype(str).str.lower().isin(["true", "1"])
    ep.to_parquet(out)
    tot = {k: st[k] for k in ("locate_fail", "natr_mismatch", "unsorted_days", "irregular_days")}
    import hashlib
    tot.update(episodes=int(len(ep)), coins=int(st["coins"]), engine="alpha-feat f12", bin_md5=hashlib.md5(open(feat_bin, "rb").read()).hexdigest())
    json.dump({"total": tot}, open(out + ".stats.json", "w"), ensure_ascii=False)
    print(json.dumps(tot, ensure_ascii=False), flush=True)
    return tot


def f12_bin():
    """Бинарь эпизодов (TK-609 v7, §13.24.7 п. 7): `P42_F12_BIN=<путь>` — Rust этим бинарём; `off` или пусто — Python; переменной нет — Rust тем же
    бинарём, что `P42_FEAT_BIN`; нет ни той, ни другой — Python. → путь или ''."""
    v = os.environ.get("P42_F12_BIN")
    if v is None:
        v = os.environ.get("P42_FEAT_BIN", "")
    return "" if v.strip().lower() in ("", "off") else v


def build_episodes(side, sigsx, out, procs):
    """Таблица «эпизодов из одного сигнала» по потоку sigsx стороны (производственный Worker); parquet со всеми колонками `ep_u0`.
    TK-609: Rust (`build_episodes_rust`, `alpha-feat f12`) — по умолчанию, когда задан бинарь (`f12_bin`); `P42_F12_BIN=off` — прежний Python."""
    if f12_bin():
        return build_episodes_rust(side, sigsx, out, procs, f12_bin())
    import multiprocessing as mp
    import pandas as pd
    sr = _SR
    s = SIDE_SIGN[side]
    df = sr.read_sigsx(sigsx)
    sigs = {}
    for sym, g in df.groupby("sym"):
        sigs[(sym, s)] = g.sort_values(["level", "sig_ms"]).reset_index(drop=True)
    cfg = {"sigs": {"b0": {}, "b0p0": sigs}, "levels": {}, "traded": {}}
    coins = sorted({k[0] for k in sigs})
    rows, stats = [], []
    with mp.get_context("fork").Pool(procs, initializer=_init, initargs=(cfg,)) as pool_:
        for _sr, er, _cr, _tr, st in pool_.imap_unordered(sr._job, coins, chunksize=1):
            rows += er
            stats.append(st)
    ep = pd.DataFrame(rows)
    ep.to_parquet(out)
    tot = {k: int(sum(x[k] for x in stats)) for k in ("locate_fail", "natr_mismatch", "pool_violation", "sigs", "unsorted_days")}
    tot["episodes"] = int(len(ep))
    tot["coins"] = len(coins)
    tot["sigsx_rows"] = int(len(df))
    json.dump({"total": tot, "by_coin": stats}, open(out + ".stats.json", "w"), ensure_ascii=False)
    print(json.dumps(tot, ensure_ascii=False), flush=True)
    return tot


def run_tk594(prod, eps, out, mode, procs, base_date=BASE_DATE):
    """Производственный CLI TK-594 по таблице эпизодов (читает ключи и turn24h; метки не открывает).
    TK-609: при `P42_FEAT_BIN=<путь alpha-feat>` счёт делает Rust (`rustfeat_run.py` — те же аргументы и таблица, побитово равная Python: ворота
    G-A1…G-A3, §13.24.7); без переменной — прежний `tk594_feat.py`."""
    feat_bin = os.environ.get("P42_FEAT_BIN", "")
    if feat_bin:
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "rustfeat_run.py"), eps, out, "--mode", mode,
               "--base-date", base_date, "--procs", str(procs), "--bin", feat_bin]
    else:
        cmd = [sys.executable, os.path.join(prod, "tk594_feat.py"), eps, out, "--mode", mode, "--base-date", base_date, "--procs", str(procs)]
    print("запуск:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def cmd_build(a):
    import pandas as pd
    side = SIDE_NAME[a.side]
    names = [n for n, _ in FEATURES[a.side]]
    os.makedirs(a.work, exist_ok=True)
    load_prod(a.prod, a.cache, a.trades, base_date=a.base_date)
    eps_path = os.path.join(a.work, "eps-%s.parquet" % a.side)
    if a.force or not os.path.exists(eps_path):
        build_episodes(side, a.sigsx, eps_path, a.procs)
    eps = pd.read_parquet(eps_path)
    parts = {}
    for mode in sorted({TK594_MODE[n] for n in names if n in TK594_MODE}):
        p = os.path.join(a.work, "tk594-%s-%s.parquet" % (mode, a.side))
        if a.force or not os.path.exists(p):
            run_tk594(a.prod, eps_path, p, mode, a.procs, a.base_date)
        parts[mode] = pd.read_parquet(p)
    sig = _SR.read_sigsx(a.sigsx)
    if sig.duplicated(["sym", "sig_ms"]).any():
        raise SystemExit("в потоке sigsx есть дубль ключа (sym, sig_ms)")
    use = eps[["ep_id", "sym", "sig_ms"] + [n for n in names if n not in TK594_MODE]].copy()   # столбцы TK-594 — только из его таблиц
    for mode, t in parts.items():
        cols = [n for n in names if TK594_MODE.get(n) == mode]
        use = use.merge(t[["ep_id"] + cols], on="ep_id", how="left", validate="one_to_one")
    tab = sig[["sym", "sig_ms"]].merge(use[["sym", "sig_ms"] + names], on=["sym", "sig_ms"], how="left", validate="one_to_one")
    assert len(tab) == len(sig), "число строк таблицы не равно числу кандидатов потока"
    write_ftab(a.out, list(tab["sym"]), tab["sig_ms"].to_numpy(np.int64), names, [tab[n].to_numpy(np.float64) for n in names])
    info = {"side": a.side, "rows": int(len(tab)), "nan_share": {n: float(tab[n].isna().mean()) for n in names},
            "prod_md5": prod_md5(a.prod)}
    if os.environ.get("P42_FEAT_BIN"):   # TK-609: признаки TK-594 посчитал Rust — md5 бинаря в таблицу (аудит §13.24.7)
        import hashlib
        info["feat_bin_md5"] = hashlib.md5(open(os.environ["P42_FEAT_BIN"], "rb").read()).hexdigest()
    if f12_bin():   # TK-609 v7: эпизоды посчитал Rust — md5 бинаря в таблицу (аудит §13.24.7)
        import hashlib
        info["f12_bin_md5"] = hashlib.md5(open(f12_bin(), "rb").read()).hexdigest()
    json.dump(info, open(a.out + ".info.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


def prod_md5(prod):
    import hashlib
    out = {}
    for fn in ("sigfeat_run.py", "sigfeat_lib.py", "tk594_feat.py"):
        out[fn] = hashlib.md5(open(os.path.join(prod, fn), "rb").read()).hexdigest()
    return out


# ───────────────────────── команды: cuts, gate ─────────────────────────

def cmd_cuts(a):
    feats, diag = build_feat_json(a.side, a.ftab)
    json.dump(feats, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("сторона %s: кандидатов A %d" % (a.side, diag[0]["n_cand_A"]))
    print("%-14s %-3s %6s %s" % ("признак", "fo", "NaN_A", "срез: fv (реально отброшено)"))
    for d in diag:
        print("%-14s %-3s %6d %s" % (d["name"], d["fo"], d["n_nan_A"],
                                     "  ".join("%s%%: %.10g (%.1f%%)" % (p, d["cuts"][p], 100 * d["dropped"][p]) for p in sorted(d["cuts"], key=int))))
        if d["distinct_cuts"] < len(CUT_PCTS):
            print("  ВНИМАНИЕ: границы срезов совпали (ничьи) — различных %d из %d" % (d["distinct_cuts"], len(CUT_PCTS)))
    json.dump(diag, open(a.out + ".diag.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def match_candidates(mine, ref):
    """Пары (i, j) кандидатов с одинаковым ключом движка (sym, sig_ms) И одинаковой личностью (расстояние dist_e8, цена уровня lp_e8):
    номер уровня между прогонами не совпадает, а у одной сделки (sym, sig_ms) бывают два уровня — ключ движка их не различает.
    mine, ref — списки кортежей (sym, sig_ms, dist_e8, lp_e8). Возвращает (пары, число кандидатов mine с тем же ключом, но другим уровнем,
    число строк ref с неоднозначной личностью — не сравниваются)."""
    cnt = {}
    for k in ref:
        cnt[k] = cnt.get(k, 0) + 1
    pos = {k: j for j, k in enumerate(ref) if cnt[k] == 1}
    keys = {(k[0], k[1]) for k in ref}
    pairs, other = [], 0
    for i, k in enumerate(mine):
        j = pos.get(k)
        if j is not None:
            pairs.append((i, j))
        elif k not in cnt and (k[0], k[1]) in keys:
            other += 1
    return pairs, other, sum(1 for k in ref if cnt[k] > 1)


def _ident(df):
    return list(zip(df["sym"], df["sig_ms"].astype("int64"), df["dist_e8"].astype("int64"), df["lp_e8"].astype("int64")))


def cmd_gate(a):
    import pandas as pd
    side = SIDE_NAME[a.side]
    syms, ms, cols = read_ftab(a.ftab)
    eps = pd.read_parquet(a.eps)[["sym", "sig_ms", "dist_e8", "lp_e8"]]
    mine = eps.merge(pd.DataFrame({"sym": syms, "sig_ms": ms, "row": np.arange(len(syms))}), on=["sym", "sig_ms"], how="inner", validate="one_to_one")
    mid = _ident(mine)
    mrow = mine["row"].to_numpy()
    res = {"side": a.side, "tol": GATE_TOL, "min_keys": GATE_MIN_KEYS, "candidates": len(syms), "features": {}}
    ep0 = pd.read_parquet(a.ep0)
    ep0 = ep0[ep0["side"] == side].reset_index(drop=True)
    tabs = {"ep_u0": ep0}
    if os.path.exists(a.ep1):
        ep1 = pd.read_parquet(a.ep1)
        tabs["ep_u1"] = ep1[ep1["side"] == side].reset_index(drop=True)
    flow = pd.read_parquet(a.flow)
    deriv = pd.read_parquet(a.deriv)
    ok_all = True
    for name, _ in FEATURES[a.side]:
        res["features"][name] = {}
        if name in TK594_MODE:
            src = flow if TK594_MODE[name] == "flow" else deriv
            t = ep0.merge(src[["ep_id", name]].rename(columns={name: "_ref"}), on="ep_id", how="inner", validate="one_to_one")
            cand = {"feat_" + TK594_MODE[name]: t}
        else:
            cand = {k: v.rename(columns={name: "_ref"}) for k, v in tabs.items()}
        for tn, t in cand.items():
            t = t.reset_index(drop=True)
            pairs, other, amb = match_candidates(mid, _ident(t))
            ii = np.array([i for i, _ in pairs], dtype=np.int64)
            jj = np.array([j for _, j in pairs], dtype=np.int64)
            r = compare_values(cols[name][mrow[ii]], t["_ref"].to_numpy(np.float64)[jj])
            r["n_key_other_level"] = other          # тот же (sym, sig_ms), другой уровень в таблице — не расхождение значения, не сравнивается
            r["n_ambiguous_ref_rows"] = amb
            r["values_ok"] = gate_values_ok(r)
            r["enough_keys"] = gate_enough(r)
            res["features"][name][tn] = r
        d = res["features"][name]
        d["_verdict"] = {"values_ok": all(r["values_ok"] for r in d.values() if "values_ok" in r),
                         "enough_keys": any(r["enough_keys"] for r in d.values() if "enough_keys" in r)}
        ok_all &= d["_verdict"]["values_ok"] and d["_verdict"]["enough_keys"]
    res["pass"] = bool(ok_all)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("кандидатов %d" % len(syms))
    print("%-14s %-10s %8s %9s %8s %9s %10s %s" % ("признак", "таблица", "ключей", "конечных", "оба NaN", "расхожд.", "др.уровень", "max |Δ|"))
    for n, d in res["features"].items():
        for tn, r in d.items():
            if tn == "_verdict":
                continue
            print("%-14s %-10s %8d %9d %8d %9d %10d %.3g %s" % (n, tn, r["n_common"], r["n_both_finite"], r["n_both_nan"], r["n_mismatch"],
                                                                  r["n_key_other_level"], r["max_abs_diff"],
                                                                  ("значения равны" if r["values_ok"] else "РАСХОЖДЕНИЕ") + ("" if r["enough_keys"] else ", ключей < %d" % GATE_MIN_KEYS)))
        v = d["_verdict"]
        print("  %-12s значения %s; ≥ %d общих ключей хотя бы в одной таблице: %s" % (n, "равны" if v["values_ok"] else "РАСХОДЯТСЯ", GATE_MIN_KEYS, "да" if v["enough_keys"] else "НЕТ"))
    print("ворота:", "ПРОЙДЕНЫ" if res["pass"] else "НЕ ПРОЙДЕНЫ")
    return 0 if res["pass"] else 1


def cmd_ctrl(a):
    """Контрольный поток для ворот: строки старого дампа b0p0 TK-592, которые были первыми сигналами эпизодов `ep_u0` (их значения в таблицах
    TK-594 есть); монеты и строки — случайно (зерно), ключи кандидатов базы F исключены. Дальше — тот же `build` и `gate`."""
    import pandas as pd
    side = SIDE_NAME[a.side]
    ep = pd.read_parquet(a.ep0, columns=["side", "sym", "level", "sig_ms"])
    ep = ep[ep["side"] == side]
    old = pd.read_csv(a.old_sigsx, header=0, names=["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members"],
                      dtype={"sym": str, "members": str})
    old = old[~old.duplicated(["sym", "sig_ms"], keep=False)]            # ключ без неоднозначности
    old = old.merge(ep[["sym", "level", "sig_ms"]], on=["sym", "level", "sig_ms"], how="inner", validate="one_to_one")
    if a.exclude:
        es, em, _ = read_ftab(a.exclude)
        ex = set(zip(es, (int(v) for v in em)))
        old = old[[(s_, int(m_)) not in ex for s_, m_ in zip(old["sym"], old["sig_ms"])]]
    rng = np.random.default_rng(a.seed)
    coins = sorted(old["sym"].unique())
    pick = set(rng.choice(coins, size=min(a.n_coins, len(coins)), replace=False))
    old = old[old["sym"].isin(pick)]
    parts = []
    for sym, g in old.groupby("sym"):
        parts.append(g.iloc[rng.permutation(len(g))[:a.per_coin]])
    ctrl = pd.concat(parts).sort_values(["sym", "level", "sig_ms"])
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,level,sig_ms,dist_e8,level_px_e8,born,end,first_bar,natr_e8,members\n")
        for r in ctrl.itertuples(index=False):
            f.write("%s,%d,%d,%d,%d,%d,%d,%d,%d,%s\n" % (r.sym, r.level, r.sig_ms, r.dist_e8, r.level_px_e8, r.born, r.end, r.first_bar, r.natr_e8, r.members))
    print("контрольных строк %d, монет %d" % (len(ctrl), ctrl["sym"].nunique()))


# ───────────────────────── ворота движка ─────────────────────────

def gate_cells(side_key, accepted):
    """Строки вариантов для ворот движка: база стороны и клетки «пропускает всех» / «режет всех» по признакам стороны."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import retitr_cells as rc
    acc = rc.load_accepted(accepted)["cumulative_keys"]
    rows = [rc.line(side_key, "%s_base" % side_key, "", acc)]
    for name, fo in FEATURES[side_key]:
        rows.append(rc.line(side_key, "G_%s_pass_lo" % name, "fa=%s fo=lo fv=%s" % (name, PASS_LO), acc))
        rows.append(rc.line(side_key, "G_%s_pass_hi" % name, "fa=%s fo=hi fv=%s" % (name, PASS_HI), acc))
        rows.append(rc.line(side_key, "G_%s_cut_lo" % name, "fa=%s fo=lo fv=%s" % (name, PASS_HI), acc))
        rows.append(rc.line(side_key, "G_%s_cut_hi" % name, "fa=%s fo=hi fv=%s" % (name, PASS_LO), acc))
    return rows


def cmd_gatecells(a):
    rows = gate_cells(a.side, a.accepted)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(rows) + "\n")
    print(len(rows), "вариантов (база + %d клеток ворот)" % (len(rows) - 1))


GATE_KINDS = ("pass_lo", "pass_hi", "cut_lo", "cut_hi")


def parse_gate_id(cid):
    """`G_<признак>_<pass|cut>_<lo|hi>` → (признак, вид); None — не клетка ворот."""
    if not cid.startswith("G_"):
        return None
    for k in GATE_KINDS:
        if cid.endswith("_" + k):
            return cid[2:-(len(k) + 1)], k
    return None


def check_gate_cell(kind, n_rows, n_nan, fc, same_bytes, trades_keys, nan_keys):
    """Вердикт одной клетки ворот движка. fc = [cands, dropped, no_key, no_value] стороны.
    pass_*: файлы байт в байт с базой, ничего не отброшено, все кандидаты нашли строку таблицы (no_key = 0), no_value = число NaN признака.
    cut_*: отброшено всё, у чего есть значение (dropped = cands − NaN); сделки — только у кандидатов без значения (NaN проходит), у признака без NaN их 0."""
    if fc is None or fc[0] != n_rows or fc[2] != 0 or fc[3] != n_nan:
        return False
    if kind.startswith("pass"):
        return bool(same_bytes) and fc[1] == 0
    return fc[1] == n_rows - n_nan and all(k in nan_keys for k in trades_keys)


def cmd_gatecheck(a):
    side = SIDE_NAME[a.side]
    syms, ms, cols = read_ftab(a.ftab)
    base = os.path.join(a.run, "%s_base" % a.side)
    files = ["%s-%s.csv" % (side, k) for k in ("trades", "daily", "coinday", "entryrej")]
    bb = {f: open(os.path.join(base, f), "rb").read() for f in files}
    ok = True
    print("клетка ворот             результат")
    for d in sorted(os.listdir(a.run)):
        pid = parse_gate_id(d)
        p = os.path.join(a.run, d)
        if pid is None or not os.path.isdir(p):
            continue
        name, kind = pid
        x = cols[name]
        nan_keys = {(s_, int(m_)) for s_, m_, v in zip(syms, ms, x) if v != v}
        meta = json.load(open(os.path.join(p, "meta.json"), encoding="utf-8"))
        fc = meta[side].get("filter_[cands,dropped,no_key,no_value]")
        same = all(open(os.path.join(p, f), "rb").read() == bb[f] for f in files)
        with open(os.path.join(p, "%s-trades.csv" % side), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        tkeys = [(r["sym"], int(r["sig_ms"])) for r in rows]
        good = check_gate_cell(kind, len(syms), len(nan_keys), fc, same, tkeys, nan_keys)
        if kind.startswith("pass"):
            print("%-26s байт в байт с базой: %s; фильтр [cands,dropped,no_key,no_value] = %s %s" % (d, "да" if same else "НЕТ", fc, "OK" if good else "FAIL"))
        else:
            print("%-26s сделок %d (из кандидатов без значения: %d); фильтр = %s; NaN-кандидатов %d %s" % (
                d, len(rows), sum(k in nan_keys for k in tkeys), fc, len(nan_keys), "OK" if good else "FAIL"))
        ok &= bool(good)
    print("ворота движка:", "ПРОЙДЕНЫ" if ok else "НЕ ПРОЙДЕНЫ")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("side", choices=["L", "S"])
    b.add_argument("--sigsx", required=True)
    b.add_argument("--work", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--prod", default="/data/tk594/py")
    b.add_argument("--cache", default="/data/p42-t566/cache80")
    b.add_argument("--trades", default="/data/aggtrades/binance")
    b.add_argument("--procs", type=int, default=6)
    b.add_argument("--base-date", default=BASE_DATE, help="T0 окна (`P42_T0`): сутки sig_ms считаются от неё (по умолчанию 2026-09-01 — TK-595)")
    b.add_argument("--force", action="store_true")
    c = sp.add_parser("cuts")
    c.add_argument("side", choices=["L", "S"])
    c.add_argument("ftab")
    c.add_argument("out")
    g = sp.add_parser("gate")
    g.add_argument("side", choices=["L", "S"])
    g.add_argument("ftab")
    g.add_argument("out")
    g.add_argument("--eps", required=True, help="таблица эпизодов, построенная `build` (личность кандидата: dist_e8, lp_e8)")
    g.add_argument("--ep0", default="/data/tk594/ep_u0.parquet")
    g.add_argument("--ep1", default="/data/tk592/ep_u1.parquet")
    g.add_argument("--flow", default="/data/tk594/feat_flow.parquet")
    g.add_argument("--deriv", default="/data/tk594/feat_deriv.parquet")
    ct = sp.add_parser("ctrl")
    ct.add_argument("side", choices=["L", "S"])
    ct.add_argument("out")
    ct.add_argument("--old-sigsx", required=True)
    ct.add_argument("--exclude", default="")
    ct.add_argument("--ep0", default="/data/tk594/ep_u0.parquet")
    ct.add_argument("--n-coins", type=int, default=100)
    ct.add_argument("--per-coin", type=int, default=4)
    ct.add_argument("--seed", type=int, default=595)
    gc = sp.add_parser("gatecells")
    gc.add_argument("side", choices=["L", "S"])
    gc.add_argument("out")
    gc.add_argument("--accepted", required=True)
    gk = sp.add_parser("gatecheck")
    gk.add_argument("run")
    gk.add_argument("side", choices=["L", "S"])
    gk.add_argument("--ftab", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        cmd_build(a)
        return 0
    if a.cmd == "cuts":
        cmd_cuts(a)
        return 0
    if a.cmd == "gate":
        return cmd_gate(a)
    if a.cmd == "ctrl":
        cmd_ctrl(a)
        return 0
    if a.cmd == "gatecells":
        cmd_gatecells(a)
        return 0
    return cmd_gatecheck(a)


if __name__ == "__main__":
    sys.exit(main())
