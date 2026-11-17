"""Диагностика L0455_1000XECUSDT (П-42 §13.13): реплика candidates()+first_signal() для уровня 0.0068825, 25.10 02:00–02:14 UTC."""
import csv, io, subprocess, sys, json
from datetime import datetime, timezone, timedelta

SYM = "1000XECUSDT"
LP = 688250  # e8
DAY = "2026-10-25"
T0 = 1_788_220_800_000
E_PM, D_PM = 300, 2000  # зона 0.3 %, слежение 2 %
NATR_MIN = 0.004  # 0.4 %
T1H = 500_000.0  # $
F_PM, R_MIN = 100, 15


def load_klines(day):
    rows = {}
    with open(f"/data/klines/binance/{SYM}/{day}.csv") as f:
        for r in csv.DictReader(f):
            t = (int(r["open_time"]) - T0) // 60000
            rows[t] = tuple(float(r[k]) for k in ("open", "high", "low", "close", "quote_volume"))
    return rows


def kl_range(d0, d1):
    out = {}
    d = datetime.fromisoformat(d0)
    while d <= datetime.fromisoformat(d1):
        try:
            out.update(load_klines(d.strftime("%Y-%m-%d")))
        except FileNotFoundError:
            pass
        d += timedelta(days=1)
    return out


def natr(kl, t, p=30):
    """NATR по p минутам, оканчивающимся минутой t (закрытая включена)."""
    s, k = 0.0, 0
    for u in range(t - p + 1, t + 1):
        if u not in kl:
            continue
        o, h, l, c, _ = kl[u]
        tr = h - l
        if u - 1 in kl:
            pc = kl[u - 1][3]
            tr = max(tr, abs(h - pc), abs(l - pc))
        s += tr
        k += 1
    if k * 2 < p:
        return None
    return s / k / kl[t][3]


def turn1h(kl, t):
    """оборот минут [t-60, t) — как pq[t]-pq[t-60]."""
    return sum(kl[u][4] for u in range(t - 60, t) if u in kl)


def ticks(day):
    z = f"/data/aggtrades/binance/{SYM}/{SYM}-aggTrades-{day}.zip"
    out = subprocess.run(["bash", "-c", f"unzip -p {z}"], capture_output=True, text=True).stdout
    rows = []
    for ln in out.splitlines()[1:]:
        a = ln.split(",")
        rows.append((int(a[5]), float(a[1]), float(a[2]), a[6].startswith("t")))
    rows.sort(key=lambda x: x[0])
    return rows


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]


