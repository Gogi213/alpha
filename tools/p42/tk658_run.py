#!/usr/bin/env python3
"""П-42 §13.58 (TK-658): калибровка входа по ленте на всех сутках окна — цена заявки на N $ по сырой ленте `aggTrades` против заменителя движка,
объём лучшего уровня, сдвиг P&L баз `L_base` / `S_base` h775 при откалиброванном входе (ноги выхода фиксированы; проверка — прогоны движка со `slip`).
  python3 tk658_run.py eng     — G-bin; базы с `P42_DETAIL=1` + `slip` ∈ {0, 2, 3, 5} (окна wa / wb / wc); G-base (байт в байт с TK-625). P&L не читается.
  python3 tk658_run.py tape    — лоты баз (из `*-detail.jsonl`: только время, цена входа, ноги для G-leg) → замер ленты → res/lots.csv; G-leg, G-px, G-tape, G-n;
                                 таблицы замера цены → res/price58.json (без P&L)
  python3 tk658_run.py cal13   — блок V: метод ленты против книги 13.11 (§13.16) → res/cal13.json
  python3 tk658_run.py pnl     — блоки P и E, G-path, G-splice, статистика и правило п. 7 → res/pnl58.json (только после PASS критика)
Доллары — ставка 500 $; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без медиан; ничего позже 13.11 не читается."""
from __future__ import annotations

import argparse
import csv
import dataclasses
import filecmp
import glob
import json
import math
import os
import sys
import zipfile
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))
sys.path.insert(0, HERE)

ROOT = os.environ.get("TK658_ROOT", "/data/p42-tk658")
SIDES = ("L", "S")
SGN = {"L": 1, "S": -1}
ENG_SIDE = {"L": "long", "S": "short"}
SLIPS = (0, 2, 3, 5)                       # п. 5 блок E (база — 1)
DELTAS = (50, 100, 250, 500, 1000)         # п. 3: окна правды, мс
COVER_MIN = 0.80                           # п. 3: Δ* — наименьшее Δ с покрытием V ≥ 80 %
W_GRID = (5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)
EW = 5                                     # ключ баз `ew`
CAP_MS = max(DELTAS)                       # проход V дальше 1 с не нужен ни одному Δ
N_LOT = {"L": (500.0,), "S": (250.0, 250.0)}
NOTIONAL = 500.0
T0_CAL = 1_788_220_800_000                 # 01.09 UTC — начало мс сигналов §13.16
CAL_SIGS = "/data/p42-t566/out/cal/b0"
CAL_BOOK = "/data/p42-t566/cal/book"
CAL_COVER_MS = 10_000
CONTRACTS = "/data/p42-tk621/universe/contracts.csv"
N_VARIANTS = 7                             # п. 7: P_A, P_V, P_m, E × 4 — для шумовой планки
DAY_MS = 86_400_000


def say(*a):
    print(*a, flush=True)


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


class GuardError(SystemExit):
    pass


# ---- лента: чистые функции (тесты — test_tk658.py) ---------------------------------------------------------------------------

def side_mask(bm, s):
    """Строки с агрессором стороны: лонг — покупатель-тейкер (`is_buyer_maker = false`), шорт — продавец-тейкер (`true`)."""
    return ~bm if s > 0 else bm


def proxy(t, p, bm, s, T):
    """Заменитель движка: последняя строка стороны со временем ≤ T (порядок файла). → (индекс, возраст мс) или (−1, None)."""
    j = int(np.searchsorted(t, T, side="right")) - 1
    m = side_mask(bm, s)
    while j >= 0:
        if m[j]:
            return j, int(T - t[j])
        j -= 1
    return -1, None


def event_rows(t, bm, j):
    """Событие тейкера с первой строкой j: непрерывная серия строк с тем же временем и той же стороной агрессора → индекс конца (не включая)."""
    k = j + 1
    while k < len(t) and t[k] == t[j] and bm[k] == bm[j]:
        k += 1
    return k


