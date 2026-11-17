"""П-42 TK-587: сделка по сделкам (тик за тиком) после входа — повтор `Pos::on_trade` / `sim_tail` из `src/breakout/tick.rs` на Python.

Берёт запись сделки из `*-detail.jsonl` (клетка `ow_e`), читает ленту aggTrades монеты, повторяет вход по ленте (§13.16: последняя сделка
стороны ≤ сигнал + lat), тейки, безубыток, трейл, стоп и сверяет выход с движком (время, цена). Движок не меняется.

  python3 level_trace.py <detail.jsonl> <СИМВОЛ> <sig_min> <out_dir> [long|short]   → <out_dir>/<СИМВОЛ>-<sig_min>-trace.json, -ticks.csv, -tape.npz
Цены в json — настоящие (не в длинной раме); шорт считается в раме с обратным знаком, как в движке."""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import charts as C  # noqa: E402  (extract_ticks, T0)
from levport import td  # noqa: E402

SCALE = 10**8
LAT = 4
SLIP_BPS = 1
LAT_EXIT = 4
EX = dict(n_tp=8, t_low_m=1000, t_high_m=5000, q_pct=50, a_m=2000, r_m=2000, s_pm=5000, be=True)
NOTIONAL_U = 500_000_000


def frac(x, num, den):
    return td(x * num, den)


class Pos:
    """Порт `Pos` (длинная рама). Возвращает вид выхода из `on_trade` или None."""

    def __init__(self, e, p0, natr_e8, ex=EX):
        ea = abs(e)

        def nat(x_m):
            return ea * natr_e8 * x_m // (SCALE * 1000)

        n = ex["n_tp"]
        self.e = e
        self.tps = [e + nat(ex["t_low_m"] + (ex["t_high_m"] - ex["t_low_m"]) * i // (n - 1)) for i in range(n)]
        self.lots = [NOTIONAL_U * ex["q_pct"] // (100 * n)] * n
        self.a_dist, self.r_dist = nat(ex["a_m"]), nat(ex["r_m"])
        self.be_cfg = ex["be"]
        self.rem = NOTIONAL_U
        self.stop_base = e - frac(ea, ex["s_pm"], 100_000)
        self.be_on = False
        self.armed = False
        self.trail = -(1 << 62)
        self.maxp = p0
        self.next_tp = 0
        self.legs = []

    def fill_takes(self, ms, p):
        while self.next_tp < len(self.tps) and p > self.tps[self.next_tp]:
            self.legs.append((ms, self.tps[self.next_tp], self.lots[self.next_tp]))
            self.rem -= self.lots[self.next_tp]
            if self.next_tp == 0 and self.be_cfg:
                self.be_on = True
                self.stop_base = max(self.stop_base, self.e)
            self.next_tp += 1

    def stop_level(self):
        return max(self.stop_base, self.trail if self.armed else -(1 << 62))

    def on_trade(self, ms, p):
        trail_lvl = self.trail if self.armed else -(1 << 62)
        sp = max(self.stop_base, trail_lvl)
        if p <= sp:
            return "trail" if trail_lvl > self.stop_base else ("be" if self.be_on else "stop")
        self.fill_takes(ms, p)
        self.maxp = max(self.maxp, p)
        if not self.armed and self.maxp - self.e >= self.a_dist:
            self.armed = True
        if self.armed:
            self.trail = max(self.trail, self.maxp - self.r_dist)
        return None


def run(detail_path, sym, sig_min, out_dir, side="long"):
    sg = 1 if side == "long" else -1
    r = None
    for line in open(detail_path, encoding="utf-8"):
        d = json.loads(line)
        if d["sym"] == sym and d["sig_min"] == sig_min:
            r = d
    assert r is not None, "сделка не найдена"
    sig_abs = r["sig_ms"] + C.T0
    entry_abs = r["entry_ms"] + C.T0
    exit_abs = r["exit_ms"] + C.T0
    tk_t, tk_p, tk_bm = C.extract_ticks(sym, sig_abs - 120_000, exit_abs + 600_000)
    tk_p8 = np.rint(tk_p * SCALE).astype(np.int64)
    # цена-заменитель: последняя сделка стороны входа (лонг — покупатель-тейкер bm = false; шорт — продавец-тейкер bm = true) ≤ сигнал + lat
    T = sig_abs + LAT
    idx = np.where((tk_t <= T) & (tk_bm == (side == "short")))[0]
    p0 = sg * int(tk_p8[idx[-1]])  # рама
    e = p0 + frac(abs(p0), SLIP_BPS, 10_000)
    pos = Pos(e, p0, r["natr_e8"])
    after = np.where(tk_t > T)[0]
    rows, exit_ = [], None
    pending = None
    lvl = r["level_px_e8"]
    for i in after:
        ms, p = int(tk_t[i]), sg * int(tk_p8[i])
        st_before = pos.stop_level()
        if pending is not None:
            due, kind = pending
            if ms >= due:
                exit_ = (ms, p, kind)
                rows.append((ms, sg * p, "выход по задержке (исполнение по этой сделке)"))
                break
            pos.fill_takes(ms, p)
            rows.append((ms, sg * p, "ожидание исполнения выхода"))
            continue
        kind = pos.on_trade(ms, p)
        rows.append((ms, sg * p, kind or ""))
        if kind:
            pending = (ms + LAT_EXIT, kind)
            trig = (ms, sg * p, kind, sg * st_before)
    xm, xp, kind = exit_
    px_frame = xp - frac(abs(xp), SLIP_BPS, 10_000)
    res = dict(sym=sym, side=side, sig_min=sig_min, p0=sg * p0, entry_px_model=sg * e, entry_px_engine=r["entry_px_e8"], level=lvl, natr_e8=r["natr_e8"],
               stop_level=sg * pos.stop_base, tps=[sg * x for x in pos.tps], lots=pos.lots, a_dist=pos.a_dist, r_dist=pos.r_dist,
               exit_ms=xm - C.T0, exit_ms_engine=r["exit_ms"], exit_px_model=sg * px_frame, exit_px_engine=r["legs"][-1][1], kind=kind,
               kind_engine=r["kind"], takes=pos.next_tp, takes_engine=r["takes"], trigger=dict(ms=trig[0], px=trig[1], kind=trig[2], stop=trig[3]),
               maxp=sg * pos.maxp, armed=pos.armed, be_on=pos.be_on, n_ticks=len(rows), entry_abs=entry_abs, T=T, tick_rows=len(tk_t),
               take_legs=[(ms_ + 0, sg * px_, u_) for ms_, px_, u_ in pos.legs], trail=sg * pos.trail if pos.armed else None)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{sym}-{sig_min}")
    json.dump(res, open(base + "-trace.json", "w"), ensure_ascii=False, indent=1)
    with open(base + "-ticks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ms_abs", "px_e8", "note"])
        for ms, p, note in rows:
            w.writerow([ms, p, note])
    # лента от сигнала − 2 мин до выхода + 10 мин (для графиков), цена настоящая
    np.savez_compressed(base + "-tape.npz", t=tk_t, p=tk_p8, bm=tk_bm)
    return res


if __name__ == "__main__":
    out = run(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "long")
    print(json.dumps({k: v for k, v in out.items() if k not in ("tps", "take_legs")}, ensure_ascii=False))