if __name__ == "__main__":
    kl = kl_range("2026-10-20", "2026-10-25")
    lp = LP / 1e8
    zone = lp * (1 - E_PM / 1e5)
    print(f"level {lp}, zone_lo {zone:.8f}, back {lp*(1-F_PM/1e5):.8f}")
    tk = ticks(DAY)
    day0 = T0 + 54 * 86400000
    # минутки
    print("\nМИНУТЫ  o h l c | natr30(t-1) | turn1h(t) | pool | lo<lp & hi>=zone | первая зона-сделка")
    pend = None
    forbidden = False
    for hh in range(1, 3):
        pass
    start_min = 54 * 1440 + 0 * 60 + 0
    # прокрутка состояния запрета от рождения (минута 73440) до 02:14
    pend = None
    forbidden = False
    forb_at = None
    rows_by_min = {}
    for ms, px, q, bm in tk:
        rows_by_min.setdefault((ms - T0) // 60000, []).append((ms, px, q, bm))
    for t in range(73440, 54 * 1440 + 2 * 60 + 15):
        if t not in kl:
            continue
        o, h, l, c, qv = kl[t]
        if t >= 73440 + 0 and not forbidden and l < lp:
            pass
        n_prev = natr(kl, t - 1)
        tv = turn1h(kl, t)
        pool = n_prev is not None and n_prev >= NATR_MIN and tv >= T1H
        zone_reach = h >= zone
        cand = (not forbidden) and l < lp and zone_reach and pool
        if t >= 54 * 1440 + 0 and t <= 54 * 1440 + 2 * 60 + 14:
            msg = f"{fmt(T0+t*60000)[:5]}  {o:.8f} {h:.8f} {l:.8f} {c:.8f} | natr {n_prev*100 if n_prev else float('nan'):.3f}% | turn1h ${tv/1e3:,.0f}k | pool={'Y' if pool else 'N'} | forbidden={forbidden} pend={pend} cand={cand}"
            print(msg)
        if pend is not None and t - pend > R_MIN:
            pend = None
        if h > lp and pend is None:
            pend = t
        if pend is not None and c < lp * (1 - F_PM / 1e5):
            if not forbidden:
                forb_at = t
            forbidden = True
    print("forbidden from minute", forb_at and fmt(T0 + forb_at * 60000))

    # секунды 02:00–02:14: по секундам: n, low, high, first, last; отметки вхождения в зону / уровень
    print("\nСЕКУНДЫ (UTC): n | first low high last | last vs level, bps | зона/уровень")
    cur = None
    agg = {}
    for ms, px, q, bm in tk:
        s = (ms - day0) // 1000
        if 2 * 3600 <= s < 2 * 3600 + 15 * 60:
            a = agg.setdefault(s, [0, px, px, px, px])
            a[0] += 1
            a[2] = min(a[2], px)
            a[3] = max(a[3], px)
            a[4] = px
    for s in sorted(agg):
        n, f, lo, hi, la = agg[s]
        d = (la - lp) / lp * 1e4
        mark = "ABOVE-LEVEL" if hi >= lp else ("in-zone" if lo >= zone and hi < lp else ("touch-zone" if hi >= zone else ""))
        print(f"{fmt(day0+s*1000)[:8]} n={n:4d} {f:.8f} {lo:.8f} {hi:.8f} {la:.8f} {d:+8.1f}bps {mark}")


def first_signal(tk, minute_start_ms, lp_px, zone_px, color=True):
    """реплика first_signal: сделки минуты по порядку; hi>=lp -> нет сигнала; зона; цвет."""
    hi, op, out = -1, None, None
    for ms, px, q, bm in tk:
        if not (minute_start_ms <= ms < minute_start_ms + 60000):
            continue
        if op is None:
            op = px
        hi = max(hi, px)
        if hi >= lp_px:
            return None
        if px < zone_px or (color and px <= op):
            continue
        return ms, px
    return None


if __name__ == "__main__":
    print("\nРЕПЛИКА first_signal:")
    day0 = T0 + 54 * 86400000
    for hhmm, lab in (((2, 3), "02:03"), ((2, 4), "02:04"), ((2, 13), "02:13")):
        ms0 = day0 + (hhmm[0] * 60 + hhmm[1]) * 60000
        r = first_signal(tk, ms0, lp, zone)
        print(lab, "->", (fmt(r[0]), r[1], f"{(r[1]-lp)/lp*1e4:+.1f}bps") if r else None)


if __name__ == "__main__":
    # «живой» пул на сигнальной сделке 02:04:24.381: формирующаяся минутка 02:04 включена (оборот и NATR по сделкам до сигнала)
    t = 54 * 1440 + 124
    ms0 = T0 + t * 60000
    cut = day0 + (2 * 60 + 4) * 60000 + 24381
    part = [(ms, px, q) for ms, px, q, bm in tk if ms0 <= ms <= cut]
    hi = max(p for _, p, _ in part); lo = min(p for _, p, _ in part); cl = part[-1][1]
    qv = sum(p * q for _, p, q in part)
    pc = kl[t - 1][3]
    tr = max(hi - lo, abs(hi - pc), abs(lo - pc))
    s, k = 0.0, 0
    for u in range(t - 29, t):
        o, h, l, c, _ = kl[u]
        x = h - l
        if u - 1 in kl:
            pcc = kl[u - 1][3]
            x = max(x, abs(h - pcc), abs(l - pcc))
        s += x; k += 1
    live_natr = (s + tr) / (k + 1) / cl
    tv_live = sum(kl[u][4] for u in range(t - 59, t)) + qv
    print(f"\nЖИВОЙ пул на 02:04:24.381 (формирующаяся минутка включена): NATR30 {live_natr*100:.3f}% (порог 0.400%), оборот 1ч ${tv_live/1e3:,.0f}k (порог $500k)")
    print(f"закрытые минуты на 02:04 (как в движке): NATR30 {natr(kl,t-1)*100:.3f}%, оборот 1ч ${turn1h(kl,t)/1e3:,.0f}k; оборот минуты 02:04 целиком ${kl[t][4]/1e3:,.0f}k")
