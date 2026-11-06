#!/usr/bin/env python3
"""TK-493 S3 (П-37 r3.1 §11.3, §10.4 (б), §18 п.7): «случайные входы» на тех же парах БЕЗ правки Rust - симуляция по панелям (numpy, ПК).

Контроль условия (б) статуса: на каждом задании клетки - столько же сделок, сколько у факта (`--real-trades`, вывод `statarb run --trades-out`),
момент сигнала - равномерно по валидным минутам задания в окне [from_day, to_day) (seed 37), направление - случайное, удержание - выбор с возвращением
из удержаний факта КЛЕТКИ (fill_out - fill_in в минутах). Комиссия, плечи (номиналы ног), β, задержка ноги X, правило выхода по open и фандинг -
как в ядре (kernel.rs; тождество проверяется режимом --replay: сделки факта пересчитываются этим кодом и сверяются с выходом ядра).

Что считается валидным сигналом s (как у ядра при входе): обе закрытия минуты s конечны; окно из W минут (s-W, s] содержит не менее max(min_obs, 2)
совместных наблюдений (при OLS ещё var(lx) > 0); open всех ног на s+1 конечны; s+1 >= from_day*1440 и выход возможен до конца данных. Сделки одной пары могут
перекрываться (каждая - независимая ставка; нулевая модель для ожидания, не портфель).

Выход - строка на (клетка, задание, сутки закрытия): net, gross, fee, funding, trades (колонки как `--job-day-out` без `active`; знаменатель
«активные пары» берёт агрегатор из факта клетки).

Запуск (один вызов на группу `statarb run`, те же аргументы источников):
  python f8_randentry.py --kind pair --panels P --funding-y F --cells cells.csv --jobs jobs.csv --real-trades trades.csv --out out.jobday.csv [--out-trades t.csv] [--seed 37]
  ... --replay        вместо случайных входов пересчитать сделки факта и сверить с ядром (печатает max |d| по gross, fee, funding, beta)
  python f8_randentry.py selftest
"""
import csv, gzip, math, os, sys
import numpy as np

MIN_DAY = 1440
GRID0_MS = 1_577_836_800_000
TAKER_FEE_BPS = 3.5
FEE_REBATE = 0.10
FEE = TAKER_FEE_BPS * (1.0 - FEE_REBATE)        # 3.15 bps на сторону на ногу (src/lob/costs.rs::leg_fee_bps(true), В-88)
SEED = 37


# ------------------------------------------------------------------ чтение
def read_sap(path):
    with open(path, "rb") as f:
        h = f.read(24)
        if h[:4] != b"SAP1":
            raise ValueError("не SAP1: " + path)
        first_min = int(np.frombuffer(h[8:16], dtype="<i8")[0])
        n = int(np.frombuffer(h[16:24], dtype="<u8")[0])
        a = np.fromfile(f, dtype="<f8", count=2 * n)
    return first_min, a[:n], a[n:]


