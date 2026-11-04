# П-34 шаг 0: t_ms OI = конец или начало 5-мин периода? Корреляция ln(taker ratio) с доходностью периода [t-5,t] против [t,t+5].
import csv, gzip, math, os, sys
home = os.path.expanduser("~/alpha-pump")
def corr(a, b):
    n = len(a); ma = sum(a)/n; mb = sum(b)/n
    sa = math.sqrt(sum((x-ma)**2 for x in a)); sb = math.sqrt(sum((x-mb)**2 for x in b))
    return sum((x-ma)*(y-mb) for x, y in zip(a, b))/(sa*sb) if sa and sb else float("nan")
for sym in sys.argv[1:]:
    close = {}
    with gzip.open(f"{home}/candles/binance-um/{sym}.csv.gz", "rt") as f:
        rd = csv.reader(f); hdr = next(rd)
        for r in rd:
            t = int(r[0])
            if "2026-09-01" <= __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(t/1000)) < "2026-10-01":
                close[t + 60000] = float(r[4])        # close минуты, открытой в t, известен в t+60 с
    oi = []
    with gzip.open(f"{home}/oi/binance/{sym}.csv.gz", "rt") as f:
        rd = csv.reader(f); hdr = next(rd); ix = {h: i for i, h in enumerate(hdr)}
        for r in rd:
            t = int(r[ix["t_ms"]])
            if t in close and (t-300000) in close and (t+300000) in close:
                tk = float(r[ix["sum_taker_long_short_vol_ratio"]]); o = float(r[ix["open_interest"]])
                if tk > 0 and o > 0:
                    oi.append((t, math.log(tk), o))
    rs_end = [math.log(close[t]/close[t-300000]) for t, _, _ in oi]
    rs_start = [math.log(close[t+300000]/close[t]) for t, _, _ in oi]
    lt = [x for _, x, _ in oi]
    print(sym, "n", len(oi), "corr(taker, ret[t-5,t])=%.3f" % corr(lt, rs_end), "corr(taker, ret[t,t+5])=%.3f" % corr(lt, rs_start))
    # OI-пик в вспышке: |ΔOI| против |ret| периода
    d = [abs(math.log(oi[i][2]/oi[i-1][2])) if oi[i][0]-oi[i-1][0] == 300000 else float("nan") for i in range(1, len(oi))]
    ok = [i for i, x in enumerate(d) if x == x]
    print("   corr(|dOI_t|,|ret[t-5,t]|)=%.3f corr(|dOI_t|,|ret[t,t+5]|)=%.3f" % (
        corr([d[i] for i in ok], [abs(rs_end[i+1]) for i in ok]), corr([d[i] for i in ok], [abs(rs_start[i+1]) for i in ok])))
