#!/usr/bin/env python3
"""TK-466: синтетика S3/S5/S6 для tk461_s_events (минутные csv.gz со ставшим известным ответом). python -I test_tk466_s_synth.py ; расхождение -> rc 1."""
import datetime as dt
import os
import sys
import tempfile
import numpy as np
import pandas as pd

H = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [H, os.path.join(H, "..")]
import tk461_s_events as T  # noqa: E402

T0 = int(dt.datetime(2024, 1, 11, 0, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)   # 19:00 EST 10.01 -> старт окна Азии G5
ok_all = True


def check(name, cond, info=""):
    global ok_all
    ok_all &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, info)


def write(path, t, o, h, l, c):
    pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1.0)).to_csv(path, index=False, compression="gzip")


def hourly(path, ob, hb, lb, cb):
    """почасовые OHLC -> минуты: мин.0 = open, мин.1 = high, мин.2 = low, мин.59 = close, остальные - середина open/close (внутри диапазона)."""
    n = len(hb)
    idx = np.arange(n * 60) % 60
    rep = lambda x: np.repeat(np.asarray(x, float), 60)
    mo, mh, ml, mc = rep(ob), rep(hb), rep(lb), rep(cb)
    v = (mo + mc) / 2
    v = np.where(idx == 0, mo, np.where(idx == 1, mh, np.where(idx == 2, ml, np.where(idx == 59, mc, v))))
    write(path, T0 + np.arange(n * 60) * 60000, v, np.maximum(v, np.where(idx == 1, mh, v)), np.minimum(v, np.where(idx == 2, ml, v)), v)


# ---- S3: 1H, баров 70; хай-свип 30, щель после свипа, слома нет (A6 без A8), t0=q+2, ss = дальняя граница щели ----
hb = np.full(70, 101.0); lb = np.full(70, 99.0); cb = np.full(70, 100.0); ob = np.full(70, 100.0)
hb[20] = 105                      # свинг-хай L=105
lb[25] = 95                       # свинг-лой (последний до свипа), last=95
hb[30], lb[30], cb[30] = 106, 102, 104      # свип: h>L, c<=L
hb[31], lb[31], cb[31] = 104, 103, 103.5    # q=31: l[31] > h[33]
hb[32], lb[32], cb[32] = 103, 100, 101
hb[33], lb[33], cb[33] = 101, 100, 100.5    # t0=33: вход по закрытию 100.5
ob[30:34] = cb[30:34]                       # open внутри [low, high] каждого бара
p3 = tempfile.gettempdir() + "/s3syn.csv.gz"
hourly(p3, ob, hb, lb, cb)
ev = T.s3_events(p3, "SYN")
check("S3: события есть", len(ev) > 0)
e = ev[(ev.tf == 60) & (ev.dir == "H") & (ev.anchor == 2) & (ev.t0 == 33)] if len(ev) else ev
check("S3: событие найдено (1H, хай, k=2, t0=33)", len(e) == 1, f"всего S3 строк {len(ev)}")
if len(e):
    e = e.iloc[0]
    check("S3: сторона шорт, A6 без A8", e.side == -1 and e.a8 == 0)
    check("S3: ss_bps = (l[q] - c[t0])/c[t0]*1e4", abs(e.ss_bps - (103 - 100.5) / 100.5 * 1e4) < 1e-6, f"{e.ss_bps:.3f}")
    check("S3: tt0 = открытие бара t0", e.tt0 == T0 + 33 * 3600000, str(e.tt0))
    check("S3: p459=0 (контрольных баров C2 в коротком ряду нет)", e.p459 == 0)
    check("S3: ot1=ss, fallback=0", e.ot1_bps == e.ss_bps and e.ot1_fallback == 0)
# слом: закрытие бара 32 ниже last=95 -> A8, t0 = max(q+2, бар слома) = 33
cb2, lb2 = cb.copy(), lb.copy(); cb2[32] = 90; lb2[32] = 90
p3b = tempfile.gettempdir() + "/s3syn2.csv.gz"
hourly(p3b, ob, hb, lb2, cb2)
ev2 = T.s3_events(p3b, "SYN")
e2 = ev2[(ev2.tf == 60) & (ev2.dir == "H") & (ev2.anchor == 2) & (ev2.a8 == 1)] if len(ev2) else ev2
check("S3: слом -> a8=1, t0 = max(q+2, бар слома) = 33", len(e2) == 1 and e2.iloc[0].t0 == 33, str(e2[["t0", "a8"]].values.tolist()) if len(e2) else "нет")

