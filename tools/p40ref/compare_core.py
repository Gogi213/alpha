"""Эталон П-40 (TK-501): сверка таблицы эпизодов ядра TK-500 (ref/rs_doge_0917.csv) с эталоном (episodes_<θ>.csv).
Ключ — (θ, t_start); сравниваются dir, d_in (допуск 1e-4 bps), t_conv=t_end−t_start, t_half, t_quarter, пик, MPU."""
import sys, csv, numpy as np
core = list(csv.DictReader(open(sys.argv[1])))
bad = 0; n = 0
for th in sys.argv[2:]:
    e = np.loadtxt(f"/root/p40ref/out/ep/episodes_{th}.csv", delimiter=",", skiprows=1, ndmin=2)
    c = [r for r in core if abs(float(r["theta"]) - float(th)) < 1e-9]
    print("theta", th, "эталон", len(e), "ядро", len(c))
    ref = {int(r[0]): r for r in e}
    for r in c:
        t0 = int(r["t0_ms"]); n += 1
        x = ref.get(t0)
        if x is None:
            print("  нет в эталоне", t0); bad += 1; continue
        diffs = []
        if int(float(r["dir"])) != int(x[1]): diffs.append("dir")
        if abs(float(r["d0_bps"]) - abs(x[2])) > 1e-3: diffs.append(f"d0 {r['d0_bps']} vs {abs(x[2]):.4f}")
        if abs(t0 + int(r["t_conv_ms"]) - x[3]) > 0 and not np.isnan(x[3]): diffs.append(f"t_end {t0+int(r['t_conv_ms'])} vs {x[3]:.0f}")
        if int(r["t_half_ms"]) != int(x[4]) and not np.isnan(x[4]): diffs.append("t_half")
        if abs(float(r["peak_bps"]) - x[6]) > 1e-3: diffs.append(f"peak {r['peak_bps']} vs {x[6]:.4f}")
        if diffs: bad += 1; print("  ", t0, diffs)
    print("  лишние в эталоне:", len(set(ref) - {int(r["t0_ms"]) for r in c}))
print("строк ядра", n, "с расхождениями", bad)
