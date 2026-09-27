#!/usr/bin/env python3
"""Python-фильтры входа П-08 (`docs/research/P-08-new-code-7.md` §7, §9 п. 2(в), §11; TK-013).

Вход — сигналы базы B1 (Г-85б, клетка `p07b-base` из `p07-cells.py`, прогон `--busy-skip off`): все `signals.csv`
под `--src` (дерево `<клетка>/<сутки>/<набор>/signals.csv`, как ищет `busy-replay.py`). Признаки — по последней
**закрытой** минуте до сигнала: m = t0 // 1 мин × 1 мин − 1 мин (минута сигнала не участвует — как `loss-corr.py`).
Функции `ret`/`rng`/`rv` и загрузчик свечей `Bars` — ИМПОРТОМ из `loss-corr.py` (одно определение, §11).

Клетки (условие ВХОДА; иначе сигнал выпадает из keep; неопределённый признак — выпадает, счёт в итоге):
  g57   p08-g57-rv   coin_rv_1h > 46 bps          p08-g57-rng  coin_range_1h > 54 bps
  g78   p08-g78-q33  coin_minus_btc_15m ≤ −13,134246 bps (§12 п. 12)  p08-g78-25   coin_minus_btc_15m ≤ −25 bps
  g126  p08-g126-5   n_other_signal_15m < 5        p08-g126-10  n_other_signal_15m < 10
        n_other_signal_15m — число ДРУГИХ монет пула (без TRX, В-105) с сигналом базы B1 (все сигналы `--src`,
        до занятости) в [t0 − 15 мин, t0) — П-08 §12 п. 2 (кэш D20 отсекал всё). База Г-126 — B2 (`--max-pos 3`).
        Сигналы соседних суток подавать вместе (окно через полночь).
  g36   p08-g36-a    g36_ratio ≥ 1,0              p08-g36-b    g36_ratio ≥ 0,46   (§12 п. 5, на взводе)
  g55   p08-g55-20/50  пропуск: съедено за 60 с ≥ 20 / 50 % видимого и BTC за минуту ≥ −5 bps (§12 п. 6)
  g140  p08-g140-30/50 доля пула во всплеске (cancel+trade ≥ 3 × медианы 60 мин) < 0,30 / < 0,50 (§12 п. 7)
  g07   p08-g07-up/mid depth_behind50 ≥ верхней / нижней трети своей монеты по B1 августа (§12 п. 8, --terc-json)

Выход (`--out DIR`): `keep-<клетка>.csv` (symbol,t0_ns,price_tick,form — ест `busy-replay.py --keep`) и
`feats-<подкоманда>.csv` — признаки и флаги по ВСЕМ сигналам (охват §6); итог — в stdout.

Дома (`--home`, повторяемый): свечи монет `<дом>/study/klines/ref-<SYM>-1m.csv`, BTC
`<дом>/study/regime/ref-BTCUSDT-1m.csv`, кэш подходов `<дом>/study/approaches/D20/<сутки>/approaches-<SYM>.csv`.

    p08-feats.py g57 --src epochs/e-aug/b5/p08-b1 --home epochs/e-aug --out tmp-p08/g57-aug
    p08-feats.py g126 --src ... --home epochs/e-archive --home . --out tmp-p08/g126-sep
    p08-feats.py selfcheck --src epochs/e-aug/b5/p07b-base --days 2026-08-03 --home epochs/e-aug --out tmp-p08/sc
    p08-feats.py selfcheck --synthetic
"""
import argparse
import csv
import json
import datetime as dt
import glob
import importlib.util
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_MS = 60_000
EXCLUDED = {"TRXUSDT"}  # В-105: TRX вне пула


