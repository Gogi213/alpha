"""П-42 TK-587: порт `src/breakout/levels.rs` (пивоты, уровни, `pk`) и состояния запрета из `tick.rs::candidates` на Python.

Нужен для разбора отдельных сделок по сырым минуткам (`/data/klines/binance/<СИМВОЛ>/<день>.csv`): какие пивоты составили уровень,
какие часовые максимумы пивотами не стали и почему, какие уровни жили в момент сигнала. Порт построчный (целая арифметика e8, деление
с отсечением к нулю, как в Rust); сверка с движком — по полям `born`, `first_bar`, `members`, `level_px_e8` в `*-detail.jsonl`.
Не заменяет движок и в счёт не входит."""
import os
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

T0 = 1_788_220_800_000  # 2026-09-01 00:00 UTC, мс
MIN = 60_000
N_DAYS = 74
N_MIN = N_DAYS * 1440
KL = "/data/klines/binance"
SCALE = 10**8
GMT4 = 4 * 3600 * 1000

# параметры опорной клетки `ow` (meta.json: LevelCfg / SigCfg)
LV = dict(tf=60, k=2, d_pm=500, n=2, tau_pm=200, l_days=7, pk=True)
SG = dict(natr_p=30, natr_min_pm=400, t1h_u=50_000_000_000, track_pm=2000, e_pm=300, f_pm=100, r_min=15, ban=True, pb=True,
          fr_h_min=240, fr_k=120, fr_share=50, fr_b_pm=500)


