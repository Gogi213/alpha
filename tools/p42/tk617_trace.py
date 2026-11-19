#!/usr/bin/env python3
"""TK-617 v2: ручная сверка сделок баз П-42 с лентой aggTrades — независимый повтор `tick.rs` (sim_tail / Pos / run_tick) на Python.

Для выбранных сделок (июль / октябрь, лонг и шорт, разные выходы) читает ленту монеты, повторяет вход по ленте (§13.16, `ew = 5 мс`, `ent_x_bp = 0`),
второй лот шорта (`e2`), тейки, безубыток, трейл, возврат за уровень (`rt`), выход по времени (`tx`), жёсткий стоп 5 %, задержку выхода 4 мс и
проскальзывание 1 бп, считает gross и комиссию и сравнивает с движком (`trades.parquet`): цена входа, время и вид выхода, число тейков, gross, комиссия.
Фандинг не сверяется (метрика TK-599 `gf = gross − fees`).
"""
import argparse
import datetime
import os
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = "/data/p42-tk599"
WINDOWS = ("wa", "wb", "wc")
D0 = datetime.date(2026, 7, 1)
DAY_MS = 86_400_000
TAKER_BPS, MAKER_BPS = 3.15, 1.26                    # leg_fee_bps (lob/costs.rs): после возврата
NOTIONAL = 500.0
LAT, LAT_EXIT, SLIP, W_MS = 4, 4, 1.0, 5
EX = {"L": dict(n_tp=8, t_low=1000, t_high=5000, q=50, be=True, a=2000, r=2000, s_pm=5000, tx_min=0, tx_x_pm=0, rt_f_pm=200, rt_s=20, be_mode=0, e2_pm=0),
      "S": dict(n_tp=8, t_low=1000, t_high=5000, q=50, be=True, a=2000, r=2000, s_pm=5000, tx_min=20, tx_x_pm=100, rt_f_pm=200, rt_s=2, be_mode=1, e2_pm=50)}


def load_day(sym, day):
    fn = "%s-aggTrades-%s.zip" % (sym, day.isoformat())
    for w in WINDOWS:
        p = "%s/%s/aggtrades/%s/%s" % (ROOT, w, sym, fn)
        if os.path.exists(p):
            with zipfile.ZipFile(p) as z:
                with z.open(z.namelist()[0]) as f:
                    df = pd.read_csv(f, usecols=["price", "transact_time", "is_buyer_maker"], dtype={"price": "float64", "transact_time": "int64"})
            return df["transact_time"].values, df["price"].values, (df["is_buyer_maker"].astype(str).str.lower() == "true").values
    return None


class Tape:
    def __init__(self, sym, t0):
        self.sym, self.cache = sym, {}
        self.day0 = datetime.datetime.fromtimestamp(t0 / 1000, datetime.timezone.utc).date()

    def day(self, d):
        if d not in self.cache:
            self.cache[d] = load_day(self.sym, d)
        return self.cache[d]

    def last_side(self, t, taker_bm, cap):
        """Последняя сделка стороны входа ≤ t не старше cap мс: (цена, возраст) или None (tape_last_side)."""
        d = datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc).date()
        for dd in (d, d - datetime.timedelta(days=1)):
            x = self.day(dd)
            if x is None:
                continue
            ts, ps, bm = x
            hi = int(np.searchsorted(ts, t, "right"))
            for i in range(hi - 1, -1, -1):
                age = t - int(ts[i])
                if age > cap:
                    return None
                if bm[i] == taker_bm:
                    return float(ps[i]), age
        return None

    def after(self, t):
        """Итератор (мс, цена, bm) по сделкам со временем > t."""
        d = datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc).date()
        for k in range(0, 6):
            dd = d + datetime.timedelta(days=k)
            x = self.day(dd)
            if x is None:
                continue
            ts, ps, bm = x
            lo = int(np.searchsorted(ts, t, "right")) if k == 0 else 0
            for i in range(lo, len(ts)):
                yield int(ts[i]), float(ps[i]), bool(bm[i])


