"""Судья, T-20 п.2 (2026-09-26): независимая сверка минуты BTC выключателя из сырья (Steam Deck, ~/alpha).

1) строки режима X−1 / X у входов 22.08 05:11 (XAG, KORU) и их пересчёт из свечей BTC (закрытия X−1 и X−61);
2) деньги сделок выключателя до/после: цена — закрытие свечи K+1 (было) / K (стало), K — первая строка ≤ −150
   после входа (было K ≥ минуты входа, стало K > t0);
3) по всем раундам главного набора (фильтр Rust btc4h_max): BTC 4 ч в строке X и X−1 против порога −44,55.
Только чтение rounds.csv, файлов режима и свечей; кодом portfolio-sim не пользуется.
"""
import csv, glob, os, datetime as dt

MIN = 60_000
FORM = "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"
SET = "t-bid-btc4h-q1"
KILL = 150.0
THR = -44.55
HOMES = {"август": ["epochs/e-aug"], "сентябрь": ["epochs/e-archive", "tmp-t20/rec"]}
REGIME = {"epochs/e-aug": "epochs/e-aug/study/regime", "epochs/e-archive": "epochs/e-archive/study/regime",
          "tmp-t20/rec": "study/regime"}


def utc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%d.%m %H:%M:%S")


def regime_rows(rdir):
    out = {}
    for f in sorted(glob.glob(os.path.join(rdir, "20??-??-??.csv"))):
        day = os.path.basename(f)[:-4]
        start = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        for r in csv.DictReader(open(f, encoding="utf-8")):
            m = int(r["minute_ms"])
            if start <= m < start + 86_400_000:
                out[m] = r
    return out


def closes(path):
    return {int(r["minute_ms"]): float(r["close"]) for r in csv.DictReader(open(path, encoding="utf-8"))}


def rounds(home):
    rows = []
    for f in sorted(glob.glob(os.path.join(home, "b5/titrc-u500r", "20*", SET, "rounds.csv"))):
        for r in csv.DictReader(l for l in open(f, encoding="utf-8") if not l.startswith("#")):
            if r["form"] == FORM and r["symbol"] != "TRXUSDT":
                rows.append(r)
    return rows


def fv(r, k):
    v = r.get(k) if r else None
    return float(v) if v not in (None, "") else None


# 1) + 2) август
reg = regime_rows(REGIME["epochs/e-aug"])
btc = closes("epochs/e-aug/study/regime/ref-BTCUSDT-1m.csv")
for hhmm in ("05:10", "05:11"):
    m = int(dt.datetime.strptime("2026-08-22 " + hhmm, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    rec = (btc[m - MIN] / btc[m - 61 * MIN] - 1) * 1e4
    print(f"строка {hhmm}: файл {fv(reg.get(m), 'btc_ret_1h_bps')}  из свечей {rec:.4f}")

kills = sorted(m for m, r in reg.items() if fv(r, "btc_ret_1h_bps") is not None and fv(r, "btc_ret_1h_bps") <= -KILL)
kl = {}
for r in rounds("epochs/e-aug"):
    t0 = int(r["t0_ns"]) // 1_000_000
    t1 = int(r["exit_ns"]) // 1_000_000
    X = t0 // MIN * MIN
    old_k = next((k for k in kills if k >= X), None)  # было: bisect_right(kills, t0−1 мин) → K ≥ X
    new_k = next((k for k in kills if k > t0), None)
    hit_old = old_k is not None and old_k < t1
    hit_new = new_k is not None and new_k < t1
    if not (hit_old or hit_new):
        continue
    sym = r["symbol"]
    if sym not in kl:
        p = f"epochs/e-aug/study/klines/ref-{sym}-1m.csv"
        kl[sym] = closes(p) if os.path.exists(p) else {}
    entry = float(r.get("entry_vwap") or 0) or float(r["entry_px"])
    d = int(r["dir"])
    fee = (float(r["exit_px"]) / entry - 1) * 1e4 * d - float(r["net_bps"])
    usd = float(r["qty"]) * entry

    def pnl(k, shift):
        px = kl[sym].get(k + shift)
        return None if px is None else ((px / entry - 1) * 1e4 * d - fee) / 1e4 * usd

    rowX = fv(reg.get(X), "btc_ret_1h_bps")
    rowX1 = fv(reg.get(X - MIN), "btc_ret_1h_bps")
    skip_new = rowX is not None and rowX <= -KILL
    skip_old = rowX1 is not None and rowX1 <= -KILL
    o = f"K {utc(old_k)} → {pnl(old_k, MIN):+.2f}$" if hit_old and not skip_old else ("не взята" if skip_old else "без выкл.")
    n = "не взята" if skip_new else (f"K {utc(new_k)} → {pnl(new_k, 0):+.2f}$" if hit_new else "без выкл.")
    print(f"{sym:12s} вход {utc(t0)} X−1 {rowX1} X {rowX} | было {o} | стало {n}")

# 3) все раунды главного набора: строка X и X−1 против порога фильтра
for month, homes in HOMES.items():
    n = above_new = above_old = miss = 0
    worst = -1e9
    for h in homes:
        rg = regime_rows(REGIME[h])
        for r in rounds(h):
            X = int(r["t0_ns"]) // 1_000_000 // MIN * MIN
            vx, vx1 = fv(rg.get(X), "btc_ret_4h_bps"), fv(rg.get(X - MIN), "btc_ret_4h_bps")
            n += 1
            if vx is None:
                miss += 1
                continue
            worst = max(worst, vx)
            above_new += vx > THR
            above_old += vx1 is not None and vx1 > THR
    print(f"{month}: раундов {n}, нет строки {miss}; строка X > {THR}: {above_new} (макс {worst:.2f}); строка X−1 > {THR}: {above_old}")
