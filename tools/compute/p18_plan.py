#!/usr/bin/env python3
"""TK-289 П-18 F1: план клеток OFAT по осям B1 (docs/research/P-18-b1-titration.md §3) в формате plan/<метка>-<ГГГГ-ММ>.cells|.args|.bin.
Запуск: python tools/compute/p18_plan.py <каталог плана> [--axes 1,2,3,4,5,6a,7,6v] [--strength-json <файл>] -> файлы + печать числа клеток по осям.
Ось 6б (strength_min) берёт пороги по месяцам из --strength-json {"2026-01": {"10": x, ...}, ...} (квантили силы по сигналам B1 — отдельный шаг);
ось 6в (6v) — решётка S × usd_min «нет·10k…300k» (90 клеток, строка S = нет — ось 6а F1); ось 8 (σ-окно) — `--axes 8 --only <мин>`, по плану на окно; проход с SIG=sigma<мин> SIGROOT=… (tk289-f8.sh).
Центр (B1) в план не пишется — он в Летописи; клетка-дубль осей (одно значение у двух осей) схлопывается по кортежу+набору."""
import argparse
import json
import os
import re
import sys

MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
B1 = {"to": "0.0409", "stop": "pct2", "take": "tr1x1", "deadline": "14400", "ttl": "1800", "btc": "-44.55"}
SET_B1 = "age=2700,side=bid"  # B1: возраст 2700, bid; btc4h_max добавляется по оси 7 (центр -44.55)

S67 = "2 4 8.1 15.9 25 30.7 50 75 100".split()   # П-05 §3б + 2/4 % по правилу «шире/гуще» (Судья 19.10): медиана q50 S по месяцам 6,4 % < 8,1; q95 макс 36 % < 100 — 150/200 не нужны (P-18-s67-dist.csv)
USD6 = "10000 20000 30000 50000 75000 100000 150000 200000 300000".split()

AX = {
    "1": ("to", "0.0102 0.0145 0.0205 0.0289 0.0578 0.0818 0.1157 0.1636".split()),
    "2": ("stop", [f"pct{x}" for x in "0.7 1 1.4 2.5 3 4 6 8".split()]),
    "3": ("take", [f"tr{x}x{x}" for x in "0.35 0.5 0.7 1.4 2 2.8 4 5.6".split()]),
    "4": ("deadline", "60 600 1800 3600 7200 21600 28800 43200".split()),
    "5": ("ttl", "300 600 900 1200 2546 3600 5091 7200".split()),
    "6a": ("usd_min", "10000 20000 30000 50000 75000 100000 150000 200000 300000".split()),
    "6b": ("strength_min", "10 20 33 50 67 80 90 95".split()),
    "6v": ("flow+usd_min", [f"{s}|{u}" for s in S67 for u in ["none"] + USD6]),   # 6в (Судья 19.10): сила В-67 (flow=, % часового оборота) × пол номинала usd_min, оба «И»; «S = нет» — это 6а
    "8": ("sigma", "60 90 120 180 360 480 720 960".split()),   # ось 8: окно σ-таблицы, мин; клетка = B1, окно задаёт --sigma-from прохода (day-cells.sh SIG/SIGROOT) — в плане по одному окну (--only)
    "7": ("btc", ["none", "-11.1", "-15.7", "-22.3", "-31.5", "-63", "-89", "-126"]),
}


def load_f2(path):
    """Сетка F2 (tk289-f2-grid.py --out) вместо F1: значения осей из CSV. Env P18_F2=<csv> применяет её при импорте — scope/routes/load/plan видят F2."""
    import csv
    f2 = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        d = json.loads(r["axes"]); ax = d.pop("ax")
        f2.setdefault(ax, []).extend(d.values())
    for ax in AX:
        AX[ax] = (AX[ax][0], f2.get(ax, []))


if os.environ.get("P18_F2"):
    load_f2(os.environ["P18_F2"])


def entry(to):
    return f"ladder3x0..{to}sw2"


