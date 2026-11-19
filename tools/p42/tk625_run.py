#!/usr/bin/env python3
"""П-42 §13.32 (TK-625): драйвер счёта на srv. Подкоманды (порядок — п. 10 протокола): gates1, coindays, eng, ep <окно>, epall, g4x, geq, g3, counts, step1, cellsall, g8, select.
Запускается юнитом (`tk625_go.sh`), не с ПК. Вселенные (`--uni`): o566 (старые 566, деревья TK-599, дампы и базы TK-604 — только чтение) и h775 (честная вселенная TK-621: 775 монет, `t24=12`).
Окна — только w0 / wa / wb / wc (`wd` и даты после 13.11 — отказ). Выходы — `/data/p42-tk625/…`. P&L печатают только подкоманды после записи протокола (`step1`, `cellsall`, `g8`, `select`); `counts` P&L не читает.
Состояние на 19.11 (передача дел v1 → v2): реализованы gates1, coindays, eng, ep, epall, g4x, geq, g3, counts; step1 / cellsall / g8 / select — после протокола §13.32 и PASS критика.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime
import hashlib
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
import tk625_stats as s5  # noqa: E402

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


def code_id():
    """Отпечаток кода счёта: sha256 содержимого tk625_{events,feat,stats,run}.py (первые 12 знаков) — печатается рядом с каждым числом отчёта; коммит — тот, где эти блобы совпадают."""
    h = hashlib.sha256()
    for n in ("tk625_events.py", "tk625_feat.py", "tk625_run.py", "tk625_stats.py"):
        h.update(open(os.path.join(HERE, n), "rb").read())
    return h.hexdigest()[:12]


def stamp(d):
    d["def"], d["code_id"] = f5.DEF_TAG, code_id()
    return d


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


# движок: `a03c91df` (TK-599/604) или турбо-сборка `a112684a` TK-607 (принята §13.24.6, побайтно равна по выходу; `alpha-master` заменён 18.11)
BIN_SHA_OK = {r4.BIN_SHA, "ee28fd7b85eada7dd30ef96e3a91bba14df0489fb929c7a7e7163c8b72a49b53"}


# ---- ворота данных ------------------------------------------------------------------------------------------------------------

def cmd_gates1(a):
    """G1: бинарь, монеты = записанным; sha256 баз вселенной (то же, что читаем): `*-trades.csv` баз на wa/wb/wc, при повторе — без изменений."""
    u = uni_of(a)
    bad, got = [], {"bin_sha": sha256(r4.BIN)}
    got["syms_sha"] = sha256(u.coins_file)
    if u.name == "o566" and got["syms_sha"] != r4.SYMS_SHA:
        bad.append("syms_sha: %s ≠ %s" % (got["syms_sha"], r4.SYMS_SHA))
    if got["bin_sha"] not in BIN_SHA_OK:
        bad.append("bin_sha: %s ∉ %s" % (got["bin_sha"], sorted(BIN_SHA_OK)))
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
    """Согласие с таблицами эпизодов TK-604 (только o566): ключи (сторона, монета, уровень, sig) равны по окнам; `indep` равны; метка TK-604 y = 1 на событии с `ok_ev = 1` ⇒ пересечение найдено (события с NATR = 0 / без суток — не события признака, считаются отдельно)."""
    u = Uni("o566")
    bad, res = [], {}
    for w in st.HIST_WINDOWS:
        old = {(r["side"], r["sym"], int(r["level"]), int(r["sig_ms"])): r for r in tf.read_episodes_csv(r4.p("ep", w.name + ".csv"))}
        new = read_ev_rows(ev_path(u, w.name))
        both = set(old) & set(new)
        y1 = [k for k in both if old[k]["y"] == "1"]
        res[w.name] = dict(old=len(old), new=len(new), only_old=len(set(old) - set(new)), only_new=len(set(new) - set(old)),
                           indep_diff=sum(1 for k in both if old[k]["indep"] != new[k]["indep"]), y1=len(y1),
                           y1_without_cross=sum(1 for k in y1 if new[k]["cross_ms"] == "" and new[k]["ok_ev"] == "1"), y1_not_ok_ev=sum(1 for k in y1 if new[k]["ok_ev"] != "1"),
                           y1_not_ok_natr0=sum(1 for k in y1 if new[k]["ok_ev"] != "1" and new[k]["natr_e8"] == "0"),
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
        r = dict(n_trades=int(len(day)), coverage={}, dist={}, cells=[], fail_alongside={}, empty={})
        for d in f5.DS:                                    # пусто по причинам (определение def2: возраст листинга — отдельная причина)
            rs = history(u, side).reason(sym, key, d)
            r["empty"][d] = {name: int((rs == i).sum()) for i, name in enumerate(f5.REASONS)}
            say("   D=%d: сделок %d, значение определено %d; пусто: %s" % (d, len(rs), int((rs == 0).sum()), {k: v for k, v in r["empty"][d].items() if k != f5.REASONS[0]}))
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
        write_json(q("res", "counts-%s-%s.json" % (u.name, side)), stamp(r))
        say("== %s %s: сделок базы %d" % (u.name, side, len(day)))
        for col, cv in r["coverage"].items():
            d_ = r["dist"][col]
            say("   %s: покрытие по месяцам %s; со значением %d, среднее %s, макс %s, корзины %s, F(x<c) %s; среднее fail %s stall %s" % (
                col, cv, d_["n"], d_["mean"], d_["max"], d_["buckets"], d_["F"], r["fail_alongside"][col]["mean_fail"], r["fail_alongside"][col]["mean_stall"]))
        for c in r["cells"]:
            say("   клетка %-12s %s c=%s E=%d N_cov=%d f_cov=%s f_all=%s%s" % (c["cid"], "пробная" if c["trial"] else "сосед", c["c"], c["E"], c["N_cov"], c["f_cov"], c["f_all"],
                                                                                " (дубль %s)" % c["dup_of"] if c["dup_of"] else ""))
    say("counts %s готово — запись в JOURNAL до чтения P&L" % u.name)


# ---- после PASS критика: ступень 1, клетки, G8, выбор (читают P&L; запускать только после PASS §13.32) ------------------------------

def load_series(u, side, kind, cid=None):
    """Склейка сделок стороны вселенной: kind 'base' или 'cell' (клетка `cid`); охрана суммы daily = net — в `st.splice`."""
    tables, daily = {}, {}
    for w in st.TRADE_WINDOWS:
        d = u.base(w.name, side) if kind == "base" else u.cells_dir(w.name, side, cid)
        tables[w.name] = st.read_trades_csv(os.path.join(d, ENG[side] + "-trades.csv"))
        daily[w.name] = st.read_daily_csv(os.path.join(d, ENG[side] + "-daily.csv"))
    return st.splice(tables, u.coins(), daily=daily)


def base_feats(u, side, ser):
    return f5.ok_matrix(feats_at(u, side, np.array(ser.sym_name, dtype=object), ser.key_ms))


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items() if k != "mask"}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if o != o else float(o)
    return o


def cmd_step1(a):
    """Ступень 1 (п. 8): корзины значения n_ok на сделках баз × D × m — сделок, Σ $, $/сделку, Sharpe годовой, по месяцам, 90 % интервал; без фильтра и без выбора."""
    u = uni_of(a)
    res = {}
    for side in SIDES:
        ser = load_series(u, side, "base")
        x = base_feats(u, side, ser)
        r = dict(base=s5.slice_stats(ser.day, ser.val), cols={})
        for m in f5.MS_NAMES:
            for d in f5.DS:
                col = f5.col_name(m, d)
                r["cols"][col] = s5.step1_rows(ser, x[col])
        res[side] = r
        b = r["base"]
        say("== ступень 1 %s %s: сделок %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f" % (u.name, side, b["n"], b["sum"], b["mean"], b["sharpe"]))
        for m in f5.MS_NAMES:
            for d in f5.DS:
                col = f5.col_name(m, d)
                for name, v in r["cols"][col].items():
                    say("   %-9s %-12s: сделок %6d, Σ %9.0f $, %7.2f $/сделку, Sharpe %6.2f, по месяцам %s" % (
                        col, name, v["n"], v["sum"], v["mean"] if v["n"] else float("nan"), v["sharpe"], [(n_, round(s_)) for n_, s_ in v["months"]]))
    write_json(q("res", "step1-%s.json" % u.name), stamp(jsonable(res)))
    say("метка: def %s, код %s" % (f5.DEF_TAG, code_id()))
    say("ступень 1 %s готова" % u.name)


def cell_info(u, side):
    return json.load(open(q("res", "counts-%s-%s.json" % (u.name, side)), encoding="utf-8"))["cells"]


def cell_variants(u, side):
    return [(c["cid"], "%s%s fa=%s fo=lo fv=%d" % (BASE[side], u.extra, f5.col_name(c["m"], c["D"]), c["c"])) for c in cell_info(u, side) if c["dup_of"] is None and c["c"] is not None]


def cmd_cells(a):
    u = uni_of(a)
    w = r4.win(a.window)
    for side in SIDES:
        out = os.path.join(ROOT, u.sub("cells"), "%s-%s" % (w.name, side))
        if os.path.exists(out + "/DONE"):
            continue
        ft = q(u.sub("ftab"), "%s-%s.csv" % (w.name, side))
        tick(u, w, cell_variants(u, side), out, {}, q("log", "cells-%s-%s-%s.log" % (u.name, w.name, side)), ftab=ft)
        with open(out + "/DONE", "w", encoding="utf-8") as f:
            f.write("%s %s %s" % (f5.DEF_TAG, code_id(), sha256(ft)))
    say("cells %s %s готово" % (u.name, w.name))


def cmd_cellsall(a):
    for w in st.TRADE_WINDOWS:
        a.window = w.name
        cmd_cells(a)


def read_ftab_cols(path):
    """{(монета, мс от T0): [9 значений ok…, NaN — пусто]} таблицы `--ftab`."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[(r["sym"], int(r["sig_ms"]))] = [float(r[c]) if r[c] != "" else float("nan") for c in f5.COLS]
    return out


