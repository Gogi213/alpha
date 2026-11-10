"""TK-500: таблицы ядра (<out>, <out>.exits.csv, <out>.features.csv) нескольких пар×суток -> таблица эпизодов схемы TK-502 (p40stats.SCHEMA + armed, trunc).
Главные параметры: θ=12,6, T_life=60 с, L=500 мс (те, с которыми запущено ядро). Одна строка = взведённый эпизод (ITT: нет исполнения -> net_bps = 0).
Запуск: assemble.py <выход.npz> <bounds.json> [--fix] [--aux-strict] [--day0 ГГГГ-ММ-ДД] [--cens-s с] [--funding ставки.csv --pairs pairs.csv --funding-window ГГГГ-ММ-ДД:ГГГГ-ММ-ДД] <asset>:<day>:<pair>:<bybit_leg 0|1>:<путь к <out>> ...
  --day0 — день 0 для колонки day (TK-522: p40stats берёт day как номер суток 0…n−1, n_d = max+1; у кусков `t0` было ГГГГММДД -> n_d ≈ 2·10⁷); без флага day = ГГГГММДД (как прежде); с флагом — номер суток UTC от day0 по t0 эпизода.
  --cens-s — порог цензуры t_conv_s (умолч. 60 = T_life, как прежде; критик 10.11: H = 600 — решение Судьи TK-523, флагом, не молча).
  Прогон ядра с P40_L1=1 (признаки без глубины, TK-515): в features.csv вместо depth_slip/bookimb колонки l1_notional/bookimb1 -> f_depth/f_bookimb считаются по ним (в npz depth_kind=1; смешивать kind в одной выборке нельзя — отказ).
  --aux-strict — liq5m только если у обеих ног есть ряды (srcA+srcB=2, aux_feats.py), иначе f_liq = -1; oi5m = нет значения (-1), если рядов OI нет хотя бы у одной ноги (srcOIA+srcOIB<2; Aster, Bybit, Gate OI не имеют);
    умолчание — по ногам с источником (oi5m по ногам с рядом OI; у Aster × Bybit/Gate значения нет).
  --funding (TK-522, П-40 §16.7) — фандинг в net_bps: net = pnl + fund_bps, fund_bps отдельной колонкой (funding.py: метки расчёта в (вход ноги, выход эпизода], лонг платит, шорт получает); обе ноги держатся до exit_ms, вход ноги — fill_*_ms из <out>.fills.csv, неисполненная нога — 0.
    Ставки по (биржа, символ) из pairs.csv; нет таблицы ноги, дыра между метками > 8 ч + 2 с или метки не покрывают эпизод (края — по эпизоду, не по окну: контракт, выпущенный внутри окна, допустим) -> отказ до счёта (дыру докачать); эпизод вне --funding-window -> отказ. Без флага колонки fund_bps нет, net_bps прежний.
  --cell КЛЕТКА (TK-526, П-41 §5): прогон ядра с P40_RULE — θ_t у каждого эпизода своё (колонка theta справочная), строки не фильтруются по θ = 12,6, mpu_X — против −2·θ_t эпизода; в npz колонка `cell` = ключ клетки `<правило>|q=<q>` (оркестратор: --cells), одна клетка = один прогон ядра.
  --aux-miss-unknown — эпизод без строки в <out>.aux.csv: oi5m/liq/Φ_t = «неизвестно» (-1), счётчик в выводе («aux нет»), не отказ. Для K1 со сдвигом ноги (P40_SHIFT_B_MS): t_start эпизодов сдвинутого прогона не совпадают с реальным, aux.csv у него копия реального (≈80 % эпизодов d1v4 без строки). Без флага — отказ с понятным текстом.
  bounds.json — границы терцилей: без --fix считаются по всей выборке и записываются (D1); с --fix читаются (D2, границы D1 не пересчитываются).
Нужен <out>.aux.csv (aux_feats.py): oi5m, liq5m, Φ_t. mpu_X = 1, если МПУ по исполнимой стороне (<out>.path.csv) хуже −2θ, bps."""
import calendar, csv, json, os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funding as FD

