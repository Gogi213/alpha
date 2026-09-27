#!/usr/bin/env python3
"""Python-фильтры входа П-08 (`docs/research/P-08-new-code-7.md` §7, §9 п. 2(в), §11; TK-013).

Вход — сигналы базы B1 (Г-85б, клетка `p07b-base` из `p07-cells.py`, прогон `--busy-skip off`): все `signals.csv`
под `--src` (дерево `<клетка>/<сутки>/<набор>/signals.csv`, как ищет `busy-replay.py`). Признаки — по последней
**закрытой** минуте до сигнала: m = t0 // 1 мин × 1 мин − 1 мин (минута сигнала не участвует — как `loss-corr.py`).
Функции `ret`/`rng`/`rv` и загрузчик свечей `Bars` — ИМПОРТОМ из `loss-corr.py` (одно определение, §11).

Клетки (условие ВХОДА; иначе сигнал выпадает из keep; неопределённый признак — выпадает, счёт в итоге):
  g57   p08-g57-rv   coin_rv_1h > 46 bps          p08-g57-rng  coin_range_1h > 54 bps
  g78   p08-g78-15   coin_minus_btc_15m ≤ −50 bps  p08-g78-25   coin_minus_btc_15m ≤ −25 bps
  g126  p08-g126-5   n_other_signal_15m < 5        p08-g126-10  n_other_signal_15m < 10
        n_other_signal_15m — число ДРУГИХ монет пула (без TRX, В-105) с сигналом базы B1 (все сигналы `--src`,
        до занятости) в [t0 − 15 мин, t0) — П-08 §12 п. 2 (кэш D20 отсекал всё). База Г-126 — B2 (`--max-pos 3`).
        Сигналы соседних суток подавать вместе (окно через полночь).
  g140  заготовка: доля пула со всплеском интенсивности — формат минутного ряда пишет TK-012 (TODO).

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
    "g78": [("p08-g78-15", "coin_minus_btc_15m", lambda v: v <= -50), ("p08-g78-25", "coin_minus_btc_15m", lambda v: v <= -25)],
    "g126": [("p08-g126-5", "n_other_signal_15m", lambda v: v < 5), ("p08-g126-10", "n_other_signal_15m", lambda v: v < 10)],
}
G126_WINDOW_MS = 15 * MIN_MS


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

def intensity_feats(signals, intensity_dir):
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
    ap.add_argument("cmd", choices=["g57", "g78", "g126", "g140", "selfcheck"])
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
    if a.cmd == "g140":
        intensity_feats(signals, a.intensity)
    if a.cmd in ("g57", "g78"):
        feats = kline_feats(lc, signals, a.home, a.klines)
        write_outputs(a.out, a.cmd, signals, feats)
    else:
        feats = signal_wave_feats(signals)
        write_outputs(a.out, a.cmd, signals, feats,
                      [f"сигналов B1 (все, без TRX): {sum(1 for r in signals if r['symbol'] not in EXCLUDED)}"])


if __name__ == "__main__":
    main()
