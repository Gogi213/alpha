#!/usr/bin/env python3
"""TK-493 S3 + w5 F (П-37 r3.2 §8-§11, §15; решения CEO D5, D8, D10, D11, D15, D16, аудиты M3-01..M3-04, L3-01, L4-01, L6-01..L6-04): агрегатор выходов `statarb run` F8
-> суточные ряды клеток, статусы (через cell_status.classify), срезы, контроли, чувствительность. Только stdlib (интервалы - portfolio через cell_status;
`jumps` и M_эфф берут numpy - Deck, ~/xdl9/venv).

Входы (каталог выходов RUN, подкаталоги стадий): w1 w2 w3 (основной счёт), w4 (контроль §11.8), pl (плацебо-2), rp (случайные пары), sm (min_obs 0,9 W), n17 (без 17 контрактов
Binance TradFi), p1 (+1 мин на обеих ногах, D11), re (случайные входы - f8_randentry.py, колонки как у jobday без active), act/<семейство> (активные пары-сутки D2 по панелям,
f8_act.py: main pl rp n17). В каждом: `*.jobday.csv` (--job-day-out), `*.cells_out.csv`, у основного счёта ещё `*.trades.csv` (--trades-ext), `*.ext.csv`, `*.jobext.csv`
(--job-ext-out: поштучные счётчики и MTM по заданию). Имя файла `<кл>__<биржа Y>__<биржа X>[__shardN]` задаёт биржи ног.

Метрика (§10.1, D10): суточное значение клетки = Σ net закрытых в сутки сделок / число активных пар-суток; сутки без активных пар значения не дают (пропуск, не ноль).
ОСНОВНОЙ ряд клетки = закрытые сделки + MTM позиций, открытых на конце окна, на ПОСЛЕДНИХ сутках окна (cell_status.build_series); закрытый ряд печатается рядом.
Знаменатель D2 - активные пары-сутки ПО ПАНЕЛЯМ (act/main; z ядра для D2 не показатель). «Сироты» (сделки в (задание, сутки) без активности) считаются по (задание, сутки).
«Без лучшей монеты» (в): база с наибольшей суммой (закрытые + MTM) по заданиям клетки; кандидаты на удаление - база ноги Y (A2: якорь BTC не кандидат; B1: только цель);
A1 - обе ноги; удаляются задания с ней. Статус клеток НЕ присваивается вручную: `cells` вызывает cell_status.classify и пишет cell_status.csv.

  python f8_agg.py cells    --run RUN --out OUT --contracts contracts.csv [--cells-params P-37-params-cells.csv] [--d2-pairs d2_pairs.csv] [--d0 2479 --d1 2500]
         (--contracts ОБЯЗАТЕЛЕН: без него условие (в) молча выполнялось бы всегда, аудит M3-02; файл должен существовать и не быть пустым, у каждого задания - известная база)
  python f8_agg.py slices   --run RUN --defs DEFS --out OUT --contracts contracts.csv [--inst-dir DIR --params P-37-params.csv --flags pair_flags.csv]
  python f8_agg.py controls --run RUN --out OUT                 (pl, rp, sm, n17, p1, re против основного счёта; разность «клетка - случайные входы» по суткам)
  python f8_agg.py jumps    --run RUN --contracts C --panels DIR --out OUT   (numpy; скачки > 40 % за минуту на ногах TradFi, D15; читает панели окна - только F8)
  python f8_agg.py breakflags_all --groups f8/flags.tsv --d0 2479 --d1 2500 --out out/flags/pair_flags.csv   (numpy; стадия flags списка единиц F8)
  python f8_agg.py breakflags --pairs jobs.csv --panels-y DIR --panels-x DIR --d0 2479 --d1 2500 --out flags.csv   (numpy; читает торговое окно - только F8)
  python f8_agg.py selftest
"""
import csv, glob, math, os, re, statistics, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cell_status as CS  # noqa: E402

P = CS._portfolio()
ALPHA = CS.ALPHA
MIN_DAY = 1440
D0, D1 = 2479, 2500
TAG = "@p1"
MAIN_STAGES = ("w1", "w2", "w3")
JUMP_THR = 0.40            # D15: минутный скачок цены > 40 % (назначено CEO без Судьи, подтвердить)
BIG_TRADE_BPS = 1000.0     # D15: сделки с |P&L| > 1000 bps и чувствительность «P&L сделки обрезан ±1000 bps»


def safe(sym):
    """Имя панели .sap: как sel_lib.safe / asset_lib.safe_name (без numpy)."""
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in sym)


def unshift(name):
    return "+".join(t[:-len(TAG)] if t.endswith(TAG) else t for t in name.split("+"))


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return float("nan")


def isnum(x):
    return x is not None and x == x and abs(x) != float("inf")


def kind_of(cell):
    return cell.split("|")[0]


def exch_of(path):
    parts = os.path.basename(path).split(".")[0].split("__")
    return parts[1], parts[2]


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def interval(vals):
    """(среднее, нижняя, верхняя, p, df) - 90 % t по блокам суток (portfolio.block_t_interval); меньше 3 значений - интервала нет."""
    vals = list(vals)
    if len(vals) >= 3:
        m, lo, hi, p, df = P.block_t_interval(vals, ALPHA)
        return m, lo, hi, p, df
    return (vals[0] if vals else float("nan")), float("nan"), float("nan"), float("nan"), float("nan")


