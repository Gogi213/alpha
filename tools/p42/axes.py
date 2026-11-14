"""П-42: оси и база (docs/research/P-42-level-breakout.md §5.2) и генерация файлов вариантов для `alpha breakout run`."""
import sys

BASE = dict(natr_p=30, natr_min=0.4, t1h=0.5, t24=0, natr_exit_p=30, tf=60, k=2, d=0.5, n_bounce=2, tau=0.2,
            l_days=7, track=2, e=0.3, color=1, f=0.1, r=15, ban=1, n_tp=8, t_low=1, t_high=5, q=50, be=1, a=2,
            rr=2, s=5, c=5, m=3, mm=60, s_glob=4, w=10, ww=60, p=3)

# значения по порядку оси, включая базу; t24: 0 = фильтра нет (база)
AXES = dict(
    natr_p=[15, 30, 60], natr_min=[0.2, 0.3, 0.4, 0.6, 0.8], t1h=[0.25, 0.5, 1, 2, 5],
    t24=[0, 100, 200, 500, 1000], natr_exit_p=[15, 30, 60], tf=[30, 60, 240], k=[1, 2, 3], d=[0.3, 0.5, 1],
    n_bounce=[2, 3, 4], tau=[0.1, 0.2, 0.3, 0.5], l_days=[3, 7, 14], track=[1, 2, 3], e=[0.1, 0.2, 0.3, 0.5],
    color=[0, 1], f=[0, 0.1, 0.3], r=[5, 15, 60], ban=[0, 1], n_tp=[4, 8, 12], t_low=[0.5, 1, 2],
    t_high=[3, 5, 8], q=[25, 50, 75], be=[0, 1], a=[1, 2, 3], rr=[1, 2, 3], s=[2, 3, 5, 8], c=[0, 5, 15],
    m=[2, 3, 5], mm=[30, 60, 120], s_glob=[3, 4, 6], w=[5, 10, 30], ww=[0, 30, 60], p=[1, 3, 5])

# оси, которые не меняют сигналы/сделки по построению (в отчёт): track (D ≥ E на всей сетке)
VACUOUS = {"track"}


def fmt(v):
    return ("%g" % v)


def vid(axis, value):
    return "v_%s_%s" % (axis, fmt(value).replace(".", "p"))


def line(vid_, params):
    kv = " ".join("%s=%s" % (k, fmt(v)) for k, v in params.items() if BASE.get(k) != v)
    return ("%s %s" % (vid_, kv)).strip()


def stage1_variants():
    out = [("b0", dict())]
    for ax, vals in AXES.items():
        for v in vals:
            if v != BASE[ax]:
                out.append((vid(ax, v), {ax: v}))
    return out


def write(path, variants):
    with open(path, "w", encoding="utf-8") as f:
        for i, p in variants:
            f.write(line(i, p) + "\n")


if __name__ == "__main__":
    d = sys.argv[1]
    write(d + "/variants-base.txt", [("b0", {})])
    v = stage1_variants()
    write(d + "/variants-stage1.txt", v)
    print("stage1 variants (with base):", len(v), " non-base per side:", len(v) - 1)
