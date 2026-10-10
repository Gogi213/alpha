#!/usr/bin/env python3
"""П-10 §6.2: признаки H–S и клетка Г-89 по сигналам B1 (`docs/research/P-10-code-exists-batch.md` §6.2, §7, §14 п. 6; TK-024).

Вход — `signals.csv` базы B1 (`b5/p08-b1/<сутки>/t-bid-btc4h-q1/`, дома aug/hist/rec как у П-08). Признаки — по последней
ЗАКРЫТОЙ минуте до сигнала m = t0 // 1 мин × 1 мин − 1 мин (как `p08-feats.py`/`loss-corr.py`); нет значения → признак пуст,
сигнал в клетке остаётся. Общее — импортом: `Bars/ret` из `loss-corr.py` (через `p08-feats.py`), автомат занятости из
`busy-replay.py`, дома и `write_keep` из `p07-h9h10.py`, `quantile_cut` и число сделок B1 из `p08-cov.py`.

Колонки исхода (`reason`/`exit_ns`/`net_bps`) читаются ТОЛЬКО в семье N (доля стопов монеты за 14 сут до t0) и в блоке Г-120 (S).

    python3 p10-feats.py feats --month aug --out tmp-p10/cov/aug     # feats-p10.csv + g89-market.csv месяца
    python3 p10-feats.py s-keep --out tmp-p10/run/s-keep.csv         # блок Г-120: keep-файл + счётчики
"""
import argparse
import bisect
import copy
import csv
import datetime as dt
import glob
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~/alpha")
MIN_MS = 60_000
DAY_NS = 86_400 * 10 ** 9
EXCLUDED = {"TRXUSDT"}  # В-105: TRX вне пула
POSITION_USD = 500.0     # --position-usd портфеля (Г-123: «$500 / оборот»)
N_WINDOW_NS = 14 * DAY_NS  # §6.2 N: 14 суток назначены договором
N_MIN_TRADES = 3           # §6.2 N: меньше 3 прежних сделок — признака нет
S_STREAK = 5               # §6.2 S / §14 п. 6: 5 убыточных подряд
S_BLOCK_NS = 24 * 3600 * 10 ** 9  # §6.2 S: блок 24 ч (назначено)