MAX_FUND_STEP_MS = 8 * 3_600_000 + 2000

THETA, H_S, T_LIFE_S = "12.6", 600.0, 60.0
VEX = {"okx": "okx_futures", "bitget": "bitget_futures", "aster": "aster_futures", "hyperliquid": "hyperliquid_futures", "binance": "binance_futures"}   # биржа пар -> имя в validity_l1/smax_l1 (как orch.VEX)
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


def fund_leg(ftab, ex, sym):
    """таблица ставок ноги (T, r): нет таблицы / дыра между метками > 8 ч + 2 с -> отказ. Края проверяются по эпизодам (fund_edges): контракт, выпущенный внутри окна, начинается позже окна"""
    if (ex, sym) not in ftab:
        sys.exit(f"TK-522: нет ставок фандинга для ноги {ex} {sym} — докачать (funding.py fetch / из mark), счёт с дырой не запускается")
    T, r = ftab[(ex, sym)]
    g = FD.gaps(T, T[0], T[-1], MAX_FUND_STEP_MS)
    if g:
        sys.exit(f"TK-522: фандинг {ex} {sym}: дыры между метками {g[:3]}")
    return T, r


def fund_edges(T, ex, sym, t_in, t_out):
    """покрытие эпизода ноги: первая метка не позже t_in + 8 ч, последняя не раньше t_out − 8 ч (иначе метки внутри эпизода потеряны) -> отказ"""
    if t_in == t_in and (T[0] > t_in + MAX_FUND_STEP_MS or T[-1] < t_out - MAX_FUND_STEP_MS):
        sys.exit(f"TK-522: фандинг {ex} {sym}: таблица {T[0]:.0f}…{T[-1]:.0f} не покрывает эпизод {t_in:.0f}…{t_out:.0f}")


def day_index(t0_ms):
    return int(t0_ms) // 86_400_000