def measure(t, p, qty, bm, s, T, N, cap_ms=CAP_MS):
    """Правда по ленте после T (п. 3): A, L_A, класс L1 и его объём, V, L_V. Сделки со временем ≤ T не берутся.
    Возвращает dict; поля None — нет сделки стороны в пределах cap_ms (или конец ленты)."""
    out = dict(A=None, L_A=None, l1="none", l1_usd=None, V=None, L_V=None)
    m = side_mask(bm, s)
    j = int(np.searchsorted(t, T, side="right"))
    while j < len(t) and not m[j]:
        if t[j] - T > cap_ms:
            return out
        j += 1
    if j >= len(t) or t[j] - T > cap_ms:
        return out
    out["A"], out["L_A"] = float(p[j]), int(t[j] - T)
    k = event_rows(t, bm, j)
    pr = p[j:k]
    first = pr[0]
    at_first = pr == first
    ev_usd = float((p[j:k] * qty[j:k]).sum())
    if len(np.unique(pr)) >= 2:
        l1 = float((pr[at_first] * qty[j:k][at_first]).sum())
        out["l1"], out["l1_usd"] = ("lt" if l1 < N else "ge"), l1
    elif ev_usd >= N:
        out["l1"], out["l1_usd"] = "ge", ev_usd
    else:
        out["l1"], out["l1_usd"] = "unk", ev_usd
    need, cost, units = N, 0.0, 0.0
    i = j
    while i < len(t):
        if t[i] - T > cap_ms:
            return out
        if m[i]:
            usd = float(p[i] * qty[i])
            take = min(usd, need)
            cost += take
            units += take / float(p[i])
            need -= take
            if need <= 1e-9:
                out["V"], out["L_V"] = cost / units, int(t[i] - T)
                return out
        i += 1
    return out


def err_bp(s, truth, px):
    """Ошибка заменителя, бп, знак «хуже для входа» > 0."""
    return s * (truth - px) / px * 1e4


