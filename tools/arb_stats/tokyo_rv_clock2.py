"""Проверяющий arb-tokyo: опоздание метки книги относительно сделок ТОЙ ЖЕ биржи (одни часы — смещение часов исключено).
Кросс-корреляция знакового потока сделок (время T сделки) и изменений середины книги (время E книги) по одной бирже, бины 1 мс, лаги ±300 мс, сегменты по 10 мин.
Положительное τ — книга меняется позже сделок на τ мс. Книги — из каталогов <kind>:<dir> (например bts:/data/arb-tokyo-review/bts, bcts:/data/arb-tokyo-review/bcts).
Запуск: python3 clock2.py <каталог сделок (с parsed/)> <t0> <t1> <out.json> kind:dir ...
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from tokyo_clock import cat, seg_signal, peak, MAXLAG, NFFT, SEG_MS

COINS = sys.argv[6].split(",") if len(sys.argv) > 6 and not ":" in sys.argv[6] else None


def main(tw, t0, t1, out, *kinds):
    clk = json.load(open("/data/arb-tokyo/clock.json"))
    top = clk["coins"]
    from arb_live import symmap_for
    symmap, _ = symmap_for("2026-11-16")
    sym = {}
    for ex, m in symmap.items():
        for s, (c, mult) in m.items():
            if c in top:
                sym.setdefault((c, ex), s)
    nseg = 24
    starts = [int(t0 + (t1 - t0 - SEG_MS) * k / (nseg - 1)) for k in range(nseg)]
    res = {}
    for kd in kinds:
        kind, bdir = kd.split(":")
        for ex in ("binance", "bybit", "okx"):
            taus = []
            data = []
            for c in top:
                if (c, ex) not in sym:
                    continue
                a = cat(str(Path(bdir, "parsed")), ex, sym[(c, ex)], ("e", "rx", "bid", "ask"))
                t = cat(str(Path(tw, "parsed")), ex, sym[(c, ex)], ("e", "et", "m"))
                if a is None or t is None:
                    continue
                o = np.lexsort((a["rx"], a["e"]))
                mid = ((a["bid"] + a["ask"]) / 2)[o]
                dm = np.r_[0.0, np.diff(mid) / mid[:-1] * 1e4]
                data.append((a["e"][o].astype(np.int64), dm, (t["e"] - t["et"]).astype(np.int64), np.where(t["m"] == 1, -1.0, 1.0)))
            for s0 in starts:
                tot, cnt = np.zeros(2 * MAXLAG + 1), 0
                for be, dm, tt, sg in data:
                    x, y = seg_signal(tt, sg, s0), seg_signal(be, dm, s0)
                    d = float(np.sqrt((x ** 2).sum() * (y ** 2).sum()))
                    if d <= 0:
                        continue
                    cc = np.fft.irfft(np.conj(np.fft.rfft(x, NFFT)) * np.fft.rfft(y, NFFT), NFFT)
                    tot += np.r_[cc[-MAXLAG:], cc[:MAXLAG + 1]] / d
                    cnt += 1
                if cnt:
                    taus.append(peak(tot / cnt)[1])
            t_ = np.array(taus)
            res[f"{kind}|{ex}"] = {"segments": len(t_), "tau_med": float(np.median(t_)), "p25": float(np.percentile(t_, 25)), "p75": float(np.percentile(t_, 75))}
            print(kind, ex, res[f"{kind}|{ex}"], flush=True)
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], *sys.argv[5:])