def cmd_g8(a):
    """G8: `no_key = 0` и `dropped` = независимому пересчёту по таблице (строки дампа базы со значением < c) — для всех прогонов клеток вселенной."""
    u = uni_of(a)
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            tab = read_ftab_cols(q(u.sub("ftab"), "%s-%s.csv" % (w.name, side)))
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            miss = sum(1 for k in dkeys if k not in tab)
            for c in cell_info(u, side):
                if c["dup_of"] is not None or c["c"] is None:
                    continue
                j = f5.COLS.index(f5.col_name(c["m"], c["D"]))
                exp_u = sum(1 for v in tab.values() if v[j] == v[j] and v[j] < c["c"])
                exp_m = sum(1 for k in dkeys if k in tab and tab[k][j] == tab[k][j] and tab[k][j] < c["c"])
                m = json.load(open(os.path.join(u.cells_dir(w.name, side, c["cid"]), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, c["cid"])] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d (уникальных) / %d (строк дампа), строк дампа без строки таблицы %d" % (w.name, side, c["cid"], fc, exp_u, exp_m, miss))
    write_json(q("gates", "g8-%s.json" % u.name), dict(res=res, bad=bad))
    say("G8 %s" % u.name, "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = пересчёту во всех %d прогонах" % len(res))
    if bad:
        sys.exit(1)


def check_inputs(u, side):
    """Охрана (критик 20.11): читаются только счётные входы текущего определения — `counts` с меткой `def`, прогоны клеток с `DONE` = `def2 <код> <sha256 --ftab>` для нынешнего --ftab; всё иное (в т. ч. `void/`) — отказ."""
    c = json.load(open(q("res", "counts-%s-%s.json" % (u.name, side)), encoding="utf-8"))
    if c.get("def") != f5.DEF_TAG:
        raise GuardError("counts %s %s: определение %r ≠ %r — вход отвергнут" % (u.name, side, c.get("def"), f5.DEF_TAG))
    for w in st.TRADE_WINDOWS:
        done = os.path.join(ROOT, u.sub("cells"), "%s-%s" % (w.name, side), "DONE")
        got = open(done, encoding="utf-8").read().split() if os.path.exists(done) else []
        want = sha256(q(u.sub("ftab"), "%s-%s.csv" % (w.name, side)))
        if len(got) != 3 or got[0] != f5.DEF_TAG or got[2] != want:
            raise GuardError("клетки %s %s-%s: DONE %s ≠ (%s, …, %s) — вход отвергнут" % (u.name, w.name, side, got, f5.DEF_TAG, want[:12]))


def build_side(u, side):
    check_inputs(u, side)
    base = load_series(u, side, "base")
    x = base_feats(u, side, base)
    cells = []
    for c in cell_info(u, side):
        mask = f5.excluded(x[f5.col_name(c["m"], c["D"])], c["c"]) if c["c"] is not None else np.zeros(len(base), dtype=bool)
        ser = None if (c["dup_of"] is not None or c["c"] is None) else load_series(u, side, "cell", c["cid"])
        cells.append(s5.Cell(c["cid"], c["m"], c["D"], c["q"], c["c"], c["trial"], c["dup_of"], ser, mask))
    return s5.SideCells(base, cells, len(u.coins()))


def with_series(side, c):
    """Клетка с рядом: у дубля — ряд клетки с тем же множеством исключённых (результат тождествен)."""
    if c.series is not None:
        return c
    src = next((x for x in side.cells if x.cid == c.dup_of), None)
    return dataclasses.replace(c, series=src.series) if src is not None and src.series is not None else None


def cmd_select(a):
    """Выбор по п. 5 на h775 (Холм, g1–g5), повтор клеток-победителей на o566, ответ по п. 7; фандинг (граница B) справочно."""
    h, o = Uni("h775"), Uni("o566")
    allom = np.arange(st.NDAYS)
    sh = {s: build_side(h, s) for s in SIDES}
    ev = s5.evaluate(sh, allom)
    so = {s: build_side(o, s) for s in SIDES}
    repl = {}
    for s, cid in ev["passed"]:
        c = with_series(so[s], next(c for c in so[s].cells if c.cid == cid))
        if c is not None:
            repl[(s, cid)] = s5.replicate(so[s], c, allom)
    rows_o = {}
    for s in SIDES:
        rows_o[s] = {}
        for c in so[s].cells:
            c2 = with_series(so[s], c)
            if c2 is not None:
                rows_o[s][c.cid] = s5.cell_row(so[s], c2, allom)
    outcomes = {s: s5.side_outcome(ev, repl, s) for s in SIDES}
    ans = s5.answer_line(outcomes)
    say("Холм: пробных допущенных клеток %d; p %s" % (ev["holm"]["m"], [round(p_, 4) for p_ in ev["holm"]["ps"]]))
    for s in SIDES:
        say("== %s h775: сделок базы %d, Σ %.0f $, %.2f $/сделку" % (s, len(sh[s].base), float(sh[s].base.val.sum()), float(sh[s].base.val.mean())))
        for cid, r in ev["rows"][s].items():
            say("   %-12s %-7s c=%s: Δ %s ± %s $/сделку, z %s, p %s, n_c %d, n_r %d, исключено %d, допуск %s, g1 %s g2 %s g3 %s g4 %s g5 %s%s" % (
                cid, "пробная" if r["trial"] else "сосед", r["c"], "%.3f" % r["d"] if r["d"] == r["d"] else "—", "%.3f" % r["se"] if r["se"] == r["se"] else "—",
                "%.2f" % r["z"] if r["z"] == r["z"] else "—", "%.4f" % r["p"] if r["p"] == r["p"] else "—", r["n_c"], r["n_r"], r["n_ex"], r["admitted"],
                r.get("g1"), r.get("g2"), r.get("g3"), r.get("g4"), r.get("g5"), " ПРОШЛА" if r.get("pass") else ""))
    for (s, cid), r in repl.items():
        say("   повтор o566 %s %s: Δ %.3f ± %.3f, g2 %s g3 %s g4 %s → %s" % (s, cid, r["d"], r["se"], r.get("g2"), r.get("g3"), r.get("g4"), r["repl"]))
    for s in SIDES:
        say("ИСХОД %s: %s %s" % (s, *outcomes[s]))
    say("ОТВЕТ: %s" % ans)
    out = dict(answer=ans, outcomes={s: list(v) for s, v in outcomes.items()}, holm=ev["holm"], passed=ev["passed"], rows=ev["rows"], repl={"%s/%s" % k: v for k, v in repl.items()}, rows_o566=rows_o)
    write_json(q("res", "select.json"), stamp(jsonable(out)))
    say("метка: def %s, код %s" % (f5.DEF_TAG, code_id()))
    try:
        rates = st.load_funding_rates("/data/p42-metrics/funding", h.coins())
        for s in SIDES:
            items = [("база", sh[s].base)] + [("клетка " + cid, next(c.series for c in sh[s].cells if c.cid == cid)) for (s_, cid) in ev["passed"] if s_ == s]
            for lab, ser in items:
                jm = st.MONTH_OF_DAY[ser.day] <= 1
                b = st.funding_bound(np.array(ser.sym_name, dtype=object)[jm], ser.t_in[jm], ser.t_out[jm], rates)
                say("   фандинг %s %s: B (июль–август) %.2f $; поле движка сент.–нояб. Σ %.2f $" % (s, lab, float(b.sum()), float(ser.fund[~jm].sum())))
    except Exception as e:                                  # noqa: BLE001 — справочно; решений не несёт
        say("фандинг: не посчитан (%s)" % e)

COMMANDS = {"gates1": cmd_gates1, "coindays": cmd_coindays, "eng": cmd_eng, "engall": cmd_engall, "ep": cmd_ep, "epall": cmd_epall, "g4x": cmd_g4x, "geq": cmd_geq, "g3": cmd_g3,
            "counts": cmd_counts, "step1": cmd_step1, "cells": cmd_cells, "cellsall": cmd_cellsall, "g8": cmd_g8, "select": cmd_select}


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