def read_funding(dirpath, name):
    """-> (минуты сетки int64, ставки float64) по возрастанию; нет файла - пусто (нога без фандинга, как у ядра)."""
    if dirpath is None:
        return np.zeros(0, np.int64), np.zeros(0)
    p = os.path.join(dirpath, name + ".csv.gz")
    if not os.path.exists(p):
        return np.zeros(0, np.int64), np.zeros(0)
    ts, rs = [], []
    with gzip.open(p, "rt", encoding="utf-8", newline="") as fh:
        fh.readline()
        for line in fh:
            parts = line.split(",")
            if len(parts) >= 2:
                ts.append((int(parts[0]) - GRID0_MS) // 60_000)
                rs.append(float(parts[1]))
    return np.asarray(ts, np.int64), np.asarray(rs, np.float64)


def read_cells(path):
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out.append(dict(cell=r["cell"], window=int(r["window"]), min_obs=int(r["min_obs"]),
                            beta_fixed=float(r["beta_fixed"]) if r["beta_fixed"].strip() else None,
                            max_hold=int(r["max_hold"])))
    return out


def read_jobs(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return [dict(y=r["y"], x=r["x"].split("+"), from_day=int(r["from_day"]), to_day=int(r["to_day"])) for r in csv.DictReader(fh)]


def read_real_trades(path):
    """-> {cell: {job: [dict(dir, fill_in, fill_x|None, fill_out, gross, fee, funding, beta|None, net)]}}"""
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            t = dict(dir=int(r["dir"]), sig_in=int(r["sig_in"]), fill_in=int(r["fill_in"]), fill_x=int(r["fill_x"]) if r.get("fill_x") else None,
                     fill_out=int(r["fill_out"]), gross=float(r["gross_bps"]), fee=float(r["fee_bps"]), funding=float(r["funding_bps"]),
                     net=float(r["net_bps"]), beta=float(r["beta_in"]) if r.get("beta_in") else None)
            out.setdefault(r["cell"], {}).setdefault(int(r["job"]), []).append(t)
    return out


class Panels:
    """Кэш панелей каталога (малый, чтобы не держать сотни МБ)."""

    def __init__(self, d, fund_dir=None, cap=48):
        self.d, self.fd, self.cap, self.c = d, fund_dir, cap, {}

    def get(self, name):
        if name in self.c:
            return self.c[name]
        if len(self.c) >= self.cap:
            self.c.pop(next(iter(self.c)))
        p = os.path.join(self.d, name + ".sap")
        v = read_sap(p) + read_funding(self.fd, name) if os.path.exists(p) else None
        self.c[name] = v
        return v


# ------------------------------------------------------------------ задание: ряды окна и оценка β
class Job:
    """Окно задания как у ядра: общий спан ног, lo = max(first, from_min - window), hi = min(конец, to_min - 1)."""

    def __init__(self, y, xs, from_day, to_day, window, min_span=1440):
        legs = [y] + xs
        first = max(l[0] for l in legs)
        end = min(l[0] + len(l[1]) for l in legs)
        from_min, to_min = from_day * MIN_DAY, to_day * MIN_DAY
        lo = max(first, from_min - window)
        hi = min(end - 1, to_min - 1)
        self.ok = hi >= lo and (hi - lo + 1) >= min_span
        if not self.ok:
            return
        self.lo, self.n, self.from_min = lo, hi - lo + 1, from_min
        sl = lambda a, l: a[lo - l[0]:lo - l[0] + self.n]
        self.oy, self.cy = sl(y[1], y), sl(y[2], y)
        self.ox = [sl(x[1], x) for x in xs]
        self.cx = [sl(x[2], x) for x in xs]
        k = len(xs)
        self.w = 1.0 / k
        with np.errstate(invalid="ignore", divide="ignore"):
            self.ly = np.log(self.cy)
            self.lx = np.log(self.cx[0]) if k == 1 else sum(self.w * np.log(c) for c in self.cx)
        self.fy = (y[3], y[4])
        self.fx = [(x[3], x[4]) for x in xs]
        self.opens_ok = np.isfinite(self.oy)
        for o in self.ox:
            self.opens_ok &= np.isfinite(o)
        self.joint = np.isfinite(self.ly) & np.isfinite(self.lx)
        self._pref = None

    def prefix(self):
        if self._pref is None:
            j = self.joint
            x = np.where(j, self.lx, 0.0)
            y = np.where(j, self.ly, 0.0)
            ax = x[j][0] if j.any() else 0.0
            ay = y[j][0] if j.any() else 0.0
            x = np.where(j, x - ax, 0.0)
            y = np.where(j, y - ay, 0.0)
            z = np.zeros(1)
            cs = lambda a: np.concatenate([z, np.cumsum(a)])
            self._pref = (cs(j.astype(np.float64)), cs(x), cs(y), cs(x * x), cs(x * y))
        return self._pref

    def window_beta(self, s, window, min_obs, beta_fixed):
        """OLS-β (или фиксированная) окна (s-W, s] по совместным наблюдениям; NaN, если не хватает наблюдений или var(lx) = 0.
        s - массив относительных индексов."""
        n, sx, sy, sxx, sxy = self.prefix()
        a = np.maximum(s + 1 - window, 0)
        b = s + 1
        cnt = n[b] - n[a]
        okc = cnt >= max(min_obs, 2)
        with np.errstate(invalid="ignore", divide="ignore"):
            mx = (sx[b] - sx[a]) / cnt
            cxx = (sxx[b] - sxx[a]) - (sx[b] - sx[a]) * mx
            cxy = (sxy[b] - sxy[a]) - (sy[b] - sy[a]) * mx
            beta = cxy / cxx if beta_fixed is None else np.full(len(s), float(beta_fixed))
        good = okc & (cxx > 0 if beta_fixed is None else True)
        return np.where(good, beta, np.nan), good


def ffill(a):
    """Open с протяжкой последнего известного вперёд (price_at ядра); до первого конечного - NaN."""
    idx = np.where(np.isfinite(a), np.arange(len(a)), -1)
    np.maximum.accumulate(idx, out=idx)
    out = np.where(idx >= 0, a[np.maximum(idx, 0)], np.nan)
    return out


def value_trades(job, fi, fx, fo, d, beta, kind_has_x_funding=True):
    """Оценка сделок (относительные индексы fill_in, fill_x (нога X), fill_out; направление d, β): -> gross, fee, funding (bps номинала Y), как kernel.rs.
    qty_Y = d / oy_in; qty_Xi = -d * β * w / ox_i(fill_x); gross - по ценам open; фандинг - события строго между входом и выходом соответствующей ноги."""
    q0 = d / job.oy[fi]
    gross = q0 * (job.oy[fo] - job.oy[fi])
    fee = np.abs(q0) * (job.oy[fi] * FEE + job.oy[fo] * FEE)
    fund = np.zeros(len(fi))
    ev_m, ev_r = job.fy
    if len(ev_m):
        pf = ffill(job.oy)
        lo_i = np.searchsorted(ev_m - job.lo, fi, side="right")
        hi_i = np.searchsorted(ev_m - job.lo, fo, side="left")
        em = ev_m - job.lo
        val = np.where((em >= 0) & (em < job.n), ev_r * pf[np.clip(em, 0, job.n - 1)], 0.0)
        cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(val, nan=0.0))])      # события до первой конечной open вне удержания: в сумму не идут
        fund -= q0 * (cs[np.maximum(hi_i, lo_i)] - cs[lo_i])
    for i, (ox, (fm, fr)) in enumerate(zip(job.ox, job.fx)):
        qx = -d * beta * job.w / ox[fx]
        gross = gross + qx * (ox[fo] - ox[fx])
        fee = fee + np.abs(qx) * (ox[fx] * FEE + ox[fo] * FEE)
        if len(fm):
            pf = ffill(ox)
            em = fm - job.lo
            lo_i = np.searchsorted(em, fx, side="right")
            hi_i = np.searchsorted(em, fo, side="left")
            val = np.where((em >= 0) & (em < job.n), fr * pf[np.clip(em, 0, job.n - 1)], 0.0)
            cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(val, nan=0.0))])
            fund -= qx * (cs[np.maximum(hi_i, lo_i)] - cs[lo_i])
    return gross * 1e4, fee, fund * 1e4


