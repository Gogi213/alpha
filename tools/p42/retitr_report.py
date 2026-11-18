"""П-42 §13.21 + §13.21.1 (TK-595): отчёт перетитровки — стадии (отбор только по A), сборка (описательно), подтверждающий тест (август), счётчик Холма.

  python3 retitr_report.py stage --side long --run <каталог прогона> --stage <стадии> [--sig-run <каталог сигнального прогона>] [--base <клетка-база>]
         [--sig-base <клетка-база сигнального режима>] [--prev-accepted <json|ключи>] [--variants <файл вариантов>] [--out <каталог>]
         [--spec auto|s19|<json>] [--min-trades 70] [--btc <BTCUSDT.b42>]
  python3 retitr_report.py final --side long --cand <каталог сборки C> --base <простая база> [--stage-base <база стадии>] [--out <каталог>] [--btc <файл>] [--force]
  python3 retitr_report.py confirm --side long --cand <клетка августа> --days <от:до> [--out <каталог>] [--force]
  python3 retitr_report.py holm --out <каталог с accepted-*.json> [--side long|short|both]

stage — по клетке и стороне печатается ТОЛЬКО половина A (и число сделок B): сделок A, сделок B (ТОЛЬКО ЧИСЛО), сумма $ A, среднее $ на сделку A, доля сделок в плюсе A
(по портфельному прогону), парная по суткам Δ_A против базы стадии ($/сут., детерминированный блочный бутстреп `detstat`, блок 4, 95 % интервал, SE_Δ).
  Режим статистики (§13.21.1 п. 1): стадии E1, Els, NS, NT, E2, T — СИГНАЛЬНЫЙ режим: Δ_A парная на одинаковых сигналах — прогон-двойник клетки `<id>_sig`
  против базы `<L|S>_base_sig` (в `--sig-run`, по умолчанию там же, где `--run`; допустим и каталог `<id>` в отдельном `--sig-run`); набор (монета, время входа)
  клетки и базы обязан совпасть — иначе остановка. P, F, asm — портфельная парная Δ_A. Бутстреп по монетам (кластер) и CUPED печатаются строками, не критерий.
  Правило отбора §13.21 (к статистике стадии, без изменений): только A; ≥ 70 сделок в A и в B (по портфельному прогону); упорядоченная ось — клетка с наибольшим
  сглаженным счётом (среднее Δ_A клетки и её соседей; соседи — ближайшие посчитанные значения оси, кроме базы); принято при Δ_A > 0 и плато (каждый сосед
  Δ_A > 0 и ≥ Δ_A(выбранной) − SE_Δ); нет соседа — принято при Δ_A > 0 «без плато»; двузначные оси (T, P) и одиночные клетки — так же; край оси `s` → «расширение»
  (1 → 0,5; 8 → 12); стоп-семейство (Els + NS) — одно с наибольшим Δ_A среди принятых; совместная сетка E2 (s × rr при той же bm); клетки E2, равные базе стадии
  на A и по числу сделок, пропускаются и в Холм не идут. Контроли (`*_base`, `*_base_sig`, `ow_e`, …) — не клетки.
  Вето по портфелю (§13.21.1 п. 2): значение, выбранное по сигнальной Δ_A, принимается, только если его портфельная Δ_A ≥ −1·SE_Δ(портфель); портфельная Δ_A
  печатается для каждого выбранного значения; вето — не второй критерий (применяется к принятию оси до выбора победителя стоп-семейства; запасного значения нет).
  Результат B (прибыль/убыток) в режиме stage не читается и не печатается — структурно: загрузчик `load_stage_cell` на строках B считает число сделок и читает только
  (монета, время входа) для сверки наборов; `net` / `pnl` строк B не разбираются.
  Выход: <out>/accepted-<стадии>-<сторона>.json — `keys`, `cumulative_keys`, причины, клетки (только A), `n_computed` для Холма (клетка — один раз при любом режиме).
final — сборка C против базы стадии и простой базы: A и B (читаются один раз; повтор без --force печатает сохранённое), «описательно, m = 754 / 747» (Холм справочный, без
  вердикта вперёд): сделок, сумма, среднее на сделку, доля в плюсе, $/сут. с интервалом, Δ, входы §10 готовыми функциями report_tick, CUPED (описательно).
confirm — подтверждающий тест на августе (m = 1, односторонне 5 %): средний $/сут., нижняя односторонняя 95 %-граница (блочный бутстреп `detstat`-типа, блок 4),
  устойчивость — знак среднего $/сут. без 2 лучших суток и без 2 лучших монет; «подтверждено» = среднее > 0, нижняя граница > 0 и оба знака +; «опровергнуто» /
  «не ясно» по §10 (знак и верхняя граница). Читается один раз.
holm — накопленный счёт испытаний: старт 694 / 687 + посчитанные клетки (равные базе и контроли не считаются; метки — 0); верхняя граница 754 / 747.
Медиана результатов не пишется нигде.

Решения по неоднозначностям (записаны здесь, а не в протоколе):
  оси T и P — «двузначные»: клетки оси не сглаживаются, берётся клетка с наибольшей Δ_A при Δ_A > 0 «без плато»; клетки, не прошедшие ≥ 70 сделок, выбраны быть не могут,
  но остаются соседями других клеток; расширение `s` помечается, только если выбранная на краю клетка имеет Δ_A > 0; в E2 соседи значения s — посчитанная сетка E1 (без 5).
  CUPED (§13.21.1 п. 7): θ = cov(Δ_i, x_i)/var(x_i) подбирается по каждой клетке на её собственных Δ_i (A; Δ_i — по объединению сделок обеих рук, 0 за отсутствующую);
  x_i — доходность BTCUSDT по закрытиям 1м от минуты входа до входа + 60 мин; центр — среднее x по сделкам A; в A среднее Δ не меняется (центрирование в выборке),
  меняется интервал; в final θ(A) применяется к B (B сдвигается на θ·(x̄_B − x̄_A)).

Запуск на srv (python3, nice 19):
  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M nice -n 19 python3 retitr_report.py <режим> …
"""
import argparse
import collections
import csv
import dataclasses
import glob
import hashlib
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detstat  # noqa: E402
import lib  # noqa: E402
import retitr_cells as rc  # noqa: E402
from lib import ND  # noqa: E402

NA = 30                                     # суток в половине A (сутки 0…29); B — сутки 30…59
MIN_TRADES = 70
M_START = {"long": 694, "short": 687}       # накопленный Холм после TK-591 (§13.21)
M_UPPER = {"long": 754, "short": 747}       # верхняя граница: старт + 60 клеток
SIDE_KEY = {"long": "L", "short": "S"}
SIGNATURE = "число не вне выборки; найдено на этих данных (подбор по A)"
DESCR = "описательно, m = %d"
BTC_DEFAULT = "/data/p42-t566/cache/BTCUSDT.b42"
CONTROL_IDS = {"ow_e", "b0", "L_ow_e", "S_ow_e", "S_es15", "L_base", "S_base", "base"}
SIG = rc.SIG_SUFFIX
NAN = float("nan")