def entry_e8(p_e8, s, slip):
    """Цена входа движка ×1e8 (`tick.rs` sim_tail: e = p0 + frac(|p0|, slip, 10⁴), p0 = s·P, в выводе s·e)."""
    return p_e8 + s * (p_e8 * slip // 10_000)


def gross_legs(legs, s, E):
    """gross лота = Σ u·s·(px − E)/E (`tick.rs` ~2029); legs = [[мс, px ×1e8, u $, мейкер], …], E ×1e8 (или float)."""
    E = float(E)
    return float(sum(float(u) * s * (float(px) - E) / E for _, px, u, _ in legs))


def gross_shift(legs, s, E, k):
    """Формула блока E (G-path): вход на (k − 1) бп хуже, тейкерские ноги на (k − 1) бп хуже, мейкерские без изменений."""
    d = (k - 1) / 1e4
    E2 = float(E) * (1 + s * d)
    tot = 0.0
    for _, px, u, mk in legs:
        x = float(px) if mk else float(px) * (1 - s * d)
        tot += float(u) * s * (x - E2) / E2
    return tot


def choose_delta(lv, deltas=DELTAS, cmin=COVER_MIN):
    """Δ* (п. 3): наименьшее Δ, при котором покрыто ≥ cmin лотов по V (L_V ≤ Δ); иначе максимальное Δ и флаг оговорки."""
    lv = list(lv)
    n = len(lv)
    for d in deltas:
        if n and sum(1 for x in lv if x is not None and x <= d) / n >= cmin:
            return d, False
    return deltas[-1], True


def w_star(ages, eabs, grid=W_GRID, thr=1.0):
    """Правило §13.16 по ленте: наибольшее W с средним |e_A| ≤ thr на лотах с возрастом ≤ W; иначе наименьшее W. → (W*, таблица)."""
    tab = []
    for w in grid:
        v = [e for a, e in zip(ages, eabs) if a is not None and a <= w and e is not None]
        tab.append(dict(W=w, n=len(v), mean_abs=(sum(v) / len(v)) if v else None))
    ok = [r["W"] for r in tab if r["n"] and r["mean_abs"] <= thr]
    return (max(ok) if ok else grid[0]), tab


def p90(v):
    v = sorted(v)
    return v[min(len(v) - 1, int(0.9 * len(v)))] if v else None


def mean(v):
    v = list(v)
    return sum(v) / len(v) if v else None


# ---- чтение ленты --------------------------------------------------------------------------------------------------------------

_TAPE = {}


def tape_day(sym, day):
    """(t, p, qty, bm) суток UTC `day` (datetime.date) из деревьев h775; None — файла нет. Сутки ≥ 14.11 — отказ (G-tape)."""
    import tk604_stats as st
    if day > st.LAST_DAY:
        raise GuardError("G-tape: запрошены сутки %s позже 13.11" % day)
    key = (sym, day)
    if key in _TAPE:
        return _TAPE[key]
    import pandas as pd
    fn = "%s-aggTrades-%s.zip" % (sym, day.isoformat())
    res = None
    for w in ("wa", "wb", "wc"):
        pth = "/data/p42-tk621/%s/aggtrades/%s/%s" % (w, sym, fn)
        if os.path.exists(pth):
            with zipfile.ZipFile(pth) as z:
                with z.open(z.namelist()[0]) as f:
                    df = pd.read_csv(f, usecols=["price", "quantity", "transact_time", "is_buyer_maker"],
                                     dtype={"price": "float64", "quantity": "float64", "transact_time": "int64"})
            res = (df["transact_time"].values, df["price"].values, df["quantity"].values,
                   (df["is_buyer_maker"].astype(str).str.lower() == "true").values)
            break
    if len(_TAPE) > 6:
        _TAPE.clear()
    _TAPE[key] = res
    return res


def tape_around(sym, T):
    """Лента суток T, склеенная с предыдущими (для заменителя у полуночи) и следующими (проход V через полночь, ≤ 13.11) сутками."""
    import datetime
    import tk604_stats as st
    d = datetime.datetime.fromtimestamp(T / 1000, tz=datetime.timezone.utc).date()
    parts = []
    for dd in (d - datetime.timedelta(days=1), d, d + datetime.timedelta(days=1)):
        if dd > st.LAST_DAY:
            continue
        if dd != d and not ((dd < d and T % DAY_MS < 60_000) or (dd > d and DAY_MS - T % DAY_MS < CAP_MS + 1)):
            continue
        x = tape_day(sym, dd)
        if x is not None:
            parts.append(x)
    if not parts:
        return None
    t = np.concatenate([x[0] for x in parts])
    if len(t) and int(t.max()) >= st.LAST_MS:
        raise GuardError("G-tape: строка ленты позже 13.11 (%s)" % sym)
    return t, np.concatenate([x[1] for x in parts]), np.concatenate([x[2] for x in parts]), np.concatenate([x[3] for x in parts])


# ---- движок ---------------------------------------------------------------------------------------------------------------------

def _srv():
    import tk604_stats as st
    import tk625_run as r5
    import tk638_post as p8
    import tk634_run as r34
    return st, r5, p8, r34


def cell_id(side, slip):
    return "%s_base" % side if slip == 1 else "%s_s%d" % (side, slip)


def eng_dir(w, side, slip=1):
    return os.path.join(ROOT, "eng", w, cell_id(side, slip))


def cmd_eng(a):
    st, r5, _, _ = _srv()
    import tk604_run as r4
    if r4.sha256(r4.BIN) not in r5.BIN_SHA_OK:
        raise GuardError("G-bin: sha256 бинаря не из BIN_SHA_OK")
    u = r5.Uni("h775")
    var = []
    for side in SIDES:
        var.append((cell_id(side, 1), r5.BASE[side] + u.extra))
        var += [(cell_id(side, k), r5.BASE[side] + u.extra + " slip=%d" % k) for k in SLIPS]
    for w in st.TRADE_WINDOWS:
        out = os.path.join(ROOT, "eng", w.name)
        if not os.path.exists(out + "/DONE"):
            r5.tick(u, w, var, out, {"P42_DETAIL": "1"}, q("log", "eng-%s.log" % w.name))
            open(out + "/DONE", "w").close()
    bad = []
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            for f in ("trades", "daily"):
                fn = "%s-%s.csv" % (ENG_SIDE[side], f)
                a1, b1 = os.path.join(eng_dir(w.name, side), fn), os.path.join(u.base(w.name, side), fn)
                if not filecmp.cmp(a1, b1, shallow=False):
                    bad.append("%s %s %s" % (w.name, side, fn))
    json.dump(dict(bad=bad, ok=not bad), open(q("gates", "g_base.json"), "w"), indent=1)
    if bad:
        raise GuardError("G-base: не байт в байт: %s" % bad)
    say("G-base PASS (6 × 2 файла); eng готово")


def read_detail(w, side, slip=1):
    with open(os.path.join(eng_dir(w, side, slip), "%s-detail.jsonl" % ENG_SIDE[side]), encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def scored(w, entry_ms):
    st = _srv()[0]
    d = int(st.abs_day(w, [entry_ms])[0])
    return (w.lod - st.D0).days <= d <= (w.hid - st.D0).days, d


def lots_of(rec, side, t0_ms):
    """Лоты сделки: [(номер, T абс. мс, E ×1e8, legs, N)]. Лот 2 с тем же моментом и ценой, что лот 1, — одна заявка: N = 500 $ у обоих (отступление п. 3)."""
    s = []
    l2 = rec.get("lot2")
    if side == "S" and l2 is not None:
        same = int(l2["entry_ms"]) == int(rec["entry_ms"]) and int(l2["entry_px_e8"]) == int(rec["entry_px_e8"])
        n = NOTIONAL if same else NOTIONAL / 2
        s.append((1, t0_ms + int(rec["entry_ms"]), int(rec["entry_px_e8"]), rec["legs"], n))
        s.append((2, t0_ms + int(l2["entry_ms"]), int(l2["entry_px_e8"]), l2["legs"], n))
    else:
        s.append((1, t0_ms + int(rec["entry_ms"]), int(rec["entry_px_e8"]), rec["legs"], NOTIONAL))
    return s


def _measure_job(args):
    sym, items = args
    rows = []
    for it in items:
        side, T, N, E = it["side"], it["T"], it["N"], it["E"]
        s = SGN[side]
        tp = tape_around(sym, T)
        r = dict(it)
        if tp is None:
            r.update(px=None, age=None, A=None, L_A=None, l1="none", l1_usd=None, V=None, L_V=None, px_src="none")
            rows.append(r)
            continue
        t, p, qty, bm = tp
        j, age = proxy(t, p, bm, s, T)
        px = float(p[j]) if j >= 0 and age <= EW else None
        r["age"] = age
        r["px_tape"] = px
        px_e8 = int(round(px * 1e8)) if px is not None else None
        if px_e8 is not None and abs(entry_e8(px_e8, s, 1) - E) <= 1:
            r["px"], r["px_src"] = px, "tape"
        else:
            r["px"], r["px_src"] = (E / 1e8) / (1 + s * 1e-4), "engine"
        r.update(measure(t, p, qty, bm, s, T, N))
        rows.append(r)
    return rows


def contracts_tradfi():
    with open(CONTRACTS, encoding="utf-8") as f:
        return {r["symbol"]: int(r["tradfi"]) for r in csv.DictReader(f)}


MONTHS = ("jul", "aug", "sep", "oct", "nov")


def month_of(day_idx):
    st = _srv()[0]
    return MONTHS[int(st.MONTH_OF_DAY[day_idx])]


def collect_lots():
    """Лоты всех зачётных сделок баз (порядок склейки), G-leg. Читаются только время, цены и ноги — сумм P&L здесь нет."""
    st = _srv()[0]
    tf = contracts_tradfi()
    items, legbad, legn = [], [], 0
    for side in SIDES:
        for w in st.TRADE_WINDOWS:
            for i, rec in enumerate(read_detail(w.name, side)):
                ok, d = scored(w, int(rec["entry_ms"]))
                if not ok:
                    continue
                g = 0.0
                for (k, T, E, legs, N) in lots_of(rec, side, w.t0_ms):
                    g += gross_legs(legs, SGN[side], E)
                    items.append(dict(side=side, win=w.name, i=i, lot=k, sym=rec["sym"], T=T, E=E, N=N, day=d, month=month_of(d),
                                      tradfi=tf.get(rec["sym"], 0), sig_ms=w.t0_ms + int(rec["sig_ms"])))
                legn += 1
                nl = len(rec["legs"]) + (len(rec["lot2"]["legs"]) if rec.get("lot2") else 0)
                if abs(g - float(rec["gross"])) > 1e-6 * max(1, nl):
                    legbad.append((side, w.name, i, rec["sym"], g, float(rec["gross"])))
    return items, legbad, legn


def cmd_tape(a):
    items, legbad, legn = collect_lots()
    gl = dict(trades=legn, bad=len(legbad), share_ok=1 - len(legbad) / max(1, legn), examples=legbad[:10])
    json.dump(gl, open(q("gates", "g_leg.json"), "w"), indent=1, default=str)
    if gl["share_ok"] < 0.995:
        raise GuardError("G-leg: Σ ног ≠ gross у %d из %d сделок" % (len(legbad), legn))
    say("G-leg PASS: %d сделок, несовпавших %d" % (legn, len(legbad)))
    by = {}
    for it in items:
        by.setdefault(it["sym"], []).append(it)
    jobs = sorted(by.items(), key=lambda x: -len(x[1]))
    with Pool(a.procs) as pool:
        rows = [r for part in pool.imap_unordered(_measure_job, jobs) for r in part]
    rows.sort(key=lambda r: (r["side"], r["win"], r["i"], r["lot"]))
    cols = ["side", "win", "i", "lot", "sym", "tradfi", "day", "month", "sig_ms", "T", "N", "E", "px", "px_tape", "px_src", "age",
            "A", "L_A", "l1", "l1_usd", "V", "L_V"]
    with open(q("res", "lots.csv"), "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    n_eng = sum(r["px_src"] != "tape" for r in rows)
    gpx = dict(lots=len(rows), engine_px=n_eng, share_tape=1 - n_eng / max(1, len(rows)),
               examples=[(r["side"], r["sym"], r["T"], r["E"], r["px_tape"]) for r in rows if r["px_src"] != "tape"][:20])
    json.dump(gpx, open(q("gates", "g_px.json"), "w"), indent=1)
    if gpx["share_tape"] < 0.99:
        raise GuardError("G-px: заменитель ленты ≠ движку у %d из %d лотов" % (n_eng, len(rows)))
    say("G-px PASS: лотов %d, из движка %d" % (len(rows), n_eng))
    gn = {}
    for side in SIDES:
        rs = [r for r in rows if r["side"] == side]
        gn[side] = dict(lots=len(rs), days=len({r["day"] for r in rs}), crypto=sum(1 for r in rs if not r["tradfi"]), tradfi=sum(1 for r in rs if r["tradfi"]))
    gn["ok"] = all(gn[s]["days"] >= 30 and gn[s]["crypto"] >= 1 and gn[s]["tradfi"] >= 1 for s in SIDES)
    json.dump(gn, open(q("gates", "g_n.json"), "w"), indent=1)
    say("G-n:", gn)
    json.dump(price_tables(rows), open(q("res", "price58.json"), "w"), indent=1, ensure_ascii=False)
    say("tape готово")


def load_lots():
    with open(q("res", "lots.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("i", "lot", "tradfi", "day", "sig_ms", "T", "E"):
            r[k] = int(r[k])
        for k in ("N", "px", "px_tape", "A", "V", "l1_usd"):
            r[k] = float(r[k]) if r[k] not in ("", "None") else None
        for k in ("age", "L_A", "L_V"):
            r[k] = int(r[k]) if r[k] not in ("", "None") else None
    return rows


def add_err(r):
    s = SGN[r["side"]]
    r["e_A"] = err_bp(s, r["A"], r["px"]) if r["A"] is not None and r["px"] else None
    r["e_V"] = err_bp(s, r["V"], r["px"]) if r["V"] is not None and r["px"] else None
    return r


def stat_block(rs, d):
    """Таблица замера цены для набора лотов при окне правды d мс (п. 4)."""
    cA = [r for r in rs if r["L_A"] is not None and r["L_A"] <= d and r["e_A"] is not None]
    cV = [r for r in rs if r["L_V"] is not None and r["L_V"] <= d and r["e_V"] is not None]
    eA, eV = [r["e_A"] for r in cA], [r["e_V"] for r in cV]
    known = [r for r in rs if r["l1"] in ("lt", "ge")]
    out = dict(lots=len(rs), days=len({r["day"] for r in rs}), cover_A=len(cA) / len(rs) if rs else None, cover_V=len(cV) / len(rs) if rs else None)
    for nm, e in (("A", eA), ("V", eV)):
        out["e_" + nm] = dict(n=len(e), mean=mean(e), mean_abs=mean(abs(x) for x in e), p90_abs=p90([abs(x) for x in e]),
                              share_gt1=(sum(x > 1 for x in e) / len(e)) if e else None, share_lt0=(sum(x < 0 for x in e) / len(e)) if e else None)
    out["under_V"] = (out["e_V"]["mean"] - 1.0) if out["e_V"]["mean"] is not None else None
    out["l1"] = dict(lt=sum(r["l1"] == "lt" for r in rs), ge=sum(r["l1"] == "ge" for r in rs), unk=sum(r["l1"] == "unk" for r in rs),
                     none=sum(r["l1"] == "none" for r in rs), share_lt_known=(sum(r["l1"] == "lt" for r in known) / len(known)) if known else None)
    out["lag_A"] = dict(mean=mean(r["L_A"] for r in cA), p90=p90([r["L_A"] for r in cA]))
    out["lag_V"] = dict(mean=mean(r["L_V"] for r in cV), p90=p90([r["L_V"] for r in cV]))
    return out


def price_tables(rows):
    rows = [add_err(dict(r)) for r in rows]
    res = {"delta_star": {}, "w_star": {}, "age": {}, "tables": {}}
    for side in SIDES:
        rs = [r for r in rows if r["side"] == side]
        dstar, flag = choose_delta([r["L_V"] for r in rs])
        res["delta_star"][side] = dict(ms=dstar, caveat=flag, cover={d: sum(1 for r in rs if r["L_V"] is not None and r["L_V"] <= d) / len(rs) for d in DELTAS})
        cov = [r for r in rs if r["L_A"] is not None and r["L_A"] <= dstar]
        ws, tab = w_star([r["age"] for r in cov], [abs(r["e_A"]) if r["e_A"] is not None else None for r in cov])
        res["w_star"][side] = dict(W=ws, table=tab)
        ages = [r["age"] for r in rs if r["age"] is not None]
        res["age"][side] = {str(k): sum(1 for x in ages if x == k) for k in range(0, EW + 1)}
        res["age"][side]["gt5"] = sum(1 for x in ages if x > EW)
        res["age"][side]["none"] = sum(1 for r in rs if r["age"] is None)
        res["tables"][side] = {}
        for cls, f in (("all", lambda r: True), ("crypto", lambda r: not r["tradfi"]), ("tradfi", lambda r: bool(r["tradfi"]))):
            for mo in MONTHS + ("all",):
                sub = [r for r in rs if f(r) and (mo == "all" or r["month"] == mo)]
                if not sub:
                    continue
                res["tables"][side]["%s|%s" % (cls, mo)] = {str(d): stat_block(sub, d) for d in DELTAS}
    return res


# ---- блок V: сверка с книгой 13.11 ---------------------------------------------------------------------------------------------

def cmd_cal13(a):
    sigs = []
    for side, eng in (("L", "long"), ("S", "short")):
        with open(os.path.join(CAL_SIGS, "%s-sigs.csv" % eng), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ms = int(r["sig_ms"])
                if ms // DAY_MS != 73:
                    continue
                sigs.append(dict(side=side, sym=r["sym"], T=T0_CAL + ms + 4, proxy=int(r["proxy_px_e8"]) / 1e8 if r["proxy_px_e8"] else None))
    book = {}
    for pth in glob.glob(os.path.join(CAL_BOOK, "*.txt")):
        for line in open(pth, encoding="utf-8"):
            x = line.rstrip("\n").split("\t")
            if len(x) < 7:
                continue
            k, tb = (x[0], int(x[1])), int(x[2])
            if k not in book or tb >= book[k][0]:
                book[k] = (tb, float(x[3]), float(x[4]), float(x[5]), float(x[6]))
    rows = []
    for g in sigs:
        b = book.get((g["sym"], g["T"]))
        if not b or g["T"] - b[0] > CAL_COVER_MS or g["proxy"] is None:
            continue
        s = SGN[g["side"]]
        tb, bid, bq, ask, aq = b
        B, Bq = (ask, aq) if s > 0 else (bid, bq)
        tp = tape_around(g["sym"], g["T"])
        if tp is None:
            continue
        m = measure(*tp, s, g["T"], NOTIONAL)
        rows.append(dict(g, B=B, book_l1_usd=B * Bq, **m))
    res = dict(n=len(rows), long=sum(r["side"] == "L" for r in rows), short=sum(r["side"] == "S" for r in rows))
    pb = [err_bp(SGN[r["side"]], r["proxy"], r["B"]) for r in rows]
    res["proxy_vs_book"] = dict(mean=mean(pb), mean_abs=mean(abs(x) for x in pb), p90_abs=p90([abs(x) for x in pb]))
    for d in DELTAS:
        ra = [r for r in rows if r["L_A"] is not None and r["L_A"] <= d]
        rv = [r for r in rows if r["L_V"] is not None and r["L_V"] <= d]
        dA = [err_bp(SGN[r["side"]], r["A"], r["B"]) for r in ra]
        dV = [err_bp(SGN[r["side"]], r["V"], r["B"]) for r in rv]
        res["d%d" % d] = dict(n_A=len(ra), A_vs_book=dict(mean=mean(dA), mean_abs=mean(abs(x) for x in dA), p90_abs=p90([abs(x) for x in dA])),
                              n_V=len(rv), V_vs_book=dict(mean=mean(dV), mean_abs=mean(abs(x) for x in dV), p90_abs=p90([abs(x) for x in dV])))
    agree = {"lt_lt": 0, "lt_ge": 0, "ge_lt": 0, "ge_ge": 0, "unk": 0}
    for r in rows:
        bk = "lt" if r["book_l1_usd"] < NOTIONAL else "ge"
        if r["l1"] in ("lt", "ge"):
            agree["%s_%s" % (r["l1"], bk)] += 1
        else:
            agree["unk"] += 1
    res["l1_tape_vs_book"] = agree
    res["book_l1_lt500"] = sum(r["book_l1_usd"] < NOTIONAL for r in rows)
    json.dump(res, open(q("res", "cal13.json"), "w"), indent=1)
    say(json.dumps(res, ensure_ascii=False)[:1500])


# ---- P&L (только после PASS критика) ------------------------------------------------------------------------------------------

def reprice_gross(rec, side, t0_ms, newE):
    """gross сделки с ценами входа лотов newE {номер лота: цена ($)}; ноги фиксированы."""
    s = SGN[side]
    g = 0.0
    for (k, T, E, legs, N) in lots_of(rec, side, t0_ms):
        g += gross_legs(legs, s, newE.get(k, E / 1e8) * 1e8)
    return g


def impute_means(rows, side, key, lag, dstar):
    """Средняя ошибка покрытых лотов стороны по классу (крипто / TradFi) при Δ* — для лотов без правды (п. 5)."""
    out = {}
    for tf in (0, 1):
        v = [r[key] for r in rows if r["side"] == side and r["tradfi"] == tf and r[lag] is not None and r[lag] <= dstar and r[key] is not None]
        out[tf] = mean(v)
    allv = [r[key] for r in rows if r["side"] == side and r[lag] is not None and r[lag] <= dstar and r[key] is not None]
    for tf in (0, 1):
        if out[tf] is None:
            out[tf] = mean(allv)
    return out


def variant_prices(rows, side, var, dstar):
    """{(окно, i): {лот: цена входа $}} варианта P_A / P_V / P_m / P_V100 (п. 5; P_V100 — заметка критика 1). Число вменённых лотов."""
    s = SGN[side]
    key, lag, d = {"P_A": ("e_A", "L_A", dstar), "P_V": ("e_V", "L_V", dstar), "P_V100": ("e_V", "L_V", 100), "P_m": ("e_V", "L_V", dstar)}[var]
    im = impute_means(rows, side, key, lag, d)
    ebar = mean(r[key] for r in rows if r["side"] == side and r[lag] is not None and r[lag] <= d and r[key] is not None)
    out, nimp = {}, 0
    for r in rows:
        if r["side"] != side:
            continue
        if var == "P_m":
            e = ebar
        elif r[lag] is not None and r[lag] <= d and r[key] is not None:
            e = r[key]
        else:
            e, nimp = im[r["tradfi"]], nimp + 1
        out.setdefault((r["win"], r["i"]), {})[r["lot"]] = r["px"] * (1 + s * e / 1e4)
    return out, nimp, ebar


def cmd_pnl(a):
    st, r5, p8, r34 = _srv()
    import tk643_run as t43
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    rows = [add_err(r) for r in load_lots()]
    price = json.load(open(q("res", "price58.json"), encoding="utf-8"))
    res = {"sides": {}}
    for side in SIDES:
        s = SGN[side]
        dirs = {w.name: (eng_dir(w.name, side), side) for w in st.TRADE_WINDOWS}
        bser, bkinds, bpnl = t43.load_cell(st, p8, u, side, dirs)
        ref = t43.load_cell(st, p8, u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})[0]
        if abs(float(bser.val.sum()) - float(ref.val.sum())) > 1e-6 or len(bser.val) != len(ref.val):
            raise GuardError("G-splice %s: Σ %.6f ≠ готовой базы %.6f" % (side, bser.val.sum(), ref.val.sum()))
        recs = {w.name: read_detail(w.name, side) for w in st.TRADE_WINDOWS}
        order = []                                      # (окно, i, gross, fees) в порядке склейки
        for w in st.TRADE_WINDOWS:
            for i, rec in enumerate(recs[w.name]):
                if scored(w, int(rec["entry_ms"]))[0]:
                    order.append((w, i, rec))
        if len(order) != len(bser.val):
            raise GuardError("порядок %s: сделок detail %d ≠ ряда %d" % (side, len(order), len(bser.val)))
        base_val = np.array([float(rec["gross"]) - float(rec["fees"]) for _, _, rec in order])
        if not np.allclose(base_val, bser.val, atol=1e-6):
            raise GuardError("порядок %s: P&L detail ≠ ряда" % side)
        dstar = price["delta_star"][side]["ms"]
        out = {"delta_star": dstar, "base": t43.stat_row(st, p8, r34, bser, bkinds, bpnl, bser, ncoins)}
        out["base"]["noise"] = t43.noise_bar(out["base"]["sharpe"], N_VARIANTS) if "sharpe" in out["base"] else None
        for var in ("P_A", "P_V", "P_m", "P_V100"):
            newp, nimp, ebar = variant_prices(rows, side, var, dstar)
            val = np.array([reprice_gross(rec, side, w.t0_ms, newp.get((w.name, i), {})) - float(rec["fees"]) for w, i, rec in order])
            ser = dataclasses.replace(bser, val=val)
            r = t43.stat_row(st, p8, r34, ser, bkinds, val, bser, ncoins)
            r.update(imputed_lots=nimp, ebar_bp=ebar, noise=t43.noise_bar(r["sharpe"], N_VARIANTS) if "sharpe" in r else None)
            d = r["delta"]
            r["dsum_ci95"] = [d["dsum"] - 1.96 * d["se_sum"], d["dsum"] + 1.96 * d["se_sum"]]
            r["mde_sum"] = 2.8 * d["se_sum"]
            out[var] = r
        gp = {}
        for k in SLIPS:
            dk = {w.name: (eng_dir(w.name, side, k), side) for w in st.TRADE_WINDOWS}
            eser, ekinds, epnl = t43.load_cell(st, p8, u, side, dk)
            r = t43.stat_row(st, p8, r34, eser, ekinds, epnl, bser, ncoins)
            r["noise"] = t43.noise_bar(r["sharpe"], N_VARIANTS) if "sharpe" in r else None
            ana = float(sum(gross_shift_trade(rec, side, w.t0_ms, k) - float(rec["gross"]) for w, i, rec in order))
            eng = float(eser.val.sum() - bser.val.sum())
            ok = abs(eng - ana) <= max(2.0, 0.10 * abs(eng))
            gp[k] = dict(eng_dsum=eng, formula_dsum=ana, ok=ok)
            out["E_slip%d" % k] = r
        out["g_path"] = gp
        out["g_path_ok"] = all(v["ok"] for v in gp.values())
        main = "P_V"
        if not out["g_path_ok"]:
            kk = int(round(1 + (out["P_V"]["ebar_bp"] or 0)))
            main = "E_slip%d" % kk if kk in SLIPS else ("base" if kk == 1 else "E_slip%d" % min(SLIPS, key=lambda x: abs(x - kk)))
        m = out[main]
        if main == "base":
            ans = "сдвиг в шуме"
        else:
            lo, hi = m["delta"]["dsum"] - 1.96 * m["delta"]["se_sum"], m["delta"]["dsum"] + 1.96 * m["delta"]["se_sum"]
            ans = "калибровка сдвигает базу" if (lo > 0 or hi < 0) else "сдвиг в шуме"
        out["answer"] = dict(main=main, answer=ans)
        res["sides"][side] = out
        say(side, main, ans)
    json.dump(res, open(q("res", "pnl58.json"), "w"), indent=1, ensure_ascii=False, default=float)
    say("pnl готово")


def gross_shift_trade(rec, side, t0_ms, k):
    s = SGN[side]
    return float(sum(gross_shift(legs, s, E, k) for (_, _, E, legs, _) in lots_of(rec, side, t0_ms)))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("eng")
    t = sp.add_parser("tape")
    t.add_argument("--procs", type=int, default=6)
    sp.add_parser("cal13")
    sp.add_parser("pnl")
    a = ap.parse_args()
    {"eng": cmd_eng, "tape": cmd_tape, "cal13": cmd_cal13, "pnl": cmd_pnl}[a.cmd](a)


if __name__ == "__main__":
    main()