def next_ok(ok, t):
    """Первая минута >= t, где ok истинно (индекс массива) или -1. t - массив."""
    idx = np.flatnonzero(ok)
    pos = np.searchsorted(idx, t, side="left")
    return np.where(pos < len(idx), idx[np.minimum(pos, max(len(idx) - 1, 0))], -1)


def next_ok_x(job, t):
    """Первая минута >= t, где open у всех ног X конечны (вход ноги X при задержке)."""
    ok = np.ones(job.n, dtype=bool)
    for o in job.ox:
        ok &= np.isfinite(o)
    return next_ok(ok, t)


# ------------------------------------------------------------------ симуляция
def simulate_job(job, cell, n_trades, holds, delay_x, rng, beta_ols, need_window=True):
    """Случайные сделки задания: -> dict массивов (fill_in, fill_x, fill_out, dir, beta, gross, fee, funding) в абсолютных минутах."""
    if n_trades == 0 or len(holds) == 0:
        return None
    hold = rng.choice(holds, size=n_trades, replace=True).astype(np.int64)
    s_all = np.arange(job.n - 1)
    valid = job.opens_ok[1:] & (s_all + 1 >= job.from_min - job.lo)
    beta_all, good = job.window_beta(s_all, cell["window"], cell["min_obs"], None if beta_ols else (cell["beta_fixed"] if cell["beta_fixed"] is not None else 1.0))
    if need_window:                      # правило z: сигнал нужен z, значит закрытие минуты и окно с совместными наблюдениями
        valid &= job.joint[:-1] & good
    else:                                # правило funding (D2): вход по уровню ставки, окно и z не участвуют; β = 1, нужны только open следующей минуты
        beta_all = np.ones(len(s_all))
    cand = np.flatnonzero(valid)
    if len(cand) == 0:
        return None
    pick = rng.integers(0, len(cand), size=n_trades)
    s = cand[pick]
    d = rng.choice(np.array([-1, 1], dtype=np.int8), size=n_trades)
    fi = s + 1
    fxx = next_ok_x(job, fi + delay_x) if delay_x > 0 else fi.copy()
    fo = next_ok(job.opens_ok, np.maximum(fi + hold, fxx + 1))
    keep = (fxx >= 0) & (fo >= 0)
    fi, fxx, fo, d, s = fi[keep], fxx[keep], fo[keep], d[keep], s[keep]
    if len(fi) == 0:
        return None
    beta = beta_all[s]
    g, f, fu = value_trades(job, fi, fxx, fo, d.astype(np.float64), beta)
    return dict(fill_in=fi + job.lo, fill_x=fxx + job.lo, fill_out=fo + job.lo, dir=d, beta=beta, gross=g, fee=f, funding=fu)


