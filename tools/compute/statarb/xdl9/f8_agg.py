#!/usr/bin/env python3
"""TK-493 S3 (П-37 r3.1 §8-§11, §15): агрегатор выходов `statarb run` F8 -> суточные ряды клеток, срезы, контроли, чувствительность. Только stdlib
(интервал - portfolio.block_t_interval через cell_status). Статусы клеток здесь НЕ присваиваются: это F10 (cell_status.classify).

Входы (каталог выходов RUN, подкаталоги стадий): w1 w2 w3 (основной счёт), w4 (контроль §11.8), pl (плацебо-2), rp (случайные пары), sm (min_obs 0,9 W),
n17 (без 17 контрактов Binance TradFi), re (случайные входы - f8_randentry.py, колонки как у jobday без active). В каждом: `*.jobday.csv`
(--job-day-out: cell,job,y,x,day,net_bps,gross_bps,fee_bps,funding_bps,trades,active), `*.trades.csv` (--trades-out --trades-ext), `*.ext.csv`, `*.cells_out.csv`.
Имя файла `<кл>__<биржа Y>__<биржа X>[__shardN]` задаёт биржи ног.

Метрика (§10.1): суточное значение клетки = Σ net закрытых в сутки сделок / число активных пар-суток; сутки без активных пар значения не дают (пропуск,
не ноль). Интервал - 90 % t по блокам суток (df = n_эфф - 1).

  python f8_agg.py cells    --run RUN --out OUT [--d0 2479 --d1 2500]
  python f8_agg.py slices   --run RUN --defs DEFS --out OUT [--contracts contracts.csv --inst-dir DIR --params P-37-params.csv --flags pair_flags.csv --d2-dir DIR]
  python f8_agg.py controls --run RUN --out OUT                 (pl, rp, sm, n17, re против основного счёта; разность «клетка - случайные входы» по суткам)
  python f8_agg.py breakflags --pairs jobs.csv --panels-y DIR --panels-x DIR --d0 2479 --d1 2500 --out flags.csv   (numpy; читает торговое окно - только F8)
  python f8_agg.py selftest
"""
import csv, glob, math, os, re, sys
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


# ------------------------------------------------------------------ загрузка
def exch_of(path):
    parts = os.path.basename(path).split(".")[0].split("__")
    return parts[1], parts[2]


def load_jobday(run, stages, d0=D0, d1=D1):
    """-> jobs {(cell, y, x): {day: [net, gross, fee, funding, trades, active]}}, meta {(cell, y, x): (exy, exx)}."""
    jobs, meta = {}, {}
    for st in stages:
        for fp in sorted(glob.glob(os.path.join(run, st, "*.jobday.csv"))):
            exy, exx = exch_of(fp)
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    d = int(r["day"])
                    if not d0 <= d < d1:
                        continue
                    k = (r["cell"], unshift(r["y"]), unshift(r["x"]))
                    meta[k] = (exy, exx)
                    v = jobs.setdefault(k, {}).setdefault(d, [0.0, 0.0, 0.0, 0.0, 0, 0])
                    v[0] += float(r["net_bps"]); v[1] += float(r["gross_bps"]); v[2] += float(r["fee_bps"]); v[3] += float(r["funding_bps"])
                    v[4] += int(float(r["trades"]))
                    v[5] += int(float(r["active"])) if r.get("active", "") != "" else 0
    return jobs, meta


def load_trades(run, stages):
    """-> [(cell, y, x, exy, exx, fill_in, fill_out, net, gross, fee, funding)] (минуты от 2020-01-01)."""
    out = []
    for st in stages:
        for fp in sorted(glob.glob(os.path.join(run, st, "*.trades.csv"))):
            exy, exx = exch_of(fp)
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    out.append((r["cell"], unshift(r["y"]), unshift(r["x"]), exy, exx, int(r["fill_in"]), int(r["fill_out"]),
                                float(r["net_bps"]), float(r["gross_bps"]), float(r["fee_bps"]), float(r["funding_bps"])))
    return out