# ---- S5: 3 суток-окна G5; пробой хая в 09:05 UTC (04:05 NY) растущими выбросами в окнах +0/+8/+16 ч ----
def s5_file(path, opp):
    n = 2200
    t = T0 + np.arange(n) * 60000
    h = np.full(n, 100.5); l = np.full(n, 99.5)
    for q in range(5):
        h[480 * q + 1] = 101.0; l[480 * q] = 99.0           # граница Азии в каждом окне R
    for q in range(4):
        h[545 + 480 * q] = 103.0 + q                        # выброс над границей, каждый следующий выше
    if opp:
        l[550] = 98.0                                       # противоположная граница касалась в том же часе
    write(path, t, (h + l) / 2, h, l, (h + l) / 2)


pa, pb = tempfile.gettempdir() + "/s5a.csv.gz", tempfile.gettempdir() + "/s5b.csv.gz"
s5_file(pa, False); s5_file(pb, True)
hk = (T0 + 545 * 60000) // 3600000
ra, xa = T.s5_removals(pa)
rb, xb = T.s5_removals(pb)
check("S5: снятие хая в 09:00 UTC найдено (rem)", ("H", hk) in ra and not xa, f"rem={sorted(ra)} excl={sorted(xa)}")
check("S5: касание противоположной границы -> excl", ("H", hk) in xb and ("H", hk) not in rb, f"rem={sorted(rb)} excl={sorted(xb)}")
evs = pd.DataFrame(dict(dir=["H", "H", "L"], tj=[hk * 3600000, (hk + 1) * 3600000, hk * 3600000]))
fa = T.s5_flags(evs, 60, pa); fb = T.s5_flags(evs, 60, pb)
check("S5: флаги s5/s5x (файл A)", fa.s5.tolist() == [1, 0, 0] and fa.s5x.tolist() == [0, 0, 0], str(fa[["s5", "s5x"]].values.tolist()))
check("S5: флаги s5/s5x (файл B)", fb.s5.tolist() == [0, 0, 0] and fb.s5x.tolist() == [1, 0, 0], str(fb[["s5", "s5x"]].values.tolist()))

# ---- S6: суточные бары: хай-фрактал на сутках 2 (подтверждён закрытием суток 3); вход шорт/лонг на сутках 5; вход до подтверждения ----
n = 30 * 1440
t = T0 + np.arange(n) * 60000
day = np.arange(n) // 1440
Hd = np.where(day == 2, 104.0, 100.0); Ld = np.full(n, 90.0)
mid = 95.0
oo = np.full(n, mid); cc = np.full(n, mid)
hh = Hd.astype(float); ll = Ld.astype(float)
hh = np.where(np.arange(n) % 1440 == 1, Hd, mid); ll = np.where(np.arange(n) % 1440 == 2, Ld, mid)
p6 = tempfile.gettempdir() + "/s6syn.csv.gz"
write(p6, t, oo, hh, ll, cc)
tm_late = T0 + (5 * 1440 + 600) * 60000
tm_early = T0 + (3 * 1440 + 600) * 60000
rl = T.s6_row(p6, "SYN", True, tm_late, 100.0, 99.0, 103.0, 1, 2024, "Лондон")
rs = T.s6_row(p6, "SYN", False, tm_late, 100.0, 101.0, 97.0, 2, 2024, "Азия")
re_ = T.s6_row(p6, "SYN", True, tm_early, 100.0, 99.0, 103.0, 3, 2024, "вне окон")
check("S6: сторона/dir/ss_bps лонг", rl["side"] == 1 and rl["dir"] == "L" and abs(rl["ss_bps"] - 100.0) < 1e-9)
check("S6: сторона/dir/ss_bps шорт", rs["side"] == -1 and rs["dir"] == "H" and abs(rs["ss_bps"] - 100.0) < 1e-9)
check("S6: t0/tt0 = минута входа", rl["t0"] == tm_late // 60000 and rl["tt0"] == tm_late)
check("S6: сессия one-hot", rl["session_london"] == 1 and rl["session_asia"] == 0 and rs["session_asia"] == 1 and re_["session_ny"] == 0 and sum(re_[k] for k in re_ if k.startswith("session_")) == 0)
check("S6: D1-фрактал хай: лонг=1, шорт=0", rl["d1"] == 1 and rs["d1"] == 0, f"{rl['d1']} {rs['d1']}")
check("S6: вход до подтверждения фрактала -> NaN", np.isnan(re_["d1"]))
check("S6: ot1=ss, fallback=0, ATR конечен", rl["ot1_bps"] == rl["ss_bps"] and rl["ot1_fallback"] == 0 and np.isfinite(rl["atr_bps"]))
sys.exit(0 if ok_all else 1)