def main():
    a = sys.argv[1:]
    fix = "--fix" in a
    strict = "--aux-strict" in a
    ctl = a[a.index("--ctl") + 1] if "--ctl" in a else "real"        # метка контроля: real | K2 | K1+60000 ... (ядро: P40_K2, P40_SHIFT_B_MS)
    if "--ctl" in a:
        del a[a.index("--ctl"):a.index("--ctl") + 2]
    cell = a[a.index("--cell") + 1] if "--cell" in a else ""   # TK-526 (П-41): ключ клетки правила `<правило>|q=<q>`; без него — прежний режим (эпизоды θ = THETA)
    if "--cell" in a:
        del a[a.index("--cell"):a.index("--cell") + 2]
    day0 = a[a.index("--day0") + 1] if "--day0" in a else ""
    if "--day0" in a:
        del a[a.index("--day0"):a.index("--day0") + 2]
    cens_s = float(a[a.index("--cens-s") + 1]) if "--cens-s" in a else T_LIFE_S
    if "--cens-s" in a:
        del a[a.index("--cens-s"):a.index("--cens-s") + 2]
    fund_csv = a[a.index("--funding") + 1] if "--funding" in a else ""
    if "--funding" in a:
        del a[a.index("--funding"):a.index("--funding") + 2]
    pairs_csv = a[a.index("--pairs") + 1] if "--pairs" in a else ""
    if "--pairs" in a:
        del a[a.index("--pairs"):a.index("--pairs") + 2]
    smax_csv = a[a.index("--stale-smax") + 1] if "--stale-smax" in a else ""   # TK-522 (Судья 10.11 §2 п. 2): столбец stale — возраст лучших цен любой ноги на t0 > S_max контракта (smax_l1.csv); нужны <база>.age.csv и --pairs
    if "--stale-smax" in a:
        del a[a.index("--stale-smax"):a.index("--stale-smax") + 2]
    fwin = a[a.index("--funding-window") + 1] if "--funding-window" in a else ""
    if "--funding-window" in a:
        del a[a.index("--funding-window"):a.index("--funding-window") + 2]
    if bool(fund_csv) != bool(fwin) or (fund_csv and not pairs_csv) or (pairs_csv and not (fund_csv or smax_csv)):
        sys.exit("TK-522: --funding, --pairs и --funding-window — только вместе")
    if smax_csv and not pairs_csv:
        sys.exit("TK-522: --stale-smax требует --pairs (биржи и символы ног)")
    ftab = FD.load_csv(fund_csv) if fund_csv else {}
    smax = {(r["ex"], r["contract"]): float(r["s_max_s"]) * 1000 for r in csv.DictReader(open(smax_csv))} if smax_csv else {}
    pairs = {int(r["pair"]): r for r in csv.DictReader(open(pairs_csv))} if pairs_csv else {}
    if fwin:
        w0, w1 = (calendar.timegm(time.strptime(x, "%Y-%m-%d")) * 1000 for x in fwin.split(":"))
        w1 += 86_400_000
    auxmiss = "--aux-miss-unknown" in a
    a = [x for x in a if x not in ("--fix", "--aux-strict", "--aux-miss-unknown")]
    out, bpath, specs = a[0], a[1], a[2:]
    kinds = set()
    d0 = int(calendar.timegm(time.strptime(day0, "%Y-%m-%d")) // 86400) if day0 else None
    schema = SCHEMA + (["fund_bps"] if ftab else [])
    cols = {k: [] for k in schema + ["armed", "trunc"] + (["stale"] if smax_csv else [])}
    nstale = [0, 0]   # stale = 1, неизвестно (нет возраста или S_max) = -1
    naux = 0          # эпизоды без строки aux.csv (--aux-miss-unknown)
    raw = {k: [] for k in ORD.values()}
    hour, leader = [], []
    for sp in specs:
        asset, day, pair, byb, base = sp.split(":", 4)
        ep = rows(base) if cell else [r for r in rows(base) if r["theta"] == THETA]   # клетка правила: θ_t — на каждый эпизод своё (справочно), все строки прогона — одна клетка
        ex = {(r["theta"], r["t0_ms"]): r for r in rows(base + ".exits.csv")}
        ft = {(r["theta"], r["t_start"]): r for r in rows(base + ".features.csv")}
        pa = {(r["theta"], r["t0_ms"]): r for r in rows(base + ".path.csv")}
        if ftab:
            fl = {(r["theta"], r["t0_ms"]): r for r in rows(base + ".fills.csv")}
            pr = pairs[int(pair)]
            legs = [fund_leg(ftab, pr["ex" + c], pr["sym" + c]) for c in "AB"]
        ax = {(r["theta"], r["t_start"]): r for r in rows(base + ".aux.csv")}
        if smax_csv:
            ages = {r["t0_ms"]: (int(r["ageA_ms"]), int(r["ageB_ms"])) for r in rows(base + ".age.csv")} if os.path.exists(base + ".age.csv") else {}
            pr_ = pairs[int(pair)]
            lim = [smax.get((VEX.get(pr_["ex" + c], pr_["ex" + c]), pr_["sym" + c])) for c in "AB"]
        for e in ep:
            k = (e["theta"], e["t0_ms"])
            x, f = ex[k], ft[k]
            tc = float(e["t_conv_ms"])
            conv = tc >= 0 and tc / 1000.0 <= H_S
            cens = not (tc >= 0 and tc / 1000.0 <= cens_s)
            pnl = float(x["pnl_bps"])
            tr = int(x["trunc"]) if "trunc" in x else int(x["outcome"] == "both" and pnl != pnl)   # 1 — конец данных, 2 — дыра книги (ядро с P40_TRUNC=1)
            cols["asset"].append(int(asset)); cols["day"].append(day_index(e["t0_ms"]) - d0 if d0 is not None else int(day) if day != "t0" else int(time.strftime("%Y%m%d", time.gmtime(int(e["t0_ms"]) / 1000)))); cols["pair"].append(int(pair)); cols["bybit_leg"].append(int(byb))
            cols["conv_H"].append(int(conv)); cols["mpu_X"].append(int(-float(pa[k]["mpu_bps"]) > 2 * float(e["theta"] if cell else THETA)))
            fund = 0.0
            if ftab and x["outcome"] != "none":
                fr = fl[k]
                rel = lambda v: float(e["t0_ms"]) + float(v) if float(v) >= 0 else float("nan")
                tin = [float("nan")] * 2
                sl = 0 if fr["sell_leg"] == "A" else 1
                tin[sl], tin[1 - sl] = rel(fr["fill_sell_ms"]), rel(fr["fill_buy_ms"])
                if not w0 <= float(e["t0_ms"]) < w1:
                    sys.exit(f"TK-522: эпизод t0={e['t0_ms']} вне окна фандинга {fwin} — таблица докачана не на то окно")
                for c, (T_, _) in zip("AB", legs):
                    fund_edges(T_, pr["ex" + c], pr["sym" + c], tin["AB".index(c)], float(x["exit_ms"]))
                fund = FD.episode_bps(legs, sl, tin, float(x["exit_ms"]))[0]
            cols["net_bps"].append(0.0 if x["outcome"] == "none" else pnl + fund)
            if ftab:
                cols["fund_bps"].append(fund)
            cols["t_conv_s"].append(cens_s if cens else tc / 1000.0); cols["cens"].append(int(cens))
            cols["armed"].append(1); cols["trunc"].append(tr)
            if smax_csv:
                ag = ages.get(e["t0_ms"])
                st = -1 if ag is None or min(ag) < 0 or None in lim else int(any(g > m for g, m in zip(ag, lim)))
                cols["stale"].append(st)
                nstale[0] += st == 1
                nstale[1] += st == -1
            a = ax.get((e["theta"], f["t_start"]))
            miss = a is None
            if miss and not auxmiss:
                sys.exit(f"TK-522: в {base}.aux.csv нет строки эпизода t_start={f['t_start']} — aux считан на другом наборе эпизодов (сдвинутый контроль K1: флаг --aux-miss-unknown)")
            naux += miss
            if miss:
                a = {}
            part = miss or (strict and int(a.get("srcA", 1)) + int(a.get("srcB", 1)) < 2)
            part_oi = miss or (strict and int(a.get("srcOIA", a.get("srcA", 1))) + int(a.get("srcOIB", a.get("srcB", 1))) < 2)   # aux.csv до TK-512 без srcOI: OI-источник = mark-источник
            for v in raw:
                if v == "oi5m":
                    raw[v].append(float("nan") if part_oi else float(a[v]))
                else:
                    x_, kd = feat(f, v)
                    raw[v].append(x_)
                    if v == "depth_slip":
                        kinds.add(kd)
            phi = float("nan") if miss else float(a["phi_bps"])
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
    t = {k: np.asarray(cols[k]) for k in schema + ["armed", "trunc"] + (["stale"] if smax_csv else [])}
    t["ctl"] = np.array([ctl] * n)
    if cell:
        t["cell"] = np.array([cell] * n)
    t["depth_kind"] = np.array([next(iter(kinds)) if kinds else 0] * n)
    np.savez_compressed(out, **t)
    print("строк", n, "усечённых", int((t["trunc"] > 0).sum()), "контроль", ctl, "сошлось за H", int(t["conv_H"].sum()), "границы", bpath, *(["aux нет", naux] if auxmiss else []), *(["stale", nstale[0], "возраст неизвестен", nstale[1]] if smax_csv else []))


main()
