"""TK-500: таблицы ядра (<out>, <out>.exits.csv, <out>.features.csv) нескольких пар×суток -> таблица эпизодов схемы TK-502 (p40stats.SCHEMA + armed, trunc).
Главные параметры: θ=12,6, T_life=60 с, L=500 мс (те, с которыми запущено ядро). Одна строка = взведённый эпизод (ITT: нет исполнения -> net_bps = 0).
Запуск: assemble.py <выход.npz> <bounds.json> [--fix] [--aux-strict] <asset>:<day>:<pair>:<bybit_leg 0|1>:<путь к <out>> ...
  --aux-strict — liq5m только если у обеих ног есть ряды (srcA+srcB=2, aux_feats.py), иначе f_liq = -1; oi5m = нет значения (-1), если рядов OI нет хотя бы у одной ноги (srcOIA+srcOIB<2; Aster, Bybit, Gate OI не имеют);
    умолчание — по ногам с источником (oi5m по ногам с рядом OI; у Aster × Bybit/Gate значения нет).
  bounds.json — границы терцилей: без --fix считаются по всей выборке и записываются (D1); с --fix читаются (D2, границы D1 не пересчитываются).
Нужен <out>.aux.csv (aux_feats.py): oi5m, liq5m, Φ_t. mpu_X = 1, если МПУ по исполнимой стороне (<out>.path.csv) хуже −2θ, bps."""
import csv, json, sys, time
import numpy as np

THETA, H_S, T_LIFE_S = "12.6", 600.0, 60.0
ORD = {"f_depth": "depth_slip", "f_spread": "spread", "f_vol5m": "vol5m", "f_plevel": "plevel", "f_flow1m": "flow1m", "f_bookimb": "bookimb", "f_d_arm": "d_in", "f_oi5m": "oi5m"}
FEATS = ["depth", "spread", "hour", "vol5m", "plevel", "phi_sign", "oi5m", "liq", "flow1m", "bookimb", "d_arm", "leader"]
SCHEMA = ["asset", "day", "pair", "conv_H", "mpu_X", "net_bps", "bybit_leg", "t_conv_s", "cens"] + ["f_" + k for k in FEATS]


def rows(path):
    return list(csv.DictReader(open(path)))


def main():
    a = sys.argv[1:]
    fix = "--fix" in a
    strict = "--aux-strict" in a
    ctl = a[a.index("--ctl") + 1] if "--ctl" in a else "real"        # метка контроля: real | K2 | K1+60000 ... (ядро: P40_K2, P40_SHIFT_B_MS)
    if "--ctl" in a:
        del a[a.index("--ctl"):a.index("--ctl") + 2]
    a = [x for x in a if x not in ("--fix", "--aux-strict")]
    out, bpath, specs = a[0], a[1], a[2:]
    cols = {k: [] for k in SCHEMA + ["armed", "trunc"]}
    raw = {k: [] for k in ORD.values()}
    hour, leader = [], []
    for sp in specs:
        asset, day, pair, byb, base = sp.split(":", 4)
        ep = [r for r in rows(base) if r["theta"] == THETA]
        ex = {(r["theta"], r["t0_ms"]): r for r in rows(base + ".exits.csv")}
        ft = {(r["theta"], r["t_start"]): r for r in rows(base + ".features.csv")}
        pa = {(r["theta"], r["t0_ms"]): r for r in rows(base + ".path.csv")}
        ax = {(r["theta"], r["t_start"]): r for r in rows(base + ".aux.csv")}
        for e in ep:
            k = (e["theta"], e["t0_ms"])
            x, f = ex[k], ft[k]
            tc = float(e["t_conv_ms"])
            conv = tc >= 0 and tc / 1000.0 <= H_S
            cens = not (tc >= 0 and tc / 1000.0 <= T_LIFE_S)
            pnl = float(x["pnl_bps"])
            tr = int(x["trunc"]) if "trunc" in x else int(x["outcome"] == "both" and pnl != pnl)   # 1 — конец данных, 2 — дыра книги (ядро с P40_TRUNC=1)
            cols["asset"].append(int(asset)); cols["day"].append(int(day) if day != "t0" else int(time.strftime("%Y%m%d", time.gmtime(int(e["t0_ms"]) / 1000)))); cols["pair"].append(int(pair)); cols["bybit_leg"].append(int(byb))
            cols["conv_H"].append(int(conv)); cols["mpu_X"].append(int(-float(pa[k]["mpu_bps"]) > 2 * float(THETA)))
            cols["net_bps"].append(0.0 if x["outcome"] == "none" else pnl)
            cols["t_conv_s"].append(T_LIFE_S if cens else tc / 1000.0); cols["cens"].append(int(cens))
            cols["armed"].append(1); cols["trunc"].append(tr)
            a = ax[(e["theta"], f["t_start"])]
            part = strict and int(a.get("srcA", 1)) + int(a.get("srcB", 1)) < 2
            part_oi = strict and int(a.get("srcOIA", a.get("srcA", 1))) + int(a.get("srcOIB", a.get("srcB", 1))) < 2   # aux.csv до TK-512 без srcOI: OI-источник = mark-источник
            for v in raw:
                raw[v].append(float("nan") if part_oi and v == "oi5m" else float(a[v] if v == "oi5m" else f[v]))
            phi = float(a["phi_bps"])
            cols["f_liq"].append(-1 if part else int(a["liq5m"])); cols["f_phi_sign"].append(-1 if phi != phi else int(phi > 0))
            hour.append(int(f["hour"])); leader.append(int(f["leader"]))
    n = len(cols["asset"])
    if fix:
        bounds = json.load(open(bpath))
    else:
        bounds = {}
        for fk, src in ORD.items():
            v = np.array(raw[src]); v = v[np.isfinite(v)]
            bounds[fk] = [float(np.quantile(v, 1 / 3)), float(np.quantile(v, 2 / 3))] if len(v) else [0.0, 0.0]
        json.dump(bounds, open(bpath, "w"))
    for fk, src in ORD.items():
        v = np.array(raw[src]); b = bounds[fk]
        cols[fk] = np.where(np.isfinite(v), np.where(v <= b[0], 0, np.where(v <= b[1], 1, 2)), -1)
    cols["f_hour"] = np.array(hour); cols["f_leader"] = np.array(leader)
    t = {k: np.asarray(cols[k]) for k in SCHEMA + ["armed", "trunc"]}
    t["ctl"] = np.array([ctl] * n)
    np.savez_compressed(out, **t)
    print("строк", n, "усечённых", int((t["trunc"] > 0).sum()), "контроль", ctl, "сошлось за H", int(t["conv_H"].sum()), "границы", bpath)


main()