def td(a, b):
    """Деление целых с отсечением к нулю (как `/` в Rust)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _dec(s, scale):
    return int((Decimal(s) * (10**scale)).to_integral_value(rounding="ROUND_DOWN"))


def load_min(sym):
    """Минутки монеты на сетке с 01.09: o,h,l,c (e8, int64; 0 — дыра) и qv (e5)."""
    a = {k: np.zeros(N_MIN, dtype=np.int64) for k in ("o", "h", "l", "c", "qv")}
    d0 = date(2026, 9, 1)
    for d in range(N_DAYS):
        f = f"{KL}/{sym}/{(d0 + timedelta(d)):%Y-%m-%d}.csv"
        if not os.path.exists(f):
            continue
        df = pd.read_csv(f, dtype=str)
        for _, r in df.iterrows():
            i = (int(r["open_time"]) - T0) // MIN
            if 0 <= i < N_MIN:
                a["o"][i] = _dec(r["open"], 8)
                a["h"][i] = _dec(r["high"], 8)
                a["l"][i] = _dec(r["low"], 8)
                a["c"][i] = _dec(r["close"], 8)
                a["qv"][i] = _dec(r["quote_volume"], 5)
    return a


def make_bars(m, tfm=60):
    nb = N_MIN // tfm
    b = {k: np.zeros(nb, dtype=np.int64) for k in ("o", "h", "l", "c")}
    b["ok"] = np.zeros(nb, dtype=bool)
    for i in range(nb):
        first = True
        for t in range(i * tfm, (i + 1) * tfm):
            if m["o"][t] == 0:
                continue
            if first:
                b["o"][i], b["h"][i], b["l"][i] = m["o"][t], m["h"][t], m["l"][t]
                first = False
            else:
                b["h"][i] = max(b["h"][i], m["h"][t])
                b["l"][i] = min(b["l"][i], m["l"][t])
            b["c"][i] = m["c"][t]
            b["ok"][i] = True
    return b


class Frame:
    def __init__(self, b, s):
        self.b, self.s = b, s

    def hi(self, i):
        return int(self.b["h"][i]) if self.s > 0 else -int(self.b["l"][i])

    def lo(self, i):
        return int(self.b["l"][i]) if self.s > 0 else -int(self.b["h"][i])

    def cl(self, i):
        return self.s * int(self.b["c"][i])


def group_ok(ps, tau_pm):
    n = len(ps)
    mean = td(sum(ps), n)
    ok = all(abs(x - mean) * 100_000 <= tau_pm * abs(mean) for x in ps)
    return ok, mean


def pk_violated(f, bars, frm, to, mean, tau_pm):
    lim = mean + td(abs(mean) * tau_pm, 100_000)
    return any(bars["ok"][bb] and (f.hi(bb) > lim or (bb > frm and f.cl(bb) > mean)) for bb in range(frm, to + 1))


def find_pivots(f, cfg=LV, diag=None):
    """→ [(active, bar, p)] по (active, bar). `diag` — список, куда пишутся все локальные максимумы ширины k и их судьба."""
    nb = len(f.b["ok"])
    k = cfg["k"]
    out = []
    for h in range(k, nb - k):
        if not all(f.b["ok"][j] for j in range(h - k, h + k + 1)):
            continue
        ph = f.hi(h)
        if any(f.hi(h - j) >= ph or f.hi(h + j) >= ph for j in range(1, k + 1)):
            continue
        thr = ph - td(abs(ph) * cfg["d_pm"], 100_000)
        status = "до конца данных отскока глубиной d не было"
        j = h + 1
        while j < nb:
            if f.b["ok"][j]:
                if f.cl(j) > ph:
                    status = f"отброшен: закрытие бара {j} выше пивота до глубины d"
                    break
                if f.lo(j) <= thr:
                    out.append((max(j, h + k), h, ph))
                    status = f"засчитан (отскок d на баре {j}; активен с бара {max(j, h + k)})"
                    break
            j += 1
        if diag is not None:
            diag.append(dict(bar=h, p=ph, status=status))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def build_levels(bars, s, cfg=LV, ev=None):
    """Порт `build_levels_tracked(.., track=true)`. Уровень: dict(born, end, segs[(мин, цена)], first_bar, mem[[(бар, цена)]], id).
    `ev` — список событий (минута, текст) для разбора."""
    f = Frame(bars, s)
    pivs = find_pivots(f, cfg)
    nb = len(bars["ok"])
    tfm = cfg["tf"]
    lwin = cfg["l_days"] * 1440
    levels, live, free = [], [], []
    pi = 0

    def log(t, x):
        if ev is not None:
            ev.append((t, x))

    for b in range(nb):
        now = (b + 1) * tfm
        free = [p for p in free if p[1] * tfm >= now - lwin]
        li = 0
        while li < len(live):
            lv = live[li]
            before = len(lv["members"])
            lv["members"] = [p for p in lv["members"] if p[1] * tfm >= now - lwin]
            id_ = lv["id"]
            if len(lv["members"]) < cfg["n"]:
                levels[id_]["end"] = now
                log(now, f"уровень {id_}: конец — членов < N после выпадения из окна L")
                live.pop(li)
                continue
            _, mean = group_ok([p[2] for p in lv["members"]], cfg["tau_pm"])
            if len(lv["members"]) != before:
                levels[id_]["segs"].append((now, abs(mean)))
                levels[id_]["mem"].append([(p[1], abs(p[2])) for p in lv["members"]])
                log(now, f"уровень {id_}: член выпал из окна L, цена {abs(mean)}")
                if cfg["pk"]:
                    frm = min(p[1] for p in lv["members"])
                    if pk_violated(f, bars, frm, b, mean, cfg["tau_pm"]):
                        levels[id_]["end"] = now
                        log(now, f"уровень {id_}: убит перепроверкой pk при смене версии")
                        live.pop(li)
                        continue
            if bars["ok"][b] and f.cl(b) > mean:
                levels[id_]["end"] = now
                log(now, f"уровень {id_}: конец — часовое закрытие бара {b} выше уровня {mean} (истинный пробой)")
                live.pop(li)
                continue
            li += 1
        while pi < len(pivs) and pivs[pi][0] == b:
            pv = pivs[pi]
            pi += 1
            joined = False
            for li in range(len(live)):
                cand = [p[2] for p in live[li]["members"]] + [pv[2]]
                ok, mean = group_ok(cand, cfg["tau_pm"])
                if ok:
                    id_ = live[li]["id"]
                    live[li]["members"].append(pv)
                    levels[id_]["segs"].append((now, abs(mean)))
                    levels[id_]["mem"].append([(p[1], abs(p[2])) for p in live[li]["members"]])
                    joined = True
                    log(now, f"уровень {id_}: присоединился пивот бар {pv[1]}, цена {abs(mean)}")
                    if cfg["pk"]:
                        frm = min(p[1] for p in live[li]["members"])
                        if pk_violated(f, bars, frm, b, mean, cfg["tau_pm"]):
                            levels[id_]["end"] = now
                            log(now, f"уровень {id_}: убит перепроверкой pk при присоединении")
                            live.pop(li)
                    break
            if joined:
                continue
            free.append(pv)
            order = [q for q in free if not (q[1] == pv[1] and q[2] == pv[2])]
            order.sort(key=lambda q: (abs(q[2] - pv[2]), -q[1]))
            grp = [pv]
            for q in order:
                if group_ok([p[2] for p in grp] + [q[2]], cfg["tau_pm"])[0]:
                    grp.append(q)
                else:
                    break
            if len(grp) < cfg["n"]:
                continue
            _, mean = group_ok([p[2] for p in grp], cfg["tau_pm"])
            first_bar = min(p[1] for p in grp)
            free = [p for p in free if not any(q[1] == p[1] and q[2] == p[2] for q in grp)]
            if cfg["pk"]:
                dead = pk_violated(f, bars, first_bar, b, mean, cfg["tau_pm"])
            else:
                dead = any(bars["ok"][bb] and f.cl(bb) > mean for bb in range(first_bar + 1, b + 1))
            if dead:
                log(now, f"группа пивотов {[p[1] for p in grp]} цена {abs(mean)} не родилась (прокол/закрытие за ценой с первого пивота)")
                continue
            levels.append(dict(born=now, end=10**9, segs=[(now, abs(mean))], first_bar=first_bar,
                               mem=[[(p[1], abs(p[2])) for p in grp]], id=len(levels)))
            live.append(dict(id=len(levels) - 1, members=grp))
            log(now, f"уровень {len(levels) - 1}: рождён, цена {abs(mean)}, пивоты (бар, цена) {[(p[1], abs(p[2])) for p in grp]}")
    return levels


def price_at(lv, t):
    p = lv["segs"][0][1]
    for mn, pr in lv["segs"]:
        if mn <= t:
            p = pr
    return p


def members_at(lv, t):
    mem = lv["mem"][0]
    for (mn, _), mm in zip(lv["segs"], lv["mem"]):
        if mn <= t:
            mem = mm
    return mem


def forbid_scan(m, lv, s, sg=SG, cfg=LV, upto=None):
    """Порт состояния запрета из `candidates()`: первая минута, в которой уровень запрещён (ban / pk / pb), и причина; действует со следующей.
    → (минута срабатывания, причина) или None. `upto` — не дальше этой минуты (включительно)."""
    end = min(lv["end"], N_MIN)
    if upto is not None:
        end = min(end, upto + 1)
    seg, price = 0, lv["segs"][0][1]
    pend = None
    for t in range(lv["born"], end):
        while seg + 1 < len(lv["segs"]) and lv["segs"][seg + 1][0] <= t:
            seg += 1
            price = lv["segs"][seg][1]
        if m["o"][t] == 0:
            continue
        lp = s * price
        hi, lo = (int(m["h"][t]), int(m["l"][t])) if s > 0 else (-int(m["l"][t]), -int(m["h"][t]))
        if pend is not None and t - pend > sg["r_min"]:
            pend = None
        if hi > lp and pend is None:
            pend = t
        if sg["ban"] and pend is not None:
            back = lp - td(abs(lp) * sg["f_pm"], 100_000)
            if s * int(m["c"][t]) < back:
                return t, "ложный пробой (закрытие минуты глубже f % за уровнем в течение r мин)"
        if cfg["pk"] and hi > lp + td(abs(lp) * cfg["tau_pm"], 100_000):
            return t, "pk (high минуты выше уровня больше чем на τ)"
        if sg["pb"] and hi >= lp:
            return t, "pb (сделка минуты на уровне или за ним)"
    return None


def natr_series(m, p=30):
    n = N_MIN
    trp = [0] * (n + 1)
    cnt = [0] * (n + 1)
    prevc = 0
    for t in range(n):
        tr = c = 0
        if m["o"][t] != 0:
            tr = int(m["h"][t] - m["l"][t])
            if prevc != 0:
                tr = max(tr, abs(int(m["h"][t]) - prevc), abs(int(m["l"][t]) - prevc))
            prevc = int(m["c"][t])
            c = 1
        trp[t + 1] = trp[t] + tr
        cnt[t + 1] = cnt[t] + c
    out = [-1] * n
    for t in range(p - 1, n):
        if m["o"][t] == 0:
            continue
        k = cnt[t + 1] - cnt[t + 1 - p]
        if k * 2 < p:
            continue
        out[t] = (trp[t + 1] - trp[t + 1 - p]) * SCALE // (k * int(m["c"][t]))
    return out
