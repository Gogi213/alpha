"""TK-620: проверка tk620_step0.py на подставном дампе P42_EXC (запуск: python tools/p42/test_tk620.py). Ничего не пишет вне временной папки; реальных дампов не читает."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk620_step0 as s0  # noqa: E402

HDR = "sym,level,sig_ms,sig_min,dist_e8,lp_e8,natr_e8,nvalid_rng,rlo_e8,rhi_e8,touch,up1" + "".join(",l%d" % k for k in range(1, 21)) + "".join(",r%d" % k for k in range(0, 21))
DAY = 86_400_000


def row(sig_day, touch, up1, lk, rk, dist_nat=0.1, rng=(95, 99)):
    """Сигнал длинной стороны: цена уровня 100 (1e10 в e8), NATR 1 % (1e6), u = 1e8 = 1 NATR; lk, rk — число достигнутых ступеней."""
    l = ",".join("1000" if i < lk else "-1" for i in range(20))
    r = ",".join("1000" if i < rk else "-1" for i in range(21))
    nv = 0 if rng is None else 1
    lo, hi = ("", "") if rng is None else (str(int(rng[0] * 1e8)), str(int(rng[1] * 1e8)))
    dist = int(dist_nat * 1e6)
    return "AAA,1,%d,10,%d,10000000000,1000000,%d,%s,%s,%d,%d,%s,%s" % (sig_day * DAY + 1000, dist, nv, lo, hi, touch, up1, l, r)


def build(root, rows_by_win):
    for w, rows in rows_by_win.items():
        d = os.path.join(root, w, "V")
        os.makedirs(d)
        with open(os.path.join(d, "long-exc.csv"), "w", encoding="utf-8") as f:
            f.write(HDR + "\n" + "\n".join(rows) + "\n")


def synth():
    """100 сигналов/окно, зачётные сутки; доля касаний падает: достигшие 4+ ступеней (1 NATR) касаются в 40 %, 8+ (2 NATR) — в 10 %."""
    rows = []
    for i in range(100):
        k = 0 if i < 40 else (4 if i < 80 else 8)
        if k == 0:
            t = 1000 if i % 10 < 8 else -1
        elif k == 4:
            t = 1000 if i % 5 < 2 else -1
        else:
            t = 1000 if i % 10 == 0 else -1
        rows.append(row(20 + i % 30, t, 2000 if t > 0 and i % 2 == 0 else -1, k, k))
    return rows


def main():
    root = tempfile.mkdtemp()
    # зачёт wa: 01.07–25.08, T0 17.06 → сутки 14–68; wb: T0 25.07, 26.08–02.10 → сутки 32–69; wc: T0 01.09, 03.10–13.11 → сутки 32–73
    build(root, {"wa": synth(), "wb": [row(40, 1000, -1, 0, 0)] * 5, "wc": [row(5, 1000, -1, 0, 0)] * 5})   # wc: сутки 5 → до зачёта, отбрасываются
    num = s0.load(root, "V", "long")
    assert len(num["touch"]) == 105, len(num["touch"])
    rows, p0, bn = s0.curve(num, "lvl")
    assert bn == 105 and rows[0][2] == 105
    # k = 4 (1 NATR): достигли l4 — сигналы с k ≥ 4 (60 в wa); начальная глубина 0,1 < 1 → все живы
    assert rows[4][2] == 60, rows[4]
    assert rows[8][2] == 20, rows[8]
    assert rows[9][2] == 0
    assert rows[8][3] == 2, rows[8]                # из 20 достигших 2 NATR касаются 2 (i % 10 == 0 среди i ≥ 80)
    # глубокий старт: начальная глубина 1,2 NATR → при d ≤ 1,0 (k ≤ 4) позиция уже «выбита», в популяцию не входит; с k = 5 (d = 1,25) входит
    build(os.path.join(root, "2"), {"wa": [row(30, 1000, -1, 8, 8, dist_nat=1.2)] * 40, "wb": [], "wc": []})
    rr, _, _ = s0.curve(s0.load(os.path.join(root, "2"), "V", "long"), "lvl")
    assert [x[2] for x in rr[3:7]] == [0, 0, 40, 40], [x[2] for x in rr[:8]]
    # край: 40 сигналов с проторговкой 95…99 (ширина 4 NATR), цена сигнала 99,9 (над краем на 4,9 NATR); r_k достигнуто для k ≤ 5, касаются 50 %
    build(os.path.join(root, "3"), {"wa": [row(30, 1000 if i % 2 == 0 else -1, -1, 0, 6) for i in range(40)] + [row(30, 1000, -1, 0, 0, rng=None)] * 10, "wb": [], "wc": []})
    n3 = s0.load(os.path.join(root, "3"), "V", "long")
    er, ep, en = s0.curve(n3, "edge")
    assert en == 40 and abs(ep - 0.5) < 1e-9, (en, ep)                       # без проторговки — вне базы кривой «край»
    assert er[0][2] == 40 and er[5][2] == 40 and er[6][2] == 0, [x[2] for x in er[:8]]
    wq, nw = s0.rule_width(n3)
    assert nw == 40 and abs(wq[10] - 4.0) < 1e-9, (nw, wq)
    test_rules()
    print("ok")


def test_rules():
    p = [0.8, 0.8, 0.7, 0.6, 0.5, 0.45, 0.4, 0.38, 0.3, 0.2, 0.19] + [0.1] * 10
    rows = [(k, 0.25 * k, 100, round(100 * q), 0) for k, q in enumerate(p)]
    assert s0.first_halving(rows, 0.8, 0.5) == 1.5      # p ≤ 0,4 на k = 6 и 7
    assert s0.first_halving(rows, 0.8, 0.25) == 2.25    # p ≤ 0,2 на k = 9 и 10
    assert s0.first_halving(rows, 0.8, 0.05) is None
    r = s0.rule_levels(rows, 0.8)
    assert r["grid"] == [1.5, 2.0, 2.25], r
    assert s0.rule_levels([(k, 0.25 * k, 100, 80, 0) for k in range(21)], 0.8)["grid"] == [2.0, 3.5, 5.0]
    thin = [(k, 0.25 * k, 20, 0, 0) for k in range(21)]                       # n < 30: точки не читаются → «нет падения»
    assert s0.rule_levels(thin, 0.8)["grid"] == [2.0, 3.5, 5.0]
    m = s0.rule_margin(rows, 0.8)
    assert m["grid"] == [1.5, 2.25], m


if __name__ == "__main__":
    main()
