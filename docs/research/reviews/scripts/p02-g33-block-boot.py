"""Судья, П-02 Г-33 (2026-09-26): блочный бутстреп по суткам (скользящие блоки, круговой) — вторая проверка у порога
эфф. N при n < 100 (навык, references/07, fb78138). Суточные счётчики data/p02/p02-g33-counts-2026-09-26.json."""
import json
import numpy as np

d = json.load(open("data/p02/p02-g33-counts-2026-09-26.json", encoding="utf-8"))["day_counts"]
for var in ("g33_chain2", "g33_chain3"):
    for m in ("2026-08", "2026-09"):
        X = np.array([[*v["variants"][var]["no"], *v["variants"][var]["yes"]] for k, v in sorted(d.items())
                      if v["month"] == m], float)
        n = len(X)
        for L in (1, 3, 5):
            rng = np.random.default_rng(20260926)
            k = int(np.ceil(n / L))
            starts = rng.integers(0, n, (10000, k))
            idx = (starts[:, :, None] + np.arange(L)[None, None, :]) % n
            B = X[idx.reshape(10000, -1)[:, :n]].sum(1)
            with np.errstate(invalid="ignore", divide="ignore"):
                bd = B[:, 0] / B[:, 1] - B[:, 2] / B[:, 3]
            bd = bd[~np.isnan(bd)]
            lo, hi = np.percentile(bd, [2.5, 97.5])
            p = min(1, 2 * min((bd <= 0).mean(), (bd >= 0).mean()))
            print(f"{var} {m} блок {L}: [{lo * 100:+.2f}; {hi * 100:+.2f}] p {p:.4f}")