def load_ext(run, stages):
    """-> {cell: суммы счётчиков F-01 по группам}"""
    acc = defaultdict(lambda: defaultdict(float))
    for st in stages:
        for fp in sorted(glob.glob(os.path.join(run, st, "*.ext.csv"))):
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    for k in ("eval_minutes", "z_def_minutes", "minutes_in_pos", "pos_z_nan_minutes", "noentry_window_minutes", "rearm_blocked", "trades_n",
                              "exit_rule", "exit_stop", "exit_timeout", "open_end"):
                        acc[r["cell"]][k] += fnum(r[k]) if fnum(r[k]) == fnum(r[k]) else 0.0
    return acc


def load_cells_out(run, stages):
    acc = defaultdict(lambda: defaultdict(float))
    for st in stages:
        for fp in sorted(glob.glob(os.path.join(run, st, "*.cells_out.csv"))):
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    for k, v in r.items():
                        if k != "cell" and fnum(v) == fnum(v):
                            acc[r["cell"]][k] += float(v)
    return acc


# ------------------------------------------------------------------ ряды и интервалы
def day_sums(jobs, keys, days):
    """[(день, net, gross, fee, funding, trades, active)] по выбранным заданиям."""
    out = []
    for d in days:
        a = [0.0, 0.0, 0.0, 0.0, 0, 0]
        for k in keys:
            v = jobs[k].get(d)
            if v:
                for i in range(6):
                    a[i] += v[i]
        out.append((d,) + tuple(a))
    return out


def stats(jobs, keys, days, extra_cost=False):
    """Оценка по выбранным заданиям и суткам. extra_cost - вычесть ещё раз комиссию (чувствительность «комиссия x2»)."""
    ds = day_sums(jobs, keys, days)
    vals = [((r[1] - (r[3] if extra_cost else 0.0)) / r[6]) for r in ds if r[6] > 0]
    trades = sum(r[5] for r in ds)
    net = sum(r[1] for r in ds)
    out = dict(n_jobs=len(keys), n_days=len(vals), trades=trades, net_sum=net, net_per_trade=(net / trades if trades else float("nan")),
               active_pair_days=sum(r[6] for r in ds), pairs_traded=sum(1 for k in keys if any(jobs[k].get(d, [0, 0, 0, 0, 0, 0])[4] for d in days)),
               fee_sum=sum(r[3] for r in ds), funding_sum=sum(r[4] for r in ds), series=vals)
    out["orphan_trades"] = sum(r[5] for r in ds if r[6] == 0)      # сделки в сутки без активных пар: в ряд не входят (D2: активность по z, у разреженных бирж нулевая)
    if len(vals) >= 3:
        m, lo, hi, p, df = P.block_t_interval(list(vals), ALPHA)
        out.update(mean=m, lo=lo, hi=hi, p=p, df=df)
    else:
        out.update(mean=(vals[0] if vals else float("nan")), lo=float("nan"), hi=float("nan"), p=float("nan"), df=float("nan"))
    return out


