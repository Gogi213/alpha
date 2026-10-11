"""Судья 27.09: Г-102 в t32-exits.py — отбрасываемые сделки по времени после стопа (O(t) → 0 делает уровень = цене стопа,
то есть бессрочный запрет входа выше цены стопа)."""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("x", "tools/compute/archive/t32-exits.py"); x = importlib.util.module_from_spec(spec)
sys.argv = ["x"]; spec.loader.exec_module(x)
tr = sorted(x.load_trades("data/t32/main-trades.csv"), key=lambda t: t["t0"])
for o0 in (50, 100, 200):
    last, dts = {}, []
    for t in tr:
        p = last.get(t["sym"])
        if p is not None:
            dt = (t["t0"] - p[0]) / x.MS_H
            if t["entry"] > p[1] * (1 - o0 * 0.5 ** (dt / 2) / 1e4):
                dts.append(dt); continue
        if t["reason"] == "stop":
            last[t["sym"]] = (t["t1"], x.exit_price(t))
        else:
            last.pop(t["sym"], None)
    dts.sort(); n = len(dts)
    print(f"O0={o0}: отброшено {n}; через > 12 ч после стопа {sum(d > 12 for d in dts)}, > 48 ч {sum(d > 48 for d in dts)}; "
          f"медиана {dts[n // 2]:.0f} ч")