def load_mod(fname, alias):
    """Модуль по имени файла (дефис не импортируется обычным import): рядом, ~/alpha/bin, ~/alpha/tmp-p07."""
    for d in (HERE, os.path.join(HOME, "bin"), os.path.join(HOME, "tmp-p07")):
        p = os.path.join(d, fname)
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location(alias, p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    sys.exit(f"нет {fname} рядом, в ~/alpha/bin и ~/alpha/tmp-p07")


p08f = load_mod("p08-feats.py", "p08f")
lc = p08f.load_loss_corr()
br = load_mod("busy-replay.py", "busy_replay")
p07 = load_mod("p07-h9h10.py", "p07base")
p08c = load_mod("p08-cov.py", "p08cov")
SET_ = p07.SET_
quantile_cut = p08c.quantile_cut

# колонки признаков; пусто = нет значения
FEATS = ["coin_abs_minus_btc_1h", "g62_er", "g74_hi5_bps", "g76_low60_sigma", "g22_turnover24_usd", "g123_cap_pct",
         "g48_stop_share_14d", "g31_off_center", "g47_wall_traded", "g14_tea", "g14_asia", "g14_quiet", "g14_wkend",
         "g45_min_to_fund"]
BASE_COLS = ["symbol", "day_utc", "form", "signal_index", "t0_ns", "price_tick", "entry_px"]
STUDY_ROOTS = [f"{HOME}/epochs/e-aug/study", f"{HOME}/epochs/e-archive/study", f"{HOME}/study",
               f"{HOME}/tmp-t29/rec/study", f"{HOME}/tmp-lsk0914.used-20260926/home/study"]
D20_ROOTS = [f"{HOME}/epochs/e-aug/study/approaches-t28/D20", f"{HOME}/epochs/e-archive/study/approaches/D20",
             f"{HOME}/study/approaches-t28/D20", f"{HOME}/tmp-t29/rec/study/approaches/D20",
             f"{HOME}/tmp-lsk0914.used-20260926/home/study/approaches/D20"]
ALL_HOMES = [f"{HOME}/epochs/e-jul", f"{HOME}/epochs/e-aug", f"{HOME}/tmp-lsk0914.used-20260926/home",
             f"{HOME}/tmp-t29/rec", f"{HOME}/epochs/e-archive", HOME]
B1_TREE = "p08-b1"        # B1 протокола §9(а): `b5/p08-b1/<сутки>/t-bid-btc4h-q1/{signals,rounds}.csv`
MKT_TREE = "p07b-t9-market"  # сигналы клетки «market» для Г-89 (TK-009)
JUL_B1 = f"{HOME}/epochs/e-jul/b5/{B1_TREE}"
JUL_FROM = "2026-07-17"  # окно N — 14 сут до 1 августа


# ---------- признаки по свечам (чистые функции от Bars) ----------

def f_h(c, btc, m):
    """Г-11: |ход монеты − ход BTC| за 1 ч (`ret` loss-corr), bps."""
    a, b = lc.ret(c, m, 60), lc.ret(btc, m, 60)
    return None if a is None or b is None else abs(a - b)


def f_i(c, m):
    """Г-62: ER = |close_t − close_{t−60}| / Σ|Δclose| по 1м за 60 мин (нужны все 61 закрытие)."""
    w = c.window(m, 61)
    if len(w) < 61:
        return None
    cl = [x[2] for x in w]
    path = sum(abs(b - a) for a, b in zip(cl, cl[1:]))
    return abs(cl[-1] - cl[0]) / path if path > 0 else None


def f_j(c, m, px):
    """Г-74: (макс high за 5 1м-баров − цена взвода) / цена, bps; 5 назначено договором."""
    w = c.window(m, 5)
    return None if len(w) < 5 or not px else (max(x[0] for x in w) - px) / px * 1e4


def f_k(c, m, px, sigma_bps):
    """Г-76: (цена взвода − мин low за 60 мин) / σ₂₄₀ монеты; числитель в bps (к цене), σ в bps — безразмерно."""
    w = c.window(m, 60)
    if len(w) < 60 or not px or not sigma_bps:
        return None
    return (px - min(x[1] for x in w)) / px * 1e4 / sigma_bps


def turnover24(c, m):
    """Оборот монеты, $ за 24 ч закрытых минут: Σ volume × close (в файле свечей оборота нет, объём — в монете)."""
    w = c.window(m, 1440)
    return sum(x[3] * x[2] for x in w) if len(w) == 1440 else None


def f_o(c, m, px):
    """Г-31: |положение цены в диапазоне 60 мин − 0,5|."""
    w = c.window(m, 60)
    if len(w) < 60 or not px:
        return None
    hi, lo = max(x[0] for x in w), min(x[1] for x in w)
    return abs((px - lo) / (hi - lo) - 0.5) if hi > lo else None


def f_p(c, m, price_tick, tick):
    """Г-47: цена стены торговалась в 1м-барах за 24 ч (1/0); сравнение в целых тиках, чтобы не зависеть от float."""
    w = c.window(m, 1440)
    if len(w) < 1440 or not tick:
        return None
    return int(any(round(lo / tick) <= price_tick <= round(hi / tick) for hi, lo, _, _ in w))


def time_feats(t0_ns):
    """Г-14 (часы/дни UTC, 1 = пропуск) и Г-45 (минут до ближайшего расчёта фандинга 00/08/16 UTC)."""
    ts = t0_ns // 10 ** 9
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    to_settle = (8 * 3600 - ts % (8 * 3600)) % (8 * 3600)
    return {"g14_tea": int(d.hour in (16, 17)), "g14_asia": int(d.hour == 0),
            "g14_quiet": int(d.hour in (2, 3, 4, 5, 6, 21, 22, 23)), "g14_wkend": int(d.weekday() >= 5),
            "g45_min_to_fund": to_settle / 60}


# ---------- клетки §6.2: имя → (семья, признак, вид, доля); квантиль по сигналам B1 августа (§6.2, §7) ----------
# вид: low/high — оставляем нижнюю/верхнюю долю num/den определённых значений; skip-high — пропускаем верхнюю долю (N);
# le/gt — порог числом договора; eq0/eq1 — бинарный признак; s-block — Г-120; usd-top — Г-89 (верхняя треть номинала).
CELLS = {
    "g11-q33": ("H", "coin_abs_minus_btc_1h", "low", 1, 3), "g11-q67": ("H", "coin_abs_minus_btc_1h", "low", 2, 3),
    "g62-q33": ("I", "g62_er", "high", 1, 3), "g62-q67": ("I", "g62_er", "high", 2, 3),
    "g74-q33": ("J", "g74_hi5_bps", "low", 1, 3), "g74-q67": ("J", "g74_hi5_bps", "low", 2, 3),
    "g76-q33": ("K", "g76_low60_sigma", "low", 1, 3), "g76-q67": ("K", "g76_low60_sigma", "low", 2, 3),
    "g22-thin33": ("L", "g22_turnover24_usd", "low", 1, 3), "g22-thin67": ("L", "g22_turnover24_usd", "low", 2, 3),
    "g123-cap1": ("M", "g123_cap_pct", "le", 1, 1),
    "g48-q33": ("N", "g48_stop_share_14d", "skip-high", 1, 3), "g48-q67": ("N", "g48_stop_share_14d", "skip-high", 2, 3),
    "g31-q33": ("O", "g31_off_center", "high", 1, 3), "g31-q67": ("O", "g31_off_center", "high", 2, 3),
    "g47-hist": ("P", "g47_wall_traded", "eq1", 1, 1),
    "g14-tea": ("Q1", "g14_tea", "eq0", 1, 1), "g14-asia": ("Q2", "g14_asia", "eq0", 1, 1),
    "g14-quiet": ("Q3", "g14_quiet", "eq0", 1, 1), "g14-wkend": ("Q4", "g14_wkend", "eq0", 1, 1),
    "g45-fund": ("R", "g45_min_to_fund", "gt", 60, 1),
    "g120-block5": ("S", None, "s-block", 0, 1),
    "p10-g89-mkt-big": ("G89", "g89_usd", "usd-top", 1, 3),
}
FUND_SKIP_MIN = 60   # §6.2 R: пропуск, если до расчёта ≤ 60 мин
CAP_PCT_MAX = 1.0    # §6.2 M: $500 / оборот > 1 % → пропуск


def rank_cut(vals, num, den, low):
    """Значение на ранге ⌈num·n/den⌉ (целая арифметика, без float): по возрастанию (low) или по убыванию. Как p08-cov.quantile_cut."""
    vs = sorted(vals) if low else sorted(vals, reverse=True)
    return vs[(num * len(vs) + den - 1) // den - 1]


def cell_threshold(name, aug_vals):
    """Порог клетки по значениям признака сигналов B1 августа (определённым): квантили; прочие виды — число договора/None."""
    fam, feat, kind, num, den = CELLS[name]
    if kind in ("low", "high"):
        return rank_cut(aug_vals, num, den, kind == "low")
    if kind == "skip-high":
        return rank_cut(aug_vals, den - num, den, True)
    if kind == "le":
        return CAP_PCT_MAX
    if kind == "gt":
        return float(FUND_SKIP_MIN)
    if kind == "usd-top":
        return rank_cut(aug_vals, num, den, False)
    return None


def cell_op(name):
    kind = CELLS[name][2]
    return {"low": "<=", "high": ">=", "skip-high": "<=", "le": "<=", "gt": ">", "eq0": "==0", "eq1": "==1",
            "usd-top": ">=", "s-block": "блок"}[kind]


def keep_value(op, thr, v):
    """Оставляем ли сигнал: нет значения — остаётся (§6.2)."""
    if v is None:
        return True
    if op == "==0":  # у флаговых клеток порога нет (thr=None) — сравнение с ним не вычисляется
        return v == 0
    if op == "==1":
        return v == 1
    return {"<=": v <= thr, ">=": v >= thr, ">": v > thr}[op]


# ---------- семья N: закрытия B1 (исход читается только здесь) ----------

def b1_accepted_closes(day_dirs):
    """{symbol: ([exit_ns…], [стоп?…])} по принятым после автомата занятости кругам B1 (без TRX), по возрастанию exit_ns."""
    acc = {}
    for d in day_dirs:
        sp, rp = os.path.join(d, "signals.csv"), os.path.join(d, "rounds.csv")
        if not (os.path.exists(sp) and os.path.exists(rp)):
            continue
        kept, _ = br.replay(sp, None)
        _, body = br.read_body(rp)
        for r in csv.DictReader(body):
            if (r["symbol"], r["day_utc"], r["form"], r["signal_index"]) in kept and r["symbol"] not in EXCLUDED:
                acc.setdefault(r["symbol"], []).append((int(r["exit_ns"]), r["reason"] == "stop"))
    return {s: ([x[0] for x in sorted(v)], [x[1] for x in sorted(v)]) for s, v in acc.items()}


def stop_share(closes, symbol, t0_ns):
    """Доля стопов закрытий монеты в [t0 − 14 сут, t0) — строго до t0; < 3 сделок → None."""
    if symbol not in closes:
        return None
    ex, st = closes[symbol]
    i, j = bisect.bisect_left(ex, t0_ns - N_WINDOW_NS), bisect.bisect_left(ex, t0_ns)
    return sum(st[i:j]) / (j - i) if j - i >= N_MIN_TRADES else None


# ---------- справочники: σ, инструменты, D20 ----------

class Ref:
    """σ₂₄₀ (`study/sigma240`), инструменты суток (`root-<сутки>/instruments.csv`) и подходы D20 — с кэшем."""

    def __init__(self, sigma_dir=f"{HOME}/study/sigma240", study_roots=None, d20_roots=None):
        self.sigma_dir, self.roots, self.d20_roots = sigma_dir, study_roots or STUDY_ROOTS, d20_roots or D20_ROOTS
        self._instr, self._d20 = {}, {}

    def sigma(self, sym):
        p = os.path.join(self.sigma_dir, f"sigma-{sym}.csv")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8", newline="") as fh:
            return {int(r["window_end_ms"]): float(r["sigma_bps"]) for r in csv.DictReader(fh)}

    def day_root(self, day):
        for s in self.roots:
            d = os.path.join(s, f"root-{day}")
            if os.path.isdir(d):
                return d
        return None

    def instrument(self, day, sym):
        """(tick_size, qty_step) монеты на сутки или None."""
        if day not in self._instr:
            tab = {}
            root = self.day_root(day)
            p = os.path.join(root, "instruments.csv") if root else ""
            if p and os.path.exists(p):
                with open(p, encoding="utf-8", newline="") as fh:
                    tab = {r["symbol"]: (float(r["tick_size"]), float(r["qty_step"])) for r in csv.DictReader(fh)}
            self._instr[day] = tab
        return self._instr[day].get(sym)

    def sizes(self, day, sym):
        """{(arm_ms, price_tick): (size_at_arm, best_own_tick)} бидовых подходов монеты в сутки (кэш D20)."""
        k = (day, sym)
        if k not in self._d20:
            out = {}
            for root in self.d20_roots:
                p = os.path.join(root, day, f"approaches-{sym}.csv")
                if os.path.exists(p):
                    with open(p, encoding="utf-8", newline="") as fh:
                        for r in csv.DictReader(fh):
                            if r["side"] == "bid":
                                out[(int(r["arm_ms"]), int(r["price_tick"]))] = (int(r["size_at_arm"]), int(r["best_own_tick"]))
                    break
            self._d20[k] = out
        return self._d20[k]

    def wall_usd(self, day, sym, t0_ns, price_tick):
        """Номинал стены на взводе, $ = price_tick × tick_size × size_at_arm × qty_step (как usd_min в Rust)."""
        ap = self.sizes(day, sym).get((int(t0_ns) // 1_000_000, int(price_tick)))
        ins = self.instrument(day, sym)
        return None if ap is None or ins is None else int(price_tick) * ins[0] * ap[0] * ins[1]

    def arm_px(self, day, sym, t0_ns, price_tick):
        """Цена на взводе = лучшая своя цена подхода (`best_own_tick` D20) × tick_size; нет в D20 — None."""
        ap = self.sizes(day, sym).get((int(t0_ns) // 1_000_000, int(price_tick)))
        ins = self.instrument(day, sym)
        return None if ap is None or ins is None else ap[1] * ins[0]


# ---------- сигналы и признаки месяца ----------

def month_homes(m):
    """[(дом, [сутки])] месяца как в `p08-cov.HOMES` (aug: e-aug 1–31; sep: история 1–15 + запись 16–23)."""
    return [(home, [f"{ym}-{d:02d}" for d in days]) for home, days, ym in p08c.HOMES[m]]


def load_month_signals(m, tree=B1_TREE):
    out = []
    for home, days in month_homes(m):
        out += p08f.load_signals([f"{home}/b5/{tree}"], {SET_}, set(days))
    out = [r for r in out if r["symbol"] not in EXCLUDED]
    out.sort(key=lambda r: (int(r["t0_ns"]), r["symbol"], int(r["signal_index"])))
    return out


def build_feats(signals, kdirs, btc_paths, ref, closes):
    """[dict признаков] в порядке `signals`; монеты по одной (память), BTC один раз."""
    btc = lc.Bars(btc_paths)
    res = [None] * len(signals)
    by_sym = {}
    for i, r in enumerate(signals):
        by_sym.setdefault(r["symbol"], []).append(i)
    for sym in sorted(by_sym):
        c = lc.Bars([os.path.join(d, f"ref-{sym}-1m.csv") for d in kdirs])
        sig = ref.sigma(sym)
        for i in by_sym[sym]:
            r = signals[i]
            t0 = int(r["t0_ns"])
            m = t0 // 1_000_000 // MIN_MS * MIN_MS - MIN_MS
            px, pt = float(r["entry_px"]), int(r["price_tick"])  # px — цена входа плана (O); взвод — ниже
            apx = ref.arm_px(r["day_utc"], sym, t0, pt)
            ins = ref.instrument(r["day_utc"], sym)
            tov = turnover24(c, m)
            f = {"coin_abs_minus_btc_1h": f_h(c, btc, m), "g62_er": f_i(c, m), "g74_hi5_bps": f_j(c, m, apx),
                 "g76_low60_sigma": f_k(c, m, apx, sig.get(m + MIN_MS)), "g22_turnover24_usd": tov,
                 "g123_cap_pct": POSITION_USD / tov * 100 if tov else None,
                 "g48_stop_share_14d": stop_share(closes, sym, t0), "g31_off_center": f_o(c, m, px),
                 "g47_wall_traded": f_p(c, m, pt, ins[0]) if ins else None}
            f.update(time_feats(t0))
            res[i] = f
    return res


def fmt(v):
    return "" if v is None else repr(v) if isinstance(v, float) else str(v)


def write_feats(path, signals, feats, extra=None):
    """CSV: база + признаки (+ extra: имя → список значений). Числа — repr (круговой разбор без потерь)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cols = BASE_COLS + FEATS + list(extra or {})
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for i, r in enumerate(signals):
            w.writerow([r[c] for c in BASE_COLS] + [fmt(feats[i][k]) for k in FEATS]
                       + [fmt(v[i]) for v in (extra or {}).values()])


def read_feats(path):
    """Строки CSV; числовые признаки → float/None."""
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            if k not in BASE_COLS:
                r[k] = None if v == "" else float(v)
    return rows


def kline_dirs():
    return [f"{h}/study/klines" for h in ALL_HOMES], [f"{h}/study/regime/ref-BTCUSDT-1m.csv" for h in ALL_HOMES]


def day_dirs(trees_root, days):
    return [os.path.join(trees_root, d, SET_) for d in days]


def b1_closes_all():
    """Закрытия B1: июль 17–31 (для начала августа) + август + сентябрь — принятые после автомата занятости."""
    dirs = [d for d in sorted(glob.glob(os.path.join(JUL_B1, "2026-07-*"))) if os.path.basename(d) >= JUL_FROM]
    dirs = [os.path.join(d, SET_) for d in dirs if os.path.isdir(d)]
    for m in ("aug", "sep"):
        for home, days in month_homes(m):
            dirs += day_dirs(f"{home}/b5/{B1_TREE}", days)
    return b1_accepted_closes(dirs)


def cmd_feats(m, out):
    """feats-p10.csv (все сигналы B1 месяца + признаки + номинал стены) и g89-market.csv (сигналы клетки market + номинал)."""
    ref = Ref()
    kd, bp = kline_dirs()
    sig = load_month_signals(m)
    closes = b1_closes_all()
    feats = build_feats(sig, kd, bp, ref, closes)
    usd = [ref.wall_usd(r["day_utc"], r["symbol"], r["t0_ns"], r["price_tick"]) for r in sig]
    write_feats(os.path.join(out, "feats-p10.csv"), sig, feats, {"g89_usd": usd})
    msig = load_month_signals(m, MKT_TREE)
    musd = [ref.wall_usd(r["day_utc"], r["symbol"], r["t0_ns"], r["price_tick"]) for r in msig]
    with open(os.path.join(out, "g89-market.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(BASE_COLS + ["g89_usd"])
        for r, u in zip(msig, musd):
            w.writerow([r[c] for c in BASE_COLS] + [fmt(u)])
    print(f"{m}: сигналов B1 {len(sig)}, market {len(msig)}, номинал определён B1 {sum(u is not None for u in usd)} "
          f"market {sum(u is not None for u in musd)}")


# ---------- блок Г-120 (S): обёртка вокруг автомата занятости busy-replay ----------

def _new_state():
    return {"streak": 0, "block_start": -1, "block_until": -1, "pending": [], "blocks": 0}


def _flush(st, t0, n, block_ns):
    """Закрытия монеты строго до t0 — по exit_ns; прибыль сбрасывает серию, n-й убыток подряд — блок и сброс серии."""
    st["pending"].sort()
    while st["pending"] and st["pending"][0][0] < t0:
        exit_ns, loss = st["pending"].pop(0)
        if not loss:
            st["streak"] = 0
            continue
        st["streak"] += 1
        if st["streak"] >= n:
            st["block_start"], st["block_until"], st["streak"] = exit_ns, exit_ns + block_ns, 0
            st["blocks"] += 1


def s_day_pass(states, accepted, n, block_ns):
    """accepted: {монета: [{key, t0, close: (exit_ns, loss)|None}] по t0}. → ({монета: ключ первого принятого сигнала в блоке},
    {монета: состояние после прохода})."""
    viol, trial = {}, {}
    for sym, acc in accepted.items():
        st = copy.deepcopy(states.get(sym) or _new_state())
        for a in acc:
            _flush(st, a["t0"], n, block_ns)
            if st["block_start"] < a["t0"] <= st["block_until"]:
                viol[sym] = a["key"]
                break
            if a["close"]:
                st["pending"].append(a["close"])
        trial[sym] = st
    return viol, trial


KEYCOLS = ("symbol", "day_utc", "form", "signal_index")


def s_block_keep(day_dirs_chrono, n=S_STREAK, block_ns=S_BLOCK_NS):
    """Г-120: сигналы B1 минус заблокированные. Дни — хронологически (дома aug → hist → rec), состояние монеты
    переносится между сутками. Блок — сигнал отброшен ДО автомата (как фильтр `--keep`), автомат занятости — штатный
    `busy-replay.replay`; найденный в блоке принятый сигнал снимается и сутки пересчитываются (причинно: раньше —
    не меняется). TRX вне пула. → ([(symbol, t0_ns, price_tick, day_utc)], {счётчики})."""
    norm = lambda r: tuple(r[c].strip() for c in KEYCOLS)  # noqa: E731
    states, keep, st_cnt = {}, [], {"signals": 0, "blocked": 0}
    for d in day_dirs_chrono:
        sp, rp = os.path.join(d, "signals.csv"), os.path.join(d, "rounds.csv")
        if not os.path.exists(sp):
            continue
        _, body = br.read_body(sp)
        rows = [r for r in csv.DictReader(body) if r["symbol"] not in EXCLUDED]
        rounds = {}
        if os.path.exists(rp):
            for r in csv.DictReader(br.read_body(rp)[1]):
                rounds[(r["symbol"], r["day_utc"], r["form"], r["signal_index"])] = (int(r["exit_ns"]), float(r["net_bps"]) < 0)
        blocked = set()
        while True:
            allowed = {norm(r) for r in rows if norm(r) not in blocked}
            kept, _ = br.replay(sp, (KEYCOLS, allowed, norm))
            acc = {}
            for r in rows:
                k = norm(r)
                if k in kept:
                    acc.setdefault(r["symbol"], []).append({"key": k, "t0": int(r["t0_ns"]), "close": rounds.get(k)})
            for v in acc.values():
                v.sort(key=lambda a: a["t0"])
            viol, trial = s_day_pass(states, acc, n, block_ns)
            if not viol:
                states.update(trial)
                break
            blocked |= set(viol.values())
        st_cnt["signals"] += len(rows)
        st_cnt["blocked"] += len(blocked)
        keep += [(r["symbol"], r["t0_ns"], r["price_tick"], r["day_utc"]) for r in rows if norm(r) not in blocked]
    st_cnt["block_events"] = sum(s["blocks"] for s in states.values())
    return keep, st_cnt


def chrono_day_dirs():
    """Дни B1 окна авг 01–31 + сен 01–23 хронологически (те же дома и сутки, что у охвата)."""
    out = []
    for m in ("aug", "sep"):
        for home, days in month_homes(m):
            out += day_dirs(f"{home}/b5/{B1_TREE}", days)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["feats", "s-keep"])
    ap.add_argument("--month", choices=["aug", "sep"])
    ap.add_argument("--out")
    ap.add_argument("--block-n", type=int, default=S_STREAK)
    a = ap.parse_args()
    if a.cmd == "feats":
        if not (a.month and a.out):
            sys.exit("нужны --month и --out")
        cmd_feats(a.month, a.out)
    else:
        keep, cnt = s_block_keep(chrono_day_dirs(), a.block_n)
        p07.write_keep(sorted({k[:3] for k in keep}), a.out)
        print(f"Г-120: сигналов {cnt['signals']}, отброшено блоком {cnt['blocked']}, блоков {cnt['block_events']} → {a.out}")


if __name__ == "__main__":
    main()