class Meta:
    def __init__(self, contracts_csv=None):
        self.asset, self.slug = {}, {}
        if contracts_csv and os.path.exists(contracts_csv):
            with open(contracts_csv, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    k = (r["exchange"], safe(r["symbol"]))
                    self.asset[k] = r["asset_id"]
                    self.slug[k] = r["slug"]

    def base(self, ex, stem):
        return self.asset.get((ex, stem), "?|?").split("|")[0]


def job_bases(meta, jmeta, k):
    exy, exx = jmeta[k]
    return {meta.base(exy, k[1])} | {meta.base(exx, t) for t in k[2].split("+")}


def drop_best_base(jobs, jmeta, keys, days, meta):
    """«Без лучшей монеты» (§10.4 в): базовый актив с наибольшей суммой net клетки; удаляются все задания с ним. -> (база, оставшиеся ключи)."""
    tot = defaultdict(float)
    for k in keys:
        net = sum(jobs[k].get(d, [0.0])[0] for d in days)
        for b in job_bases(meta, jmeta, k):
            tot[b] += net
    if not tot:
        return None, keys
    best = max(sorted(tot), key=lambda b: tot[b])
    return best, [k for k in keys if best not in job_bases(meta, jmeta, k)]


def top_share(trades_cell, frac=0.01):
    """Доля суммарного net в топ-1 % сделок (по убыванию net); None, если сумма <= 0 или сделок нет."""
    nets = sorted((t[7] for t in trades_cell), reverse=True)
    if not nets:
        return None
    tot = sum(nets)
    if tot <= 0:
        return None
    k = max(1, math.ceil(frac * len(nets)))
    return sum(nets[:k]) / tot


def p5(trades_cell):
    nets = sorted(t[7] for t in trades_cell)
    return nets[max(0, math.ceil(0.05 * len(nets)) - 1)] if nets else float("nan")


# ------------------------------------------------------------------ cells
WEEKS = (("W1", range(2479, 2486)), ("W2", range(2486, 2493)), ("W3", range(2493, 2500)))


def cmd_cells(a):
    d0, d1 = a["d0"], a["d1"]
    days = list(range(d0, d1))
    jobs, jmeta = load_jobday(a["run"], MAIN_STAGES, d0, d1)
    trades = load_trades(a["run"], MAIN_STAGES)
    ext = load_ext(a["run"], MAIN_STAGES)
    cout = load_cells_out(a["run"], MAIN_STAGES)
    meta = Meta(a.get("contracts"))
    by_cell = defaultdict(list)
    for k in jobs:
        by_cell[k[0]].append(k)
    os.makedirs(a["out"], exist_ok=True)
    day_rows, sum_rows = [], []
    for cell in sorted(by_cell):
        keys = sorted(by_cell[cell])
        st = stats(jobs, keys, days)
        for r in day_sums(jobs, keys, days):
            day_rows.append([cell, r[0], r[1], r[2], r[3], r[4], r[5], r[6], (r[1] / r[6]) if r[6] > 0 else ""])
        st2 = stats(jobs, keys, days, extra_cost=True)
        best, rest = drop_best_base(jobs, jmeta, keys, days, meta) if meta.asset else (None, keys)
        stb = stats(jobs, rest, days) if rest else None
        tr = [t for t in trades if t[0] == cell]
        e, c = ext.get(cell, {}), cout.get(cell, {})
        wk = []
        for nm, rg in WEEKS:
            w = stats(jobs, keys, [d for d in rg if d0 <= d < d1])
            wk.append(w["mean"])
        sum_rows.append([cell, len(keys), st["n_days"], st["trades"], st["pairs_traded"], st["active_pair_days"], st["mean"], st["lo"], st["hi"], st["p"],
                         st["net_per_trade"], st["fee_sum"], st["funding_sum"], st["orphan_trades"], st2["mean"], st2["lo"], st2["hi"],
                         best or "", (stb["mean"] if stb else ""), CS.mean_without_best_days(st["series"]) if len(st["series"]) > CS.BEST_DAYS else "",
                         (top_share(tr) if tr else ""), (p5(tr) if tr else ""), (min((t[7] for t in tr), default="")),
                         wk[0], wk[1], wk[2],
                         (e.get("z_def_minutes", 0) / e["eval_minutes"] if e.get("eval_minutes") else ""),
                         (e.get("pos_z_nan_minutes", 0) / e["minutes_in_pos"] if e.get("minutes_in_pos") else ""),
                         c.get("open_at_end", ""), c.get("entry_deferred", ""), c.get("exit_deferred", ""), c.get("mtm_net_bps", "")])
    hdr_d = ["cell", "day", "net_bps", "gross_bps", "fee_bps", "funding_bps", "trades", "active_pairs", "value_bps_per_active_pair"]
    hdr_s = ["cell", "jobs", "days_with_value", "trades", "pairs_traded", "active_pair_days", "mean_bps_day_pair", "lo90", "hi90", "p_two_sided", "net_per_trade_bps",
             "fee_sum_bps", "funding_sum_bps", "trades_on_days_without_active_pairs", "fee_x2_mean", "fee_x2_lo90", "fee_x2_hi90", "best_base", "mean_without_best_base", "mean_without_best_2_days",
             "share_net_top1pct_trades", "p05_trade_net_bps", "worst_trade_bps", "week1_mean", "week2_mean", "week3_mean", "z_defined_share",
             "pos_z_nan_share_of_pos_minutes", "open_at_end", "entry_deferred", "exit_deferred", "mtm_net_sum_bps"]
    for nm, hdr, rows in (("cell_days.csv", hdr_d, day_rows), ("cell_summary.csv", hdr_s, sum_rows)):
        with open(os.path.join(a["out"], nm), "w", encoding="utf-8", newline="\n") as fh:
            w = csv.writer(fh)
            w.writerow(hdr)
            w.writerows(rows)
    print("cells: клеток %d, строк суток %d -> %s" % (len(sum_rows), len(day_rows), a["out"]))
    return sum_rows


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
    """{(биржа, символ): delisted_ms} для контрактов, снятых в торговом окне."""
    out = {}
    lo = 1_577_836_800_000 + d0 * MIN_DAY * 60_000
    hi = 1_577_836_800_000 + d1 * MIN_DAY * 60_000
    for fp in glob.glob(os.path.join(inst_dir, "*.csv")):
        with open(fp, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                v = r.get("delisted_ms", "")
                if v and lo <= int(float(v)) < hi:
                    out[(r["exchange"], safe(r["symbol"]))] = int(float(v))
    return out


def row_of(slice_id, grp, cell, st, note=""):
    return [slice_id, grp, cell, st["n_jobs"], st["n_days"], st["trades"], st["pairs_traded"], st["active_pair_days"], st["net_sum"], st["net_per_trade"],
            st["mean"], st["lo"], st["hi"], note]


SLICE_HDR = ["slice", "group", "cell", "jobs", "days_with_value", "trades", "pairs_traded", "active_pair_days", "net_sum_bps", "net_per_trade_bps",
             "mean_bps_day_pair", "lo90", "hi90", "note"]


def tr_stats(trs):
    n = len(trs)
    s = sum(t[7] for t in trs)
    return dict(n_jobs=len({(t[0], t[1], t[2]) for t in trs}), n_days=0, trades=n, pairs_traded=len({(t[1], t[2]) for t in trs}), active_pair_days=0, net_sum=s,
                net_per_trade=(s / n if n else float("nan")), mean=float("nan"), lo=float("nan"), hi=float("nan"))


def cmd_slices(a):
    d0, d1 = a["d0"], a["d1"]
    days_all = list(range(d0, d1))
    jobs, jmeta = load_jobday(a["run"], MAIN_STAGES, d0, d1)
    trades = load_trades(a["run"], MAIN_STAGES)
    meta = Meta(a.get("contracts"))
    cells = sorted({k[0] for k in jobs})
    by_cell = defaultdict(list)
    for k in jobs:
        by_cell[k[0]].append(k)
    rows = []
    # 1. сутки
    for r in read_defs(a["defs"], "slices_days.csv"):
        ds = [d for d in parse_days(r["days"]) if d0 <= d < d1]
        for c in cells:
            rows.append(row_of(r["slice"], "days", c, stats(jobs, sorted(by_cell[c]), ds), r["description"]))
    # 2. часы входа (по сделкам)
    for r in read_defs(a["defs"], "slices_hours.csv"):
        h0, h1 = int(r["hour_from"]), int(r["hour_to"])
        for c in cells:
            trs = [t for t in trades if t[0] == c and h0 <= (t[5] % MIN_DAY) // 60 <= h1]
            rows.append(row_of(r["slice"], "hours", c, tr_stats(trs), r["description"] + " (по часу UTC fill_in)"))
    # 3. гипотезы H1-H7 (срезы пар по базовым активам)
    for r in read_defs(a["defs"], "slices_hyp.csv"):
        kinds, expairs, bases = (r[k].split("|") for k in ("kinds", "ex_pairs", "bases"))
        if not r["kinds"]:
            rows.append([r["slice"], "hyp", "-", 0, 0, 0, 0, 0, 0.0, float("nan"), float("nan"), float("nan"), float("nan"), r["text"] + "; " + r["availability"]])
            continue
        for c in cells:
            cp = cell_parts(c)
            if cp["kind"] not in kinds or "~".join(sorted(cp["exs"])) not in expairs:
                continue
            keys = [k for k in by_cell[c] if job_bases(meta, jmeta, k) & set(bases)]
            rows.append(row_of(r["slice"], "hyp", c, stats(jobs, sorted(keys), days_all), r["text"] + "; ГИПОТЕЗА из материала владельца, результат - верхняя граница (§0.6)"))
    # 4. сессия США / выходные / закрытие (по сделкам), только полосы с маской США
    for c in cells:
        if cell_parts(c)["slug"] not in ("eq_us", "idx_us"):
            continue
        for cls in ("inside", "across_close", "weekend"):
            trs = [t for t in trades if t[0] == c and session_class(t[5], t[6]) == cls]
            rows.append(row_of("session_" + cls, "session", c, tr_stats(trs), "касса США (§9)"))
    # 5. сделки через окно выпадения биржи (§3, §8)
    if a.get("params"):
        win = parse_windows(a["params"])
        for c in cells:
            exs = [e for e in cell_parts(c)["exs"] if e in win]
            if not exs:
                continue
            trs = [t for t in trades if t[0] == c and any(t[5] <= w0 + wl and w0 <= t[6] for e in exs for (w0, wl) in win[e])]
            rows.append(row_of("through_outage", "outage", c, tr_stats(trs), ",".join(exs)))
    # 6. снятые в торговом окне контракты и 17 контрактов Binance TradFi (пары)
    if a.get("inst_dir"):
        dl = load_delisted(a["inst_dir"], d0, d1)
        for c in cells:
            keys = [k for k in by_cell[c] if (jmeta[k][0], k[1]) in dl or any((jmeta[k][1], t) in dl for t in k[2].split("+"))]
            if keys:
                rows.append(row_of("delisted_in_window", "delisted", c, stats(jobs, sorted(keys), days_all), "контракт снят в торговом окне (instruments.csv)"))
    n17 = {b + "USDT" for b in ("ACN ASML BX CAT COST CSCO DELL EQT GEV JPM KODEX200 MRVL MU NOK ORCL STRC TXN".split())}
    for c in cells:
        keys = [k for k in by_cell[c] if (jmeta[k][0] == "binance" and k[1] in n17) or (jmeta[k][1] == "binance" and any(t in n17 for t in k[2].split("+")))]
        if keys:
            rows.append(row_of("binance_tradfi_17", "n17", c, stats(jobs, sorted(keys), days_all), "с 17 контрактами Binance TradFi (фандинг как отдано биржей, §11.9)"))
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
                    rows.append(row_of(nm, "flag", c, stats(jobs, sorted(keys), days_all), "метка пары"))
    # 8. TradFi между биржами: (вид x не-крипто класс) по объединению пар слагаемых клеток (§9, Н32); классы между собой не суммируются
    blocks = defaultdict(list)
    for c in cells:
        cp = cell_parts(c)
        if cp["slug"] == "crypto":
            continue
        big = {"eq_us": "акции+ETF", "eq_nonus": "акции+ETF", "idx_us": "индексы", "idx_nonus": "индексы", "metal_energy": "металлы+энергия", "fx": "фиат"}.get(cp["slug"], cp["slug"])
        blocks[(cp["kind"], big, cp["hand"])].extend(by_cell[c])
    for (kind, big, hand), keys in sorted(blocks.items()):
        rows.append(row_of("tradfi_%s_%s_%s" % (kind, big, hand), "tradfi", "|".join((kind, big, hand)), stats(jobs, sorted(keys), days_all),
                           "объединение пар слагаемых клеток, без статуса, вне M (§9)"))
    os.makedirs(a["out"], exist_ok=True)
    with open(os.path.join(a["out"], "slice_table.csv"), "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(SLICE_HDR)
        w.writerows(rows)
    print("slices: строк %d -> %s" % (len(rows), a["out"]))
    return rows


# ------------------------------------------------------------------ контроли и чувствительность
def load_re(run, d0, d1):
    """Случайные входы: jobday-подобные файлы `re/*.jobday.csv` без active -> {(cell,y,x): {day: net}}"""
    out = defaultdict(lambda: defaultdict(float))
    for fp in sorted(glob.glob(os.path.join(run, "re", "*.jobday.csv"))):
        with open(fp, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                d = int(r["day"])
                if d0 <= d < d1:
                    out[(r["cell"], unshift(r["y"]), unshift(r["x"]))][d] += float(r["net_bps"])
    return out


def cmd_controls(a):
    d0, d1 = a["d0"], a["d1"]
    days = list(range(d0, d1))
    main, mm = load_jobday(a["run"], MAIN_STAGES, d0, d1)
    by_cell = defaultdict(list)
    for k in main:
        by_cell[k[0]].append(k)
    rows, cdays = [], []
    others = {}
    for st in ("pl", "rp", "sm", "n17"):
        if os.path.isdir(os.path.join(a["run"], st)):
            others[st] = load_jobday(a["run"], (st,), d0, d1)[0]
    re = load_re(a["run"], d0, d1) if os.path.isdir(os.path.join(a["run"], "re")) else None
    for c in sorted(by_cell):
        keys = sorted(by_cell[c])
        m = stats(main, keys, days)
        rows.append([c, "main", m["n_jobs"], m["n_days"], m["trades"], m["mean"], m["lo"], m["hi"], "", "", "", ""])
        for st, jb in others.items():
            ck = sorted(k for k in jb if k[0] == c)
            if not ck:
                continue
            s = stats(jb, ck, days)
            note = ""
            if st == "n17":
                ref = stats(main, [k for k in keys if k in set(ck)], days)
                note = "net_sum с17 %.3f / без17 %.3f; funding_sum с17 %.3f / без17 %.3f (те же задания)" % (m["net_sum"], s["net_sum"], m["funding_sum"], s["funding_sum"])
            rows.append([c, st, s["n_jobs"], s["n_days"], s["trades"], s["mean"], s["lo"], s["hi"], "", "", "", note])
        if re is not None:
            # знаменатель - активные пары основного счёта: те же задания и сутки, поэтому разность по суткам - Σ(net) факта минус Σ(net) случайных, на активную пару
            ds = day_sums(main, keys, days)
            cell_v, ctrl_v, diff = [], [], []
            for r in ds:
                if r[6] <= 0:
                    continue
                rn = sum(re.get((c, k[1], k[2]), {}).get(r[0], 0.0) for k in keys)
                cell_v.append(r[1] / r[6]); ctrl_v.append(rn / r[6]); diff.append((r[1] - rn) / r[6])
                cdays.append([c, r[0], r[1] / r[6], rn / r[6]])
            if len(diff) >= 2:
                cm, cl, ch, _, _ = P.block_t_interval(list(ctrl_v), ALPHA)
                dm, dl, dh, _, _ = P.block_t_interval(list(diff), ALPHA)
                rows.append([c, "re", len(keys), len(diff), "", cm, cl, ch, dm, dl, dh, "разность «клетка - случайные входы» (условие (б) §10.4): нижняя граница %.3f %s 0" % (dl, ">" if dl > 0 else "<=")])
    hdr = ["cell", "stage", "jobs", "days_with_value", "trades", "mean_bps_day_pair", "lo90", "hi90", "diff_mean", "diff_lo90", "diff_hi90", "note"]
    os.makedirs(a["out"], exist_ok=True)
    with open(os.path.join(a["out"], "controls.csv"), "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        w.writerows(rows)
    with open(os.path.join(a["out"], "control_days.csv"), "w", encoding="utf-8", newline=chr(10)) as fh:     # вход cell_status.classify (F10): ряд клетки и ряд контроля по суткам
        w = csv.writer(fh)
        w.writerow(["cell", "day", "cell_value", "random_entries_value"])
        w.writerows(cdays)
    print("controls: строк %d -> %s" % (len(rows), a["out"]))
    return rows


def cmd_breakflags(a):
    """S = σ(ln cY - ln cX) / σ(ln cY) по общим валидным минутам торгового окна (П-37 §4.2, путь К); флаг 1, если S > stab_threshold. Читает торговое окно: только F8."""
    import numpy as np
    from f8_randentry import read_sap
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


def selftest():
    import tempfile
    # session_class: четверг 15.10 внутри сессии, через закрытие, выходные, переход на EST
    d = 2479
    assert session_class(d * 1440 + 14 * 60, d * 1440 + 15 * 60) == "inside"
    assert session_class(d * 1440 + 19 * 60, d * 1440 + 22 * 60) == "across_close"
    assert session_class(2480 * 1440 + 19 * 60, 2481 * 1440 + 60) == "weekend"
    assert session_class(2497 * 1440 + 14 * 60 + 40, 2497 * 1440 + 15 * 60) == "inside" and session_class(2497 * 1440 + 14 * 60, 2497 * 1440 + 15 * 60) == "across_close"
    assert parse_days("2479-2481;2490") == [2479, 2480, 2481, 2490]
    jobs = {("c", "A", "B"): {2479: [10.0, 20.0, 10.0, 0.0, 2, 1], 2480: [-4.0, 6.0, 10.0, 0.0, 1, 1], 2481: [0, 0, 0, 0, 0, 0]},
            ("c", "C", "B"): {2479: [2.0, 12.0, 10.0, 0.0, 1, 1], 2480: [0.0, 0.0, 0.0, 0.0, 0, 1], 2481: [0, 0, 0, 0, 0, 0]}}
    st = stats(jobs, sorted(jobs), [2479, 2480, 2481])
    assert st["n_days"] == 2 and abs(st["series"][0] - 6.0) < 1e-12 and abs(st["series"][1] - (-2.0)) < 1e-12 and st["trades"] == 4
    st2 = stats(jobs, sorted(jobs), [2479, 2480], extra_cost=True)
    assert abs(st2["series"][0] - (12.0 - 20.0) / 2) < 1e-12
    meta = Meta()
    meta.asset = {("x", "A"): "AAA|k", ("x", "B"): "BTC|k", ("x", "C"): "CCC|k"}
    jm = {k: ("x", "x") for k in jobs}
    best, rest = drop_best_base(jobs, jm, sorted(jobs), [2479, 2480], meta)
    assert best == "BTC" and rest == [], (best, rest)    # BTC в обоих заданиях -> удаляются оба
    assert top_share([("c", "y", "x", "", "", 0, 0, v, 0, 0, 0) for v in [100.0] + [1.0] * 99]) == 100.0 / 199.0
    assert unshift("A@p1+B@p1") == "A+B" and unshift("A") == "A"
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "selftest":
        selftest()
        raise SystemExit(0)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cells", "slices", "controls", "breakflags"])
    ap.add_argument("--run")
    ap.add_argument("--out", required=True)
    ap.add_argument("--defs")
    ap.add_argument("--contracts")
    ap.add_argument("--inst-dir", dest="inst_dir")
    ap.add_argument("--params")
    ap.add_argument("--flags")
    ap.add_argument("--d0", type=int, default=D0)
    ap.add_argument("--d1", type=int, default=D1)
    ap.add_argument("--pairs")
    ap.add_argument("--panels-y", dest="panels_y")
    ap.add_argument("--panels-x", dest="panels_x")
    ap.add_argument("--stab-threshold", dest="stab_threshold", default="0.06171426")
    args = vars(ap.parse_args())
    {"cells": cmd_cells, "slices": cmd_slices, "controls": cmd_controls, "breakflags": cmd_breakflags}[args["cmd"]](args)