def load_loss_corr(path=None):
    """`loss-corr.py` по пути (дефис в имени — не импортируется обычным import): одно определение rv/rng/ret/Bars."""
    for p in [path] if path else [os.path.join(HERE, "loss-corr.py"), os.path.expanduser("~/alpha/bin/loss-corr.py")]:
        if p and os.path.exists(p):
            spec = importlib.util.spec_from_file_location("loss_corr", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            m.__path_used__ = p
            return m
    sys.exit(f"не найден loss-corr.py (рядом со скриптом или ~/alpha/bin; --loss-corr {path})")


# ---------- клетки §7: имя → (признак, условие ВХОДА) ----------
CELLS = {
    "g57": [("p08-g57-rv", "coin_rv_1h", lambda v: v > 46), ("p08-g57-rng", "coin_range_1h", lambda v: v > 54)],
    "g78": [("p08-g78-q33", "coin_minus_btc_15m", lambda v: v <= -13.134246), ("p08-g78-25", "coin_minus_btc_15m", lambda v: v <= -25)],
    "g126": [("p08-g126-5", "n_other_signal_15m", lambda v: v < 5), ("p08-g126-10", "n_other_signal_15m", lambda v: v < 10)],
}
CELLS.update({
    "g36": [("p08-g36-a", "g36_ratio", lambda v: v >= 1.0), ("p08-g36-b", "g36_ratio", lambda v: v >= 0.46)],
    "g55": [("p08-g55-20", "g55_eat_share", None), ("p08-g55-50", "g55_eat_share", None)],  # условие — g55_cond
    "g140": [("p08-g140-30", "g140_pool_share", lambda v: v < 0.30), ("p08-g140-50", "g140_pool_share", lambda v: v < 0.50)],
    "g07": [("p08-g07-up", "g07_vs_up", lambda v: v >= 0), ("p08-g07-mid", "g07_vs_mid", lambda v: v >= 0)],
})
G126_WINDOW_MS = 15 * MIN_MS
# Столбцы signals.csv от TK-012 (П-08 §12 п. 4–8; имена — по сообщению Исследователя в TK-012, 02:50)
COL36 = ("traded_lots_at_arm", "size_max_at_arm", "size_monotonic_at_arm")
COL55 = ("eat_60s_lots", "size_max_60s_lots")
COL07 = "depth_behind50_lots_at_arm"
BTC_1M_MIN_BPS = -5.0   # §7 Г-55: BTC за минуту ≥ −5 bps — «BTC стоит»
G140_SPIKE_X = 3.0      # §7 Г-140: всплеск = минута ≥ 3 × медианы 60 мин


# ---------- сигналы ----------

def load_signals(src_dirs, sets=None, days=None):
    """Все строки `signals.csv` под каталогами (строки `#` пропускаются), в порядке обхода."""
    out = []
    for src in src_dirs:
        found = 0
        for root, _, files in sorted(os.walk(src)):
            if "signals.csv" not in files:
                continue
            if sets and os.path.basename(root) not in sets:
                continue
            found += 1
            with open(os.path.join(root, "signals.csv"), encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(line for line in fh if not line.startswith("#")):
                    if days and r["day_utc"] not in days:
                        continue
                    out.append(r)
        if not found:
            sys.exit(f"{src}: нет ни одного signals.csv — нужен прогон `--busy-skip off`")
    return out


def entry_minute(t0_ns):
    """Последняя закрытая минута до сигнала — ровно как loss-corr.py: t0 // MIN × MIN − MIN."""
    t0 = int(t0_ns) // 1_000_000
    return t0 // MIN_MS * MIN_MS - MIN_MS


# ---------- Г-57 / Г-78: свечи ----------

def kline_feats(lc, signals, homes, extra_klines=()):
    """{id(строки): признаки} — монеты по одной (память), BTC один раз."""
    btc = lc.Bars([os.path.join(h, "study", "regime", "ref-BTCUSDT-1m.csv") for h in homes])
    kdirs = [os.path.join(h, "study", "klines") for h in homes] + list(extra_klines)
    by_sym = {}
    for r in signals:
        by_sym.setdefault(r["symbol"], []).append(r)
    feats = {}
    for sym in sorted(by_sym):
        c = lc.Bars([os.path.join(d, f"ref-{sym}-1m.csv") for d in kdirs])
        for r in by_sym[sym]:
            m = entry_minute(r["t0_ns"])
            c1, b1 = lc.ret(c, m, 60), lc.ret(btc, m, 60)
            c15, b15 = lc.ret(c, m, 15), lc.ret(btc, m, 15)
            c5, b5 = lc.ret(c, m, 5), lc.ret(btc, m, 5)
            feats[id(r)] = {
                "coin_rv_1h": lc.rv(c, m, 60), "coin_range_1h": lc.rng(c, m, 60),
                "coin_minus_btc_1h": None if c1 is None or b1 is None else c1 - b1,
                "coin_minus_btc_15m": None if c15 is None or b15 is None else c15 - b15,
                "coin_minus_btc_5m": None if c5 is None or b5 is None else c5 - b5,
            }
        del c
    return feats


# ---------- Г-126: кэш подходов D20 ----------

def load_approach_starts(homes, days, side):
    """Начала подходов по монете пула (без TRX) за нужные сутки и сутки перед ними (окно 15 мин через полночь):
    {symbol: [arm_ms, …] отсортировано}, счётчик строк по суткам {day: n}."""
    need = set()
    for d in days:
        x = dt.date.fromisoformat(d)
        need |= {d, (x - dt.timedelta(days=1)).isoformat()}
    starts, n_rows = {}, {}
    for h in homes:
        for day in sorted(need):
            for fp in sorted(glob.glob(os.path.join(h, "study", "approaches", "D20", day, "approaches-*.csv"))):
                sym = os.path.basename(fp)[len("approaches-"):-len(".csv")]
                if sym in EXCLUDED:
                    continue
                with open(fp, encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh):
                        if side != "all" and row["side"] != side:
                            continue
                        starts.setdefault(sym, []).append(int(row["arm_ms"]))
                        n_rows[day] = n_rows.get(day, 0) + 1
    for v in starts.values():
        v.sort()
    return starts, n_rows


def approach_feats(signals, homes, side):
    import bisect
    days = sorted({r["day_utc"] for r in signals})
    starts, n_rows = load_approach_starts(homes, days, side)
    if not starts:
        sys.exit(f"кэш D20 пуст для суток {days[:3]}… в домах {homes} — Г-126 не считается")
    feats = {}
    for r in signals:
        t0 = int(r["t0_ns"]) // 1_000_000
        n = 0
        for sym, arr in starts.items():
            if sym == r["symbol"]:
                continue
            i = bisect.bisect_left(arr, t0 - G126_WINDOW_MS)
            if i < len(arr) and arr[i] < t0:
                n += 1
        feats[id(r)] = {"n_other_approach_15m": n}
    return feats, n_rows


def signal_wave_feats(signals):
    """Г-126 (П-08 §12 п. 2): число ДРУГИХ монет пула с сигналом базы B1 (все сигналы набора, до занятости)
    в окне [t0 − 15 мин, t0). Кэш D20 не годится: начало подхода там почти каждую минуту у каждой монеты."""
    import bisect
    by_sym = {}
    for r in signals:
        if r["symbol"] in EXCLUDED:
            continue
        by_sym.setdefault(r["symbol"], []).append(int(r["t0_ns"]) // 1_000_000)
    for arr in by_sym.values():
        arr.sort()
    feats = {}
    for r in signals:
        t0 = int(r["t0_ns"]) // 1_000_000
        n = 0
        for sym, arr in by_sym.items():
            if sym == r["symbol"]:
                continue
            i = bisect.bisect_left(arr, t0 - G126_WINDOW_MS)
            if i < len(arr) and arr[i] < t0:
                n += 1
        feats[id(r)] = {"n_other_signal_15m": n}
    return feats


# ---------- Г-140: заготовка ----------

def _num(r, col):
    v = r.get(col)
    if v is None:
        sys.exit(f"в signals.csv нет столбца {col} — нужен бинарник TK-012 с флагом признаков П-08")
    return float(v) if v not in ("", "nan", "NaN") else None


def g36_feats(signals):
    """Г-36 на взводе (§12 п. 5): частичный снос был (size_monotonic_at_arm = 0) и traded / max(size_max, 1)."""
    feats = {}
    for r in signals:
        tr, sm, mono = (_num(r, c) for c in COL36)
        if tr is None or sm is None or mono is None:
            feats[id(r)] = {"g36_ratio": None}
            continue
        feats[id(r)] = {"g36_ratio": -1.0 if mono else tr / max(sm, 1.0), "g36_monotonic": int(mono)}
    return feats


def g55_feats(lc, signals, homes):
    """Г-55 (§12 п. 6): доля съеденного за 60 с до взвода от наибольшего видимого в том же окне; BTC — последняя
    закрытая к сигналу минута (close→close, `ret` loss-corr)."""
    btc = lc.Bars([os.path.join(h, "study", "regime", "ref-BTCUSDT-1m.csv") for h in homes])
    feats = {}
    for r in signals:
        eat, smax = (_num(r, c) for c in COL55)
        b1 = lc.ret(btc, entry_minute(r["t0_ns"]), 1)
        share = None if eat is None or smax is None else eat / max(smax, 1.0)
        feats[id(r)] = {"g55_eat_share": share, "g55_btc_1m": b1}
    return feats


def g55_cond(f, thr):
    """ВХОД, если НЕ «местное съедание»: пропуск при доле ≥ thr и BTC ≥ −5 bps. Что-то не определено — None."""
    sh, b1 = f.get("g55_eat_share"), f.get("g55_btc_1m")
    if sh is None or b1 is None:
        return None
    return not (sh >= thr and b1 >= BTC_1M_MIN_BPS)


def g07_feats(signals, terc_json):
    """Г-07 (§12 п. 8): depth_behind50 против терцилей своей монеты по сигналам B1 АВГУСТА (заморозка — JSON;
    нет JSON — считается по августовским строкам входа и пишется; монета с < 30 сигналами — терциль пула)."""
    import math
    if os.path.exists(terc_json):
        terc = json.load(open(terc_json, encoding="utf-8"))
    else:
        by = {}
        for r in signals:
            if r["day_utc"].startswith("2026-08"):
                v = _num(r, COL07)
                if v is not None:
                    by.setdefault(r["symbol"], []).append(v)

        def q(vs):
            vs = sorted(vs)
            return [vs[math.ceil(len(vs) / 3) - 1], vs[math.ceil(2 * len(vs) / 3) - 1]]
        pool = [v for vs in by.values() for v in vs]
        if not pool:
            sys.exit("g07: нет августовских сигналов со столбцом глубины — заморозить терцили нечем")
        terc = {"_pool": q(pool), "_rule": "треть / две трети сигналов B1 августа; < 30 сигналов — _pool",
                **{sym: q(vs) for sym, vs in by.items() if len(vs) >= 30}}
        os.makedirs(os.path.dirname(os.path.abspath(terc_json)), exist_ok=True)
        with open(terc_json, "w", encoding="utf-8", newline="") as fh:
            json.dump(terc, fh, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"g07: терцили заморожены → {terc_json} (своих монет {len(terc) - 2})")
    feats = {}
    for r in signals:
        v = _num(r, COL07)
        lo, up = terc.get(r["symbol"], terc["_pool"])
        feats[id(r)] = {"g07_depth": v, "g07_vs_up": None if v is None else v - up,
                        "g07_vs_mid": None if v is None else v - lo}
    return feats


def intensity_feats(signals, intensity_dir):
    """Г-140 (§12 п. 7): минутный ряд TK-012 `minute_ms,symbol,add_lots,cancel_lots,trade_lots` (все *.csv под
    каталогом). x = cancel + trade за минуту. Монета во всплеске в минуте m (последняя закрытая к сигналу), если
    x(m) ≥ 3 × медиана x за 60 минут до m; монета без строки в m, с < 30 минутами истории или с медианой 0 —
    «нет данных», в знаменатель не входит. Признак — доля монет пула (без TRX) во всплеске."""
    import statistics
    if not intensity_dir:
        sys.exit("g140: нужен --intensity")
    series = {}
    for fp in sorted(glob.glob(os.path.join(intensity_dir, "**", "*.csv"), recursive=True)):
        with open(fp, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(line for line in fh if not line.startswith("#")):
                if r["symbol"] in EXCLUDED:
                    continue
                series.setdefault(r["symbol"], {})[int(r["minute_ms"])] = float(r["cancel_lots"]) + float(r["trade_lots"])
    if not series:
        sys.exit(f"g140: пустой каталог минутных рядов {intensity_dir}")
    memo, feats = {}, {}
    for r in signals:
        m = entry_minute(r["t0_ns"])
        if m not in memo:
            n = k = 0
            for ser in series.values():
                x = ser.get(m)
                prev = [ser[t] for t in range(m - 60 * MIN_MS, m, MIN_MS) if t in ser]
                if x is None or len(prev) < 30:
                    continue
                med = statistics.median(prev)
                if med <= 0:
                    continue
                n += 1
                k += x >= G140_SPIKE_X * med
            memo[m] = (k / n if n else None, n)
        feats[id(r)] = {"g140_pool_share": memo[m][0], "g140_n_coins": memo[m][1]}
    return feats


def _intensity_feats_stub(signals, intensity_dir):
    # TODO(TK-012): формат минутного ряда интенсивностей по монете (снятия + сделки) пишет Инженер. Когда будет:
    # по каждой монете пула (без TRX) — сумма за 60 с до входа против 3 × медианы минутных сумм за 60 мин до входа;
    # доля монет со всплеском ≥ 30 % / ≥ 50 % → сигнал пропускается (клетки p08-g140-30 / p08-g140-50).
    sys.exit(f"g140: формат минутного ряда интенсивностей TK-012 ещё не задан (каталог {intensity_dir}) — "
             "функция intensity_feats не реализована, счёт Г-140 невозможен")


# ---------- выход ----------

def write_outputs(out, sub, signals, feats, extra_cols=()):
    os.makedirs(out, exist_ok=True)
    cells = CELLS[sub]
    fcols = list(dict.fromkeys(k for f in feats.values() for k in f))
    base = ["symbol", "day_utc", "form", "signal_index", "t0_ns", "price_tick"]
    summary = []
    keeps = {name: [] for name, _, _ in cells}
    stats = {name: [0, 0] for name, _, _ in cells}  # (прошло, признак не определён)
    with open(os.path.join(out, f"feats-{sub}.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(base + fcols + [name for name, _, _ in cells])
        for r in signals:
            f = feats[id(r)]
            flags = []
            for name, key, cond in cells:
                v = f.get(key)
                if sub == "g55":
                    c = g55_cond(f, 0.20 if name.endswith("-20") else 0.50)
                    v = None if c is None else v
                    ok = bool(c)
                else:
                    ok = v is not None and cond(v)
                if v is None:
                    stats[name][1] += 1
                if ok:
                    stats[name][0] += 1
                    keeps[name].append((r["symbol"], r["t0_ns"], r["price_tick"], r["form"]))
                flags.append(1 if ok else 0)
            w.writerow([r[c] for c in base] + ["" if f.get(k) is None else f"{f[k]:.6f}" if isinstance(f[k], float)
                                                else f[k] for k in fcols] + flags)
    for name, _, _ in cells:
        with open(os.path.join(out, f"keep-{name}.csv"), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["symbol", "t0_ns", "price_tick", "form"])
            w.writerows(sorted(set(keeps[name])))
        summary.append(f"{name}: прошло {stats[name][0]} из {len(signals)} сигналов, признак не определён {stats[name][1]}")
    for line in list(extra_cols) + summary:
        print(line)


# ---------- selfcheck (§9 п. 2(в)) ----------

def run_loss_corr(lc, signals, homes, extra_klines, tmp):
    """Независимый путь: те же сигналы как «сделки» в сам `loss-corr.py` (подпроцесс) → его --csv."""
    trades = os.path.join(tmp, "sc-trades.csv")
    with open(trades, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["month", "symbol", "t0_ns", "t1_ns", "episode", "reason", "pnl_usd"])
        for r in signals:
            w.writerow(["sc", r["symbol"], r["t0_ns"], r["t0_ns"], "0", "sc", "0"])
    out_csv = os.path.join(tmp, "sc-loss-corr.csv")
    kdirs = [os.path.join(h, "study", "klines") for h in homes] + list(extra_klines)
    cmd = [sys.executable, lc.__path_used__, "--trades", trades, "--homes", "sc=" + ",".join(homes),
           "--klines", "sc=" + ",".join(kdirs), "--csv", out_csv]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        sys.exit("loss-corr.py упал:\n" + p.stderr[-2000:])
    with open(out_csv, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def selfcheck(lc, signals, homes, extra_klines, side, tmp):
    """Красное — код 1. Сравнение с loss-corr по округлению его --csv (3 знака); плюс число строк кэша D20."""
    feats = kline_feats(lc, signals, homes, extra_klines)
    ref = run_loss_corr(lc, signals, homes, extra_klines, tmp)
    # loss-corr сортирует сделки по t0 (устойчиво) — сопоставляем по (symbol, t0_ns), дубли по ключу — по порядку
    mine = {}
    for r in sorted(signals, key=lambda r: int(r["t0_ns"])):
        mine.setdefault((r["symbol"], r["t0_ns"]), []).append(feats[id(r)])
    bad, n, defined = 0, 0, {k: 0 for k in ("coin_rv_1h", "coin_range_1h", "coin_minus_btc_1h")}
    seen = {}
    for x in ref:
        key = (x["symbol"], x["t0_ns"])
        i = seen.get(key, 0)
        seen[key] = i + 1
        f = mine[key][i]
        for k in defined:
            a = "" if f[k] is None else str(round(f[k], 3))
            b = x[k]
            n += 1
            if b != "":
                defined[k] += 1
            if a != b:
                bad += 1
                if bad <= 10:
                    print(f"  РАСХОЖДЕНИЕ {key} {k}: p08={a!r} loss-corr={b!r}")
    ok_kl = bad == 0 and len(ref) == len(signals)
    print(f"свечи: сигналов {len(signals)}, строк loss-corr {len(ref)}, сравнений {n}, расхождений {bad}; "
          f"определено {defined} → {'ЗЕЛЁНОЕ' if ok_kl else 'КРАСНОЕ'}")
    # Г-126: строки кэша, прочитанные фильтром, против независимого подсчёта строк файлов (сторона side)
    days = sorted({r["day_utc"] for r in signals})
    _, n_rows = load_approach_starts(homes, days, side)
    ok_ap = True
    for day in days:
        raw = 0
        for h in homes:
            for fp in glob.glob(os.path.join(h, "study", "approaches", "D20", day, "approaches-*.csv")):
                if os.path.basename(fp)[len("approaches-"):-len(".csv")] in EXCLUDED:
                    continue
                with open(fp, encoding="utf-8") as fh:
                    next(fh, None)
                    raw += sum(1 for line in fh if line.strip() and (side == "all" or line.split(",")[1] == side))
        ok_ap &= raw == n_rows.get(day, 0) and raw > 0
        print(f"кэш D20 {day}: строк ({side}, без TRX) в файлах {raw}, прочитано фильтром {n_rows.get(day, 0)}")
    print(f"Г-126 → {'ЗЕЛЁНОЕ' if ok_ap else 'КРАСНОЕ'}")
    return ok_kl and ok_ap


def synthetic(lc, tmp):
    """Синтетика: 3 монеты + TRX + BTC, 3 ч свечей; проверки — совпадение с loss-corr, минута сигнала не участвует
    (всплеск в ней не меняет признаки), счёт других монет с подходом за 15 мин и исключение TRX."""
    rnd = random.Random(7)
    day = "2026-08-03"
    t_start = int(dt.datetime(2026, 8, 3, tzinfo=dt.timezone.utc).timestamp() * 1000)
    home = os.path.join(tmp, "home")
    for sub in ("study/klines", "study/regime", f"study/approaches/D20/{day}", f"b5/b1/{day}/set"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "TRXUSDT"]
    sig_minute = t_start + 150 * MIN_MS

    # цены — один раз (seed), пишутся дважды: без всплеска и со всплеском ×1,5 в минуте сигнала
    series = {}
    for s in syms + ["BTCUSDT"]:
        px, rows = 100.0, []
        for i in range(180):
            o = px
            px *= 1 + rnd.gauss(0, 0.002)
            rows.append((t_start + i * MIN_MS, o, px, rnd.random() * 10))
        series[s] = rows

    def write_all(spike):
        for s, rows in series.items():
            path = os.path.join(home, "study/regime" if s == "BTCUSDT" else "study/klines", f"ref-{s}-1m.csv")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("minute_ms,open,high,low,close,volume\n")
                for m, o, c, v in rows:
                    c = c * 1.5 if spike and m == sig_minute else c
                    fh.write(f"{m},{o},{max(o, c) * 1.001},{min(o, c) * 0.999},{c},{v}\n")

    # подходы: у BBB и TRX — за 10 мин до сигнала, у CCC — за 20 мин (вне окна), у AAA — свой (не считается)
    t0 = sig_minute + 30_000
    for s, dts in {"AAAUSDT": [-60_000], "BBBUSDT": [-600_000], "CCCUSDT": [-1_200_000], "TRXUSDT": [-300_000]}.items():
        with open(os.path.join(home, f"study/approaches/D20/{day}/approaches-{s}.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("day_utc,side,price_tick,approach_index,arm_ms\n")
            for d in dts:
                fh.write(f"{day},bid,100,0,{t0 + d}\n")
            fh.write(f"{day},ask,101,0,{t0 - 60_000}\n")
    signals = [{"symbol": s, "day_utc": day, "form": "f", "signal_index": str(i), "t0_ns": str((t0 + i * 500) * 1_000_000),
                "price_tick": "100"} for i, s in enumerate(syms[:3] * 12)]
    ok = True
    write_all(True)
    f_spike = kline_feats(lc, signals, [home])
    write_all(False)
    f_plain = kline_feats(lc, signals, [home])
    same = all(f_spike[id(r)] == f_plain[id(r)] for r in signals)
    print(f"минута сигнала не участвует (всплеск ×1,5 не меняет признаки): {'ДА' if same else 'НЕТ'}")
    ok &= same
    ok &= selfcheck(lc, signals, [home], (), "bid", tmp)
    ap, _ = approach_feats(signals, [home], "bid")
    want = {"AAAUSDT": 1, "BBBUSDT": 1, "CCCUSDT": 2}  # AAA: BBB (CCC −20 мин вне окна); BBB: AAA (TRX исключён); CCC: AAA, BBB
    got = {r["symbol"]: ap[id(r)]["n_other_approach_15m"] for r in signals}
    print(f"Г-126 синтетика: ожидалось {want}, получено {got}")
    ok &= got == want
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["g57", "g78", "g126", "g36", "g55", "g140", "g07", "selfcheck"])
    ap.add_argument("--terc-json", help="g07: файл заморозки терцилей (нет — считается по августу и пишется)")
    ap.add_argument("--src", action="append", default=[], help="каталог клетки B1 (обход до signals.csv), повторяемый")
    ap.add_argument("--home", action="append", default=[], help="дом: study/klines, study/regime, study/approaches/D20")
    ap.add_argument("--klines", action="append", default=[], help="доп. каталог ref-<SYM>-1m.csv")
    ap.add_argument("--sets", help="наборы через запятую (умолчание — все)")
    ap.add_argument("--days", help="сутки через запятую (умолчание — все)")
    ap.add_argument("--side", default="bid", choices=["bid", "ask", "all"], help="сторона подходов Г-126")
    ap.add_argument("--intensity", help="g140: каталог минутных рядов интенсивностей TK-012")
    ap.add_argument("--loss-corr", help="путь к loss-corr.py (умолчание — рядом или ~/alpha/bin)")
    ap.add_argument("--out", help="каталог выхода")
    ap.add_argument("--synthetic", action="store_true", help="selfcheck на синтетике во временном каталоге")
    a = ap.parse_args()
    lc = load_loss_corr(a.loss_corr)
    print(f"loss-corr: {lc.__path_used__}")
    if a.cmd == "selfcheck" and a.synthetic:
        with tempfile.TemporaryDirectory() as tmp:
            ok = synthetic(lc, tmp)
        print("SELFCHECK synthetic:", "ЗЕЛЁНОЕ" if ok else "КРАСНОЕ")
        sys.exit(0 if ok else 1)
    if not a.src or not a.home:
        sys.exit("нужны --src и --home")
    sets = set(a.sets.split(",")) if a.sets else None
    days = set(a.days.split(",")) if a.days else None
    signals = [r for r in load_signals(a.src, sets, days) if r["symbol"] not in EXCLUDED]
    print(f"сигналов {len(signals)} (без TRX), суток {len({r['day_utc'] for r in signals})}")
    if a.cmd == "selfcheck":
        os.makedirs(a.out or ".", exist_ok=True)
        ok = selfcheck(lc, signals, a.home, a.klines, a.side, a.out or tempfile.mkdtemp())
        print("SELFCHECK:", "ЗЕЛЁНОЕ" if ok else "КРАСНОЕ")
        sys.exit(0 if ok else 1)
    if not a.out:
        sys.exit("нужен --out")
    if a.cmd in ("g36", "g55", "g140", "g07"):
        feats = {"g36": lambda: g36_feats(signals), "g55": lambda: g55_feats(lc, signals, a.home),
                 "g140": lambda: intensity_feats(signals, a.intensity),
                 "g07": lambda: g07_feats(signals, a.terc_json or os.path.join(a.out, "g07-terc.json"))}[a.cmd]()
        write_outputs(a.out, a.cmd, signals, feats)
    elif a.cmd in ("g57", "g78"):
        feats = kline_feats(lc, signals, a.home, a.klines)
        write_outputs(a.out, a.cmd, signals, feats)
    else:
        feats = signal_wave_feats(signals)
        write_outputs(a.out, a.cmd, signals, feats,
                      [f"сигналов B1 (все, без TRX): {sum(1 for r in signals if r['symbol'] not in EXCLUDED)}"])


if __name__ == "__main__":
    main()
