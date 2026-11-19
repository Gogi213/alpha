#!/usr/bin/env python3
"""П-42 §13.32 (TK-625): драйвер счёта на srv. Подкоманды (порядок — п. 10 протокола): gates1, coindays, eng, ep <окно>, epall, g4x, geq, g3, counts, step1, cellsall, g8, select.
Запускается юнитом (`tk625_go.sh`), не с ПК. Вселенные (`--uni`): o566 (старые 566, деревья TK-599, дампы и базы TK-604 — только чтение) и h775 (честная вселенная TK-621: 775 монет, `t24=12`).
Окна — только w0 / wa / wb / wc (`wd` и даты после 13.11 — отказ). Выходы — `/data/p42-tk625/…`. P&L печатают только подкоманды после записи протокола (`step1`, `cellsall`, `g8`, `select`); `counts` P&L не читает.
Состояние на 19.11 (передача дел v1 → v2): реализованы gates1, coindays, eng, ep, epall, g4x, geq, g3, counts; step1 / cellsall / g8 / select — после протокола §13.32 и PASS критика.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402  (пути TK-604 — только чтение)
import tk604_stats as st  # noqa: E402
import tk625_feat as f5  # noqa: E402

ROOT = "/data/p42-tk625"
SIDES = st.SIDES
SIDE_NAME = r4.SIDE_NAME
ENG = r4.ENG
VID = r4.VID
BASE = r4.BASE
B0P0 = r4.B0P0
say = r4.say
write_json = r4.write_json
sha256 = r4.sha256
GuardError = st.GuardError
TICK_STORE = ["--tick-store", "/data/p42-tk599/tick-store", "--tick-store-cap-gb", "3", "--tick-store-min-free-gb", "5"]
M_TRIAL = "10"                                  # главное m = 1 NATR (первый тейк T_low); 05 и 20 — соседи для плато
CELL_Q = (0.15, 0.30)                           # доли исключённых среди сделок со значением, из которых берётся порог c (п. 4 протокола)
_C = {}


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


class Uni:
    """o566 — старые 566 монет без порога оборота (деревья TK-599, дампы и базы TK-604, только чтение); h775 — честная вселенная TK-621 (775 монет, `t24=12`, деревья TK-621)."""

    def __init__(self, name):
        self.name = name
        if name == "o566":
            self.coins_file, self.extra, self.tree = r4.SYMS, "", "/data/p42-tk599"
        elif name == "h775":
            self.coins_file, self.extra, self.tree = "/data/p42-tk621/universe/symbols-all.txt", " t24=12", "/data/p42-tk621"
        else:
            raise GuardError("вселенная %r неизвестна" % name)

    def coins(self):
        key = ("coins", self.name)
        if key not in _C:
            _C[key] = sorted(x for x in open(self.coins_file, encoding="utf-8").read().replace("\n", ",").split(",") if x)
        return _C[key]

    def wdir(self, w):
        if w == "w0":
            return "/data/p42-tk604/w0" if self.name == "o566" else os.path.join(ROOT, "h775", "w0")
        return os.path.join(self.tree, w)

    def eng(self, w):
        return r4.p("eng", w) if self.name == "o566" else os.path.join(ROOT, "h775", "eng", w)

    def base(self, w, side):
        return r4.p("base", w, VID[side]) if self.name == "o566" else os.path.join(ROOT, "h775", "base", w, VID[side])

    def sub(self, name):
        return name if self.name == "o566" else name + "-" + self.name

    def cells_dir(self, w, side, cid):
        return os.path.join(ROOT, self.sub("cells"), "%s-%s" % (w, side), cid)


def uni_of(a):
    return Uni(getattr(a, "uni", None) or "o566")


# ---- ворота данных ------------------------------------------------------------------------------------------------------------

def cmd_gates1(a):
    """G1: бинарь, монеты = записанным; sha256 баз вселенной (то же, что читаем): `*-trades.csv` баз на wa/wb/wc, при повторе — без изменений."""
    u = uni_of(a)
    bad, got = [], {"bin_sha": sha256(r4.BIN)}
    got["syms_sha"] = sha256(u.coins_file)
    if u.name == "o566" and got["syms_sha"] != r4.SYMS_SHA:
        bad.append("syms_sha: %s ≠ %s" % (got["syms_sha"], r4.SYMS_SHA))
    if got["bin_sha"] != r4.BIN_SHA:
        bad.append("bin_sha: %s ≠ %s" % (got["bin_sha"], r4.BIN_SHA))
    got["base_trades"] = {}
    for w in st.TRADE_WINDOWS:
        for s in SIDES:
            f = os.path.join(u.base(w.name, s), ENG[s] + "-trades.csv")
            if os.path.exists(f):
                got["base_trades"]["%s/%s" % (w.name, s)] = sha256(f)
    path = q("gates", "g1-%s.json" % u.name)
    if os.path.exists(path):
        prev = json.load(open(path, encoding="utf-8"))["got"]["base_trades"]
        if prev and prev != got["base_trades"]:
            bad.append("sha256 баз изменились между запусками")
    write_json(path, dict(got=got, bad=bad))
    say("G1 %s" % u.name, "FAIL " + "; ".join(bad) if bad else "ok: бинарь = записанному; баз с файлами %d из 6; sha256 записаны" % len(got["base_trades"]))
    if bad:
        sys.exit(1)


def cmd_coindays(a):
    """Наличие файлов сделок монеты по суткам 17.06–13.11 (объединение деревьев wa/wb/wc, размер > 0) — для пропусков в окне признака."""
    u = uni_of(a)
    d0 = datetime.date(2026, 6, 17)
    n = (st.LAST_DAY - d0).days + 1
    out = {}
    for s in u.coins():
        pres = np.zeros(n, dtype=bool)
        for w in st.TRADE_WINDOWS:
            root = os.path.join(u.wdir(w.name), "aggtrades", s)
            if not os.path.isdir(root):
                continue
            for fn in os.listdir(root):
                try:
                    d = datetime.date.fromisoformat(fn.rsplit("-aggTrades-", 1)[1][:10])
                except (IndexError, ValueError):
                    continue
                i = (d - d0).days
                if 0 <= i < n and os.path.getsize(os.path.join(root, fn)) > 0:
                    pres[i] = True
        out[s] = "".join("1" if x else "0" for x in pres)
    write_json(q("res", "coindays.json" if u.name == "o566" else "coindays-%s.json" % u.name), out)
    say("coindays %s: монет %d, суток %d, монет с пропусками внутри диапазона %d, без данных %d" % (
        u.name, len(out), n, sum(1 for v in out.values() if "1" in v and "0" in v[v.index("1"):v.rindex("1")]), sum(1 for v in out.values() if "1" not in v)))


def coin_days(u):
    key = ("cd", u.name)
    if key not in _C:
        raw = json.load(open(q("res", "coindays.json" if u.name == "o566" else "coindays-%s.json" % u.name), encoding="utf-8"))
        _C[key] = {s: np.array([c == "1" for c in v], dtype=bool) for s, v in raw.items()}
    return _C[key]


# ---- движок (только h775: дампы и базы; o566 — готовое TK-604) ---------------------------------------------------------------

def tick(u, w, variants, out, env, log, ftab=None):
    d = u.wdir(w.name)
    os.makedirs(out, exist_ok=True)
    vf = out + ".variants.txt"
    with open(vf, "w", encoding="utf-8") as f:
        f.write("".join("%s %s\n" % v for v in variants))
    cmd = [r4.BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf, "--symbols", open(u.coins_file, encoding="utf-8").read().strip(),
           "--out", out, "--threads", str(r4.THREADS)] + TICK_STORE
    if ftab:
        cmd += ["--ftab", ftab]
    e = {"P42_T0": w.t0}
    e.update(env)
    r4.run(cmd, e, log)


def cmd_eng(a):
    """Дампы `b0p0` (U0) и базы `L_base` / `S_base` (с дампом кандидатов) вселенной h775 на окнах w0 (только дамп) / wa / wb / wc."""
    u = uni_of(a)
    if u.name == "o566":
        raise GuardError("o566: дампы и базы готовы (TK-604), не пересчитываются")
    w = r4.win(a.window)
    st.check_dates(w.t0, w.hi)
    days = "14:29" if w.name == "w0" else "14:73"
    out = u.eng(w.name)
    if not os.path.exists(out + "/DONE"):
        tick(u, w, [("b0p0", B0P0)], out, {"P42_SIGSX_ONLY": "1", "P42_SIGSX": days}, q("log", "eng-h775-%s.log" % w.name))
        open(out + "/DONE", "w").close()
    if w.name != "w0":
        out = os.path.dirname(u.base(w.name, "L"))
        if not os.path.exists(out + "/DONE"):
            tick(u, w, [(VID[s], BASE[s] + u.extra) for s in SIDES], out, {"P42_SIGSX": days}, q("log", "base-h775-%s.log" % w.name))
            open(out + "/DONE", "w").close()
    say("eng %s %s готово" % (u.name, w.name))


def cmd_engall(a):
    for name in st.ALLOWED_WINDOWS:
        a.window = name
        cmd_eng(a)


# ---- события ------------------------------------------------------------------------------------------------------------------

def ev_path(u, w):
    return q("ev", w + ".csv") if u.name == "o566" else q("ev-h775", w + ".csv")


def cmd_ep(a):
    u = uni_of(a)
    w = r4.win(a.window)
    st.check_dates(w.t0, w.hi)
    hi = "2026-07-02" if w.name == "w0" else w.hi
    out = ev_path(u, w.name)
    d = u.wdir(w.name)
    env = {"SIGFEAT_BASE_DATE": w.t0, "SIGFEAT_DAYS": "80"}
    r4.run([sys.executable, os.path.join(HERE, "tk625_events.py"), "--t0", w.t0, "--eng", u.eng(w.name), "--cache", d + "/cache", "--trades", d + "/aggtrades",
            "--lo", w.lo, "--hi", hi, "--out", out, "--procs", str(r4.THREADS)], env, q("log", "ev-%s-%s.log" % (u.name, w.name)))
    say("ev %s %s: %s" % (u.name, w.name, open(out + ".stats.json", encoding="utf-8").read().replace("\n", " ")))


def cmd_epall(a):
    for name in st.ALLOWED_WINDOWS:
        a.window = name
        cmd_ep(a)


def events_all(u):
    """(все эпизоды склейки, пересечения без дублей)."""
    key = ("ev", u.name)
    if key not in _C:
        per = {w.name: f5.read_events_csv(ev_path(u, w.name), w.t0_ms) for w in st.HIST_WINDOWS}
        allev = f5.splice_events(per)
        _C[key] = (allev, f5.dedup_crossings(allev))
    return _C[key]


def history(u, side):
    key = ("hist", u.name, side)
    if key not in _C:
        _, cr = events_all(u)
        m = cr.side == SIDE_NAME[side]
        _C[key] = f5.History(cr.take(m), coin_days(u), u.coins())
    return _C[key]


def feats_at(u, side, sym, t_ms):
    return f5.features(history(u, side), sym, t_ms)


# ---- кандидаты и ключи сделок вселенной ---------------------------------------------------------------------------------------

def cand_stream(u, side, wname):
    """Все строки дампа базы окна (включая разгон) — ключи таблицы `--ftab`: монета, мс от T0 окна, абсолютное время."""
    w = r4.WINS[wname]
    sym, rel = tf.read_sigsx_keys(os.path.join(u.base(wname, side), ENG[side] + "-sigsx.csv"))
    return sym, rel, rel + w.t0_ms


def cand_arrays(u, side):
    """Кандидаты стороны в зачётных диапазонах wa/wb/wc: монета, абсолютное время сигнала, мс от T0 окна, окно."""
    key = ("cand", u.name, side)
    if key in _C:
        return _C[key]
    S, A, R, W = [], [], [], []
    for w in st.TRADE_WINDOWS:
        sym, rel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
        ab = rel + w.t0_ms
        m = tf.in_range(ab, w)
        S.append(sym[m]); A.append(ab[m]); R.append(rel[m]); W.append(np.array([w.name] * int(m.sum()), dtype=object))
    out = tuple(np.concatenate(x) for x in (S, A, R, W))
    st.check_abs_ms(out[1], "время кандидата")
    _C[key] = out
    return out


def trade_keys(u, side):
    """Ключи сделок базы: сутки входа, монета, абсолютное время сигнала (gross/fees/net не читаются)."""
    day, sym, key = [], [], []
    for w in st.TRADE_WINDOWS:
        with open(os.path.join(u.base(w.name, side), ENG[side] + "-trades.csv"), encoding="utf-8", newline="") as f:
            rows = [(r["sym"], int(r["sig_ms"]), int(r["entry_ms"])) for r in csv.DictReader(f)]
        e = np.array([r[2] for r in rows], dtype=np.int64) + w.t0_ms
        m = tf.in_range(e, w)
        day.append(tf.abs_to_day(e[m]))
        sym.append(np.array([r[0] for r in rows], dtype=object)[m])
        key.append((np.array([r[1] for r in rows], dtype=np.int64) + w.t0_ms)[m])
    return np.concatenate(day), np.concatenate(sym), np.concatenate(key)


# ---- ворота событий -----------------------------------------------------------------------------------------------------------

def read_ev_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return {(r["side"], r["sym"], int(r["level"]), int(r["sig_ms"])): r for r in csv.DictReader(f)}


def cmd_g4x(a):
    """Согласие с таблицами эпизодов TK-604 (только o566): ключи (сторона, монета, уровень, sig) равны по окнам; `indep` равны; метка TK-604 y = 1 ⇒ пересечение найдено."""
    u = Uni("o566")
    bad, res = [], {}
    for w in st.HIST_WINDOWS:
        old = {(r["side"], r["sym"], int(r["level"]), int(r["sig_ms"])): r for r in tf.read_episodes_csv(r4.p("ep", w.name + ".csv"))}
        new = read_ev_rows(ev_path(u, w.name))
        both = set(old) & set(new)
        y1 = [k for k in both if old[k]["y"] == "1"]
        res[w.name] = dict(old=len(old), new=len(new), only_old=len(set(old) - set(new)), only_new=len(set(new) - set(old)),
                           indep_diff=sum(1 for k in both if old[k]["indep"] != new[k]["indep"]), y1=len(y1),
                           y1_without_cross=sum(1 for k in y1 if new[k]["cross_ms"] == ""), y1_not_ok_ev=sum(1 for k in y1 if new[k]["ok_ev"] != "1"),
                           ok_ev=sum(1 for r in new.values() if r["ok_ev"] == "1"), crossed=sum(1 for r in new.values() if r["cross_ms"] != ""))
        if res[w.name]["only_old"] or res[w.name]["only_new"] or res[w.name]["indep_diff"] or res[w.name]["y1_without_cross"]:
            bad.append("%s: %s" % (w.name, res[w.name]))
    write_json(q("gates", "g4x.json"), dict(res=res, bad=bad))
    for k, v in res.items():
        say("G4x", k, json.dumps(v, ensure_ascii=False))
    say("G4x", "FAIL " + "; ".join(bad) if bad else "ok: ключи эпизодов = TK-604, indep равны, каждая метка y = 1 имеет пересечение")
    if bad:
        sys.exit(1)


DELISTED_CHANGED = {"STGUSDT", "1000000BOBUSDT", "EPICUSDT", "MITOUSDT", "PTBUSDT", "PUMPBTCUSDT", "VELODROMEUSDT"}      # TK-621 §2: кэши с добавленными плоскими днями


def cmd_geq(a):
    """h775: события 566 старых монет в честной вселенной = событиям o566 по строкам (кроме 7 монет TK-621 §2 с добавленными плоскими днями — их расхождения печатаются);
    байтовое равенство `L_base` / `S_base` h775 с `out/all_t24` TK-621 — по sha256 файлов сделок."""
    uo, uh = Uni("o566"), Uni("h775")
    old = set(uo.coins())
    bad, res = [], {}
    for w in st.HIST_WINDOWS:
        A, B = read_ev_rows(ev_path(uo, w.name)), read_ev_rows(ev_path(uh, w.name))
        Bo = {k: v for k, v in B.items() if k[1] in old}
        diff_coins = {}
        for k in set(A) ^ set(Bo):
            diff_coins[k[1]] = diff_coins.get(k[1], 0) + 1
        for k in set(A) & set(Bo):
            if A[k] != Bo[k]:
                diff_coins[k[1]] = diff_coins.get(k[1], 0) + 1
        unexpl = {c: n for c, n in diff_coins.items() if c not in DELISTED_CHANGED}
        res[w.name] = dict(o566=len(A), h775_on_old=len(Bo), h775_total=len(B), diff_coins=diff_coins, unexplained=unexpl)
        if unexpl:
            bad.append("%s: %s" % (w.name, unexpl))
    eq = {}
    for w in st.TRADE_WINDOWS:
        for s in SIDES:
            mine = os.path.join(uh.base(w.name, s), ENG[s] + "-trades.csv")
            theirs = os.path.join("/data/p42-tk621/out/all_t24", w.name, VID[s], ENG[s] + "-trades.csv")
            eq["%s/%s" % (w.name, s)] = bool(os.path.exists(mine) and os.path.exists(theirs) and sha256(mine) == sha256(theirs))
    if not all(eq.values()):
        bad.append("базы h775 ≠ out/all_t24 TK-621: %s" % {k: v for k, v in eq.items() if not v})
    write_json(q("gates", "geq.json"), dict(res=res, bases_equal_tk621=eq, bad=bad))
    for k, v in res.items():
        say("Geq", k, json.dumps(v, ensure_ascii=False))
    say("Geq", "FAIL " + "; ".join(bad) if bad else "ok: события старых монет в h775 = o566 (расхождения только у 7 монет TK-621 §2), базы h775 = out/all_t24 TK-621 побайтно (6 из 6)")
    if bad:
        sys.exit(1)


def brute_counts(cr, side, sym, t, d_days, m, cls, close_l_ms=f5.L_MS):
    """Независимый пересчёт: перебор событий монеты (без searchsorted)."""
    n = 0
    for i in np.nonzero((cr.side == side) & (cr.sym == sym))[0]:
        if cr.code[m][i] != cls:
            continue
        if cr.tres[m][i] + close_l_ms <= t and cr.cross[i] > t - d_days * f5.DAY_MS:
            n += 1
    return n


def garbage_after(cr, t, seed):
    """Копия событий, в которой всё, что закрывается позже t, заменено мусором (случайные коды и времена после t)."""
    rng = np.random.default_rng(seed)
    g = cr.take(np.ones(len(cr), dtype=bool))
    for m in f5.MS_NAMES:
        late = g.tres[m] + f5.L_MS > t
        k = int(late.sum())
        g.code[m] = g.code[m].copy()
        g.code[m][late] = rng.choice(np.array(["o", "f", "s"], dtype=object), size=k)
        g.tres[m] = g.tres[m].copy()
        g.tres[m][late] = t + rng.integers(1, 10 ** 9, size=k)
    return g


def cmd_g3(a):
    """G3 причинность: (а) вектор = перебор; (б) мусор вместо событий, закрывающихся после t, признак не меняет; (в) ≥ 50 кандидатов на сторону, у швов 01.07 / 26.08 / 03.10 ≥ 10."""
    u = uni_of(a)
    rng = np.random.default_rng(625)
    _, cr = events_all(u)
    bad, res, picks = [], {}, 0
    seams = [datetime.datetime(2026, m, d, tzinfo=datetime.timezone.utc).timestamp() * 1000 for m, d in ((7, 1), (8, 26), (10, 3))]
    for side in SIDES:
        sym, ab, _rel, _w = cand_arrays(u, side)
        idx = []
        for sm in seams:
            near = np.nonzero(np.abs(ab - sm) <= 3 * st.DAY_MS)[0]
            idx += list(rng.choice(near, size=min(10, len(near)), replace=False))
        idx += list(rng.choice(len(ab), size=30, replace=False))
        hv = history(u, side)
        for i in idx:
            t, s = int(ab[i]), sym[i]
            f0 = f5.features(hv, np.array([s], dtype=object), np.array([t]))
            for (m, d), (o, f, st_, v) in f0.items():
                for cls, got in (("o", o), ("f", f), ("s", st_)):
                    want = brute_counts(cr, SIDE_NAME[side], s, t, d, m, cls)
                    if int(got[0]) != want:
                        bad.append("%s %s t=%d (m=%s D=%d %s): вектор %d ≠ перебор %d" % (side, s, t, m, d, cls, int(got[0]), want))
            gar = garbage_after(cr, t, seed=int(t % 2**31))
            hg = f5.History(gar.take(gar.side == SIDE_NAME[side]), coin_days(u), u.coins())
            f1 = f5.features(hg, np.array([s], dtype=object), np.array([t]))
            for k_, v0 in f0.items():
                if any(int(x[0]) != int(y[0]) for x, y in zip(v0, f1[k_])):
                    bad.append("%s %s t=%d %s: признак изменился при замене закрытых позже t событий" % (side, s, t, k_))
            picks += 1
        res[side] = len(idx)
    write_json(q("gates", "g3-%s.json" % u.name), dict(res=res, picks=picks, bad=bad[:20]))
    say("G3 %s" % u.name, "FAIL %d: %s" % (len(bad), bad[:5]) if bad else "ok: %d кандидатов (шов 01.07 / 26.08 / 03.10 ≥ 10 на сторону), вектор = перебор, мусор после t признак не меняет" % picks)
    if bad:
        sys.exit(1)


# ---- подсчёты без P&L: покрытие, распределение, пороги клеток -----------------------------------------------------------------

def cell_id(m, d, qq):
    return "m%s_d%d_q%02d" % (m, d, int(round(qq * 100)))


def build_cells(x_by_col, c_map=None):
    """Клетки стороны: пробные — m = 1 NATR (`M_TRIAL`) × D × доля q; соседи для плато — m = 0,5 и 2 NATR при тех же D и q. Порог c — по распределению значений на сделках базы (п. 4), без P&L;
    `c_map` {cid: c} — готовые пороги (вселенная повтора). Порядок для дублей: пробные (D по возрастанию, q по возрастанию), затем соседи."""
    out, seen = [], {}
    order = [(M_TRIAL, d, qq, True) for d in f5.DS for qq in CELL_Q] + [(m, d, qq, False) for m in f5.MS_NAMES if m != M_TRIAL for d in f5.DS for qq in CELL_Q]
    for m, d, qq, trial in order:
        col = f5.col_name(m, d)
        cid = cell_id(m, d, qq)
        c = f5.threshold_for(x_by_col[col], qq) if c_map is None else c_map.get(cid)
        mask = f5.excluded(x_by_col[col], c) if c is not None else np.zeros(len(x_by_col[col]), dtype=bool)
        key = np.packbits(mask).tobytes() + len(mask).to_bytes(8, "little")
        dup = seen.get(key) if c is not None else "нет порога"
        if c is not None and dup is None:
            seen[key] = cid
        out.append(dict(cid=cid, m=m, D=d, q=qq, col=col, c=c, trial=trial, dup_of=dup, E=int(mask.sum()), mask=mask))
    return out


def trade_feats(u, side):
    """Значения признаков на сделках базы (ключ — монета, абсолютное время сигнала; P&L не читается)."""
    day, sym, key = trade_keys(u, side)
    f = feats_at(u, side, sym, key)
    return day, sym, key, f5.ok_matrix(f), f


def cmd_counts(a):
    """Покрытие, распределение, пороги и множества клеток; таблицы `--ftab` — без P&L (читаются только ключи сделок: монета, время сигнала, сутки входа).
    `--cmap <файл json>` — готовые пороги клеток {сторона: {cid: c}} (вселенная повтора); без него пороги считаются по распределению этой вселенной."""
    u = uni_of(a)
    allev, cr = events_all(u)
    cmaps = json.load(open(a.cmap, encoding="utf-8")) if getattr(a, "cmap", None) else None
    say("%s события: эпизодов %d (пригодных %d), пересечений без дублей %d" % (u.name, len(allev), int((allev.ok == 1).sum()), len(cr)))
    for side in SIDES:
        for w in st.TRADE_WINDOWS:                         # таблицы --ftab на все строки дампа окна (в том числе разгон)
            s_, r_, a_ = cand_stream(u, side, w.name)
            cols = f5.ok_matrix(feats_at(u, side, s_, a_))
            n = f5.write_ftab(q(u.sub("ftab"), "%s-%s.csv" % (w.name, side)), s_, r_, cols)
            say("ftab %s %s-%s: %d строк (дамп %d)" % (u.name, w.name, side, n, len(s_)))
        day, sym, key, x, f = trade_feats(u, side)
        mon = st.MONTH_OF_DAY[day]
        r = dict(n_trades=int(len(day)), coverage={}, dist={}, cells=[], fail_alongside={})
        for m in f5.MS_NAMES:
            for d in f5.DS:
                col = f5.col_name(m, d)
                v = x[col]
                have = ~np.isnan(v)
                r["coverage"][col] = [round(float(have[mon == k].mean()), 3) if (mon == k).any() else None for k in range(len(st.MONTH_LEN))]
                vv = v[have]
                o, fl, sl, valid = f[(m, d)]
                r["dist"][col] = dict(n=int(have.sum()), mean=round(float(vv.mean()), 3) if len(vv) else None, max=int(vv.max()) if len(vv) else None,
                                      buckets={"0": int((vv == 0).sum()), "1": int((vv == 1).sum()), "2-3": int(((vv >= 2) & (vv <= 3)).sum()), "4+": int((vv >= 4).sum())},
                                      F={str(c): round(float((vv < c).mean()), 3) for c in range(1, 8)} if len(vv) else {})
                r["fail_alongside"][col] = dict(mean_fail=round(float(fl[have].mean()), 3) if have.any() else None, mean_stall=round(float(sl[have].mean()), 3) if have.any() else None)
        cells = build_cells(x, None if cmaps is None else cmaps[side])
        ids = [c["cid"] for c in cells if c["dup_of"] is None]
        for c in cells:
            cov = int((~np.isnan(x[c["col"]])).sum())
            r["cells"].append(dict(cid=c["cid"], m=c["m"], D=c["D"], q=c["q"], c=c["c"], trial=c["trial"], dup_of=c["dup_of"], E=c["E"], N_cov=cov,
                                   f_cov=round(c["E"] / cov, 3) if cov else None, f_all=round(c["E"] / len(day), 3)))
        jm = np.array([[st.jaccard(a_["mask"], b_["mask"]) for b_ in cells if b_["cid"] in ids] for a_ in cells if a_["cid"] in ids])
        r["jaccard_ids"], r["jaccard"] = ids, np.round(jm, 3).tolist()
        write_json(q("res", "counts-%s-%s.json" % (u.name, side)), r)
        say("== %s %s: сделок базы %d" % (u.name, side, len(day)))
        for col, cv in r["coverage"].items():
            d_ = r["dist"][col]
            say("   %s: покрытие по месяцам %s; со значением %d, среднее %s, макс %s, корзины %s, F(x<c) %s; среднее fail %s stall %s" % (
                col, cv, d_["n"], d_["mean"], d_["max"], d_["buckets"], d_["F"], r["fail_alongside"][col]["mean_fail"], r["fail_alongside"][col]["mean_stall"]))
        for c in r["cells"]:
            say("   клетка %-12s %s c=%s E=%d N_cov=%d f_cov=%s f_all=%s%s" % (c["cid"], "пробная" if c["trial"] else "сосед", c["c"], c["E"], c["N_cov"], c["f_cov"], c["f_all"],
                                                                                " (дубль %s)" % c["dup_of"] if c["dup_of"] else ""))
    say("counts %s готово — запись в JOURNAL до чтения P&L" % u.name)


COMMANDS = {"gates1": cmd_gates1, "coindays": cmd_coindays, "eng": cmd_eng, "engall": cmd_engall, "ep": cmd_ep, "epall": cmd_epall, "g4x": cmd_g4x, "geq": cmd_geq, "g3": cmd_g3,
            "counts": cmd_counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    ap.add_argument("window", nargs="?")
    ap.add_argument("--uni", default="o566", choices=("o566", "h775"))
    ap.add_argument("--cmap", default="")
    ap.add_argument("--code-sha", default="")
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