def is_control(cid):
    return cid in CONTROL_IDS or cid.endswith("_base") or cid.endswith("_base" + SIG)


def fnum(x):
    """Число для JSON: NaN → None."""
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else x


def pct(x):
    return "н/д" if x is None or math.isnan(x) else "%.1f %%" % (x * 100)


def tri(st):
    return "%+.2f [%.2f; %.2f] SE %.2f" % (st["mean"], st["lo"], st["hi"], st["se"])


# ---- загрузчик режима stage: B-деньги не читаются -------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class StageCell:
    """Клетка для режима stage. Полей про B-деньги НЕТ: только число сделок в B (`n_b`) и множество ключей (монета, время входа) сделок A и B."""
    cid: str
    n_a: int
    n_b: int
    sum_a: float
    mean_a: float
    pos_a: float
    daily_a: np.ndarray
    keys: frozenset            # {(sym, entry_ms)} сделок A и B — для проверки «одинаковые сигналы»
    trades_a: dict             # (sym, entry_ms) → (entry_day, entry_min, net) только сделок A
    coin_a: object             # {sym: ряд A по суткам} из coinday (строки B не разбираются) или None


def load_stage_cell(vdir, side, cid=None):
    """Читает `<side>-trades.csv`, `-daily.csv`, `-coinday.csv`; строки B (entry_day ≥ 30 / day ≥ 30) — число и ключ (монета, время входа), `net` / `pnl` не разбираются."""
    n_a = n_b = n_pos = 0
    s = 0.0
    keys, tr = set(), {}
    with open(os.path.join(vdir, side + "-trades.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            k = (r["sym"], r["entry_ms"])
            keys.add(k)
            ed = int(r["entry_day"])
            if ed >= NA:
                n_b += 1
                continue                      # B: только счёт и ключ
            net = float(r["net"])
            n_a += 1
            s += net
            n_pos += net > 0
            tr[k] = (ed, int(r["entry_min"]), net)
    daily = np.full(NA, np.nan)
    with open(os.path.join(vdir, side + "-daily.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            day = int(r["day"])
            if day >= NA:
                continue
            daily[day] = float(r["pnl"])
    if np.isnan(daily).any():
        raise ValueError("%s: нет суток A в %s-daily.csv" % (vdir, side))
    coin = None
    cf = os.path.join(vdir, side + "-coinday.csv")
    if os.path.exists(cf):
        coin = {}
        with open(cf, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                day = int(r["day"])
                if day >= NA:
                    continue
                coin.setdefault(r["sym"], np.zeros(NA))[day] += float(r["pnl"])
    return StageCell(cid or os.path.basename(vdir.rstrip("/")), n_a, n_b, s, (s / n_a) if n_a else NAN, (n_pos / n_a) if n_a else NAN, daily,
                     frozenset(keys), tr, coin)


def assert_same_signals(cell, base, what):
    """§13.21.1 п. 3 (iii): набор (монета, время входа) сделок клетки равен набору базы сигнального режима — иначе остановка и разбор."""
    if cell.keys != base.keys:
        raise SystemExit("%s: набор (монета, время входа) не равен базе сигнального режима: только в клетке %d, только в базе %d (всего %d / %d) — пара не на одинаковых "
                         "сигналах: стоп и разбор" % (what, len(cell.keys - base.keys), len(base.keys - cell.keys), len(cell.keys), len(base.keys)))


# ---- устойчивость: бутстреп по монетам; CUPED (только печать) ------------------------------------------------------------------

def coin_cluster(cell, base, nboot=2000):
    """Бутстреп по монетам (кластер): Δ по монетам и суткам A; пересэмпл монет с возвращением; среднее по суткам суммы. Устойчивость — не критерий."""
    if cell.coin_a is None or base.coin_a is None:
        return None
    coins = sorted(set(cell.coin_a) | set(base.coin_a))
    if not coins:
        return None
    zero = np.zeros(NA)
    M = np.array([cell.coin_a.get(c, zero) - base.coin_a.get(c, zero) for c in coins])
    rng = np.random.default_rng(int(hashlib.md5(np.round(M, 9).tobytes()).hexdigest()[:8], 16))
    n = len(coins)
    means = np.empty(nboot)
    for s in range(0, nboot, 250):
        k = min(250, nboot - s)
        idx = rng.integers(0, n, size=(k, n))
        means[s:s + k] = M[idx].sum(axis=1).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return dict(mean=float(M.sum(axis=0).mean()), se=float(means.std(ddof=1)), lo=float(lo), hi=float(hi), n_coins=n)


def read_btc_close(path):
    """Закрытия 1м BTCUSDT из кэша движка `.b42`: 8 байт заголовка (метка, n), колонки o, h, l, c, qv по n × i64 LE; индекс — минута от начала сетки."""
    if not path or not os.path.exists(path):
        return None
    n = int(np.fromfile(path, dtype="<u4", count=2)[1])
    return np.fromfile(path, dtype="<i8", count=n, offset=8 + 3 * n * 8).astype(float)


def btc_ret(close, entry_min):
    m = int(entry_min)
    if m < 0 or m + 60 >= len(close) or close[m] <= 0 or close[m + 60] <= 0:
        return NAN
    return close[m + 60] / close[m] - 1.0


def union_delta(tr_c, tr_b):
    """Δ_i по объединению сделок двух рук: (entry_day, entry_min, net_c − net_b); сделки, которых нет в руке, считаются с P&L 0."""
    out = {}
    for k in set(tr_c) | set(tr_b):
        d, m = (tr_c.get(k) or tr_b.get(k))[:2]
        out[k] = (d, m, (tr_c[k][2] if k in tr_c else 0.0) - (tr_b[k][2] if k in tr_b else 0.0))
    return out


def cuped_fit(items, close):
    """θ = cov(Δ_i, x_i)/var(x_i) на сделках (entry_day, entry_min, Δ); x — доходность BTC за 60 мин от минуты входа. None, если не из чего подбирать."""
    xs, ds = [], []
    for day, mn, d in items.values():
        x = btc_ret(close, mn)
        if not math.isnan(x):
            xs.append(x)
            ds.append(d)
    if len(xs) < 5 or np.var(xs) <= 0:
        return None
    xs, ds = np.array(xs), np.array(ds)
    theta = float(np.cov(ds, xs)[0, 1] / np.var(xs, ddof=1))
    return dict(theta=theta, xbar=float(xs.mean()), n=len(xs))


def cuped_apply(items, close, fit, day_lo, ndays):
    """Ряды по суткам [day_lo, day_lo+ndays): сумма Δ_i (до) и сумма Δ_i − θ(x_i − x̄_A) (после); доля снятой дисперсии по сделкам; x NaN → поправка 0."""
    raw, adj = np.zeros(ndays), np.zeros(ndays)
    dr, da = [], []
    for day, mn, d in items.values():
        if not (day_lo <= day < day_lo + ndays):
            continue
        x = btc_ret(close, mn)
        a = d - fit["theta"] * (x - fit["xbar"]) if not math.isnan(x) else d
        raw[day - day_lo] += d
        adj[day - day_lo] += a
        dr.append(d)
        da.append(a)
    share = (1.0 - np.var(da, ddof=1) / np.var(dr, ddof=1)) if len(dr) > 2 and np.var(dr, ddof=1) > 0 else NAN
    return raw, adj, share


def cuped_line(items, close, label_a="A"):
    """Строка CUPED для режима stage (только A): θ на собственных Δ_i клетки."""
    if close is None:
        return None, "CUPED (описательно): н/д — нет файла BTCUSDT (--btc)"
    fit = cuped_fit(items, close)
    if fit is None:
        return None, "CUPED (описательно): н/д — мало сделок / нулевая дисперсия ковариаты"
    raw, adj, share = cuped_apply(items, close, fit, 0, NA)
    sr, sa = detstat.stat(raw), detstat.stat(adj)
    res = dict(theta=fit["theta"], share=fnum(share), mean_raw=sr["mean"], mean_adj=sa["mean"], se_raw=sr["se"], se_adj=sa["se"], lo=sa["lo"], hi=sa["hi"], n=fit["n"])
    return res, ("CUPED (описательно, не критерий): θ = %.3g, доля снятой дисперсии сделок %s, Δ_A после CUPED %+.2f [%.2f; %.2f] SE %.2f (до: %+.2f SE %.2f)" % (
        fit["theta"], pct(share), sa["mean"], sa["lo"], sa["hi"], sa["se"], sr["mean"], sr["se"]))


# ---- ядро правила отбора (чистые функции) -------------------------------------------------------------------------------

@dataclasses.dataclass
class Cand:
    cid: str
    spec: dict
    n_a: int
    n_b: int
    d_a: float          # Δ_A статистики стадии (сигнальной для E/T, портфельной для P/F/asm), $/сут.
    se: float           # SE_Δ статистики стадии
    sum_a: float = NAN
    mean_a: float = NAN
    pos_a: float = NAN
    lo: float = NAN
    hi: float = NAN
    mode: str = "port"  # "sig" | "port"
    port_d: object = None   # портфельная Δ_A и SE_Δ (для вето и печати); у портфельных клеток совпадают со статистикой стадии
    port_se: object = None
    port_lo: float = NAN
    port_hi: float = NAN
    cluster: object = None
    cuped: object = None
    cuped_text: object = None

    def eligible(self, mt):
        return self.n_a >= mt and self.n_b >= mt


def _coord_key(c):
    return tuple(c.spec["coords"][k] for k in sorted(c.spec["coords"]))


def _neighbours(c, cs):
    """Соседи по упорядоченным осям клетки: на каждой оси ближайшее посчитанное значение слева и справа при тех же остальных координатах.
    Базы среди посчитанных нет (клетка = изменение базы) — она в точки сглаживания не входит."""
    out = []
    for ax in c.spec["ordered_axes"]:
        v = c.spec["coords"][ax]
        same = [x for x in cs if x is not c and all(x.spec["coords"][k] == c.spec["coords"][k] for k in c.spec["coords"] if k != ax)]
        lo = [x for x in same if x.spec["coords"][ax] < v]
        hi = [x for x in same if x.spec["coords"][ax] > v]
        if lo:
            out.append(max(lo, key=lambda x: x.spec["coords"][ax]))
        if hi:
            out.append(min(hi, key=lambda x: x.spec["coords"][ax]))
    return out


def _decision(group, kind, scores, nb):
    return dict(group=group, kind=kind, family=None, chosen=None, accepted=False, selected=False, reason="", flags=[], d_a=None, se=None,
                smoothed=scores, neighbours=nb, extend=None, mode=None, port_d=None, port_se=None, vetoed=False)


def _decide_ordered(group, kind, cs, mt):
    cs = sorted(cs, key=_coord_key)
    nbs = {c.cid: _neighbours(c, cs) for c in cs}
    scores = {c.cid: float(np.mean([c.d_a] + [n.d_a for n in nbs[c.cid]])) for c in cs}
    d = _decision(group, kind, scores, {k: [n.cid for n in v] for k, v in nbs.items()})
    elig = [c for c in cs if c.eligible(mt)]
    if not elig:
        d["reason"] = "нет клетки с ≥ %d сделками в A и в B" % mt
        return d
    best = max(elig, key=lambda c: (round(scores[c.cid], 9), round(c.d_a, 9)))  # ничья по счёту — больше собственная Δ_A, затем меньшие координаты
    d.update(chosen=best.cid, d_a=best.d_a, se=best.se)
    nb = nbs[best.cid]
    if best.d_a <= 0:
        d["reason"] = "Δ_A ≤ 0 у выбранной %s (%+.2f)" % (best.cid, best.d_a)
    elif not nb:
        d["accepted"] = True
        d["flags"].append("без плато")
        d["reason"] = "нет соседа: принято при Δ_A > 0 (%+.2f), без плато" % best.d_a
    else:
        bad = [x for x in nb if not (x.d_a > 0 and x.d_a >= best.d_a - best.se)]
        thr = best.d_a - best.se
        if bad:
            d["reason"] = "плато нарушено: %s; порог соседа: > 0 и ≥ %+.2f (Δ_A − SE)" % (", ".join("%s Δ_A %+.2f" % (x.cid, x.d_a) for x in bad), thr)
        else:
            d["accepted"] = True
            d["reason"] = "плато: соседи %s (Δ_A > 0 и ≥ %+.2f)" % (", ".join("%s %+.2f" % (x.cid, x.d_a) for x in nb), thr)
    ext = cs[0].spec.get("ext")
    if ext and len(cs[0].spec["ordered_axes"]) == 1 and best.d_a > 0:
        ax = cs[0].spec["ordered_axes"][0]
        vals = sorted(c.spec["coords"][ax] for c in cs)
        if len(vals) >= 2 and best.spec["coords"][ax] in (vals[0], vals[-1]):
            d["extend"] = ext["low"] if best.spec["coords"][ax] == vals[0] else ext["high"]
            d["flags"].append("расширение %s → %g" % (ax, d["extend"]))
    return d


def _decide_pair(group, cs, mt):
    cs = sorted(cs, key=_coord_key)
    d = _decision(group, "pair", {c.cid: c.d_a for c in cs}, {c.cid: [] for c in cs})
    elig = [c for c in cs if c.eligible(mt)]
    if not elig:
        d["reason"] = "нет клетки с ≥ %d сделками в A и в B" % mt
        return d
    best = max(elig, key=lambda c: round(c.d_a, 9))
    d.update(chosen=best.cid, d_a=best.d_a, se=best.se)
    if best.d_a > 0:
        d["accepted"] = True
        d["flags"].append("без плато")
        d["reason"] = "двузначная ось: принято при Δ_A > 0 (%+.2f), без плато" % best.d_a
    else:
        d["reason"] = "Δ_A ≤ 0 у лучшей %s (%+.2f)" % (best.cid, best.d_a)
    return d


def _decide_single(group, c, mt):
    d = _decision(group, "single", {c.cid: c.d_a}, {c.cid: []})
    d.update(chosen=c.cid, d_a=c.d_a, se=c.se)
    if not c.eligible(mt):
        d["reason"] = "меньше %d сделок в A или в B (A %d, B %d)" % (mt, c.n_a, c.n_b)
    elif c.d_a > 0:
        d["accepted"] = True
        d["flags"].append("без плато")
        d["reason"] = "одна клетка: принято при Δ_A > 0 (%+.2f), без плато" % c.d_a
    else:
        d["reason"] = "Δ_A ≤ 0 (%+.2f)" % c.d_a
    return d


def _apply_veto(d, cs):
    """Вето по портфелю (§13.21.1 п. 2): выбранное по сигнальной Δ_A принимается, только если портфельная Δ_A ≥ −1·SE_Δ(портфель). Не второй критерий."""
    if d["chosen"] is None:
        return
    c = next(x for x in cs if x.cid == d["chosen"])
    d["mode"], d["port_d"], d["port_se"] = c.mode, c.port_d, c.port_se
    if d["accepted"] and c.mode == "sig" and c.port_d is not None and c.port_d < -c.port_se - 1e-12:
        d["accepted"] = False
        d["vetoed"] = True
        d["flags"].append("вето по портфелю")
        d["reason"] += "; ВЕТО: портфельная Δ_A %+.2f < −SE_Δ %.2f" % (c.port_d, -c.port_se)


def decide(cands, min_trades=MIN_TRADES):
    """Решения по осям / клеткам / совместной сетке, вето по портфелю и стоп-семейство. Только A (в Cand про B — лишь число сделок)."""
    groups = collections.OrderedDict()
    for c in cands:
        groups.setdefault((c.spec["kind"], c.spec["group"]), []).append(c)
    decisions = []
    for (kind, g), cs in groups.items():
        if kind in ("ordered", "grid"):
            d = _decide_ordered(g, kind, cs, min_trades)
        elif kind == "pair":
            d = _decide_pair(g, cs, min_trades)
        elif kind == "single":
            d = _decide_single(g, cs[0], min_trades)
        else:
            continue  # asm: не выбирается
        _apply_veto(d, cs)
        d["family"] = cs[0].spec.get("family")
        d["selected"] = d["accepted"]
        decisions.append(d)
    fams = collections.OrderedDict()
    for d in decisions:
        if d["family"] and d["accepted"]:
            fams.setdefault(d["family"], []).append(d)
    for fam, ds in fams.items():
        win = max(ds, key=lambda d: round(d["d_a"], 9))
        for d in ds:
            if d is win:
                d["flags"].append("победитель семейства «%s»" % fam)
            else:
                d["selected"] = False
                d["reason"] += "; семейство «%s»: берётся %s (Δ_A %+.2f)" % (fam, win["chosen"], win["d_a"])
    accepted = [d for d in decisions if d["selected"]]
    extend = [d["extend"] for d in decisions if d["extend"] is not None and d["selected"]]
    return dict(decisions=decisions, accepted=accepted, extend=extend)


# ---- метаданные клеток: источники ---------------------------------------------------------------------------------------

def _s19_specs():
    """Псевдо-стадия на опорных клетках TK-591 (`/data/p42-t566/out/s19`, база `ow_e`): все виды правил на уже посчитанных данных; ничего нового не считается."""
    o, p, s = rc._ordered, rc._pair, rc._single
    sp = {}
    for cid, k, v in (("ls05", "ls=0.5", 0.5), ("ls10", "ls=1", 1.0), ("ls20", "ls=2", 2.0)):
        sp[cid] = o("s19", cid, k, "ls", v)
    for cid, k, v in (("es15", "e=0.15", 0.15), ("es10", "e=0.1", 0.1), ("es05", "e=0.05", 0.05)):
        sp[cid] = o("s19", cid, k, "e", v, family="es")
    sp["es15_e2_05"] = s("s19", "es15_e2_05", "e=0.15 e2=0.05", family="es")      # семейство es: одно (как в TK-591)
    for cid, k, v in (("e2_10", "e2=0.1", 0.1), ("e2_05", "e2=0.05", 0.05)):
        sp[cid] = p("s19", cid, k, "e2", v)
    for cid, k in (("bm1", "bm=1"), ("bm2", "bm=2"), ("bm3", "bm=3")):
        sp[cid] = s("s19", cid, k, family="bm")          # стоп-семейство на bm: одно с наибольшим Δ_A
    for cid, tt, tb in (("tx5_x1", 5, 0.1), ("tx5_x3", 5, 0.3), ("tx20_x1", 20, 0.1), ("tx20_x3", 20, 0.3)):
        sp[cid] = dict(id=cid, keys="tt=%g tb=%g" % (tt, tb), stage="s19", kind="grid", group="tx", coords={"tt": float(tt), "tb": tb},
                       ordered_axes=["tt", "tb"], group_axes=[], family=None, ext=None)
    for cid, rf, rs_ in (("rt01_2", 0.1, 2), ("rt01_20", 0.1, 20), ("rt02_2", 0.2, 2), ("rt02_20", 0.2, 20)):
        sp[cid] = dict(id=cid, keys="rf=%g rs=%g" % (rf, rs_), stage="s19", kind="grid", group="rt", coords={"rf": rf, "rs": float(rs_)},
                       ordered_axes=["rf", "rs"], group_axes=[], family=None, ext=None)
    return sp


def make_spec_fn(arg):
    if arg == "auto":
        return rc.spec_from_id
    if arg == "s19":
        return _s19_specs().get
    return json.load(open(arg, encoding="utf-8")).get   # {id: spec}


def read_variants(path):
    out = collections.OrderedDict()
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        cid, _, rest = ln.partition(" ")
        out[cid] = rest.strip()
    return out


def change_keys(line, base_line):
    """Ключи клетки = то, что в её строке отличается от строки базы стадии."""
    pl, ft = rc.parse_keys(line)
    pb, fb = rc.parse_keys(base_line)
    pbd = dict(pb)
    toks = ["%s=%s" % (k, v) for k, v in pl if pbd.get(k) != v]
    for f in ft:
        if f not in fb:
            toks += ["%s=%s" % (k, f[k]) for k in rc.MULTI if k in f]
    return " ".join(toks)


# ---- режим stage -----------------------------------------------------------------------------------------------------------

def _stage_set(label):
    return None if label in ("all", "*") else set(label.replace("+", ",").split(","))


def _rank(c):
    order = rc.cell_order()
    st = rc.STAGE_ORDER.index(c.spec["stage"]) if c.spec["stage"] in rc.STAGE_ORDER else 99
    return (st, order.index(c.cid) if c.cid in order else 999, c.cid)


def _find_sig_dir(sig_run, run, cid):
    cands = [os.path.join(sig_run, cid + SIG)]
    if os.path.abspath(sig_run) != os.path.abspath(run):
        cands.append(os.path.join(sig_run, cid))
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


def run_stage(a, side):
    spec_fn = make_spec_fn(a.spec)
    stages = _stage_set(a.stage)
    run = a.run.rstrip("/")
    sig_run = (a.sig_run or run).rstrip("/")
    base_dir = a.base or os.path.join(run, SIDE_KEY[side] + "_base")
    base = load_stage_cell(base_dir, side, "base")
    sig_base_dir = a.sig_base or _find_sig_dir(sig_run, run, SIDE_KEY[side] + "_base")
    sig_base = None
    variants = read_variants(a.variants) if a.variants else None
    base_id = a.base_id or os.path.basename(base_dir.rstrip("/"))
    prev = rc.load_accepted(a.prev_accepted)
    if prev["side"] and prev["side"] != side:
        raise SystemExit("--prev-accepted другой стороны: %s" % prev["side"])
    close = read_btc_close(a.btc)
    controls, unknown, skipped, cands = [], [], [], []
    loaded = {}
    for name in sorted(os.listdir(run)):
        d = os.path.join(run, name)
        if not os.path.isdir(d) or not os.path.exists(os.path.join(d, side + "-trades.csv")) or name.endswith(SIG):
            continue
        if is_control(name):
            controls.append(name)
            continue
        sp = spec_fn(name)
        if sp is None:
            unknown.append(name)
            continue
        if stages is not None and sp["stage"] not in stages:
            continue
        loaded[name] = (sp, load_stage_cell(d, side, name))
    for name, (sp, cell) in loaded.items():
        if sp["kind"] == "grid" and cell.n_a == base.n_a and cell.n_b == base.n_b and np.array_equal(cell.daily_a, base.daily_a) and cell.sum_a == base.sum_a:
            skipped.append(name)   # совместная сетка: клетка равна базе стадии (на A и по числу сделок) — не считается
            continue
        stp = detstat.stat(cell.daily_a - base.daily_a)                      # портфельная Δ_A
        mode = "sig" if (sp["stage"] in rc.SIG_STAGES and not a.portfolio_only) else "port"
        crit_cell, crit_base, st = cell, base, stp
        if mode == "sig":
            sd = _find_sig_dir(sig_run, run, name)
            if sd is None or sig_base_dir is None:
                raise SystemExit("нет сигнального двойника %s%s в %s (или базы %s_base%s): стадии E1, Els, NS, NT, E2, T читаются в сигнальном режиме" % (
                    name, SIG, sig_run, SIDE_KEY[side], SIG))
            if sig_base is None:
                sig_base = load_stage_cell(sig_base_dir, side, "sig_base")
            crit_cell = load_stage_cell(sd, side, name + SIG)
            crit_base = sig_base
            assert_same_signals(crit_cell, sig_base, name)
            st = detstat.stat(crit_cell.daily_a - sig_base.daily_a)          # критерий: Δ_A на одинаковых сигналах
        cc = coin_cluster(crit_cell, crit_base)
        cu, cu_text = cuped_line(union_delta(crit_cell.trades_a, crit_base.trades_a), close)
        cands.append(Cand(name, sp, cell.n_a, cell.n_b, st["mean"], st["se"], cell.sum_a, cell.mean_a, cell.pos_a, st["lo"], st["hi"], mode,
                          stp["mean"], stp["se"], stp["lo"], stp["hi"], cc, cu, cu_text))
    cands.sort(key=_rank)
    res = decide(cands, a.min_trades)
    note_e2 = None
    if any(c.spec["kind"] == "grid" and c.spec["stage"] == "E2" for c in cands):
        pk = dict(rc.parse_keys(prev["cumulative_keys"])[0])
        if "sn" in pk or "ls" in pk:
            note_e2 = "ось s в E2 — потолок стопа NS (sn)" if "sn" in pk else "ось s в E2 — предел стопа ls (не ниже e − S %)"
    for d in res["accepted"]:
        c = next(x for x in cands if x.cid == d["chosen"])
        if variants is not None and c.cid in variants and base_id in variants:
            keys = change_keys(variants[c.cid], variants[base_id])
        else:
            keys = c.spec.get("keys")
        if keys is None:
            raise SystemExit("нет ключей клетки %s: нужен --variants" % c.cid)
        d["keys"] = keys
    new_keys = rc.merge_keys(*[d["keys"] for d in res["accepted"]])
    seen_k, conflicts = {}, []
    for d in res["accepted"]:
        for k, v in rc.parse_keys(d["keys"])[0]:
            if k in seen_k and seen_k[k][1] != v:
                conflicts.append("ключ %s: %s=%s (%s) и %s=%s (%s) — побеждает последний" % (k, k, seen_k[k][1], seen_k[k][0], k, v, d["chosen"]))
            seen_k[k] = (d["chosen"], v)
    cum = rc.merge_keys(prev["cumulative_keys"], new_keys)
    ext_prev = list(prev["extend"])
    ext_new = [dict(axis=d["group"], value=d["extend"], from_cell=d["chosen"], stage=a.stage) for d in res["decisions"] if d["extend"] is not None and d["selected"]]
    # печать
    print("== %s, стадия %s: прогон %s, база %s%s" % (side, a.stage, run, base_dir, (", сигнальный прогон %s" % sig_run) if sig_base is not None else ""))
    print("   база стадии (портфель): A сделок %d, B сделок %d (число), сумма A %+.2f $, среднее на сделку A %+.3f $, в плюсе A %.1f %%" % (
        base.n_a, base.n_b, base.sum_a, base.mean_a, base.pos_a * 100))
    for c in cands:
        sc = next((d["smoothed"][c.cid] for d in res["decisions"] if c.cid in d["smoothed"]), c.d_a)
        print("  [%s] сделок A %d, B %d (число) | сумма A %+.2f $ | среднее A %+.3f $/сд. | в плюсе A %.1f %% | Δ_A[%s] %+.2f [%.2f; %.2f] $/сут., SE_Δ %.2f | сглаж. %+.2f%s" % (
            c.cid, c.n_a, c.n_b, c.sum_a, c.mean_a, c.pos_a * 100, "сигнал" if c.mode == "sig" else "портфель", c.d_a, c.lo, c.hi, c.se, sc,
            "" if c.eligible(a.min_trades) else " | < %d сделок" % a.min_trades))
        if c.mode == "sig":
            print("      портфельная Δ_A %+.2f [%.2f; %.2f] SE_Δ %.2f" % (c.port_d, c.port_lo, c.port_hi, c.port_se))
        if c.cluster:
            print("      кластер по монетам (устойчивость, не критерий): Δ_A %+.2f [%.2f; %.2f] SE %.2f, монет %d" % (
                c.cluster["mean"], c.cluster["lo"], c.cluster["hi"], c.cluster["se"], c.cluster["n_coins"]))
        if c.cuped_text:
            print("      " + c.cuped_text)
    for nm in skipped:
        print("  [%s] равна базе стадии на A и по числу сделок — пропущена, в Холм не идёт" % nm)
    for d in res["decisions"]:
        pf = ""
        if d["chosen"] is not None and d["mode"] == "sig":
            pf = " | портфельная Δ_A выбранного %+.2f, SE_Δ %.2f, вето %s" % (d["port_d"], d["port_se"], "СРАБОТАЛО" if d["vetoed"] else "нет")
        print("  ось/клетка %s (%s%s): выбрана %s → %s | %s%s%s" % (
            d["group"], d["kind"], ", семейство " + d["family"] if d["family"] else "", d["chosen"], "ПРИНЯТО" if d["selected"] else "не принято", d["reason"],
            " | " + "; ".join(d["flags"]) if d["flags"] else "", pf))
    if note_e2:
        print("  пометка: " + note_e2)
    for cf in conflicts:
        print("  ВНИМАНИЕ, конфликт принятых ключей: " + cf)
    if controls:
        print("  контроли (не клетки, в Холм не идут): " + ", ".join(controls))
    if unknown:
        print("  ПРОПУЩЕНО (нет метаданных клетки): " + ", ".join(unknown))
    print("  принятые ключи стадии: %r; накопленные: %r; расширение: %s; посчитано клеток (Холм, один раз при любом режиме): %d" % (new_keys, cum, ext_new or "—", len(cands)))
    out = dict(stage=a.stage, side=side, run=run, sig_run=sig_run if sig_base is not None else None, base=base_dir, spec=a.spec, min_trades=a.min_trades,
               keys=new_keys, prev_keys=prev["cumulative_keys"], cumulative_keys=cum, extend=ext_prev + ext_new, notes=([note_e2] if note_e2 else []) + conflicts,
               accepted=[dict(group=d["group"], cell=d["chosen"], keys=d["keys"], mode=d["mode"], d_a=fnum(d["d_a"]), se=fnum(d["se"]), port_d=fnum(d["port_d"]),
                              port_se=fnum(d["port_se"]), flags=d["flags"], reason=d["reason"], smoothed=fnum(d["smoothed"][d["chosen"]]),
                              neighbours=d["neighbours"][d["chosen"]]) for d in res["accepted"]],
               decisions=[dict(group=d["group"], kind=d["kind"], family=d["family"], chosen=d["chosen"], accepted=d["accepted"], selected=d["selected"], vetoed=d["vetoed"],
                               mode=d["mode"], port_d=fnum(d["port_d"]), port_se=fnum(d["port_se"]), reason=d["reason"], flags=d["flags"], extend=d["extend"],
                               smoothed={k: fnum(v) for k, v in d["smoothed"].items()}) for d in res["decisions"]],
               cells={c.cid: dict(mode=c.mode, n_a=c.n_a, n_b=c.n_b, sum_a=fnum(c.sum_a), mean_a=fnum(c.mean_a), pos_a=fnum(c.pos_a), d_a=fnum(c.d_a), se=fnum(c.se),
                                  lo=fnum(c.lo), hi=fnum(c.hi), port_d=fnum(c.port_d), port_se=fnum(c.port_se), port_lo=fnum(c.port_lo), port_hi=fnum(c.port_hi),
                                  cluster=({k: fnum(v) for k, v in c.cluster.items()} if c.cluster else None),
                                  cuped=({k: fnum(v) for k, v in c.cuped.items()} if c.cuped else None), eligible=c.eligible(a.min_trades)) for c in cands},
               n_computed=len(cands), computed=[c.cid for c in cands], skipped_equal_to_base=skipped, controls=controls, unknown=unknown)
    path = os.path.join(a.out or run, "accepted-%s-%s.json" % (a.stage.replace(",", "+"), side))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("  → %s" % path)
    return out


# ---- режим holm ------------------------------------------------------------------------------------------------------------

def holm_table(side, files):
    """files — список dict из accepted-*.json (порядок стадий). Клетка считается один раз при любом режиме (двойник `_sig` не отдельная клетка)."""
    files = sorted(files, key=lambda d: (min((rc.STAGE_ORDER.index(t) for t in d["stage"].replace("+", ",").split(",") if t in rc.STAGE_ORDER), default=99), d["stage"]))
    m = M_START[side]
    seen = set()
    rows = []
    for d in files:
        skip = set(d.get("skipped_equal_to_base", []))
        new = []
        for c in d["computed"]:
            c = c[:-len(SIG)] if c.endswith(SIG) else c
            if c not in seen and not is_control(c) and c not in skip:
                new.append(c)
                seen.add(c)
        m += len(new)
        rows.append((d["stage"], len(new), m))
    lines = ["== Холм %s: старт %d (после TK-591), верхняя граница %d (z = %.3f); стадия меток — 0 испытаний; справочный счёт (§13.21.1 п. 6)" % (
        side, M_START[side], M_UPPER[side], ND.inv_cdf(1 - 0.05 / (2 * M_UPPER[side])))]
    for st, n, mm in rows:
        lines.append("  стадия %-10s посчитано %2d клеток (равные базе и контроли не считаются) → m = %d, z = %.3f" % (st, n, mm, ND.inv_cdf(1 - 0.05 / (2 * mm))))
    lines.append("  итого m = %d; запас до границы %d%s" % (m, M_UPPER[side] - m, "" if m <= M_UPPER[side] else "  ПРЕВЫШЕНИЕ ГРАНИЦЫ"))
    return lines, m


def run_holm(a):
    for side in (("long", "short") if a.side == "both" else (a.side,)):
        files = [json.load(open(p, encoding="utf-8")) for p in glob.glob(os.path.join(a.out, "accepted-*-%s.json" % side))]
        lines, _ = holm_table(side, files)
        print("\n".join(lines))


# ---- режимы final и confirm: B читается здесь и только здесь ----------------------------------------------------------------------

def load_full(vdir, side):
    """Полная загрузка клетки (A и B) — только для режимов final и confirm."""
    nets_a, nets_b, trades = [], [], {}
    with open(os.path.join(vdir, side + "-trades.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            net = float(r["net"])
            ed = int(r["entry_day"])
            (nets_a if ed < NA else nets_b).append(net)
            trades[(r["sym"], r["entry_ms"])] = (ed, int(r["entry_min"]), net)
    with open(os.path.join(vdir, side + "-daily.csv"), encoding="utf-8", newline="") as f:
        daily = np.array([float(r["pnl"]) for r in csv.DictReader(f)])
    return dict(nets_a=nets_a, nets_b=nets_b, daily=daily, trades=trades)


def _tstat(nets):
    n = len(nets)
    return dict(n=n, sum=float(sum(nets)), mean=(sum(nets) / n) if n else NAN, pos=(sum(1 for x in nets if x > 0) / n) if n else NAN)


def cuped_final_lines(label, tr_c, tr_b, close):
    """CUPED в final: θ — на A по собственным Δ_i пары, применяется к A и B (центр — среднее x по сделкам A); печать, не критерий."""
    if close is None:
        return ["  CUPED (описательно) Δ(C − %s): н/д — нет файла BTCUSDT (--btc)" % label]
    items = union_delta(tr_c, tr_b)
    fit = cuped_fit({k: v for k, v in items.items() if v[0] < NA}, close)
    if fit is None:
        return ["  CUPED (описательно) Δ(C − %s): н/д — мало сделок A / нулевая дисперсия ковариаты" % label]
    out = []
    for h, lo in (("A", 0), ("B", NA)):
        raw, adj, share = cuped_apply(items, close, fit, lo, NA)
        sr, sa = detstat.stat(raw), detstat.stat(adj)
        out.append("  CUPED (описательно) Δ(C − %s) %s: θ(A) = %.3g, доля снятой дисперсии сделок %s, Δ после CUPED %+.2f [%.2f; %.2f] SE %.2f (до: %+.2f [%.2f; %.2f] SE %.2f)" % (
            label, h, fit["theta"], pct(share), sa["mean"], sa["lo"], sa["hi"], sa["se"], sr["mean"], sr["lo"], sr["hi"], sr["se"]))
    return out


def final_lines(side, cand_dir, base_dir, stage_base_dir=None, btc=None):
    """Строки отчёта и данные: C против базы стадии и простой базы; §10 при m = верхняя граница (описательно, без вердикта вперёд)."""
    import report_tick  # noqa: E402  (тяжёлые импорты только здесь)
    import ws_report as wr  # noqa: E402
    from lib import A as SA, B as SB  # noqa: E402
    from report import fq_days  # noqa: E402
    m = M_UPPER[side]
    z = ND.inv_cdf(1 - 0.05 / (2 * m))
    detstat.patch(report_tick)
    fq = fq_days()
    close = read_btc_close(btc)
    dirs = collections.OrderedDict([("C", cand_dir)])
    if stage_base_dir:
        dirs["база стадии"] = stage_base_dir
    dirs["простая база"] = base_dir
    full = {k: load_full(v, side) for k, v in dirs.items()}
    P = {(side, k): v for k, v in dirs.items()}
    D = {side: {k: full[k]["daily"] for k in dirs}}
    tag = DESCR % m
    lines = ["== final %s: %s (Холм справочный; B читается один раз), z = %.3f. Подпись: %s" % (side, tag, z, SIGNATURE)]
    data = {"m": m, "z": z, "label": tag, "cells": {}, "deltas": {}}
    for k in dirs:
        ta, tb = _tstat(full[k]["nets_a"]), _tstat(full[k]["nets_b"])
        hs = report_tick.halves_stats(side, P, D, k, k, z, fq, None)
        ver = report_tick.verdict(hs, z)
        a_, b_ = hs["A"], hs["B"]
        lines.append("  [%s] %s | §10 (%s): %s" % (k, dirs[k], tag, ver))
        lines.append("      A (%s): сделок %d, сумма %+.2f $, среднее %+.3f $/сд., в плюсе %.1f %%, %+.2f [%.2f; %.2f] $/сут., нижняя после Холма %.2f" % (
            tag, ta["n"], ta["sum"], ta["mean"], ta["pos"] * 100, a_["mean"], a_["lo"], a_["hi"], a_["low_holm"]))
        lines.append("      B (%s): сделок %d, сумма %+.2f $, среднее %+.3f $/сд., в плюсе %.1f %%, %+.2f [%.2f; %.2f] $/сут., нижняя после Холма %.2f" % (
            tag, tb["n"], tb["sum"], tb["mean"], tb["pos"] * 100, b_["mean"], b_["lo"], b_["hi"], b_["low_holm"]))
        for kk in ("no_best2_days", "no_best2_coins", "after_first_day", "no_worst2_days", "no_worst2_coins"):
            lines.append("        %s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f]" % (kk, a_[kk]["mean"], a_[kk]["lo"], a_[kk]["hi"], b_[kk]["mean"], b_[kk]["lo"], b_[kk]["hi"]))
        data["cells"][k] = dict(A=ta, B=tb, verdict=ver)
    for k in dirs:
        if k == "C":
            continue
        d = D[side]["C"] - D[side][k]
        st = {"A": detstat.stat(d[SA]), "B": detstat.stat(d[SB]), "60": detstat.stat(d)}
        low = st["60"]["mean"] - z * st["60"]["se"]
        rd = wr.read_rule(st["A"], st["B"], st["60"], z)
        lines.append("  Δ(C − %s) (%s): A %+.2f [%.2f; %.2f] SE %.2f | B %+.2f [%.2f; %.2f] SE %.2f | 60 сут. %+.2f [%.2f; %.2f], нижняя после Холма %.2f → %s" % (
            k, tag, st["A"]["mean"], st["A"]["lo"], st["A"]["hi"], st["A"]["se"], st["B"]["mean"], st["B"]["lo"], st["B"]["hi"], st["B"]["se"],
            st["60"]["mean"], st["60"]["lo"], st["60"]["hi"], low, rd))
        lines += cuped_final_lines(k, full["C"]["trades"], full[k]["trades"], close)
        data["deltas"][k] = dict(st=st, low_holm_60=low, read=rd)
    lines.append("  " + SIGNATURE)
    return lines, data


def _guarded(a, side, name, build):
    out_dir = a.out or os.path.dirname(a.cand.rstrip("/"))
    path = os.path.join(out_dir, "%s-%s.json" % (name, side))
    if os.path.exists(path) and not a.force:
        print("B (окно) уже прочитана (%s, сторона %s, %s) — повтор печатает сохранённое; пересчёт — только с --force и пометкой в журнале" % (name, side, path))
        print("\n".join(json.load(open(path, encoding="utf-8"))["lines"]))
        return
    lines, data = build()
    print("\n".join(lines))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(side=side, cand=a.cand, forced=bool(a.force), lines=lines, data=data), f, ensure_ascii=False, indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("  → %s" % path)


def run_final(a, side):
    _guarded(a, side, "final", lambda: final_lines(side, a.cand, a.base, a.stage_base, a.btc))


def onesided(x, nboot=lib.NBOOT):
    """Среднее, односторонние 95 %-границы (нижняя = 5-й, верхняя = 95-й перцентиль блочного бутстрепа, блок 4); зерно — от самого ряда (как detstat)."""
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(int(hashlib.md5(np.round(x, 9).tobytes()).hexdigest()[:8], 16))
    bm = lib.block_boot_means(x, rng, nboot)
    lo, hi = np.percentile(bm, [5, 95])
    return dict(mean=float(x.mean()), se=float(bm.std(ddof=1)), lower=float(lo), upper=float(hi), n=len(x))


def confirm_verdict(day, coin):
    """Подтверждающий тест (§13.21.1 п. 6). day — суточный P&L окна; coin — {монета: ряд}. «Подтверждено»: среднее > 0, нижняя одностор. 95 % > 0 и знак среднего + без
    2 лучших суток и без 2 лучших монет (без нижней границы на усечённых). «Опровергнуто» / «не ясно» — по §10: neg = среднее < 0 или верхняя < 0; устойчивость знака − без 2 худших."""
    day = np.asarray(day, dtype=float)
    st = onesided(day)
    tot = {c: float(v.sum()) for c, v in coin.items()}
    best_c = [c for c, _ in sorted(tot.items(), key=lambda kv: -kv[1])[:2]]
    worst_c = [c for c, _ in sorted(tot.items(), key=lambda kv: kv[1])[:2]]
    no_best_days = float(np.delete(day, np.argsort(-day)[:2]).mean())
    no_worst_days = float(np.delete(day, np.argsort(day)[:2]).mean())
    no_best_coins = float((day - sum((coin[c] for c in best_c), np.zeros(len(day)))).mean())
    no_worst_coins = float((day - sum((coin[c] for c in worst_c), np.zeros(len(day)))).mean())
    ok = st["mean"] > 0 and st["lower"] > 0 and no_best_days > 0 and no_best_coins > 0
    neg = st["mean"] < 0 or st["upper"] < 0
    ref = neg and st["mean"] < 0 and no_worst_days < 0 and no_worst_coins < 0
    verdict = "подтверждено на августе" if ok else ("опровергнуто" if ref else "не ясно")
    return dict(st=st, no_best_days=no_best_days, no_best_coins=no_best_coins, no_worst_days=no_worst_days, no_worst_coins=no_worst_coins,
                best_coins=best_c, worst_coins=worst_c, verdict=verdict)


def confirm_lines(side, cand_dir, lo, hi):
    full = load_full(cand_dir, side)
    day = full["daily"][lo:hi]
    coin = {}
    with open(os.path.join(cand_dir, side + "-coinday.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            d = int(r["day"])
            if lo <= d < hi:
                coin.setdefault(r["sym"], np.zeros(hi - lo))[d - lo] += float(r["pnl"])
    nets = [t[2] for t in full["trades"].values() if lo <= t[0] < hi]
    ts = _tstat(nets)
    v = confirm_verdict(day, coin)
    st = v["st"]
    lines = ["== confirm %s: подтверждающий тест m = 1, односторонне 5 %% (замороженный кандидат, август: сутки %d…%d, %d сут.)" % (side, lo, hi - 1, hi - lo),
             "  сделок %d, сумма %+.2f $, среднее %+.3f $/сд., в плюсе %.1f %%" % (ts["n"], ts["sum"], ts["mean"], ts["pos"] * 100),
             "  средний $/сут. %+.2f, SE %.2f, нижняя односторонняя 95 %%-граница %+.2f, верхняя %+.2f" % (st["mean"], st["se"], st["lower"], st["upper"]),
             "  устойчивость (знак среднего $/сут.): без 2 лучших суток %+.2f; без 2 лучших монет (%s) %+.2f | без 2 худших суток %+.2f; без 2 худших монет (%s) %+.2f" % (
                 v["no_best_days"], ", ".join(v["best_coins"]), v["no_best_coins"], v["no_worst_days"], ", ".join(v["worst_coins"]), v["no_worst_coins"]),
             "  вердикт (m = 1, односторонне 5 %%): %s" % v["verdict"]]
    return lines, v


def run_confirm(a, side):
    lo, hi = (int(x) for x in a.days.split(":"))
    _guarded(a, side, "confirm", lambda: confirm_lines(side, a.cand, lo, hi))


# ---- main --------------------------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--side", choices=("long", "short", "both"), required=True)
    s.add_argument("--run", required=True)
    s.add_argument("--stage", required=True, help="метка стадии(й) через «+» или «,»: E1, Els+NS+NT, E2, T, P, F, asm; all — без фильтра")
    s.add_argument("--sig-run", help="каталог сигнального прогона (по умолчанию — тот же, клетки `<id>_sig`)")
    s.add_argument("--base", help="каталог клетки-базы стадии (по умолчанию <run>/<L|S>_base)")
    s.add_argument("--sig-base", help="каталог базы сигнального режима (по умолчанию <sig-run>/<L|S>_base_sig)")
    s.add_argument("--base-id", help="id строки базы в файле вариантов (по умолчанию — имя каталога базы)")
    s.add_argument("--prev-accepted")
    s.add_argument("--variants")
    s.add_argument("--out")
    s.add_argument("--spec", default="auto")
    s.add_argument("--min-trades", type=int, default=MIN_TRADES)
    s.add_argument("--btc", default=BTC_DEFAULT)
    s.add_argument("--portfolio-only", action="store_true", help="читать все клетки в портфельном режиме (диагностика; не для отбора E/T)")
    f = sub.add_parser("final")
    f.add_argument("--side", choices=("long", "short"), required=True)
    f.add_argument("--cand", required=True)
    f.add_argument("--base", required=True)
    f.add_argument("--stage-base")
    f.add_argument("--out")
    f.add_argument("--btc", default=BTC_DEFAULT)
    f.add_argument("--force", action="store_true")
    c = sub.add_parser("confirm")
    c.add_argument("--side", choices=("long", "short"), required=True)
    c.add_argument("--cand", required=True)
    c.add_argument("--days", required=True, help="срез суток окна августа в строках daily: от:до (до не включая)")
    c.add_argument("--out")
    c.add_argument("--force", action="store_true")
    h = sub.add_parser("holm")
    h.add_argument("--out", required=True)
    h.add_argument("--side", choices=("long", "short", "both"), default="both")
    a = ap.parse_args(argv)
    if a.mode == "stage":
        for side in (("long", "short") if a.side == "both" else (a.side,)):
            run_stage(a, side)
    elif a.mode == "final":
        run_final(a, a.side)
    elif a.mode == "confirm":
        run_confirm(a, a.side)
    else:
        run_holm(a)


if __name__ == "__main__":
    main()
