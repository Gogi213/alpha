#!/usr/bin/env python3
"""П-42 §13.69 (TK-669): признаки карточек «Викинг» (банк, строки 57–61) на h775 — ключи движка `xb` / `fg` / `tpp` / `trp`, портфель моделируется движком.
  python3 tk669_run.py variants   — клетки → res/variants69.json (без движка и без P&L)
  python3 tk669_run.py off        — строки баз L / S бинарём TK-669, окна wa / wb / wc; G-off (байт в байт против /data/p42-tk660, P&L не читается) → res/goff69.json
  python3 tk669_run.py counts     — счёт без P&L (п. 6): сделки E57 по окнам / месяцам, события E59 (OPPD < 1) → res/counts69.json, таблицы --ftab
  python3 tk669_run.py cells      — клетки E58, E60, X61 (и E59 при ≥ 70 событий) — только после PASS критика на счёт
  python3 tk669_run.py tables     — G-bin, G-off, G-key, G-tape, G-dup, G-win; статистика, парная Δ к базе → res/cells69-<сторона>.json
  python3 tk669_run.py verdict    — правило п. 7 (а)–(ж), семьи E (m = 71) и X (m = 2) → res/verdict69.json
Механика склейки, статистика, Холм, шум — `tk643_run` (§13.43) без изменений. Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import argparse
import csv
import filecmp
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_run as t  # noqa: E402

ROOT = os.environ.get("TK669_ROOT", "/data/p42-tk669")
t.ROOT = ROOT                                   # run_variants / q / cell_dir пишут в каталог этой задачи
BIN = ROOT + "/bin/alpha-tk669"                 # бинарь ветки TK-669; sha256 — res/bin69.json
REF = "/data/p42-tk660"                         # строки баз бинарём master (с `ek`, `tl`) — эталон G-off
SIDES = ("S", "L")
DAY_MS = 86_400_000
OPPD_BACK_MS = DAY_MS                           # п. 4: предыдущие 24 ч
OPPD_MAX = 1.0                                  # п. 4: отбросить сигнал при OPPD < 1,0
MIN_EVENTS = 70                                 # п. 6: < 70 сигналов у стороны ⇒ DROP
PRIOR_E = 61                                    # п. 7 (б): прежние гипотезы семьи E входят с p = 1
N_E = 71                                        # п. 7 (ж): шум семьи E
N_X = 401                                       # п. 7 (ж): шум семьи X
OFF_FILES = ("long-trades.csv", "long-daily.csv", "long-coinday.csv", "long-entryrej.csv",
             "short-trades.csv", "short-daily.csv", "short-coinday.csv", "short-entryrej.csv")
SIGSX_DAYS = "14:73"                            # как дампы баз (`tk625_run.cmd_eng`)
X_COMMON = {"n_tp": 2, "t_low": 4, "t_high": 12, "tpp": 1, "q": 100, "s": 4, "bm": 2, "trp": 1, "rr": 2, "rf": 0}
MONTHS = ("07", "08", "09", "10", "01–13.11")


def cells():
    """[клетка]; dict(cid, fam, row, side, patch). Семья E — 10 клеток (m_new), семья X — 2."""
    out = []
    for s in SIDES:
        out.append(dict(cid="E57_" + s, fam="E", row=57, side=s, patch={"tf": 240, "n_bounce": 1}))
        out.append(dict(cid="E58_" + s, fam="E", row=58, side=s, patch={"xb": 0.4, "e2": 0} if s == "S" else {"xb": 0.4}))
        for v, name in ((0.5, "05"), (2, "2")):
            out.append(dict(cid="E60_%s%s" % (s, name), fam="E", row=60, side=s, patch={"fg": v}))
        out.append(dict(cid="E59_" + s, fam="E", row=59, side=s, patch={"fa": "OPPD", "fo": "lo", "fv": 1}))
        xp = dict(X_COMMON)
        if s == "S":
            xp.update(tt=0, e2=0)
        out.append(dict(cid="X61_" + s, fam="X", row=61, side=s, patch=xp))
    return out


def by_cid():
    return {c["cid"]: c for c in cells()}


def fam(f):
    return [c for c in cells() if c["fam"] == f]


def patch_line(patch):
    return " ".join("%s=%s" % (k, v if isinstance(v, str) else t.fmt(v)) for k, v in patch.items())


def vline(r5, u, side, patch):
    return "%s%s%s" % (r5.BASE[side], u.extra, (" " + patch_line(patch)) if patch else "")


# ---- чистые функции счёта (без движка, без P&L) ----------------------------------------------------------------------------------------
def oppd(sig_sym, sig_ms, sig_px, sig_natr, ent_sym, ent_ms, ent_px, back_ms=OPPD_BACK_MS):
    """п. 4: OPPD сигнала = минимум по входам базы противоположной стороны той же монеты за [t − 24 ч, t) величины |L − p_вх| / (NATR·L);
    нет входа или нет NATR (< 0) — NaN (сигнал проходит). Цены и NATR — как в дампе (×1e8; NATR — доля цены)."""
    by = {}
    for i, s in enumerate(ent_sym):
        by.setdefault(s, []).append(i)
    ems = {s: np.array([ent_ms[i] for i in ix], dtype=np.int64) for s, ix in by.items()}
    epx = {s: np.array([ent_px[i] for i in ix], dtype=float) for s, ix in by.items()}
    for s in by:
        o = np.argsort(ems[s], kind="stable")
        ems[s], epx[s] = ems[s][o], epx[s][o]
    out = np.full(len(sig_sym), np.nan)
    for j, s in enumerate(sig_sym):
        if s not in ems or sig_natr[j] <= 0 or sig_px[j] <= 0:
            continue
        lo = int(np.searchsorted(ems[s], int(sig_ms[j]) - back_ms, side="left"))
        hi = int(np.searchsorted(ems[s], int(sig_ms[j]), side="left"))        # [t − 24 ч, t): вход в t не считается
        if hi <= lo:
            continue
        L = float(sig_px[j])
        out[j] = float(np.min(np.abs(L - epx[s][lo:hi]))) / (sig_natr[j] / 1e8 * L)
    return out


def gate_a(n, months, min_n=None, min_month=None):
    """п. 7 (а): ≥ 70 сделок и ≥ 20 в каждом месяце (01–13.11 — ≥ 8); n — всего, months — по 5 месяцам."""
    mn = t.MIN_N if min_n is None else min_n
    mm = t.MIN_MONTH if min_month is None else min_month
    return bool(n >= mn and all(m >= x for m, x in zip(months, mm)))


def read_sigsx(path):
    """sym, sig_ms (от T0 окна), цена уровня e8, NATR e8 — позиции полей как `tk604_feat.read_sigsx_keys` (0, 2, 4, 8)."""
    sym, ms, px, natr = [], [], [], []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            sym.append(r[0]); ms.append(int(r[2])); px.append(int(r[4])); natr.append(int(r[8]))
    return (np.array(sym, dtype=object), np.array(ms, dtype=np.int64), np.array(px, dtype=np.int64), np.array(natr, dtype=np.int64))


def read_entries(path):
    """sym, level, sig_ms, entry_ms (от T0), entry_px e8 — столбцы файла сделок; P&L не читается."""
    sym, lv, sg, em, px = [], [], [], [], []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            sym.append(r["sym"]); lv.append(int(r["level"])); sg.append(int(r["sig_ms"])); em.append(int(r["entry_ms"])); px.append(int(r["entry_px_e8"]))
    return (np.array(sym, dtype=object), np.array(lv, dtype=np.int64), np.array(sg, dtype=np.int64), np.array(em, dtype=np.int64),
            np.array(px, dtype=np.int64))


def month_of(st, abs_ms):
    day = (np.asarray(abs_ms, dtype=np.int64) - _d0_ms(st)) // DAY_MS
    return st.MONTH_OF_DAY[day], day


def _d0_ms(st):
    import datetime
    return int(datetime.datetime(st.D0.year, st.D0.month, st.D0.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def in_counted(st, w, abs_ms):
    a = np.asarray(abs_ms, dtype=np.int64)
    lo = _d0_ms(st) + (w.lod - st.D0).days * DAY_MS
    hi = _d0_ms(st) + ((w.hid - st.D0).days + 1) * DAY_MS
    return (a >= lo) & (a < hi)


def month_counts(st, abs_ms):
    mo, _ = month_of(st, abs_ms)
    return [int((mo == k).sum()) for k in range(len(st.MONTH_LEN))]


# ---- движок --------------------------------------------------------------------------------------------------------------------------------
def cmd_variants(a):
    cs = cells()
    json.dump(dict(cells=cs, m_E=len(fam("E")), m_E_total=PRIOR_E + len(fam("E")), m_X=len(fam("X")), trials_E=N_E, trials_X=N_X),
              open(t.q("res", "variants69.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t.say("клеток: E %d (Холм m = %d, шум N = %d), X %d (Холм m = %d, шум N = %d)" % (
        len(fam("E")), PRIOR_E + len(fam("E")), N_E, len(fam("X")), len(fam("X")), N_X))


def _bin():
    import tk604_run as r4
    r4.BIN = BIN                                # r5.tick берёт r4.BIN при вызове
    sha = r4.sha256(BIN)
    path = t.q("res", "bin69.json")
    if os.path.exists(path):
        prev = json.load(open(path, encoding="utf-8"))["sha256"]
        if prev != sha:
            raise SystemExit("G-bin: sha256 бинаря %s ≠ записанному %s (бинарь подменён)" % (sha, prev))
    json.dump(dict(bin=BIN, sha256=sha), open(path, "w", encoding="utf-8"), indent=1)
    return sha


def run_cells(u, side, variants, tag, env=None, ftab_for=None):
    """Как `tk643_run.run_variants`, но с окружением и таблицей --ftab на окно (`ftab_for(w) -> путь`)."""
    st, r5, _, _ = t._srv()
    for w in st.TRADE_WINDOWS:
        out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
        done = os.path.join(ROOT, "done", "%s-%s-%s-0" % (tag, w.name, side))
        if os.path.exists(done):
            continue
        r5.tick(u, w, variants, out, env or {}, t.q("log", "%s-%s-%s-0.log" % (tag, w.name, side)), ftab=ftab_for(w) if ftab_for else None)
        os.makedirs(os.path.dirname(done), exist_ok=True)
        open(done, "w").write("%d\n" % len(variants))
        t.say("%s %s-%s готово" % (tag, w.name, side))


def g_off(st):
    """G-off: строки баз (новые ключи выключены) бинарём TK-669 = строкам баз TK-660 (бинарь master) байт в байт, все CSV обеих сторон; meta.json равен."""
    bad = []
    for side in ("L", "S"):
        for w in st.TRADE_WINDOWS:
            a_, b_ = t.cell_dir(w.name, side, "base"), os.path.join(REF, "cells", "%s-%s" % (w.name, side), "base")
            for f in OFF_FILES:
                if not filecmp.cmp(os.path.join(a_, f), os.path.join(b_, f), shallow=False):
                    bad.append("%s-%s/%s" % (w.name, side, f))
            ma, mb = (json.load(open(os.path.join(d, "meta.json"), encoding="utf-8")) for d in (a_, b_))
            if ma != mb:
                bad.append("%s-%s/meta.json" % (w.name, side))
    return bad


def cmd_off(a):
    st, r5, _, _ = t._srv()
    t.say("G-bin: %s sha256 %s" % (BIN, _bin()))
    u = r5.Uni("h775")
    for side in ("L", "S"):
        t.run_variants(u, side, [("base", t.variant_line(r5, u, side, {}))], "off")
    bad = g_off(st)
    json.dump(dict(ok=not bad, bad=bad, files=len(OFF_FILES), rows=6), open(t.q("res", "goff69.json"), "w", encoding="utf-8"), indent=1)
    t.say("G-off: %s" % ("ok — 6 строк баз (L, S × 3 окна), 8 CSV + meta.json байт в байт" if not bad else "ОШИБКА " + ", ".join(bad)))
    if bad:
        sys.exit(1)


def goff_ok():
    p = t.q("res", "goff69.json")
    return os.path.exists(p) and json.load(open(p, encoding="utf-8"))["ok"]


# ---- счёт без P&L (п. 6) ---------------------------------------------------------------------------------------------------------------
def ftab_path(w, side):
    return t.q("ftab", "%s-%s.csv" % (w.name, side))


def opposite(side):
    return "L" if side == "S" else "S"


def oppd_window(r5, st, u, w, side):
    """OPPD по всем строкам дампа базы стороны в окне (с разгоном): входы базы противоположной стороны — собственная лента окна."""
    eng = r5.ENG
    sym, ms, px, natr = read_sigsx(os.path.join(u.base(w.name, side), eng[side] + "-sigsx.csv"))
    es, _lv, _sg, em, ep = read_entries(os.path.join(u.base(w.name, opposite(side)), eng[opposite(side)] + "-trades.csv"))
    return sym, ms, oppd(sym, ms + w.t0_ms, px, natr, es, em + w.t0_ms, ep)


def write_oppd_ftab(path, sym, ms, val):
    order = sorted(range(len(sym)), key=lambda i: (sym[i], int(ms[i])))
    seen = set()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms,OPPD\n")
        for i in order:
            k = (sym[i], int(ms[i]))
            if k in seen:
                continue
            seen.add(k)
            f.write("%s,%d,%s\n" % (sym[i], int(ms[i]), "" if val[i] != val[i] else repr(float(val[i]))))


def count_e57(st, r5, side):
    """Сделки клетки E57 по окнам и месяцам (читаются только entry_ms; P&L не читается)."""
    res = dict(windows={}, months=[0] * len(st.MONTH_LEN))
    for w in st.TRADE_WINDOWS:
        _s, _l, _g, em, _p = read_entries(os.path.join(t.cell_dir(w.name, side, "E57_" + side), r5.ENG[side] + "-trades.csv"))
        ab = em + w.t0_ms
        m = in_counted(st, w, ab)
        mc = month_counts(st, ab[m])
        res["windows"][w.name] = dict(n=int(m.sum()), months=mc)
        res["months"] = [x + y for x, y in zip(res["months"], mc)]
    res["n"] = sum(v["n"] for v in res["windows"].values())
    res["gate_a"] = gate_a(res["n"], res["months"])
    res["base_n"] = None
    return res


def count_e59(st, r5, u, side):
    """События строки 59: сигналы стороны в зачётных диапазонах с OPPD < 1 и сделки базы, которые правило отбросило бы (ключ (монета, время сигнала))."""
    flagged = [0] * len(st.MONTH_LEN)
    with_val = nsig = ntr = ndrop = 0
    wins = {}
    for w in st.TRADE_WINDOWS:
        sym, ms, val = oppd_window(r5, st, u, w, side)
        write_oppd_ftab(ftab_path(w, side), sym, ms, val)
        ab = ms + w.t0_ms
        m = in_counted(st, w, ab)
        with np.errstate(invalid="ignore"):
            fl = m & (val < OPPD_MAX)                              # NaN < 1 — ложь: сигнал проходит
        mc = month_counts(st, ab[fl])
        flagged = [x + y for x, y in zip(flagged, mc)]
        bad_keys = set(zip(sym[fl].tolist(), ms[fl].tolist()))
        ts, _lv, tg, te, _px = read_entries(os.path.join(u.base(w.name, side), r5.ENG[side] + "-trades.csv"))
        tm = in_counted(st, w, te + w.t0_ms)
        k = sum(1 for s_, g_ in zip(ts[tm].tolist(), tg[tm].tolist()) if (s_, g_) in bad_keys)
        wins[w.name] = dict(signals=int(m.sum()), with_value=int((m & (val == val)).sum()), flagged=int(fl.sum()), base_trades=int(tm.sum()), dropped_trades=k)
        nsig += int(m.sum()); with_val += int((m & (val == val)).sum()); ntr += int(tm.sum()); ndrop += k
    n = sum(v["flagged"] for v in wins.values())
    return dict(windows=wins, signals=nsig, with_value=with_val, flagged=n, flagged_months=flagged, base_trades=ntr, dropped_trades=ndrop,
                keep=bool(n >= MIN_EVENTS))


def cmd_counts(a):
    st, r5, _, _ = t._srv()
    _bin()
    if not goff_ok():
        raise SystemExit("G-off не пройден или не записан — сначала `off`")
    u = r5.Uni("h775")
    cb = by_cid()
    for side in SIDES:
        c = cb["E57_" + side]
        run_cells(u, side, [(c["cid"], vline(r5, u, side, c["patch"]))], "e57")
    out = dict(e57={}, e59={})
    for side in SIDES:
        e = count_e57(st, r5, side)
        out["e57"][side] = e
        t.say("E57_%s: сделок %d (по месяцам %s), ворота (а) %s ⇒ %s" % (side, e["n"], e["months"], "да" if e["gate_a"] else "НЕТ",
                                                                      "считаем P&L" if e["gate_a"] else "DROP без P&L"))
        e = count_e59(st, r5, u, side)
        out["e59"][side] = e
        t.say("E59_%s: сигналов %d (со значением %d), OPPD < 1: %d (по месяцам %s); сделок базы отброшено бы %d из %d ⇒ %s" % (
            side, e["signals"], e["with_value"], e["flagged"], e["flagged_months"], e["dropped_trades"], e["base_trades"],
            "считаем P&L" if e["keep"] else "DROP без P&L (< 70 событий)"))
    json.dump(out, open(t.q("res", "counts69.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t.say("counts готово → res/counts69.json (P&L не читался)")


def counts():
    p = t.q("res", "counts69.json")
    if not os.path.exists(p):
        raise SystemExit("G-count: нет res/counts69.json — P&L клеток читать нельзя (п. 9)")
    return json.load(open(p, encoding="utf-8"))


def dropped(cid, cn):
    """Клетка, снятая счётом п. 6: E57 при невыполненных воротах (а), E59 при < 70 событий."""
    c = by_cid()[cid]
    if c["row"] == 57:
        return not cn["e57"][c["side"]]["gate_a"]
    if c["row"] == 59:
        return not cn["e59"][c["side"]]["keep"]
    return False


def cmd_cells(a):
    st, r5, _, _ = t._srv()
    _bin()
    if not goff_ok():
        raise SystemExit("G-off не пройден или не записан")
    cn = counts()
    u = r5.Uni("h775")
    cb = by_cid()
    for side in SIDES:
        main = [cb[c] for c in ("E58_" + side, "X61_" + side)]
        run_cells(u, side, [(c["cid"], vline(r5, u, side, c["patch"])) for c in main], "e58x61")
        fg = [cb["E60_%s%s" % (side, n)] for n in ("05", "2")]
        run_cells(u, side, [(c["cid"], vline(r5, u, side, c["patch"])) for c in fg], "e60", env={"P42_SIGSX": SIGSX_DAYS})
        if not dropped("E59_" + side, cn):
            c = cb["E59_" + side]
            run_cells(u, side, [(c["cid"], vline(r5, u, side, c["patch"]))], "e59", ftab_for=lambda w, s=side: ftab_path(w, s))
    t.say("cells готово")


# ---- таблицы ---------------------------------------------------------------------------------------------------------------------------
def meta_sum(st, r5, side, cid, key):
    tot = None
    for w in st.TRADE_WINDOWS:
        m = json.load(open(os.path.join(t.cell_dir(w.name, side, cid), "meta.json"), encoding="utf-8"))[r5.ENG[side]]
        if key not in m:
            return None
        tot = list(m[key]) if tot is None else [x + y for x, y in zip(tot, m[key])]
    return tot


def fg_resumed(st, r5, u, side, cid):
    """Критик (заметка 2): сколько возобновлённых уровней дали сигналы и сделки. Новые сигналы — строки дампа клетки, которых нет в дампе базы
    (ключ монета + время сигнала), в зачётных диапазонах; сделки клетки с новым ключом — по уровню (монета, номер)."""
    lv_sig, lv_tr, n_sig, n_tr = set(), set(), 0, 0
    for w in st.TRADE_WINDOWS:
        eng = r5.ENG[side]
        bs, bm_, _, _ = read_sigsx(os.path.join(u.base(w.name, side), eng + "-sigsx.csv"))
        base_keys = set(zip(bs.tolist(), bm_.tolist()))
        d = t.cell_dir(w.name, side, cid)
        cs_, cm_, _, _ = read_sigsx(os.path.join(d, eng + "-sigsx.csv"))
        with open(os.path.join(d, eng + "-sigsx.csv"), encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            next(rd)
            lvl = {(r[0], int(r[2])): int(r[1]) for r in rd}
        ab = cm_ + w.t0_ms
        inr = in_counted(st, w, ab)
        new = [(s_, g_) for s_, g_, ok in zip(cs_.tolist(), cm_.tolist(), inr.tolist()) if ok and (s_, g_) not in base_keys]
        n_sig += len(new)
        lv_sig |= {(s_, lvl[(s_, g_)]) for s_, g_ in new}
        newk = set(new)
        ts, tl_, tg, te, _px = read_entries(os.path.join(d, eng + "-trades.csv"))
        tm = in_counted(st, w, te + w.t0_ms)
        for s_, l_, g_, ok in zip(ts.tolist(), tl_.tolist(), tg.tolist(), tm.tolist()):
            if ok and (s_, g_) in newk:
                n_tr += 1
                lv_tr.add((s_, l_))
    return dict(new_signals=n_sig, levels_with_signals=len(lv_sig), new_trades=n_tr, levels_with_trades=len(lv_tr))


def cmd_tables(a):
    st, r5, p8, r34 = t._srv()
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    import tk604_run as r4
    cn = counts()                                                   # G-count
    sha = json.load(open(t.q("res", "bin69.json"), encoding="utf-8"))["sha256"]
    if r4.sha256(BIN) != sha:
        t.say("СТОП: G-bin — sha256 бинаря ≠ res/bin69.json")
        sys.exit(1)
    off = g_off(st)
    t.say("G-off: %s" % ("ok — 6 строк баз, 8 CSV + meta.json байт в байт" if not off else "ОШИБКА " + ", ".join(off)))
    if off:
        t.say("СТОП: G-off — клетки не читаются")
        sys.exit(1)
    bad, inert_ids, sig, tmax = [], [], {}, 0
    for side in SIDES:
        bser, bk, bp = t.load_cell(st, p8, u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        gser, _gk, gp = t.load_cell(st, p8, u, side, t.load_dirs(st, side, "base"))
        gb = p8.keys_of(gser) == p8.keys_of(bser) and len(gp) == len(bp) and bool(np.allclose(gp, bp, atol=1e-6))
        if not gb:
            bad.append("G-base %s: строка базы в пакете ≠ готовой базе h775" % side)
        bkeys = list(p8.keys_of(bser))
        bmap = dict(zip(bkeys, bp))
        out = dict(side=side, bin_sha=sha, goff=not off, gbase=gb, base=p8.cell_stats(bser, bk, bp), cells={},
                   gwin_forced_base=int(sum(1 for k in bk if k == "forced")), base_kinds={k: int(sum(1 for x in bk if x == k)) for k in sorted(set(bk))})
        ctx = x43 = None
        try:
            ctx = json.load(open("/data/p42-tk643/res/cells43-%s.json" % side, encoding="utf-8"))["cells"]
            x43 = next((v for v in ctx.values() if v.get("ax", {}).get("rt") == "off" and v.get("ax", {}).get("tx") in ("off", None)), None)
        except OSError:
            pass
        out["ctx_tk643_rt_off"] = x43 and dict(n=x43["n"], sum=x43["sum"], mean=x43["mean"], sharpe=x43["sharpe"], delta=x43["delta"])
        for c in [c for c in cells() if c["side"] == side]:
            if dropped(c["cid"], cn):
                out["cells"][c["cid"]] = dict(dropped=True, row=c["row"], patch=c["patch"], fam=c["fam"])
                continue
            ser, kinds, pnl = t.load_cell(st, p8, u, side, t.load_dirs(st, side, c["cid"]))
            r = t.stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
            keys = list(p8.keys_of(ser))
            ks = set(keys)
            gone = [k for k in bkeys if k not in ks]
            common = [(k, x) for k, x in zip(keys, pnl) if k in bmap]
            same = dict(only_cell=len(ks - set(bkeys)), only_base=len(gone), common=len(common))
            changed = int(sum(1 for k, x in common if abs(x - bmap[k]) > 1e-6))
            inert = same["only_cell"] + same["only_base"] + changed == 0
            if inert:
                inert_ids.append(c["cid"])
            r.update(dropped=False, fam=c["fam"], row=c["row"], patch=c["patch"], same=same, pnl_changed=changed, inert=inert,
                     skipped_base_n=len(gone), skipped_base_sum=float(sum(bmap[k] for k in gone)),
                     new_cell_sum=float(sum(x for k, x in zip(keys, pnl) if k not in bmap)),
                     by_selection=bool(r["delta"]["d"] > 0 and r["n"] < out["base"]["n"]))
            if c["row"] == 58:
                r["xb"] = dict(zip(("filled", "expired", "rejected"), meta_sum(st, r5, side, c["cid"], "xb_[filled,expired,rejected]") or []))
            if c["row"] == 60:
                fgc = meta_sum(st, r5, side, c["cid"], "fg_[susp_pk,susp_pb,susp_ban,resumed]")
                r["fg"] = dict(zip(("susp_pk", "susp_pb", "susp_ban", "resumed"), fgc or []))
                try:
                    r["fg"].update(fg_resumed(st, r5, u, side, c["cid"]))
                except FileNotFoundError:
                    r["fg"]["resumed_levels_note"] = "дамп сигналов клетки не записан"

            if c["row"] == 61:
                r["be_active"] = int(r["kinds_n"].get("be", 0))
                r["context_tk643_rt_off"] = out["ctx_tk643_rt_off"]
            out["cells"][c["cid"]] = r
            sig.setdefault((side, tuple(keys), tuple(np.round(pnl, 6))), []).append(c["cid"])
            for w in st.TRADE_WINDOWS:
                tb = st.read_trades_csv(os.path.join(t.cell_dir(w.name, side, c["cid"]), r5.ENG[side] + "-trades.csv"))
                if len(tb.exit_ms):
                    tmax = max(tmax, int(tb.exit_ms.max()) + w.t0_ms)
        out["gwin_forced"] = {cid: r["kinds_n"].get("forced", 0) for cid, r in out["cells"].items() if not r.get("dropped") and r["kinds_n"].get("forced", 0)}
        json.dump(out, open(t.q("res", "cells69-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        t.say("== %s: база %d сделок; клеток прочитано %d из %d" % (side, out["base"]["n"], sum(1 for r in out["cells"].values() if not r.get("dropped")), len(out["cells"])))
    if tmax >= st.LAST_MS:
        bad.append("G-tape: выход сделки клетки позже 13.11")
    gdup = [v for v in sig.values() if len(v) > 1]
    json.dump(dict(inert=inert_ids, gdup=gdup, tape_max_exit_ms=tmax), open(t.q("res", "gates69.json"), "w", encoding="utf-8"), indent=1)
    t.say("G-key «не действует»: %s; G-dup групп %d; последний выход клеток %d" % (inert_ids, len(gdup), tmax))
    if len(inert_ids) >= 3:
        bad.append("G-key: «не действует» %d клеток ≥ 3 — ключи не дошли до движка: %s" % (len(inert_ids), inert_ids))
    if bad:
        t.say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    t.say("tables готово")


# ---- вердикт (п. 7) --------------------------------------------------------------------------------------------------------------------
def neighbours60(cid, idx):
    """п. 7 (е), только строка 60: сосед — другое значение `fg` той же стороны (0,5 ↔ 2); {ось: [cid]}."""
    return {"fg": [x for x in idx if x != cid]}


def verdict(rows):
    """rows: {cid: строка `stat_row` (или dropped)}; семья E — Холм по [1]·61 + p(10 клеток), шум N = 71; семья X — Холм по 2 p, шум N = 401."""
    cs = cells()
    res = {}
    for f_, prior, nz_n in (("E", PRIOR_E, N_E), ("X", 0, N_X)):
        ids = [c["cid"] for c in cs if c["fam"] == f_]
        ps = [1.0 if (rows[c].get("dropped") or rows[c].get("inert")) else rows[c]["delta"]["p"] for c in ids]
        rej = t.holm([1.0] * prior + ps)[prior:]
        r60 = {s: [c["cid"] for c in cs if c["row"] == 60 and c["side"] == s] for s in SIDES}
        dmap = {c: rows[c]["delta"]["d"] for c in ids if not rows[c].get("dropped")}
        for cid, rj in zip(ids, rej):
            r, c = rows[cid], by_cid()[cid]
            if r.get("dropped"):
                res[cid] = dict(dropped=True, passed=False, gates={})
                continue
            dl = r["delta"]
            g = {}
            g["a"] = bool(r["n"] >= t.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t.MIN_MONTH)))
            g["b"] = bool(rj) and not r.get("inert", False)
            g["c"] = sum(1 for x in r["dmonths"] if x is not None and x > 0) >= t.NEED_MONTHS
            g["d"] = r["robust_days"] is not None and r["robust_coins"] is not None and r["robust_days"] > 0 and r["robust_coins"] > 0
            g["e"] = dl["s_c"] > 0 and (dl["dsum"] > 0 if f_ == "E" else dl["dsum"] >= -1.0 * dl["se_sum"])
            nb = {}
            if c["row"] == 60:
                nb = neighbours60(cid, r60[c["side"]])
                g["f"] = dl["d"] > 0 and t.plateau_ok(dl["d"], nb, dmap)
            else:
                g["f"] = None                                       # не применяется (п. 7 (е))
            nz = t.noise_bar(r["sharpe"], nz_n)
            g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
            res[cid] = dict(gates=g, passed=all(v for v in g.values() if v is not None), neighbours=nb, noise=nz, by_selection=bool(r.get("by_selection")),
                            carries_rt_off=bool(c["row"] == 61))
    passed = [c for c, v in res.items() if v["passed"]]
    return dict(m_E=PRIOR_E + len(fam("E")), m_X=len(fam("X")), trials_E=N_E, trials_X=N_X, passed=passed, cells=res,
                answer=("кандидат вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: базы остаются")


def cmd_verdict(a):
    rows = {}
    for s in SIDES:
        rows.update(json.load(open(t.q("res", "cells69-%s.json" % s), encoding="utf-8"))["cells"])
    gt = json.load(open(t.q("res", "gates69.json"), encoding="utf-8"))
    for cid in gt["inert"]:
        rows[cid]["inert"] = True
    v = verdict(rows)
    t.say("== E: m = %d, N шума = %d; X: m = %d, N шума = %d; %s" % (v["m_E"], v["trials_E"], v["m_X"], v["trials_X"], v["answer"]))
    for cid, c in v["cells"].items():
        t.say("   %-8s %s" % (cid, "DROP по счёту" if c.get("dropped") else " ".join("%s=%s" % (k, {True: "да", False: "НЕТ", None: "—"}[x]) for k, x in c["gates"].items())))
    json.dump(v, open(t.q("res", "verdict69.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


COMMANDS = {"variants": cmd_variants, "off": cmd_off, "counts": cmd_counts, "cells": cmd_cells, "tables": cmd_tables, "verdict": cmd_verdict}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    COMMANDS[ap.parse_args().cmd](None)


if __name__ == "__main__":
    main()