# ------------------------------------------------------------------ метаданные контрактов
class Meta:
    """contracts.csv (exchange, symbol, asset_id, slug): база актива и полоса. strict (по умолчанию): файл обязан существовать и быть непустым (аудит M3-02)."""

    def __init__(self, contracts_csv=None, strict=True):
        self.asset, self.slug = {}, {}
        if contracts_csv and os.path.exists(contracts_csv):
            with open(contracts_csv, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    k = (r["exchange"], safe(r["symbol"]))
                    self.asset[k] = r["asset_id"]
                    self.slug[k] = r["slug"]
        if strict and not self.asset:
            raise SystemExit("ОТКАЗ: нет файла контрактов или он пуст (%r): без него «без лучшей монеты» и срезы H1-H7 молча становятся пустыми (аудит M3-02)" % (contracts_csv,))

    def base(self, ex, stem):
        return self.asset.get((ex, stem), "?|?").split("|")[0]

    def slug_of(self, ex, stem):
        return self.slug.get((ex, stem), "?")

    def unknown_jobs(self, jmeta, keys):
        bad = []
        for k in keys:
            exy, exx = jmeta[k]
            if self.base(exy, k[1]) == "?" or any(self.base(exx, t) == "?" for t in k[2].split("+")):
                bad.append(k)
        return bad


def candidate_bases(meta, jmeta, k):
    """Базы задания - кандидаты на удаление (M3-03): A1 - обе ноги (каждая - монета пары); A2, B1, C1, D2 - только нога Y (якорь BTC у A2 и компоненты корзины B1 - не кандидаты)."""
    exy, exx = jmeta[k]
    by = meta.base(exy, k[1])
    if kind_of(k[0]) == "A1":
        return {by} | {meta.base(exx, t) for t in k[2].split("+")}
    return {by}


def job_bases(meta, jmeta, k):
    """Все базы задания (обе ноги, все компоненты) - для срезов H1-H7 (по любому участию актива)."""
    exy, exx = jmeta[k]
    return {meta.base(exy, k[1])} | {meta.base(exx, t) for t in k[2].split("+")}


# ------------------------------------------------------------------ загрузка прогона
class Run:
    """Выходы стадий одного прогона: суточные строки заданий, сделки, счётчики клетки и задания."""

    def __init__(self, run, stages, d0=D0, d1=D1, want=("jobday", "cells_out"), act=None):
        self.run, self.stages, self.d0, self.d1 = run, tuple(stages), d0, d1
        self.jobs, self.jmeta = {}, {}                       # {(cell,y,x): {day: [net,gross,fee,funding,trades,active]}}, {(cell,y,x): (exy, exx)}
        self.cout = defaultdict(lambda: defaultdict(float))  # {cell: {колонка: Σ}}
        self.cout_nan = defaultdict(set)                     # {cell: колонки, где встретилось NaN}
        self.jext = {}                                       # {(cell,y,x): {колонка: число|строка}}
        self.trades = []
        self.files = defaultdict(int)
        self.act_missing = set()
        if "jobday" in want:
            self._load_jobday()
        if "cells_out" in want:
            self._load_cells_out()
        if "jobext" in want:
            self._load_jobext()
        if "trades" in want:
            self._load_trades()
        if act is not None:
            self.apply_act(act)

    def _files(self, suffix):
        for st in self.stages:
            for fp in sorted(glob.glob(os.path.join(self.run, st, "*." + suffix + ".csv"))):
                self.files[suffix] += 1
                yield fp

    def _load_jobday(self):
        for fp in self._files("jobday"):
            exy, exx = exch_of(fp)
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    d = int(r["day"])
                    if not self.d0 <= d < self.d1:
                        continue
                    k = (r["cell"], unshift(r["y"]), unshift(r["x"]))
                    self.jmeta[k] = (exy, exx)
                    v = self.jobs.setdefault(k, {}).setdefault(d, [0.0, 0.0, 0.0, 0.0, 0, 0])
                    v[0] += float(r["net_bps"]); v[1] += float(r["gross_bps"]); v[2] += float(r["fee_bps"]); v[3] += float(r["funding_bps"])
                    v[4] += int(float(r["trades"]))
                    v[5] += int(float(r["active"])) if r.get("active", "") != "" else 0

    def _load_cells_out(self):
        for fp in self._files("cells_out"):
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    for k, v in r.items():
                        if k == "cell":
                            continue
                        f = fnum(v)
                        if f == f:
                            self.cout[r["cell"]][k] += f
                        else:
                            self.cout_nan[r["cell"]].add(k)

    def _load_jobext(self):
        for fp in self._files("jobext"):
            exy, exx = exch_of(fp)
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    k = (r["cell"], unshift(r["y"]), unshift(r["x"]))
                    self.jmeta.setdefault(k, (exy, exx))
                    row = {c: (fnum(v) if c not in ("cell", "y", "x", "status") else v) for c, v in r.items()}
                    self.jext[k] = row

    def _load_trades(self):
        for fp in self._files("trades"):
            exy, exx = exch_of(fp)
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    self.trades.append((r["cell"], unshift(r["y"]), unshift(r["x"]), exy, exx, int(r["fill_in"]), int(r["fill_out"]),
                                        float(r["net_bps"]), float(r["gross_bps"]), float(r["fee_bps"]), float(r["funding_bps"])))

    # ---- D2: активность по панелям (D5 iii)
    def apply_act(self, act):
        """act = {(cell,y,x): ({day: 0/1}, (exy, exx))}. У D2 колонка active = активность по панелям; пары D2 без записи в act получают active = 0 (act_missing)."""
        days = range(self.d0, self.d1)
        for k, (per_day, ex) in act.items():
            self.jmeta.setdefault(k, ex)
            row = self.jobs.setdefault(k, {})
            for d in days:
                v = row.setdefault(d, [0.0, 0.0, 0.0, 0.0, 0, 0])
                v[5] = int(per_day.get(d, 0))
        for k in list(self.jobs):
            if kind_of(k[0]) == "D2" and k not in act:
                self.act_missing.add(k)
                for v in self.jobs[k].values():
                    v[5] = 0

    # ---- доступ
    def cells(self):
        s = {k[0] for k in self.jobs} | {k[0] for k in self.jext} | set(self.cout)
        return sorted(s)

    def keys_of(self, cell):
        return sorted(k for k in set(self.jobs) | set(self.jext) if k[0] == cell)

    def job_total(self, k, days):
        net = sum(self.jobs.get(k, {}).get(d, [0.0])[0] for d in days)
        return net + self.job_mtm(k)[0]

    def job_mtm(self, k):
        """(mtm_net_bps, open_at_end) задания; (nan, 0), если MTM не считался."""
        e = self.jext.get(k)
        if not e:
            return float("nan"), 0
        m = e.get("mtm_net_bps", float("nan"))
        return (m if m == m else float("nan")), int(e.get("open_at_end", 0) or 0)

    def mtm_of(self, cell, keys):
        """(Σ mtm_net_bps, Σ open_at_end) по заданиям keys: из jobext, а для целой клетки без jobext - из cells_out; None, если MTM не посчитан."""
        if self.jext:
            tot, nopen = 0.0, 0
            for k in keys:
                if k not in self.jext:
                    continue                      # задание без строки jobext: ядро его не запускало (нет вклада)
                m, o = self.job_mtm(k)
                if not isnum(m):
                    return None, 0
                tot += m
                nopen += o
            return tot, nopen
        c = self.cout.get(cell, {})
        if cell in self.cout_nan and "mtm_net_bps" in self.cout_nan[cell]:
            return None, 0
        if "mtm_net_bps" not in c:
            return None, 0
        return c["mtm_net_bps"], int(c.get("open_at_end", 0))

    def day_dicts(self, keys, days):
        dn, da = {}, {}
        for d in days:
            n = a = 0
            net = 0.0
            for k in keys:
                v = self.jobs.get(k, {}).get(d)
                if v:
                    net += v[0]
                    a += v[5]
            dn[d], da[d] = net, a
        return dn, da

    def series(self, cell, keys, days, control=None):
        """cell_status.build_series по заданиям keys (закрытые + MTM)."""
        if not list(days):                                       # срез суток вне окна прогона (например, торговые недели на формировании): пустой ряд
            return dict(days=[], closed=[], primary=None, primary_days=None, control=None, control_closed=None, mtm_available=False, mtm_value=0.0,
                        mtm_denominator=None, mtm_day=None, mtm_day_added=False)
        dn, da = self.day_dicts(keys, days)
        m, o = self.mtm_of(cell, keys)
        try:
            return CS.build_series(dn, da, list(days), mtm_net_sum=m, n_open=o, control_net=control)
        except ValueError as e:                                   # MTM не нулевой, а активных пар/открытых позиций нет: MTM недоступен
            s = CS.build_series(dn, da, list(days), mtm_net_sum=None, n_open=0, control_net=control)
            s["mtm_error"] = str(e)
            return s

    def sums(self, keys, days):
        """Суммы по заданиям и суткам: fee, funding, gross, net, сделки, активные пары-сутки."""
        a = dict(net=0.0, gross=0.0, fee=0.0, funding=0.0, trades=0, active=0)
        for k in keys:
            for d in days:
                v = self.jobs.get(k, {}).get(d)
                if v:
                    a["net"] += v[0]; a["gross"] += v[1]; a["fee"] += v[2]; a["funding"] += v[3]; a["trades"] += v[4]; a["active"] += v[5]
        return a

    def orphans(self, keys, days):
        """Сделки в (задание, сутки) без активности (D5 iii, M3-01): число и Σ net."""
        n, net = 0, 0.0
        for k in keys:
            for d in days:
                v = self.jobs.get(k, {}).get(d)
                if v and v[4] and v[5] == 0:
                    n += v[4]
                    net += v[0]
        return n, net


def load_act(run, family):
    """act/<семейство>/*.act.csv -> {(cell,y,x): ({day: active}, (exy, exx))} или None, если каталога нет."""
    d = os.path.join(run, "act", family)
    if not os.path.isdir(d):
        return None
    out = {}
    for fp in sorted(glob.glob(os.path.join(d, "*.act.csv"))):
        exy, exx = exch_of(fp)
        with open(fp, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                k = (r["cell"], unshift(r["y"]), unshift(r["x"]))
                out.setdefault(k, ({}, (exy, exx)))[0][int(r["day"])] = int(r["active"])
    return out


def load_re(run, d0, d1):
    """Случайные входы: `re/*.jobday.csv` без active -> {(cell,y,x): {day: net}}"""
    out = defaultdict(lambda: defaultdict(float))
    fps = sorted(glob.glob(os.path.join(run, "re", "*.jobday.csv")))
    for fp in fps:
        with open(fp, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                d = int(r["day"])
                if d0 <= d < d1:
                    out[(r["cell"], unshift(r["y"]), unshift(r["x"]))][d] += float(r["net_bps"])
    return (out if fps else None), len(fps)


def load_sigma(cells_params):
    """P-37-params-cells: `sigma_formation_bps_day_per_active_pair` вида `a0=182.7748;a1=139.1782` / `a0=не измерена` -> {(базовая клетка, рука): σ|None}."""
    out = {}
    if not cells_params or not os.path.exists(cells_params):
        return out
    with open(cells_params, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            for part in r.get("sigma_formation_bps_day_per_active_pair", "").split(";"):
                if "=" in part:
                    h, v = part.split("=", 1)
                    f = fnum(v)
                    out[(r["cell"], h.strip())] = f if f == f else None
    return out


def sigma_for(sigma, cell):
    """σ формирования клетки: строка params-cells по `вид|биржи|полоса[|wNN]`, рука a0/a1; для C1 при отсутствии точного имени - без окна."""
    base, hand = cell.rsplit("|", 1)
    if (base, hand) in sigma:
        return sigma[(base, hand)]
    if cell.startswith("C1|") and "|w" in base:
        return sigma.get((base.rsplit("|w", 1)[0], hand))
    return None


def grid_kinds(d2_pairs):
    """D5 (i), L3-01: вид сетки выплат пары D2 из d2_pairs.csv отбора (sel_d2.grid_kind: «равные» / «Y реже» / «сдвиг»). -> {(клетка|a0, y, x): равные | y_rarer | shift}"""
    names = {"равные": "equal", "Y реже": "y_rarer", "сдвиг": "shift"}
    out = {}
    if not d2_pairs or not os.path.exists(d2_pairs):
        return out
    with open(d2_pairs, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out[(r["cell"] + "|a0", r["y"], r["x"])] = names.get(r["grid_kind"], "unknown")
    return out


# ------------------------------------------------------------------ лучшая база (в)
def drop_best_base(run, keys, days, meta):
    """«Без лучшей монеты» (§10.4 в, M3-03): база с наибольшей суммой (закрытые + MTM) по заданиям; кандидаты - candidate_bases; удаляются задания с ней.
    -> (база, оставшиеся ключи, доля удалённых заданий)."""
    tot = defaultdict(float)
    cand = {}
    for k in keys:
        cand[k] = candidate_bases(meta, run.jmeta, k)
        t = run.job_total(k, days)
        t = t if isnum(t) else sum(run.jobs.get(k, {}).get(d, [0.0])[0] for d in days)
        for b in cand[k]:
            tot[b] += t
    if not tot:
        return None, list(keys), 0.0
    best = max(sorted(tot), key=lambda b: tot[b])
    rest = [k for k in keys if best not in cand[k]]
    return best, rest, (1.0 - len(rest) / len(keys)) if keys else 0.0


def top_share(nets, frac=0.01):
    """Доля суммарного net в топ-1 % сделок (по убыванию net); None, если сумма <= 0 или сделок нет."""
    nets = sorted(nets, reverse=True)
    if not nets:
        return None
    tot = sum(nets)
    if tot <= 0:
        return None
    k = max(1, math.ceil(frac * len(nets)))
    return sum(nets[:k]) / tot


def p5(nets):
    nets = sorted(nets)
    return nets[max(0, math.ceil(0.05 * len(nets)) - 1)] if nets else float("nan")


def mean_or_none(series):
    return (sum(series) / len(series)) if series else None


# ------------------------------------------------------------------ cells
WEEKS = (("W1", range(2479, 2486)), ("W2", range(2486, 2493)), ("W3", range(2493, 2500)))
NONE_SAFE = lambda x: "" if x is None else x          # noqa: E731


def clipped_series(run, cell, keys, days, trades_cell, thr=BIG_TRADE_BPS):
    """Чувствительность D15: суточные значения закрытых сделок с net, обрезанным до ±thr, + MTM без обрезки (MTM - оценка позиции, сделки в ней нет)."""
    clip_adj = defaultdict(float)
    for t in trades_cell:
        d = t[6] // MIN_DAY
        v = t[7]
        c = max(-thr, min(thr, v))
        clip_adj[d] += c - v
    dn, da = run.day_dicts(keys, days)
    dn = {d: dn[d] + clip_adj.get(d, 0.0) for d in days}
    m, o = run.mtm_of(cell, keys)
    try:
        s = CS.build_series(dn, da, list(days), mtm_net_sum=m, n_open=o)
    except ValueError:
        s = CS.build_series(dn, da, list(days), mtm_net_sum=None, n_open=0)
    return (s["primary"] if s["mtm_available"] else s["closed"])


def consistency_checks(main, d0, d1):
    """Сверки выходов (ошибка сверки - код возврата 3): Σ сделок jobday = сделкам ядра в окне (jobext: trades - pre_window = jobday); Σ MTM jobext = cells_out; число trades.csv = jobday."""
    rows = []
    for cell in main.cells():
        keys = main.keys_of(cell)
        jd_tr = sum(v[4] for k in keys for dd, v in main.jobs.get(k, {}).items())
        ok_t = ok_m = True
        note = ""
        if main.jext:
            je_tr = sum((main.jext[k]["trades"] - main.jext[k]["pre_window"]) for k in keys if k in main.jext)
            ok_t = abs(je_tr - jd_tr) < 0.5
            if not ok_t:
                note += "jobext trades-pre_window=%d, jobday=%d; " % (je_tr, jd_tr)
            jm = sum(main.jext[k]["mtm_net_bps"] for k in keys if k in main.jext and isnum(main.jext[k]["mtm_net_bps"]))
            cm = main.cout.get(cell, {}).get("mtm_net_bps")
            if cm is not None:
                ok_m = abs(jm - cm) <= 1e-6 * max(1.0, abs(cm))
                if not ok_m:
                    note += "Σ mtm jobext=%r, cells_out=%r; " % (jm, cm)
        tr_n = sum(1 for t in main.trades if t[0] == cell) if main.trades else None
        ok_r = True
        if tr_n is not None and main.jext:
            pre = sum(main.jext[k]["pre_window"] for k in keys if k in main.jext)
            ok_r = abs(tr_n - jd_tr - pre) < 0.5 or tr_n == jd_tr
            if not ok_r:
                note += "trades.csv=%d, jobday=%d, pre_window=%d; " % (tr_n, jd_tr, pre)
        rows.append([cell, jd_tr, int(ok_t), int(ok_m), int(ok_r), note.strip()])
    return rows


def cmd_cells(a):
    d0, d1 = a["d0"], a["d1"]
    days = list(range(d0, d1))
    if not a.get("contracts"):
        raise SystemExit("ОТКАЗ: --contracts обязателен для cells (аудит M3-02)")
    meta = Meta(a["contracts"], strict=True)
    act_main = load_act(a["run"], "main")
    main = Run(a["run"], MAIN_STAGES, d0, d1, want=("jobday", "cells_out", "jobext", "trades"), act=act_main)
    if not main.jobs:
        raise SystemExit("ОТКАЗ: в %s нет суточных выходов основного счёта (%s)" % (a["run"], ",".join(MAIN_STAGES)))
    bad = meta.unknown_jobs(main.jmeta, [k for k in main.jobs])
    if bad:
        raise SystemExit("ОТКАЗ: у %d заданий нет известной базы в contracts.csv (первое: %r) - «без лучшей монеты» было бы неверным" % (len(bad), bad[0]))
    p1 = Run(a["run"], ("p1",), d0, d1, act=act_main) if os.path.isdir(os.path.join(a["run"], "p1")) else None
    re_net, n_re = load_re(a["run"], d0, d1)
    sigma = load_sigma(a.get("cells_params"))
    gk = grid_kinds(a.get("d2_pairs"))
    os.makedirs(a["out"], exist_ok=True)
    stale = os.path.join(a["out"], "d2_act_missing.csv")
    if os.path.exists(stale):
        os.remove(stale)                                                 # файл остаётся только если пары D2 без активности были и в этом прогоне
    day_rows, sum_rows, st_rows, series_by_cell = [], [], [], {}
    trades_by_cell = defaultdict(list)
    for t in main.trades:
        trades_by_cell[t[0]].append(t)
    big_rows = []
    for cell in main.cells():
        keys = main.keys_of(cell)
        ctrl = None
        if re_net is not None:
            ctrl = defaultdict(float)
            for k in keys:
                for d, v in re_net.get((cell, k[1], k[2]), {}).items():
                    ctrl[d] += v
        s = main.series(cell, keys, days, control=ctrl)
        use_p = s["mtm_available"]
        cell_ser, ctrl_ser = (s["primary"], s["control"]) if use_p else (s["closed"], s["control_closed"])
        series_by_cell[cell] = (s["primary_days"] if use_p else s["days"], cell_ser)
        sm = main.sums(keys, days)
        tr = trades_by_cell.get(cell, [])
        nets = [t[7] for t in tr]
        jx = [main.jext[k] for k in keys if k in main.jext]
        pre_w = sum(e["pre_window"] for e in jx)
        pre_net = sum(e["pre_window_net_bps"] for e in jx)
        warm = sum(e.get("warmup_blocked", 0.0) for e in jx)
        open_pre = sum(e["open_pre_window"] for e in jx)
        is_d2 = kind_of(cell) == "D2"
        # лучшая база (в)
        best, rest, removed = drop_best_base(main, keys, days, meta)
        sw = main.series(cell, rest, days) if rest else None
        wser = (sw["primary"] if sw["mtm_available"] else sw["closed"]) if sw else []
        mean_wo = mean_or_none(wser)
        # +1 мин (ж)
        net_p1 = None
        if p1 is not None and any(k[0] == cell for k in p1.jobs):
            sp = p1.series(cell, p1.keys_of(cell), days)
            if sp["mtm_available"] and sp["primary"]:
                net_p1 = mean_or_none(sp["primary"])
        durs = [t[6] - t[5] for t in tr]
        share_short = CS.short_trade_share(durs)
        exs = sorted({e for k in keys for e in main.jmeta.get(k, ())})
        worst = None
        if tr and use_p:
            wt = min(tr, key=lambda t: t[7])
            dd = wt[6] // MIN_DAY
            if dd in s["primary_days"]:
                dn, da = main.day_dicts(keys, days)
                worst = (s["primary_days"].index(dd), wt[7] / da[dd]) if da.get(dd, 0) > 0 else None
        fee_burden = (sm["fee"] / sm["active"]) if sm["active"] else None
        sig = sigma_for(sigma, cell)
        # D15: сделки с |P&L| > 1000 и обрезка
        big = [t for t in tr if abs(t[7]) > BIG_TRADE_BPS]
        for t in big:
            big_rows.append([cell, t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], t[9], t[10]])
        clip = clipped_series(main, cell, keys, days, tr)
        cm, cl, ch, _, _ = interval(clip) if clip else (float("nan"),) * 5
        # D5 (i): вид сетки D2 и доля сделок
        gshare = {}
        if is_d2:
            tot_tr = sm["trades"] or 0
            for kind in ("equal", "y_rarer", "shift", "unknown"):
                ks = [k for k in keys if gk.get((k[0], k[1], k[2]), "unknown") == kind]
                gshare[kind] = (main.sums(ks, days)["trades"] / tot_tr) if tot_tr else None
        # статус
        if cell_ser:
            res = CS.classify(cell_ser, ctrl_ser, n_trades=sm["trades"], n_pairs=sum(1 for k in keys if main.sums([k], days)["trades"] > 0), sigma_form=sig,
                              mean_without_best_coin=mean_wo, mtm_ok=use_p, closed_days=s["closed"], net_plus1=net_p1, short_trade_share=share_short, exchanges=exs,
                              worst_trade=worst, fee_burden_bps=fee_burden)
            st_rows.append(CS.status_row(cell, res))
        else:
            res = None
            st_rows.append([cell, CS.UNCLEAR, CS.LABEL_RU[CS.UNCLEAR]] + [""] * (len(CS.STATUS_COLUMNS) - 3))
            st_rows[-1][CS.STATUS_COLUMNS.index("n_days")] = 0
        closed_m = interval(s["closed"])
        prim_m = interval(s["primary"]) if use_p and s["primary"] else (float("nan"),) * 5
        x2 = []
        for d in s["days"]:
            tot_net = sum(main.jobs.get(k, {}).get(d, [0.0])[0] for k in keys)
            tot_fee = sum(main.jobs.get(k, {}).get(d, [0, 0, 0.0])[2] for k in keys)
            act = sum(main.jobs.get(k, {}).get(d, [0, 0, 0, 0, 0, 0])[5] for k in keys)
            x2.append((tot_net - tot_fee) / act)
        x2m = interval(x2)
        wk = []
        for nm, rg in WEEKS:
            vals = [v for d, v in zip(s["days"], s["closed"]) if d in rg]
            wk.append(sum(vals) / len(vals) if vals else "")
        e = main.cout.get(cell, {})
        orph_n, orph_net = main.orphans(keys, days)
        days_no_act = sum(1 for d in days if main.day_dicts(keys, [d])[1][d] == 0)
        done = sum(1 for e_ in jx if e_.get("status") == "done"); miss = sum(1 for e_ in jx if e_.get("status") == "missing"); nospan = sum(1 for e_ in jx if e_.get("status") == "no_span")
        evm = sum(e_["eval_minutes"] for e_ in jx)
        zdef = sum(e_["z_def_minutes"] for e_ in jx)
        mip = sum(e_["minutes_in_pos"] for e_ in jx)
        pzn = sum(e_["pos_z_nan_minutes"] for e_ in jx)
        sum_rows.append(dict(
            cell=cell, kind=kind_of(cell), jobs=len(keys), jobs_done=done, jobs_missing=miss, jobs_no_span=nospan, days_with_value=len(s["closed"]), trades=sm["trades"],
            pairs_traded=sum(1 for k in keys if main.sums([k], days)["trades"] > 0), active_pair_days=sm["active"],
            closed_mean=closed_m[0], closed_lo90=closed_m[1], closed_hi90=closed_m[2], closed_p=closed_m[3],
            primary_mean=prim_m[0], primary_lo90_t=prim_m[1], primary_hi90_t=prim_m[2], primary_available=int(use_p),
            net_per_trade_bps=(sm["net"] / sm["trades"]) if sm["trades"] else "", fee_sum_bps=sm["fee"], funding_sum_bps=sm["funding"], gross_sum_bps=sm["gross"],
            mtm_net_sum_bps=NONE_SAFE(main.mtm_of(cell, keys)[0]), open_at_end=e.get("open_at_end", ""), mtm_denominator=NONE_SAFE(s.get("mtm_denominator")),
            mtm_day_added=int(bool(s.get("mtm_day_added"))), mtm_error=s.get("mtm_error", ""),
            pre_window=pre_w, pre_window_net_bps=pre_net, warmup_blocked_minutes=warm, open_pre_window=open_pre,
            entry_deferred=e.get("entry_deferred", ""), exit_deferred=e.get("exit_deferred", ""),
            trades_on_job_days_without_active=orph_n, net_on_job_days_without_active=orph_net, cell_days_without_active_pairs=days_no_act,
            fee_x2_mean=x2m[0], fee_x2_lo90=x2m[1], fee_x2_hi90=x2m[2],
            best_base=best or "", best_base_jobs_removed_share=removed, mean_without_best_base=NONE_SAFE(mean_wo),
            mean_without_best_2_days=NONE_SAFE(CS.mean_without_best_days(cell_ser) if len(cell_ser) > CS.BEST_DAYS else None),
            share_net_top1pct_trades=NONE_SAFE(top_share(nets)), p05_trade_net_bps=(p5(nets) if nets else ""), worst_trade_bps=(min(nets) if nets else ""),
            week1_mean=wk[0], week2_mean=wk[1], week3_mean=wk[2],
            z_defined_share=("" if is_d2 or not evm else zdef / evm), pos_z_nan_share_of_pos_minutes=("" if is_d2 or not mip else pzn / mip),
            net_plus1=NONE_SAFE(net_p1), short_trade_share_le_2min=NONE_SAFE(share_short), open_semantics=CS.open_semantics(exs), exchanges="+".join(exs),
            trades_abs_gt_1000bps=len(big), clip1000_mean=cm, clip1000_lo90=cl, clip1000_hi90=ch,
            d2_grid_equal_trade_share=NONE_SAFE(gshare.get("equal")), d2_grid_y_rarer_trade_share=NONE_SAFE(gshare.get("y_rarer")),
            d2_grid_shift_trade_share=NONE_SAFE(gshare.get("shift")),
            d2_grid_unknown_trade_share=NONE_SAFE(gshare.get("unknown")), control_re=int(ctrl is not None)))
        # суточные строки
        closed_by_day = dict(zip(s["days"], s["closed"]))
        prim_by_day = dict(zip(s["primary_days"], s["primary"])) if use_p else {}
        ctrl_by_day = dict(zip(s["primary_days"], s["control"])) if (use_p and s["control"] is not None) else {}
        dn, da = main.day_dicts(keys, days)
        for d in days:
            sd = main.sums(keys, [d])
            day_rows.append([cell, d, sd["net"], sd["gross"], sd["fee"], sd["funding"], sd["trades"], sd["active"], closed_by_day.get(d, ""), prim_by_day.get(d, ""), ctrl_by_day.get(d, "")])
    hdr_d = ["cell", "day", "net_bps", "gross_bps", "fee_bps", "funding_bps", "trades", "active_pairs", "closed_value_bps_per_active_pair", "primary_value_closed_plus_mtm", "random_entries_value"]
    write_csv(os.path.join(a["out"], "cell_days.csv"), hdr_d, day_rows)
    hdr_s = list(sum_rows[0].keys()) if sum_rows else []
    write_csv(os.path.join(a["out"], "cell_summary.csv"), hdr_s, [[r[h] for h in hdr_s] for r in sum_rows])
    write_csv(os.path.join(a["out"], "cell_status.csv"), CS.STATUS_COLUMNS, st_rows)
    write_csv(os.path.join(a["out"], "big_trades.csv"), ["cell", "y", "x", "ex_y", "ex_x", "fill_in", "fill_out", "net_bps", "gross_bps", "fee_bps", "funding_bps"], big_rows)
    # M, M_эфф, диапазон ожидаемого числа ложных (D9)
    msum = m_summary(series_by_cell, main, days)
    write_csv(os.path.join(a["out"], "m_summary.csv"), ["field", "value"], list(msum.items()))
    chk = consistency_checks(main, d0, d1)
    write_csv(os.path.join(a["out"], "consistency.csv"), ["cell", "trades_jobday", "ok_trades_jobext", "ok_mtm_cells_out", "ok_trades_csv", "note"], chk)
    bad_chk = [r for r in chk if not (r[2] and r[3] and r[4])]
    if main.act_missing:
        write_csv(os.path.join(a["out"], "d2_act_missing.csv"), ["cell", "y", "x"], sorted(main.act_missing))
    print("cells: клеток %d, строк суток %d, сделок %d, p1 %s, re %s -> %s" % (len(sum_rows), len(day_rows), len(main.trades), "есть" if p1 else "нет", ("%d файлов" % n_re) if re_net is not None else "нет", a["out"]))
    if main.act_missing:
        print("ВНИМАНИЕ: у %d пар D2 нет активности по панелям (act/main): active = 0" % len(main.act_missing))
    if bad_chk:
        print("ОШИБКА СВЕРКИ: %d клеток не сошлись (consistency.csv)" % len(bad_chk))
        return 3
    return 0


def m_summary(series_by_cell, main, days):
    """M (торгуемых клеток-рук), M без тождественных рядов, M_эфф по корреляциям суточных рядов, диапазон ожидаемого числа ложных (D9)."""
    ser = {c: v[1] for c, v in series_by_cell.items() if v[1]}
    out = {"M_cells_with_series": len(ser), "M_distinct": CS.m_distinct(ser) if ser else 0}
    m_eff = float("nan")
    try:
        import numpy as np
        dayset = sorted({d for v in series_by_cell.values() for d in v[0]})
        if len(ser) >= 2 and len(dayset) >= 3:
            mat = np.full((len(dayset), len(ser)), np.nan)
            pos = {d: i for i, d in enumerate(dayset)}
            for j, c in enumerate(sorted(ser)):
                for d, v in zip(series_by_cell[c][0], ser[c]):
                    mat[pos[d], j] = v
            col_ok = np.isfinite(mat).all(axis=0)
            m_eff, used = CS.m_eff(mat[:, col_ok]) if col_ok.sum() >= 2 else (float("nan"), int(col_ok.sum()))
            out["M_eff_columns_used"] = used
    except ImportError:
        out["M_eff_note"] = "numpy недоступен"
    out["M_eff_daily_series"] = m_eff
    if out["M_distinct"] and m_eff == m_eff:
        lo, hi = CS.expected_false_range(out["M_distinct"], m_eff)
        out["expected_false_candidates_range_low"] = lo
        out["expected_false_candidates_range_high"] = hi
    out["note"] = "M_eff по суточным рядам клеток с полным рядом (часовые ряды не строились); оценка занижена при числе суток < числа клеток (cell_status.m_eff); ориентир, не оценка"
    return out


# ------------------------------------------------------------------ срезы
def read_defs(defs, name):
    p = os.path.join(defs, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def parse_days(spec):
    out = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def cell_parts(cell):
    p = cell.split("|")
    return dict(kind=p[0], exs=p[1].split("~"), slug=p[2], hand=p[-1])


def parse_windows(params_csv):
    """exclusion_windows из params: `биржа:2026-10-01T07:01Z+21m|...;биржа2:...` -> {биржа: [(начало_мин, длина)]} (минуты от 2020-01-01)."""
    import datetime
    out = {}
    with open(params_csv, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["key"] == "exclusion_windows":
                for blk in r["value"].split(";"):
                    ex, lst = blk.split(":", 1)
                    for w in lst.split("|"):
                        m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)Z\+(\d+)m", w)
                        t = datetime.datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), tzinfo=datetime.timezone.utc)
                        out.setdefault(ex, []).append((int((t - datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)).total_seconds() // 60), int(m[6])))
    return out


def session_class(fi, fo):
    """Касса США (П-37 §9): будни 13:30-20:00 UTC до 31.10 включительно, 14:30-21:00 с 02.11 (переход 01.11, сутки 2496). Класс сделки по минутам fill_in..fill_out:
    'weekend' - интервал содержит субботу или воскресенье; 'inside' - обе точки внутри одной сессии; 'across_close' - иначе (через закрытие в будни)."""
    d_in, d_out = fi // MIN_DAY, fo // MIN_DAY
    for d in range(d_in, d_out + 1):
        if (d + 2) % 7 >= 5:
            return "weekend"

    def sess(d):
        return (13 * 60 + 30, 20 * 60) if d < 2496 else (14 * 60 + 30, 21 * 60)
    if d_in == d_out:
        s0, s1 = sess(d_in)
        if s0 <= fi % MIN_DAY < s1 and s0 <= fo % MIN_DAY < s1:
            return "inside"
    return "across_close"


def load_delisted(inst_dir, d0=D0, d1=D1):
    """{(биржа, символ): delisted_ms} для контрактов, снятых в торговом окне; + счётчики по бирже (все инструменты биржи, D16)."""
    out, per_ex, total_ex, has_flag = {}, defaultdict(int), defaultdict(int), defaultdict(int)
    lo = 1_577_836_800_000 + d0 * MIN_DAY * 60_000
    hi = 1_577_836_800_000 + d1 * MIN_DAY * 60_000
    for fp in glob.glob(os.path.join(inst_dir, "*.csv")):
        with open(fp, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                ex = r.get("exchange", "")
                total_ex[ex] += 1
                v = r.get("delisted_ms", "")
                if v:
                    has_flag[ex] += 1
                    if lo <= int(float(v)) < hi:
                        out[(ex, safe(r["symbol"]))] = int(float(v))
                        per_ex[ex] += 1
    return out, dict(per_ex), dict(total_ex), dict(has_flag)


SLICE_HDR = ["slice", "group", "cell", "jobs", "days_with_value", "trades", "pairs_traded", "active_pair_days", "net_sum_bps", "net_per_trade_bps",
             "closed_mean_bps_day_pair", "closed_lo90", "closed_hi90", "closed_plus_mtm_mean", "mtm_net_sum_bps", "note"]


def jobset_row(slice_id, grp, cell, run, keys, days, note=""):
    """Срез по набору заданий: закрытый ряд и «закрытые + MTM» (по заданию, jobext)."""
    s = run.series(cell, keys, days)
    sm = run.sums(keys, days)
    m = interval(s["closed"])
    mtm, _ = run.mtm_of(cell, keys)
    pm = mean_or_none(s["primary"]) if s["mtm_available"] and s["primary"] else None
    return [slice_id, grp, cell, len(keys), len(s["closed"]), sm["trades"], sum(1 for k in keys if run.sums([k], days)["trades"] > 0), sm["active"], sm["net"],
            (sm["net"] / sm["trades"]) if sm["trades"] else float("nan"), m[0], m[1], m[2], NONE_SAFE(pm), NONE_SAFE(mtm), note]


def days_row(slice_id, grp, cell, run, keys, days, note=""):
    s = run.series(cell, keys, days)
    sm = run.sums(keys, days)
    m = interval(s["closed"])
    return [slice_id, grp, cell, len(keys), len(s["closed"]), sm["trades"], sum(1 for k in keys if run.sums([k], days)["trades"] > 0), sm["active"], sm["net"],
            (sm["net"] / sm["trades"]) if sm["trades"] else float("nan"), m[0], m[1], m[2], "", "", note + " (закрытые; MTM только в весь ряд клетки)"]


def trades_row(slice_id, grp, cell, trs, note=""):
    n = len(trs)
    s = sum(t[7] for t in trs)
    return [slice_id, grp, cell, len({(t[0], t[1], t[2]) for t in trs}), "", n, len({(t[1], t[2]) for t in trs}), "", s, (s / n if n else float("nan")), "", "", "", "", "",
            note + " (по сделкам; суточного ряда нет)"]


def cmd_slices(a):
    d0, d1 = a["d0"], a["d1"]
    days_all = list(range(d0, d1))
    meta = Meta(a.get("contracts"), strict=True)
    main = Run(a["run"], MAIN_STAGES, d0, d1, want=("jobday", "cells_out", "jobext", "trades"), act=load_act(a["run"], "main"))
    cells = main.cells()
    by_cell = {c: main.keys_of(c) for c in cells}
    rows = []
    # 1. сутки
    for r in read_defs(a["defs"], "slices_days.csv"):
        ds = [d for d in parse_days(r["days"]) if d0 <= d < d1]
        for c in cells:
            rows.append(days_row(r["slice"], "days", c, main, by_cell[c], ds, r["description"]))
    # 2. часы входа (по сделкам)
    for r in read_defs(a["defs"], "slices_hours.csv"):
        h0, h1 = int(r["hour_from"]), int(r["hour_to"])
        for c in cells:
            trs = [t for t in main.trades if t[0] == c and h0 <= (t[5] % MIN_DAY) // 60 <= h1]
            rows.append(trades_row(r["slice"], "hours", c, trs, r["description"] + " (по часу UTC fill_in)"))
    # 3. гипотезы H1-H7 (срезы пар по базовым активам); D8: пустая C1 / нет подходящих клеток - «нет данных», диагностика с параметрами чужой клетки не строится
    for r in read_defs(a["defs"], "slices_hyp.csv"):
        if not r["kinds"]:
            rows.append([r["slice"], "hyp", "-", 0, 0, 0, 0, 0, 0.0, float("nan"), float("nan"), float("nan"), float("nan"), "", "", "нет данных: " + r["text"] + "; " + r["availability"]])
            continue
        kinds, expairs, bases = (r[k].split("|") for k in ("kinds", "ex_pairs", "bases"))
        got = 0
        for c in cells:
            cp = cell_parts(c)
            if cp["kind"] not in kinds or "~".join(sorted(cp["exs"])) not in expairs:
                continue
            keys = [k for k in by_cell[c] if job_bases(meta, main.jmeta, k) & set(bases)]
            if not keys:
                continue
            got += 1
            rows.append(jobset_row(r["slice"], "hyp", c, main, keys, days_all, r["text"] + "; ГИПОТЕЗА из материала владельца, результат - верхняя граница (§0.6)"))
        if not got:
            rows.append([r["slice"], "hyp", "-", 0, 0, 0, 0, 0, 0.0, float("nan"), float("nan"), float("nan"), float("nan"), "", "",
                         "нет данных: в торгуемых клетках вида %s нет пар гипотезы (клетка не торгуется или пары вне отобранных); диагностика с параметрами другой клетки не строится (D8). %s" % ("/".join(kinds), r["text"])])
    # 4. сессия США / выходные / закрытие (по сделкам), только полосы с маской США
    for c in cells:
        if cell_parts(c)["slug"] not in ("eq_us", "idx_us"):
            continue
        for cls in ("inside", "across_close", "weekend"):
            trs = [t for t in main.trades if t[0] == c and session_class(t[5], t[6]) == cls]
            rows.append(trades_row("session_" + cls, "session", c, trs, "касса США (§9)"))
    # 5. сделки через окно выпадения биржи (§3, §8)
    if a.get("params"):
        win = parse_windows(a["params"])
        for c in cells:
            exs = [e for e in cell_parts(c)["exs"] if e in win]
            if not exs:
                continue
            trs = [t for t in main.trades if t[0] == c and any(t[5] <= w0 + wl and w0 <= t[6] for e in exs for (w0, wl) in win[e])]
            rows.append(trades_row("through_outage", "outage", c, trs, ",".join(exs)))
    # 6. снятые в торговом окне контракты (D16: со счётом по биржам и MTM) и 17 контрактов Binance TradFi
    if a.get("inst_dir"):
        dl, per_ex, total_ex, has_flag = load_delisted(a["inst_dir"], d0, d1)
        ex_rows = []
        for ex in sorted(total_ex):
            blind = "список снятых не виден по построению (источник не отдаёт снятые)" if ex in ("okx", "bitget", "bingx", "kraken", "lighter") else ""
            ex_rows.append([ex, total_ex[ex], has_flag.get(ex, 0), per_ex.get(ex, 0), blind])
        write_csv(os.path.join(a["out"], "delisted_by_exchange.csv"), ["exchange", "instruments", "with_delisted_ms", "delisted_in_window", "note"], ex_rows)
        for c in cells:
            keys = [k for k in by_cell[c] if (main.jmeta[k][0], k[1]) in dl or any((main.jmeta[k][1], t) in dl for t in k[2].split("+"))]
            if keys:
                rows.append(jobset_row("delisted_in_window", "delisted", c, main, keys, days_all, "контракт снят в торговом окне (instruments.csv); MTM по заданиям - jobext"))
    n17 = {b + "USDT" for b in ("ACN ASML BX CAT COST CSCO DELL EQT GEV JPM KODEX200 MRVL MU NOK ORCL STRC TXN".split())}
    for c in cells:
        keys = [k for k in by_cell[c] if (main.jmeta[k][0] == "binance" and k[1] in n17) or (main.jmeta[k][1] == "binance" and any(t in n17 for t in k[2].split("+")))]
        if keys:
            rows.append(jobset_row("binance_tradfi_17", "n17", c, main, keys, days_all, "с 17 контрактами Binance TradFi (фандинг как отдано биржей, §11.9)"))
    # 7. метки пар (разрыв после формирования, неравные сетки D2)
    if a.get("flags") and os.path.exists(a["flags"]):
        fl = defaultdict(set)
        with open(a["flags"], encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["flag"] == "1":
                    fl[r["name"]].add((r["y"], r["x"]))
        for nm, pairs in sorted(fl.items()):
            for c in cells:
                keys = [k for k in by_cell[c] if (k[1], k[2]) in pairs]
                if keys:
                    rows.append(jobset_row(nm, "flag", c, main, keys, days_all, "метка пары"))
    # 8. D2: вид сетки выплат (L3-01)
    gk = grid_kinds(a.get("d2_pairs"))
    if gk:
        for c in cells:
            if kind_of(c) != "D2":
                continue
            for kind in ("equal", "y_rarer", "shift"):
                keys = [k for k in by_cell[c] if gk.get((k[0], k[1], k[2])) == kind]
                if keys:
                    rows.append(jobset_row("grid_" + kind, "d2_grid", c, main, keys, days_all, "вид сетки выплат пары D2 (d2_pairs.csv отбора)"))
    # 9. TradFi между биржами: (вид x не-крипто класс) по объединению пар слагаемых клеток (§9, Н32); классы между собой не суммируются
    blocks = defaultdict(list)
    for c in cells:
        cp = cell_parts(c)
        if cp["slug"] == "crypto":
            continue
        big = {"eq_us": "акции+ETF", "eq_nonus": "акции+ETF", "idx_us": "индексы", "idx_nonus": "индексы", "metal_energy": "металлы+энергия", "fx": "фиат"}.get(cp["slug"], cp["slug"])
        blocks[(cp["kind"], big, cp["hand"])].extend(by_cell[c])
    for (kind, big, hand), keys in sorted(blocks.items()):
        rows.append(jobset_row("tradfi_%s_%s_%s" % (kind, big, hand), "tradfi", "|".join((kind, big, hand)), main, sorted(keys), days_all,
                               "объединение пар слагаемых клеток, без статуса, вне M (§9)"))
    write_csv(os.path.join(a["out"], "slice_table.csv"), SLICE_HDR, rows)
    print("slices: строк %d -> %s" % (len(rows), a["out"]))
    return rows


# ------------------------------------------------------------------ контроли и чувствительность
CONTROL_HDR = ["cell", "stage", "jobs", "days_with_value", "trades", "closed_mean", "closed_lo90", "closed_hi90", "primary_mean", "primary_lo90_t", "primary_hi90_t",
               "diff_mean", "diff_lo90", "diff_hi90", "note"]


def cmd_controls(a):
    d0, d1 = a["d0"], a["d1"]
    days = list(range(d0, d1))
    act_main = load_act(a["run"], "main")
    main = Run(a["run"], MAIN_STAGES, d0, d1, want=("jobday", "cells_out", "jobext"), act=act_main)
    others = {}
    for st, fam in (("pl", "pl"), ("rp", "rp"), ("sm", None), ("n17", "n17"), ("p1", "main")):
        if os.path.isdir(os.path.join(a["run"], st)):
            others[st] = Run(a["run"], (st,), d0, d1, act=(load_act(a["run"], fam) if fam and fam != "main" else (act_main if fam == "main" else None)))
    re_net, _ = load_re(a["run"], d0, d1)
    rows, cdays = [], []
    for c in main.cells():
        keys = main.keys_of(c)
        ctrl = None
        if re_net is not None:
            ctrl = defaultdict(float)
            for k in keys:
                for d, v in re_net.get((c, k[1], k[2]), {}).items():
                    ctrl[d] += v
        s = main.series(c, keys, days, control=ctrl)
        cm, pm = interval(s["closed"]), (interval(s["primary"]) if s["mtm_available"] and s["primary"] else (float("nan"),) * 5)
        sm = main.sums(keys, days)
        rows.append([c, "main", len(keys), len(s["closed"]), sm["trades"], cm[0], cm[1], cm[2], pm[0], pm[1], pm[2], "", "", "", ""])
        for st, run in others.items():
            ck = run.keys_of(c)
            if not ck:
                continue
            s2 = run.series(c, ck, days)
            c2, p2 = interval(s2["closed"]), (interval(s2["primary"]) if s2["mtm_available"] and s2["primary"] else (float("nan"),) * 5)
            note = ""
            if st == "n17":
                ref = main.sums([k for k in keys if k in set(ck)], days)
                sn = run.sums(ck, days)
                note = "net_sum с17 %.3f / без17 %.3f; funding_sum с17 %.3f / без17 %.3f (те же задания)" % (ref["net"], sn["net"], ref["funding"], sn["funding"])
            if st == "p1":
                note = "задержка +1 мин на обеих ногах (панели open'[i] = open[i+1]); активные пары основного счёта; (ж): точечная оценка 'primary_mean' > 0"
            if run.act_missing:
                note += " [пар D2 без активности по панелям: %d]" % len(run.act_missing)
            rows.append([c, st, len(ck), len(s2["closed"]), run.sums(ck, days)["trades"], c2[0], c2[1], c2[2], p2[0], p2[1], p2[2], "", "", "", note])
        if ctrl is not None and s["mtm_available"] and s["control"] is not None and len(s["primary"]) >= 2:
            dser = [p - q for p, q in zip(s["primary"], s["control"])]
            cmr = interval(s["control"])
            dm = interval(dser)
            rows.append([c, "re", len(keys), len(dser), "", cmr[0], cmr[1], cmr[2], "", "", "", dm[0], dm[1], dm[2],
                         "разность «клетка (закрытые+MTM) - случайные входы» (условие (б) §10.4): нижняя граница %.3f %s 0" % (dm[1], ">" if dm[1] > 0 else "<=")])
            for d, v, q in zip(s["primary_days"], s["primary"], s["control"]):
                cdays.append([c, d, v, q])
    write_csv(os.path.join(a["out"], "controls.csv"), CONTROL_HDR, rows)
    write_csv(os.path.join(a["out"], "control_days.csv"), ["cell", "day", "cell_value", "random_entries_value"], cdays)
    print("controls: строк %d -> %s" % (len(rows), a["out"]))
    return rows


# ------------------------------------------------------------------ скачки цены (D15), разрыв после формирования
def read_sap(path):
    import numpy as np
    with open(path, "rb") as f:
        h = f.read(24)
    if h[:4] != b"SAP1":
        raise ValueError("не SAP1: " + path)
    first_min = int(np.frombuffer(h[8:16], dtype="<i8")[0])
    n = int(np.frombuffer(h[16:24], dtype="<u8")[0])
    a = np.fromfile(path, dtype="<f8", count=2 * n, offset=24)
    return first_min, a[:n], a[n:]


def cmd_jumps(a):
    """D15: число минутных скачков close > JUMP_THR (между соседними конечными минутами подряд) на ногах TradFi заданий основного счёта; список по символам и по биржам."""
    import numpy as np
    d0, d1 = a["d0"], a["d1"]
    meta = Meta(a["contracts"], strict=True)
    main = Run(a["run"], MAIN_STAGES, d0, d1, want=("jobday", "jobext"))
    legs = set()
    for k in set(main.jmeta):
        exy, exx = main.jmeta[k]
        legs.add((exy, k[1]))
        for t in k[2].split("+"):
            legs.add((exx, t))
    rows, by_ex = [], defaultdict(lambda: [0, 0, 0])
    for ex, sym in sorted(legs):
        slug = meta.slug_of(ex, sym)
        if slug in ("crypto", "?"):
            continue
        fp = os.path.join(a["panels"], ex, sym + ".sap")
        if not os.path.exists(fp):
            continue
        fm, op, cl = read_sap(fp)
        lo, hi = max(fm, d0 * MIN_DAY), min(fm + len(cl), d1 * MIN_DAY)
        if hi - lo < 2:
            continue
        c = cl[lo - fm:hi - fm]
        ok = np.isfinite(c[1:]) & np.isfinite(c[:-1])
        r = np.abs(c[1:][ok] / c[:-1][ok] - 1.0)
        nj = int((r > JUMP_THR).sum())
        rows.append([ex, sym, slug, int(ok.sum()), nj, float(r.max()) if len(r) else 0.0])
        by_ex[(ex, slug)][0] += 1
        by_ex[(ex, slug)][1] += int(ok.sum())
        by_ex[(ex, slug)][2] += nj
    write_csv(os.path.join(a["out"], "jumps_by_symbol.csv"), ["exchange", "symbol", "slug", "minute_pairs", "jumps_gt_40pct", "max_abs_return"], rows)
    write_csv(os.path.join(a["out"], "jumps_by_exchange.csv"), ["exchange", "slug", "legs", "minute_pairs", "jumps_gt_40pct"],
              [[ex, sl, v[0], v[1], v[2]] for (ex, sl), v in sorted(by_ex.items())])
    print("jumps: ног TradFi %d, скачков > %d %% всего %d -> %s" % (len(rows), int(JUMP_THR * 100), sum(r[4] for r in rows), a["out"]))


def cmd_breakflags(a):
    """S = σ(ln cY - ln cX) / σ(ln cY) по общим валидным минутам торгового окна (П-37 §4.2, путь К); флаг 1, если S > stab_threshold. Читает торговое окно: только F8."""
    import numpy as np
    thr = float(a["stab_threshold"])
    rows = []
    with open(a["pairs"], encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            fy, fx = os.path.join(a["panels_y"], r["y"] + ".sap"), os.path.join(a["panels_x"], r["x"] + ".sap")
            if not (os.path.exists(fy) and os.path.exists(fx)):
                continue
            my, _, cy = read_sap(fy)
            mx, _, cx = read_sap(fx)
            lo, hi = max(my, mx, a["d0"] * MIN_DAY), min(my + len(cy), mx + len(cx), a["d1"] * MIN_DAY)
            if hi - lo < 100:
                continue
            ly, lx = np.log(cy[lo - my:hi - my]), np.log(cx[lo - mx:hi - mx])
            ok = np.isfinite(ly) & np.isfinite(lx)
            if ok.sum() < 100 or np.std(ly[ok]) == 0:
                continue
            s = float(np.std(ly[ok] - lx[ok]) / np.std(ly[ok]))
            rows.append((r["y"], r["x"], s, 1 if s > thr else 0))
    with open(a["out"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("name,y,x,S,flag\n")
        for y, x, s, f in rows:
            fh.write("break_after_formation,%s,%s,%.6f,%d\n" % (y, x, s, f))
    print("breakflags: пар %d, с флагом %d (порог S %.6f)" % (len(rows), sum(r[3] for r in rows), thr))


def cmd_breakflags_all(a):
    """Метки «разрыв после формирования» по всем группам C1 и D2 (стадия flags списка единиц F8): список групп tsv `stem, kind, panels_y, panels_x, pairs`;
    пары - файл y,x[,from_day,to_day]; выход - один pair_flags.csv (name,y,x,S,flag), который читает `slices --flags`."""
    import numpy as np
    thr = float(a["stab_threshold"])
    rows = []
    with open(a["groups"], encoding="utf-8", newline="") as fh:
        groups = [r for r in csv.reader(fh, delimiter="\t")][1:]
    for stem, kind, py, px, pairs in groups:
        py, px, pairs = (os.path.expandvars(os.path.expanduser(x)) for x in (py, px, pairs))
        if not os.path.exists(pairs):
            continue
        with open(pairs, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                fy, fx = os.path.join(py, r["y"] + ".sap"), os.path.join(px, r["x"] + ".sap")
                if not (os.path.exists(fy) and os.path.exists(fx)):
                    continue
                my, _, cy = read_sap(fy)
                mx, _, cx = read_sap(fx)
                lo, hi = max(my, mx, a["d0"] * MIN_DAY), min(my + len(cy), mx + len(cx), a["d1"] * MIN_DAY)
                if hi - lo < 100:
                    continue
                with np.errstate(all="ignore"):
                    ly, lx = np.log(cy[lo - my:hi - my]), np.log(cx[lo - mx:hi - mx])
                ok = np.isfinite(ly) & np.isfinite(lx)
                if ok.sum() < 100 or np.std(ly[ok]) == 0:
                    continue
                s = float(np.std(ly[ok] - lx[ok]) / np.std(ly[ok]))
                rows.append(("break_after_formation", r["y"], r["x"], s, 1 if s > thr else 0, stem))
    write_csv(a["out"], ["name", "y", "x", "S", "flag", "group"], rows)
    print("breakflags_all: пар %d, с флагом %d (порог S %.6f) -> %s" % (len(rows), sum(r[4] for r in rows), thr, a["out"]))


# ------------------------------------------------------------------ самопроверка
def selftest():
    import tempfile
    d = 2479
    assert session_class(d * 1440 + 14 * 60, d * 1440 + 15 * 60) == "inside"
    assert session_class(d * 1440 + 19 * 60, d * 1440 + 22 * 60) == "across_close"
    assert session_class(2480 * 1440 + 19 * 60, 2481 * 1440 + 60) == "weekend"
    assert session_class(2497 * 1440 + 14 * 60 + 40, 2497 * 1440 + 15 * 60) == "inside" and session_class(2497 * 1440 + 14 * 60, 2497 * 1440 + 15 * 60) == "across_close"
    assert parse_days("2479-2481;2490") == [2479, 2480, 2481, 2490]
    assert unshift("A@p1+B@p1") == "A+B" and unshift("A") == "A"
    assert top_share([100.0] + [1.0] * 99) == 100.0 / 199.0
    # Meta: без файла - отказ (M3-02)
    try:
        Meta(None)
        raise AssertionError("Meta без файла должна отказывать")
    except SystemExit:
        pass
    # Run на синтетике: A2-подобная клетка, якорь BTC во всех заданиях; лучшая база по ноге Y (M3-03)
    meta = Meta(strict=False)
    meta.asset = {("x", "AAA"): "AAA|k", ("x", "BBB"): "BBB|k", ("x", "BTC"): "BTC|k"}
    run = Run.__new__(Run)
    run.d0, run.d1 = 2479, 2482
    run.jobs = {("A2|x|crypto|a0", "AAA", "BTC"): {2479: [30.0, 0, 0, 0, 1, 1], 2480: [30.0, 0, 0, 0, 1, 1], 2481: [30.0, 0, 0, 0, 1, 1]},
                ("A2|x|crypto|a0", "BBB", "BTC"): {2479: [-3.0, 0, 0, 0, 1, 1], 2480: [-3.0, 0, 0, 0, 1, 1], 2481: [-3.0, 0, 0, 0, 1, 1]}}
    run.jmeta = {k: ("x", "x") for k in run.jobs}
    run.jext = {}
    run.cout = defaultdict(lambda: defaultdict(float))
    run.cout_nan = defaultdict(set)
    run.trades, run.files, run.act_missing = [], defaultdict(int), set()
    best, rest, removed = drop_best_base(run, sorted(run.jobs), [2479, 2480, 2481], meta)
    assert best == "AAA" and rest == [("A2|x|crypto|a0", "BBB", "BTC")] and abs(removed - 0.5) < 1e-12, (best, rest)   # BTC не кандидат: убирается AAA, не вся клетка
    s = run.series("A2|x|crypto|a0", rest, [2479, 2480, 2481])
    assert all(abs(v + 3.0) < 1e-12 for v in s["closed"]) and len(s["closed"]) == 3, s["closed"]
    # A1: обе ноги - кандидаты
    assert candidate_bases(meta, {("A1|x|crypto|a0", "AAA", "BBB"): ("x", "x")}, ("A1|x|crypto|a0", "AAA", "BBB")) == {"AAA", "BBB"}
    assert candidate_bases(meta, run.jmeta, ("A2|x|crypto|a0", "AAA", "BTC")) == {"AAA"}
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "selftest":
        selftest()
        raise SystemExit(0)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cells", "slices", "controls", "jumps", "breakflags", "breakflags_all"])
    ap.add_argument("--run")
    ap.add_argument("--out", required=True)
    ap.add_argument("--defs")
    ap.add_argument("--contracts")
    ap.add_argument("--cells-params", dest="cells_params")
    ap.add_argument("--d2-pairs", dest="d2_pairs")
    ap.add_argument("--inst-dir", dest="inst_dir")
    ap.add_argument("--params")
    ap.add_argument("--flags")
    ap.add_argument("--panels")
    ap.add_argument("--d0", type=int, default=D0)
    ap.add_argument("--d1", type=int, default=D1)
    ap.add_argument("--pairs")
    ap.add_argument("--groups")
    ap.add_argument("--panels-y", dest="panels_y")
    ap.add_argument("--panels-x", dest="panels_x")
    ap.add_argument("--stab-threshold", dest="stab_threshold", default="0.06171426")
    args = vars(ap.parse_args())
    rc = {"cells": cmd_cells, "slices": cmd_slices, "controls": cmd_controls, "jumps": cmd_jumps, "breakflags": cmd_breakflags, "breakflags_all": cmd_breakflags_all}[args["cmd"]](args)
    raise SystemExit(rc if isinstance(rc, int) else 0)
