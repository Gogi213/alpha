"""TK-500: таблицы ядра (<out>, <out>.exits.csv, <out>.features.csv) нескольких пар×суток -> таблица эпизодов схемы TK-502 (p40stats.SCHEMA + armed, trunc).
Главные параметры: θ=12,6, T_life=60 с, L=500 мс (те, с которыми запущено ядро). Одна строка = взведённый эпизод (ITT: нет исполнения -> net_bps = 0).
Запуск: assemble.py <выход.npz> <bounds.json> [--fix] [--aux-strict] [--day0 ГГГГ-ММ-ДД] [--cens-s с] <asset>:<day>:<pair>:<bybit_leg 0|1>:<путь к <out>> ...
  --day0 — день 0 для колонки day (TK-522: p40stats берёт day как номер суток 0…n−1, n_d = max+1; у кусков `t0` было ГГГГММДД -> n_d ≈ 2·10⁷); без флага day = ГГГГММДД (как прежде); с флагом — номер суток UTC от day0 по t0 эпизода.
  --cens-s — порог цензуры t_conv_s (умолч. 60 = T_life, как прежде; критик 10.11: H = 600 — решение Судьи TK-523, флагом, не молча).
  Прогон ядра с P40_L1=1 (признаки без глубины, TK-515): в features.csv вместо depth_slip/bookimb колонки l1_notional/bookimb1 -> f_depth/f_bookimb считаются по ним (в npz depth_kind=1; смешивать kind в одной выборке нельзя — отказ).
  --aux-strict — liq5m только если у обеих ног есть ряды (srcA+srcB=2, aux_feats.py), иначе f_liq = -1; oi5m = нет значения (-1), если рядов OI нет хотя бы у одной ноги (srcOIA+srcOIB<2; Aster, Bybit, Gate OI не имеют);
    умолчание — по ногам с источником (oi5m по ногам с рядом OI; у Aster × Bybit/Gate значения нет).
  bounds.json — границы терцилей: без --fix считаются по всей выборке и записываются (D1); с --fix читаются (D2, границы D1 не пересчитываются).
Нужен <out>.aux.csv (aux_feats.py): oi5m, liq5m, Φ_t. mpu_X = 1, если МПУ по исполнимой стороне (<out>.path.csv) хуже −2θ, bps."""
import calendar, csv, json, sys, time
import numpy as np

THETA, H_S, T_LIFE_S = "12.6", 600.0, 60.0
ORD = {"f_depth": "depth_slip", "f_spread": "spread", "f_vol5m": "vol5m", "f_plevel": "plevel", "f_flow1m": "flow1m", "f_bookimb": "bookimb", "f_d_arm": "d_in", "f_oi5m": "oi5m"}
FEATS = ["depth", "spread", "hour", "vol5m", "plevel", "phi_sign", "oi5m", "liq", "flow1m", "bookimb", "d_arm", "leader"]
SCHEMA = ["asset", "day", "pair", "conv_H", "mpu_X", "net_bps", "bybit_leg", "t_conv_s", "cens"] + ["f_" + k for k in FEATS]


def rows(path):
    return list(csv.DictReader(open(path)))


def feat(f, name):
    """TK-522: признак из features.csv; при P40_L1=1 глубина — l1_notional (↑ = глубже, не проскальзывание), дисбаланс — bookimb1 -> (значение, kind: 0 прежний | 1 L1)"""
    if name in f:
        return float(f[name]), 0
    alt = {"depth_slip": "l1_notional", "bookimb": "bookimb1"}.get(name)
    if alt in f:
        return float(f[alt]), 1
    raise KeyError(f"features.csv: нет колонки {name} (и {alt}) — прогон ядра без признаков?")


def day_index(t0_ms):
    return int(t0_ms) // 86_400_000


def main():
    a = sys.argv[1:]
    fix = "--fix" in a
    strict = "--aux-strict" in a
    ctl = a[a.index("--ctl") + 1] if "--ctl" in a else "real"        # метка контроля: real | K2 | K1+60000 ... (ядро: P40_K2, P40_SHIFT_B_MS)
    if "--ctl" in a:
        del a[a.index("--ctl"):a.index("--ctl") + 2]
    day0 = a[a.index("--day0") + 1] if "--day0" in a else ""
    if "--day0" in a:
        del a[a.index("--day0"):a.index("--day0") + 2]
    cens_s = float(a[a.index("--cens-s") + 1]) if "--cens-s" in a else T_LIFE_S
    if "--cens-s" in a:
        del a[a.index("--cens-s"):a.index("--cens-s") + 2]
    a = [x for x in a if x not in ("--fix", "--aux-strict")]
    out, bpath, specs = a[0], a[1], a[2:]
    kinds = set()
    d0 = int(calendar.timegm(time.strptime(day0, "%Y-%m-%d")) // 86400) if day0 else None
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
            cens = not (tc >= 0 and tc / 1000.0 <= cens_s)
            pnl = float(x["pnl_bps"])
            tr = int(x["trunc"]) if "trunc" in x else int(x["outcome"] == "both" and pnl != pnl)   # 1 — конец данных, 2 — дыра книги (ядро с P40_TRUNC=1)
            cols["asset"].append(int(asset)); cols["day"].append(day_index(e["t0_ms"]) - d0 if d0 is not None else int(day) if day != "t0" else int(time.strftime("%Y%m%d", time.gmtime(int(e["t0_ms"]) / 1000)))); cols["pair"].append(int(pair)); cols["bybit_leg"].append(int(byb))
            cols["conv_H"].append(int(conv)); cols["mpu_X"].append(int(-float(pa[k]["mpu_bps"]) > 2 * float(THETA)))
            cols["net_bps"].append(0.0 if x["outcome"] == "none" else pnl)
            cols["t_conv_s"].append(cens_s if cens else tc / 1000.0); cols["cens"].append(int(cens))
            cols["armed"].append(1); cols["trunc"].append(tr)
            a = ax[(e["theta"], f["t_start"])]
            part = strict and int(a.get("srcA", 1)) + int(a.get("srcB", 1)) < 2
            part_oi = strict and int(a.get("srcOIA", a.get("srcA", 1))) + int(a.get("srcOIB", a.get("srcB", 1))) < 2   # aux.csv до TK-512 без srcOI: OI-источник = mark-источник
            for v in raw:
                if v == "oi5m":
                    raw[v].append(float("nan") if part_oi else float(a[v]))
                else:
                    x_, kd = feat(f, v)
                    raw[v].append(x_)
                    if v == "depth_slip":
                        kinds.add(kd)
            phi = float(a["phi_bps"])
            cols["f_liq"].append(-1 if part else int(a["liq5m"])); cols["f_phi_sign"].append(-1 if phi != phi else int(phi > 0))
            hour.append(int(f["hour"])); leader.append(int(f["leader"]))
    n = len(cols["asset"])
    if len(kinds) > 1:
        sys.exit("TK-522: в выборке смешаны прогоны ядра с глубиной (depth_slip) и без (P40_L1=1, l1_notional) — f_depth несравним, собирайте раздельно")
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
    t["depth_kind"] = np.array([next(iter(kinds)) if kinds else 0] * n)
    np.savez_compressed(out, **t)
    print("строк", n, "усечённых", int((t["trunc"] > 0).sum()), "контроль", ctl, "сошлось за H", int(t["conv_H"].sum()), "границы", bpath)


main()