def replay_job(job, cell, trades, beta_ols):
    """Пересчёт сделок факта: входы/выходы из csv ядра, β - своя оценка по окну на сигнале (fill_in - 1)."""
    fi = np.array([t["fill_in"] for t in trades], np.int64) - job.lo
    fo = np.array([t["fill_out"] for t in trades], np.int64) - job.lo
    fxx = np.array([t["fill_x"] if t["fill_x"] is not None else t["fill_in"] for t in trades], np.int64) - job.lo
    d = np.array([t["dir"] for t in trades], np.float64)
    sg = np.array([t["sig_in"] for t in trades], np.int64) - job.lo          # β заморожена на сигнале (при отложенном входе fill_in > sig_in + 1)
    beta_all, good = job.window_beta(sg, cell["window"], cell["min_obs"], None if beta_ols else (cell["beta_fixed"] if cell["beta_fixed"] is not None else 1.0))
    g, f, fu = value_trades(job, fi, fxx, fo, d, beta_all)
    return beta_all, g, f, fu


def run(a):
    cells = {c["cell"]: c for c in read_cells(a["cells"])}
    jobs = read_jobs(a["jobs"])
    real = read_real_trades(a["real_trades"])
    two = a["kind"] in ("cross", "funding-pp", "funding-ps")
    beta_ols = a["kind"] in ("pair", "basket")
    ys = Panels(a["panels"], a.get("funding_y"))
    xs = Panels(a["panels_b"], a.get("funding_x")) if two else ys
    rows, trade_rows = {}, []
    cmp_max = dict(gross=0.0, fee=0.0, funding=0.0, beta=0.0, n=0, nan=0)
    for ci, cname in enumerate(cells):
        cell = cells[cname]
        by_job = real.get(cname, {})
        holds = np.array([t["fill_out"] - t["fill_in"] for ts in by_job.values() for t in ts], np.int64)
        for ji, jb in enumerate(jobs):
            if ji not in by_job:
                continue
            y = ys.get(jb["y"])
            xl = [xs.get(x) for x in jb["x"]]
            if y is None or any(x is None for x in xl):
                continue
            job = Job(y, xl, jb["from_day"], jb["to_day"], cell["window"])
            if not job.ok:
                continue
            if a["replay"]:
                tr = by_job[ji]
                beta, g, f, fu = replay_job(job, cell, tr, beta_ols)
                cmp_max["nan"] += int(np.isnan(g).sum())
                for k, v, w in (("gross", g, [t["gross"] for t in tr]), ("fee", f, [t["fee"] for t in tr]), ("funding", fu, [t["funding"] for t in tr])):
                    dv = np.abs(v - np.array(w))
                    cmp_max[k] = max(cmp_max[k], float(np.nanmax(dv)) if np.isfinite(dv).any() else 0.0)
                bb = [t["beta"] for t in tr]
                if all(b is not None for b in bb):
                    cmp_max["beta"] = max(cmp_max["beta"], float(np.nanmax(np.abs(beta - np.array(bb)))))
                cmp_max["n"] += len(tr)
                continue
            rng = np.random.default_rng([a["seed"], ci, ji])
            r = simulate_job(job, cell, len(by_job[ji]), holds, a["delay_x"], rng, beta_ols, need_window=a["kind"] not in ("funding-pp", "funding-ps"))
            if r is None:
                continue
            for i in range(len(r["fill_in"])):
                day = int(r["fill_out"][i]) // MIN_DAY
                acc = rows.setdefault((cname, ji, jb["y"], "+".join(jb["x"]), day), [0.0, 0.0, 0.0, 0.0, 0])
                acc[0] += float(r["gross"][i] - r["fee"][i] + r["funding"][i])
                acc[1] += float(r["gross"][i])
                acc[2] += float(r["fee"][i])
                acc[3] += float(r["funding"][i])
                acc[4] += 1
            if a.get("out_trades"):
                for i in range(len(r["fill_in"])):
                    trade_rows.append((cname, ji, jb["y"], "+".join(jb["x"]), int(r["dir"][i]), int(r["fill_in"][i]), int(r["fill_x"][i]), int(r["fill_out"][i]),
                                       float(r["gross"][i]), float(r["fee"][i]), float(r["funding"][i]), float(r["gross"][i] - r["fee"][i] + r["funding"][i])))
    if a["replay"]:
        print("replay: сделок %d, оценка не получена (NaN) %d, max|d gross| %.3e, max|d fee| %.3e, max|d funding| %.3e, max|d beta| %.3e" % (
            cmp_max["n"], cmp_max["nan"], cmp_max["gross"], cmp_max["fee"], cmp_max["funding"], cmp_max["beta"]))
        return cmp_max
    with open(a["out"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("cell,job,y,x,day,net_bps,gross_bps,fee_bps,funding_bps,trades\n")
        for k in sorted(rows, key=lambda k: (k[0], k[1], k[4])):
            v = rows[k]
            fh.write("%s,%d,%s,%s,%d,%r,%r,%r,%r,%d\n" % (k[0], k[1], k[2], k[3], k[4], v[0], v[1], v[2], v[3], v[4]))
    if a.get("out_trades"):
        with open(a["out_trades"], "w", encoding="utf-8", newline="\n") as fh:
            fh.write("cell,job,y,x,dir,fill_in,fill_x,fill_out,gross_bps,fee_bps,funding_bps,net_bps\n")
            for r in trade_rows:
                fh.write("%s,%d,%s,%s,%d,%d,%d,%d,%r,%r,%r,%r\n" % r)
    n = sum(v[4] for v in rows.values())
    print("randentry: строк (клетка, задание, сутки) %d, сделок %d, seed %d" % (len(rows), n, a["seed"]))
    return rows


def selftest():
    import tempfile, struct
    rng = np.random.default_rng(1)
    n = 3 * MIN_DAY
    first = 2465 * MIN_DAY
    cy = 100 * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
    cx = 50 * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
    oy, ox = np.concatenate([[cy[0]], cy[:-1]]), np.concatenate([[cx[0]], cx[:-1]])
    y, x = (first, oy, cy, np.zeros(0, np.int64), np.zeros(0)), (first, ox, cx, np.zeros(0, np.int64), np.zeros(0))
    job = Job(y, [x], 2466, 2468, 1000)
    assert job.ok
    # β против прямого МНК на окне
    s = np.array([1500, 2000, 2500])
    beta, good = job.window_beta(s, 1000, 500, None)
    for i, si in enumerate(s):
        w = slice(si + 1 - 1000, si + 1)
        ref = np.polyfit(job.lx[w], job.ly[w], 1)[0]
        assert abs(beta[i] - ref) < 1e-9, (beta[i], ref)
    # оценка одной сделки руками
    fi, fo = np.array([1500]), np.array([1600])
    g, f, fu = value_trades(job, fi, fi.copy(), fo, np.array([1.0]), np.array([0.7]))
    q0, qx = 1.0 / job.oy[1500], -0.7 / job.ox[0][1500]
    g_ref = (q0 * (job.oy[1600] - job.oy[1500]) + qx * (job.ox[0][1600] - job.ox[0][1500])) * 1e4
    f_ref = abs(q0) * (job.oy[1500] + job.oy[1600]) * FEE + abs(qx) * (job.ox[0][1500] + job.ox[0][1600]) * FEE
    assert abs(g[0] - g_ref) < 1e-9 and abs(f[0] - f_ref) < 1e-9 and fu[0] == 0.0
    assert abs(f[0] - FEE * (2 + 2 * 0.7 * 1.0)) < 0.2     # ~ 3,15 x (1+1) на Y + 3,15 x (1+1) x 0,7 на X (цены меняются мало)
    # фандинг: выплата строго между входом и выходом, Y лонг платит при положительной ставке
    ev = np.array([1500 + 10, 1500 + 50, 1600], np.int64) + job.lo
    job.fy = (ev, np.array([1e-4, 2e-4, 5e-4]))
    g2, f2, fu2 = value_trades(job, fi, fi.copy(), fo, np.array([1.0]), np.array([0.7]))
    exp = -(q0 * job.oy[1510] * 1e-4 + q0 * job.oy[1550] * 2e-4) * 1e4
    assert abs(fu2[0] - exp) < 1e-9, (fu2, exp)
    # симуляция детерминирована и даёт ожидание около -комиссии
    job.fy = (np.zeros(0, np.int64), np.zeros(0))
    cell = dict(window=1000, min_obs=500, beta_fixed=None, max_hold=100)
    holds = np.array([5, 10, 30, 60])
    r1 = simulate_job(job, cell, 400, holds, 0, np.random.default_rng([37, 0, 0]), True)
    r2 = simulate_job(job, cell, 400, holds, 0, np.random.default_rng([37, 0, 0]), True)
    assert np.array_equal(r1["fill_in"], r2["fill_in"]) and np.array_equal(r1["gross"], r2["gross"])
    net = r1["gross"] - r1["fee"]
    assert abs(r1["gross"].mean()) < 4 * r1["gross"].std() / math.sqrt(len(net)), r1["gross"].mean()
    assert r1["fee"].mean() > 6.0 and (r1["fill_in"] >= 2466 * MIN_DAY + 1).all()
    print("selftest ok: средний gross %.3f, fee %.3f bps" % (r1["gross"].mean(), r1["fee"].mean()))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
        raise SystemExit(0)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["pair", "cross", "basket", "funding-pp", "funding-ps"])
    ap.add_argument("--panels", required=True)
    ap.add_argument("--panels-b")
    ap.add_argument("--funding-y")
    ap.add_argument("--funding-x")
    ap.add_argument("--cells", required=True)
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--real-trades", required=True)
    ap.add_argument("--out")
    ap.add_argument("--out-trades")
    ap.add_argument("--delay-x", type=int, default=0)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--replay", action="store_true")
    args = vars(ap.parse_args())
    if not args["replay"] and not args["out"]:
        raise SystemExit("--out обязателен без --replay")
    run(args)