def cell(ax, val, ym, strength):
    p = dict(B1)
    extra = ""
    name = "p18a%sv%s" % (ax, re.sub(r"[^0-9A-Za-z]", "_", val.replace("-", "m")))
    key, _ = AX[ax]
    if key in p:
        p[key] = val
    elif key == "usd_min":
        extra = f",usd_min={val}"
    elif key == "strength_min":
        extra = f",strength_min={strength[ym][val]}"
    elif key == "flow+usd_min":
        sv, uv = val.split("|")
        extra = f",flow={sv}" + ("" if uv == "none" else f",usd_min={uv}")
    btc = "" if p["btc"] == "none" else f",btc4h_max={p['btc']}"
    tup = f"{entry(p['to'])}-{p['stop']}-{p['take']}-{p['deadline']}-ttl{p['ttl']}"
    sset = f"{name}-{ym}:{SET_B1}{btc}{extra}"
    return tup, f"{name}-{ym}", sset


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--axes", default="1,2,3,4,5,6a,7")
    ap.add_argument("--strength-json")
    ap.add_argument("--label", default="P-18-F1", help="метка файлов плана (боевой путь day-cells.sh: L)")
    ap.add_argument("--skip", default="", help="клетки, которые не пишем (уже в Летописи): имена через запятую, напр. p18a5v300")
    ap.add_argument("--only", default="", help="одно значение оси (ось 8: окно σ, мин; на проход одно окно)")
    ap.add_argument("--f2", help="сетка F2 (tk289-f2-grid.py --out): значения осей берутся из неё вместо F1 (центры/окна — те же правила клетки)")
    ap.add_argument("--smoke", action="store_true", help="10 клеток: первое значение каждой оси + верх осей 2, 4, 5; только март (малый срез [вживую])")
    a = ap.parse_args()
    axes = a.axes.split(",")
    strength = json.load(open(a.strength_json)) if a.strength_json else {}
    skip = set(filter(None, a.skip.split(",")))
    if a.f2:
        load_f2(a.f2)
    if "6b" in axes and not strength:
        sys.exit("ось 6б: нужен --strength-json (квантили силы по сигналам B1 в месяц)")
    os.makedirs(a.out, exist_ok=True)
    count = {}
    months = ["2026-03"] if a.smoke else MONTHS
    for ym in months:
        cells, flags = [], {"entry": set(), "stop": set(), "take": set(), "dl": set(), "ttl": set()}
        sets = []
        for ax in axes:
            vals = AX[ax][1]
            if a.only:
                vals = [v for v in vals if v == a.only]
            if a.smoke:
                vals = [vals[0]] + ([vals[-1]] if ax in ("2", "4", "5") else [])
            for val in vals:
                tup, nm, sset = cell(ax, val, ym, strength)
                if nm.rsplit("-", 2)[0] in skip:
                    continue
                e, s, t, d, tl = tup.split("-")
                tl = tl[3:]
                cells.append(f"{tup} {nm}")
                sets.append(f"--set {sset}")
                flags["entry"].add(e); flags["stop"].add(s); flags["take"].add(t); flags["dl"].add(d); flags["ttl"].add(tl)
                count[ax] = count.get(ax, 0) + (ym == months[0])
        args = ([f"--entry-form {x}" for x in sorted(flags["entry"])] + [f"--entry-ttl-secs {x}" for x in sorted(flags["ttl"], key=int)]
                + [f"--stop-form {x}" for x in sorted(flags["stop"])] + [f"--take-form {x}" for x in sorted(flags["take"])]
                + [f"--deadline-secs {x}" for x in sorted(flags["dl"], key=int)] + sorted(sets))
        with open(f"{a.out}/{a.label}-{ym}.cells", "w", newline="\n") as f:
            f.write("\n".join(cells) + "\n")
        with open(f"{a.out}/{a.label}-{ym}.args", "w", newline="\n") as f:
            f.write("\n".join(args) + "\n")
        with open(f"{a.out}/{a.label}-{ym}.bin", "w", newline="\n") as f:
            f.write("r2\n")
    print("клеток по осям:", count, "итого", sum(count.values()), "хеш-мес.", sum(count.values()) * len(months))


if __name__ == "__main__":
    main()