class Pos:
    """Позиция в длинной раме (цены × s); логика `tick.rs::Pos`."""

    def __init__(self, s, e, p0, entry_ms, natr, cfg, lp, notional, log):
        self.s, self.e, self.cfg, self.lp, self.log = s, e, cfg, lp, log
        ea = abs(e)
        self.nat = lambda m: ea * natr * m / 1000.0
        n = cfg["n_tp"]
        self.tps = [e + self.nat(cfg["t_low"] + (cfg["t_high"] - cfg["t_low"]) * i // (n - 1)) for i in range(n)]
        self.lot = notional * cfg["q"] / (100.0 * n)
        self.rem = notional
        self.a_dist, self.r_dist = self.nat(cfg["a"]), self.nat(cfg["r"])
        self.stop_base = e - ea * cfg["s_pm"] / 100000.0
        self.be_on = self.armed = False
        self.trail, self.maxp, self.next_tp = -1e30, p0, 0
        self.legs = []                                     # (мс, цена в раме, $ , мейкер)
        la = abs(lp)
        self.tx_ms, self.tx_x = cfg["tx_min"] * 60000, la * cfg["tx_x_pm"] / 100000.0
        self.rt_d, self.rt_ms = la * cfg["rt_f_pm"] / 100000.0, cfg["rt_s"] * 1000
        self.rules = cfg["tx_min"] > 0 or cfg["rt_f_pm"] > 0
        self.touched, self.broke, self.below = None, False, None

    def fill_takes(self, ms, p):
        while self.next_tp < len(self.tps) and p > self.tps[self.next_tp]:
            self.legs.append((ms, self.tps[self.next_tp], self.lot, True))
            self.rem -= self.lot
            self.log("тейк %d: цена %.8g (сделка ленты %.8g)" % (self.next_tp + 1, self.s * self.tps[self.next_tp], self.s * p))
            if self.cfg["be"] and ((self.cfg["be_mode"] == 0 and self.next_tp == 0) or (self.cfg["be_mode"] == 2 and self.next_tp == 1)):
                self.be_on = True
                self.stop_base = max(self.stop_base, self.e)
                self.log("стоп → цена входа %.8g" % (self.s * self.e))
            self.next_tp += 1

    def on_trade(self, ms, p):
        tl = self.trail if self.armed else -1e30
        sp = max(self.stop_base, tl)
        if p <= sp:
            return "trail" if tl > self.stop_base else ("be" if self.be_on else "stop")
        self.fill_takes(ms, p)
        self.maxp = max(self.maxp, p)
        if not self.armed and self.maxp - self.e >= self.a_dist:
            self.armed = True
            self.log("трейл взведён (макс %.8g)" % (self.s * self.maxp))
        if self.armed:
            self.trail = max(self.trail, self.maxp - self.r_dist)
        if self.rules:
            return self.level_exit(ms, p)
        return None

    def level_exit(self, ms, p):
        if self.touched is None and p >= self.lp:
            self.touched = ms
            self.log("касание уровня %.8g сделкой %.8g" % (self.s * self.lp, self.s * p))
        if self.touched is None:
            return None
        if not self.broke and p >= self.lp + self.tx_x:
            self.broke = True
        if self.tx_ms > 0 and not self.broke and ms >= self.touched + self.tx_ms:
            return "tx"
        if self.rt_d > 0:
            if p <= self.lp - self.rt_d:
                if self.below is None:
                    self.below = ms
                if ms - self.below >= self.rt_ms:
                    return "rt"
            else:
                self.below = None
        return None


def sim_lot(tape, entry_ms, p0, s, natr, cfg, lp, notional, log):
    """sim_fill_lc: вход в `entry_ms` по рамной цене p0 (+проскальзывание), сделки строго после entry_ms."""
    e = p0 + abs(p0) * SLIP / 1e4
    pos = Pos(s, e, p0, entry_ms, natr, cfg, lp, notional, log)
    log("вход: мс %d, цена ленты %.8g → с проскальзыванием %.8g; стоп %.8g; тейк1 %.8g; тейк8 %.8g; лот $%.3f; A=%.8g R=%.8g" % (
        entry_ms, s * p0, s * e, s * pos.stop_base, s * pos.tps[0], s * pos.tps[-1], pos.lot, pos.a_dist, pos.r_dist))
    pending, ex, last = None, None, (entry_ms, p0)
    for ms, px, _ in tape.after(entry_ms):
        p = s * px
        last = (ms, p)
        if pending is not None:
            if ms >= pending[0]:
                ex = (ms, p, pending[1])
                break
            pos.fill_takes(ms, p)
            continue
        k = pos.on_trade(ms, p)
        if k:
            log("срабатывание %s: сделка ленты %.8g в мс %d (выход через %d мс)" % (k, s * p, ms, LAT_EXIT))
            pending = (ms + LAT_EXIT, k)
    if ex is None:
        ex = (last[0], last[1], "forced")
    xm, xp, kind = ex
    pxf = xp - abs(xp) * SLIP / 1e4
    pos.legs.append((xm, pxf, pos.rem, False))
    gross = fees = 0.0
    entry_true = s * e
    fees += notional * TAKER_BPS / 1e4
    for _, lpx, u, mk in pos.legs:
        px_true = s * lpx
        gross += u * s * (px_true - entry_true) / entry_true
        fees += u * (px_true / entry_true) * (MAKER_BPS if mk else TAKER_BPS) / 1e4
    log("выход %s: мс %d, цена %.8g (после проскальзывания %.8g), тейков %d" % (kind, xm, s * xp, s * pxf, pos.next_tp))
    return dict(entry_ms=entry_ms, entry_px=entry_true, exit_ms=xm, kind=kind, takes=pos.next_tp, gross=gross, fees=fees)


def replay(r, log):
    s = int(r.s)
    cfg = EX["L"] if s > 0 else EX["S"]
    tape = Tape(r.sym, r.t_in)
    taker_bm = s < 0
    lp = s * abs(r.level_px_e8) / 1e8
    natr = r.natr_e8 / 1e8
    T = int(r.t_sig) + LAT
    ls = tape.last_side(T, taker_bm, W_MS)
    if ls is None:
        log("вход: нет сделки стороны входа ≤ T за %d мс — отказ (Stale)" % W_MS)
        return None
    p0 = s * ls[0]
    if p0 - lp > 0:
        log("вход: цена %.8g за уровнем %.8g — отказ (Beyond)" % (ls[0], abs(lp)))
        return None
    log("сигнал мс %d, уровень %.8g, NATR %.6g, расстояние до уровня %.1f бп" % (r.t_sig, abs(lp), natr, (lp - p0) / abs(lp) * 1e4))
    lot_n = NOTIONAL / 2 if cfg["e2_pm"] > 0 else NOTIONAL
    t1 = sim_lot(tape, T, p0, s, natr, cfg, lp, lot_n, log)
    res = t1
    if cfg["e2_pm"] > 0:
        d = abs(lp) * cfg["e2_pm"] / 100000.0
        if p0 >= lp - d:
            log("лот 2: сразу (цена лота 1 в пределах d = %.8g)" % d)
            m2, q0 = T, p0
        else:
            m2 = q0 = None
            for ms, px, _ in tape.after(T):
                if ms >= t1["exit_ms"]:
                    break
                p = s * px
                if p >= lp:
                    break
                if p >= lp - d:
                    t2 = ms + LAT
                    if t2 >= t1["exit_ms"]:
                        break
                    l2 = tape.last_side(t2, taker_bm, W_MS)
                    if l2 is not None and s * l2[0] - lp <= 0:
                        m2, q0 = t2, s * l2[0]
                    break
        if m2 is None:
            log("лот 2: отменён/отказ")
        else:
            log("лот 2: вход в мс %d" % m2)
            t2 = sim_lot(tape, m2, q0, s, natr, cfg, lp, lot_n, log)
            res = dict(entry_ms=t1["entry_ms"], entry_px=t1["entry_px"], exit_ms=max(t1["exit_ms"], t2["exit_ms"]), kind=t1["kind"] if t1["exit_ms"] >= t2["exit_ms"] else t2["kind"],
                       takes=t1["takes"] + t2["takes"], gross=t1["gross"] + t2["gross"], fees=t1["fees"] + t2["fees"])
            if t1["kind"] == "stop" or t2["kind"] == "stop":
                res["kind"] = "stop"
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/data/p42-tk617")
    ap.add_argument("--out", default="/data/p42-tk617/trace.md")
    ap.add_argument("--per", type=int, default=1, help="сделок на (ряд, месяц, вид выхода)")
    a = ap.parse_args()
    T = pd.read_parquet(os.path.join(a.dir, "trades.parquet"))
    T["hold"] = T["t_out"] - T["t_in"]
    T["month"] = pd.cut(T["abs_day"], [-1, 30, 61, 91, 122, 140], labels=["jul", "aug", "sep", "oct", "nov"]).astype(str)
    sel = []
    for st in ("L_base", "S_base"):
        for m in ("jul", "oct"):
            for k in ("stop", "trail", "rt", "be", "tx"):
                d = T[(T["set"] == st) & (T["month"] == m) & (T["kind"] == k)].sort_values("hold")
                d = d[d["hold"] <= 4 * 3600_000]
                if len(d):
                    sel.append(d.iloc[len(d) // 2: len(d) // 2 + a.per])
    S = pd.concat(sel)
    out, ok, bad = [], 0, 0
    for r in S.itertuples():
        lines = []
        res = replay(r, lines.append)
        t_out_eng = int(r.t_out)
        head = "### %s %s %s: сигнал %s UTC, ур. %d, вид %s, тейков %d" % (r.set, r.sym, r.month, pd.to_datetime(r.t_sig, unit="ms", utc=True).strftime("%m-%d %H:%M:%S.%f")[:-3], r.level, r.kind, r.takes)
        if res is None:
            bad += 1
            out.append(head + "\n\nповтор: вход отклонён (движок вошёл) → РАСХОЖДЕНИЕ\n\n" + "\n".join("- " + x for x in lines))
            continue
        e_px = r.entry_px_e8 / 1e8
        chk = [("цена входа", abs(res["entry_px"] / e_px - 1) < 5e-6, "%.8g / %.8g" % (res["entry_px"], e_px)),
               ("вид выхода", res["kind"] == r.kind, "%s / %s" % (res["kind"], r.kind)),
               ("тейков", res["takes"] == r.takes, "%d / %d" % (res["takes"], r.takes)),
               ("время выхода, мс", abs(res["exit_ms"] - t_out_eng) <= 1, "%d / %d" % (res["exit_ms"], t_out_eng)),
               ("gross $", abs(res["gross"] - r.gross) < 0.005, "%+.4f / %+.4f" % (res["gross"], r.gross)),
               ("комиссия $", abs(res["fees"] - r.fees) < 0.005, "%.4f / %.4f" % (res["fees"], r.fees))]
        good = all(c[1] for c in chk)
        ok += good
        bad += not good
        out.append(head + "\n\n" + "| проверка | повтор / движок | |\n|---|---|---|\n" + "\n".join("| %s | %s | %s |" % (c[0], c[2], "ок" if c[1] else "**РАСХОЖДЕНИЕ**") for c in chk)
                   + "\n\nХод повтора:\n" + "\n".join("- " + x for x in lines[:40]) + "\n")
        print(r.set, r.sym, r.month, r.kind, "OK" if good else "DIFF", flush=True)
    open(a.out, "w", encoding="utf-8").write("# Ручная сверка сделок с лентой\n\nСовпало %d из %d.\n\n" % (ok, ok + bad) + "\n".join(out) + "\n")
    print("written", a.out, ok, bad)


if __name__ == "__main__":
    main()
