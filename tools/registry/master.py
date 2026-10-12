"""Летопись — мастер плана (TK-187, В-225): слои params / stacks / hyp_axes / rules / plan_cells / sieve в БД.
Канон — docs/registry/*.jsonl (git); БД собирается из него. Команды (через registry.py):
  load-axes <каталог>            params.csv, stacks.csv, hyp_axes.csv, rules.csv (';', UTF-8) -> jsonl канона
  plan --protocol P-14 --fraction k   сетка из hyp_axes (k>=2 — окрестность плато из sieve), дедуп по хешу против results
  sieve --protocol P-14 --fraction k --stats f.csv   правило §4 (числа — rules) -> sieve.jsonl
  render matrix|coverage         docs/findings/hyp-{matrix,coverage}-<дата>.{md,csv} из БД, руками не править
Хеш клетки — cellhash.cell_hash (одна функция для генератора и загрузчика, TK-186)."""
import collections, csv, datetime, itertools, json, math, os, re, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellhash as ch, db, mechanics as mech  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIND = os.path.join(ROOT, "docs", "findings")
LAYERS = {"params.csv": ("params.jsonl", ("name",)), "stacks.csv": ("stacks.jsonl", ("stack", "version", "param")),
          "hyp_axes.csv": ("hyp_axes.jsonl", ("hyp", "protocol", "param")), "rules.csv": ("rules.jsonl", ("protocol", "version", "key"))}
COLS = {"params.csv": ("name", "unit", "resolution", "axis_type", "cfg_path", "note"), "stacks.csv": ("stack", "version", "param", "value"),
        "hyp_axes.csv": ("hyp", "protocol", "version", "stack", "param", "lo", "hi", "points_f1", "old_points", "note", "role", "engine", "mechanic"),
        "rules.csv": ("protocol", "version", "key", "value")}
RULE_DEFAULTS = {"months": 9}


def fval(x):
    """Значение оси -> число ('1/3' -> 0.333…); категория (не число) -> None."""
    s = str(x).strip().replace(",", ".")
    m = re.fullmatch(r"(-?[\d.]+)/([\d.]+)", s)
    try:
        return float(m.group(1)) / float(m.group(2)) if m else float(s)
    except ValueError:
        return None


def _wjl(name, rows):
    with open(os.path.join(db.REGD, name), "w", encoding="utf-8", newline="\n") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


def _replace(name, new, keyf):
    """Строки канона с теми же ключами заменяются, остальные остаются (порядок: старые, потом новые)."""
    keys = {keyf(d) for d in new}
    _wjl(name, [d for d in db.jl(name) if keyf(d) not in keys] + new)


AXIS_TYPES = {"num": "numeric", "quantile": "numeric", "cat": "categorical", "circ": "circular"}


def _read(p):
    with open(p, encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f, delimiter=";")]


def _adapt(fn, r, protocol, ver):
    """Формат TK-188 (docs/findings/p14-axes-*.csv) -> строки слоёв; свой формат (name/points_f1/protocol) проходит как есть."""
    if fn == "params.csv" and "param" in r:
        r = {"name": r["param"], "unit": r.get("unit", ""), "resolution": r.get("resolution", ""),
             "axis_type": AXIS_TYPES.get(r["axis_type"], r["axis_type"]), "cfg_path": r.get("cfg_path", ""), "note": r.get("description", "")}
    if fn == "hyp_axes.csv" and "f1_points" in r:
        r = {"hyp": r["hyp"], "protocol": protocol, "version": ver, "stack": r["stack"], "param": r["param"], "lo": r.get("lo", ""),
             "hi": r.get("hi", ""), "points_f1": r["f1_points"], "old_points": r.get("old_points", ""), "note": r.get("note", ""),
             "role": r.get("role", ""), "engine": r.get("engine", ""), "mechanic": r.get("mechanic", "")}
    return r


def load_axes(dirp, protocol="P-14"):
    """CSV (формат TK-187 или TK-188; имена файлов params|stacks|hyp_axes|rules[.csv] или p14-axes-<слой>-*.csv) -> jsonl;
    повторная загрузка заменяет строки по ключу. Возвращает {файл: строк}."""
    out = {}
    for fn, (jn, key) in LAYERS.items():
        stem = fn[:-4]
        cand = [x for x in sorted(os.listdir(dirp)) if x == fn or re.fullmatch(r"p14-axes-" + stem + r"-.*\.csv", x)]
        if not cand:
            continue
        rows = _read(os.path.join(dirp, cand[-1]))
        ver = next((r["version"] for r in _read(os.path.join(dirp, next((x for x in sorted(os.listdir(dirp)) if x == "stacks.csv" or x.startswith("p14-axes-stacks")), cand[-1]))) if r.get("version")), "") if fn == "hyp_axes.csv" else ""
        rows = [_adapt(fn, r, protocol, ver) for r in rows]
        for r in rows:
            miss = [k for k in key if not r.get(k)]
            if miss:
                sys.exit(f"load-axes: {fn}: пустой ключ {miss} в строке {r}")
            if fn == "params.csv":
                if r.get("axis_type") not in ("numeric", "log", "categorical", "circular"):
                    sys.exit(f"load-axes: params.csv: axis_type у {r['name']} ∈ numeric|log|categorical|circular")
                r["resolution"] = fval(r.get("resolution")) if r.get("resolution") else None
        cols = COLS[fn]
        rows = [{k: v for k, v in r.items() if k in cols} for r in rows]
        _replace(jn, rows, lambda d, key=key: tuple(d[k] for k in key))
        out[fn] = len(rows)
    return out


# ---------- сборка конфига клетки из стека и осей ----------
def set_path(cfg, path, val):
    cur = cfg
    parts = path.split(".")
    for k in parts[:-1]:
        cur = cur.setdefault(k, {})
    cur[parts[-1]] = val


def stack_values(c, stack, version=None):
    q = "select param, value, version from stacks where stack=?" + (" and version=?" if version else "")
    rows = c.execute(q, (stack, version) if version else (stack,)).fetchall()
    if not rows:
        sys.exit(f"plan: стека {stack} {version or ''} нет в БД")
    last = max(r[2] for r in rows)
    return {r[0]: r[1] for r in rows if r[2] == last}, last


def _val(v):
    """Значение из csv: JSON для {…}/[…], иначе число/None/bool как в cellhash.num ('1/3' остаётся строкой)."""
    v = str(v).strip()
    return json.loads(v) if v[:1] in "{[" else ch.num(v)


_FAMILY = {}


def family_of(hyp):
    """Г-87 -> g87 / e112 …: имя семьи, как в p12-cells (Летопись); нет в таблице -> g<номер> (хеш с Летописью не сверен)."""
    if not _FAMILY:
        for fn in ("p12-cells-2026-10-06.csv", "p12-cells-ext-2026-10-07.csv"):
            if os.path.exists(os.path.join(FIND, fn)):
                for r in csv.DictReader(open(os.path.join(FIND, fn), encoding="utf-8")):
                    m = re.fullmatch(r"[a-z]+(\d+)(?:-[a-z]+)?", r["family"])
                    if m:
                        _FAMILY.setdefault(m.group(1), r["family"])
    n = re.sub(r"\D", "", hyp)
    return _FAMILY.get(n, "g" + n)  # может быть «g73-conj» (семья-признак R1)


MONTHLY = {}  # хеш клетки -> {ГГГГ-ММ: строка --cells}: число в имени формы меняется по месяцам (Г-133 chase<a·W_m>); файл --cells на месяц
_THR = os.path.join(FIND, "p12-r2b-thresholds-2026-10-10.json")
MSETS = {}  # имя набора -> спецификация --set: наборы с порогом по месяцам (Г-10 btc<N>h_max=T_m); write_set добавляет их к --set
_TAPE_THR = os.path.join(FIND, "tape-thresholds-v7-2026-10-11.json")   # TK-120 v7 (thr7/summary.json): ключи e112_W<W>_q<q> по месяцам
_TK202_THR = os.path.join(FIND, "tk202-thresholds-2026-10-12.json")   # tools/analyze/tk202-thresholds.py: usd {месяц: {q: T}}, ctx {ось: {месяц: {q20..q80}}}
_BTC_THR = os.path.join(FIND, "btc-thresholds-2026-10-11.json")   # tools/analyze/btc-thresholds.py: {месяц: {N: {q: T}}}


def w_median_ms():
    """{ГГГГ-ММ: W_ms_median} — медиана ожидания исполнения мейкер-лимитки по B1 прошлых месяцев (v6, TK-120; январь — по январю)."""
    return {r["month"]: r["W_ms_median"] for r in json.load(open(_THR, encoding="utf-8"))["summary"]}


R1COL = {}  # хеш R1-клетки -> колонка (None — одна на семью); R1 фев…окт на 9 точках посчитан r1ext (TK-189 шаг 15), кроме колонок без данных
R1EXT_COLS = frozenset("""front_add_max_lots cancel_life_lots size_share15_bp opp_wall_ratio_bp wall_add_max_lots frontrun_delta_10s_lots vpin_bp
    tape_all_lots_15s obi50_bp best_move_1s_cbps best_move_10s_cbps obi1_bp obi5_bp obi10_bp tape_press_lots_15s tape_press_lots_30s
    tape_press_lots_60s best_flips_15s best_flips_60s ofi_10s_lots ofi_60s_lots sign_ac_15s_bp sign_ac_60s_bp tape_burst_press_15s_bp
    tape_burst_press_30s_bp tape_burst_press_60s_bp""".split())   # колонки, посчитанные r1ext (30 осей); прочие — к счёту (В-227 М1)
R1EXT_SIGN = {  # знак колонок r1ext (FAM tk064-r1cells.py): hi — больше лучше (квантиль «оставить» сверху), lo — меньше лучше
    **{c: "lo" for c in ("tape_press_lots_15s", "tape_press_lots_30s", "tape_press_lots_60s", "vpin_bp", "sign_ac_15s_bp", "sign_ac_60s_bp",
                         "obi50_bp", "best_flips_15s", "best_flips_60s", "front_add_max_lots", "tape_burst_press_15s_bp",
                         "tape_burst_press_30s_bp", "tape_burst_press_60s_bp", "cancel_life_lots")},
    **{c: "hi" for c in ("tape_all_lots_15s", "obi1_bp", "obi5_bp", "obi10_bp", "ofi_10s_lots", "ofi_60s_lots", "frontrun_delta_10s_lots",
                         "size_share15_bp", "best_move_1s_cbps", "best_move_10s_cbps", "opp_wall_ratio_bp", "wall_add_max_lots")}}
# В-227 М1, решение Судьи 12.10 04:14 (П-14 §3.1 Q-3): один знак по смыслу гипотезы (зеркало = другая гипотеза); клетка идёт как «посчитано»
# только при совпадении колонка+знак+q с r1ext. Знак: hi — «оставить» верхние q %, lo — нижние.
R1_SIGN = {
    "Г-28": {c: "hi" for c in ("nz_levels15", "size_share15_bp", "frontrun_levels", "front_add_max_lots")},
    "Г-33": {c: "lo" for c in ("cancel_1s_lots", "cancel_3s_lots", "cancel_60m_lots")},
    "Г-36": {"cancel_life_lots": "lo", "wall_add_max_lots": "hi"},
    "Г-46": {"ofi_10s_lots": "lo", "ofi_60s_lots": "lo", "best_move_1s_cbps": "hi", "best_move_10s_cbps": "hi"},
    "Г-60": {"trade_size_p50_15m": "lo", "trade_size_p90_15m": "lo"},
    "Г-78": {"best_move_1s_cbps": "lo"}, "Г-55": {"tape_press_lots_30s": "lo"}, "Г-140": {"best_flips_15s": "lo", "ofi_10s_lots": "hi"},
}
R1X = set()   # хеши R1-клеток, посчитанных tk202-r1x.py (TK-202 B11): /data/tk202/r1x/cells/<мес>/<хеш>.csv, янв–сен (окт 90 кл. на 31 сигнале); в --cells не идут
R1_NODATA = ("tape_burst_press_60s_bp",)   # coverage-r1.json r1ext: jan_n_defined 0, охват 0 во всех месяцах — файлов нет
R1EXT = "r1ext TK-189: /data/tk189/r1ext/cells/<мес>/ (271 кл./мес, фев…окт; 5 прежних точек побайтно = /data/tk064/r1)"
R2B_MONTHS = tuple(f"2026-{m:02d}" for m in range(1, 10))   # TK-115 R2-B (job 1011200729464, calc /data/p12r3a/r2b): 31 форма × B1-сутки янв–сен, B1 = набор t-bid-btc4h-q1
OUTSIDE = {}  # хеш клетки -> где посчитано вне Летописи (не «к счёту», не в --cells): загрузка результатов в Летопись — отдельный шаг
LINES = {}  # хеш клетки -> (строка --cells «форма набор» | None, причина, что нет кодировки); заполняет build_grid, читает plan


def r2b_lines():
    """{ГГГГ-ММ: множество строк --cells} 31 формы R2-B TK-115 (tk115-r2b-day.sh) из порогов v7/v6 — те же числа, что у плана (сверено с thr7 e-thresholds-*.csv, янв–сен)."""
    thr = {r["month"]: r for r in json.load(open(_TAPE_THR, encoding="utf-8"))["summary"]} if os.path.exists(_TAPE_THR) else {}
    b, out = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800", {}
    for m, w_m in w_median_ms().items():
        if m not in R2B_MONTHS or m not in thr:
            continue
        ls = {f"{b}-tape{w}q{float(thr[m][f'e112_W{w}_q{q}'])!r} t-bid-btc4h-q1" for w in (15, 30, 60) for q in (33, 50, 67, 80) if f"e112_W{w}_q{q}" in thr[m]}
        ls |= {f"{b}-chase{int(round(float(w_m) * a))} t-bid-btc4h-q1" for a in (0.5, 1, 2)}
        ls |= {f"{b}-wall2 t-bid-btc4h-q1", f"{b}-wall2x t-bid-btc4h-q1", "ladder3x0..0.0409sw4-pct2-tr1x1-14400-ttl1800 t-bid-btc4h-q1", "ladder3x0..0.0409sw5-pct2-tr1x1-14400-ttl1800 t-bid-btc4h-q1"}
        out[m] = ls
    return out


def r2b_covers(h, r2b):
    """Клетка целиком на строках R2-B: все её строки янв–сен (месячные — по месяцам, общая — в каждом) входят в 31 форму."""
    if h in MONTHLY:
        mm = {m: ln for m, ln in MONTHLY[h].items() if m in r2b}
        return bool(mm) and all(ln in r2b[m] for m, ln in mm.items())
    ln = (LINES.get(h) or (None,))[0]
    return bool(ln) and bool(r2b) and all(ln in v for v in r2b.values())


def _frac(v):
    """'1/3' / '0.5' / 2 -> float."""
    n, _, d = str(v).partition("/")
    return float(n) / float(d) if d else float(n)


def _int_in(x, ok):
    """x — целое из ok (допуск на дробь 1e-9) -> int, иначе None."""
    r = round(x)
    return int(r) if abs(x - r) < 1e-9 and r in ok else None


def _u12(u12):
    """Часть имени pyre/pynw: u<k> при u12 = 4k (доля k/3, прежние имена), иначе w<u12> (forms.rs u12_label)."""
    return f"u{u12 // 4}" if u12 in (4, 8, 12) else f"w{u12}"


# механика каталога -> (набор клеток, фиксированные параметры); привязка оси к параметру — колонка hyp_axes.mechanic
# `<MechId>:<параметр>[*множитель]`; имена и домены — mechanics.json / mechanics.DERIVED
MECH_RUN = {
    "PyrFresh": ("g87-fresh", {}),
    "PyrReinstall": ("t-bid-btc4h-q1", {}),
    "PyrNewWall": ("t-bid-btc4h-q1", {}),
    "NoStop": ("e106-pop", {}),
    "Converge": ("t-bid-btc4h-q1", {"a_bps": 20}),   # A = D20 прогона подходов
    "GoneWall": (None, {}),   # набор клетки — из стека
    "Gone": (None, {}),
    "Wall2": ("t-bid-btc4h-q1", {}),
    "Half": ("t-bid-btc4h-q1", {}),
    "EntryLadderSigma3": ("t-bid-btc4h-q1", {}),   # запись kind=entry: имя идёт во вход клетки без суффикса выхода
    "StopSigma": (None, {}),   # запись kind=stop: имя идёт в стоп клетки, не в суффикс выхода
}


def parse_mechanic(spec):
    """`PyrReinstall:u12*12` -> (механика, параметр, множитель); `GoneWall:~` — категориальная ось: её значение выбирает запись
    каталога (имя шаблона содержит `<значение>{`), параметра нет."""
    mid, _, rest = spec.partition(":")
    par, _, mul = rest.partition("*")
    if par == "-":   # ось входит в ключ карты другой оси той же механики (`~ось,ось>ключ:имя;…`)
        return (mid, "-", 1.0) if mid in MECH_RUN else sys.exit(f"hyp_axes.mechanic: «{spec}» — MechId ∈ {sorted(MECH_RUN)}")
    if par.startswith("~"):   # `~` или `~[ось,ось>]ключ:шаблон|имя;ключ:шаблон|имя`
        return (mid, "~", 1.0) if mid in MECH_RUN else sys.exit(f"hyp_axes.mechanic: «{spec}» — MechId ∈ {sorted(MECH_RUN)}")
    if mid not in MECH_RUN or not par:
        sys.exit(f"hyp_axes.mechanic: «{spec}» — ожидается <MechId>:<параметр>[*множитель], MechId ∈ {sorted(MECH_RUN)}")
    return mid, par, float(mul) if mul else 1.0


def r2_exit(hyp, av, mm=None, stack_set=None):
    """R2-гипотеза Rust-класса -> (входная форма или None, суффикс --exit-form, имя набора, причина отказа); имена и наборы — как в клетках Летописи
    (tk065-r2-gen.py, tk115-delta-day.sh) и в forms.rs; значение оси вне ограничений формы -> суффикс None и причина."""
    n = re.sub(r"\D", "", hyp)
    g = lambda k: _frac(av[k])
    if mm:   # оси с колонкой mechanic: имя формы строит каталог
        specs = {k: parse_mechanic(v) for k, v in mm.items()}
        if len({v[0] for v in specs.values()}) != 1:
            sys.exit(f"hyp_axes.mechanic: у {hyp} разные механики {sorted({v[0] for v in specs.values()})}")
        mid = next(iter(specs.values()))[0]
        set_name, fixed = MECH_RUN[mid]
        sel = []
        for k, (_, par, _) in specs.items():
            if par == "~":
                axs, gt, mp = mm[k].partition("~")[2].partition(">")
                if not gt:
                    axs, mp = k, axs
                sel.append((",".join(str(av[a]).strip() for a in axs.split(",")), mp))
        tmpl = None
        if sel:   # категориальная ось выбирает запись-вариант (подстрока `<значение>{` или явная карта `~значение:шаблон;…`); вне каталога -> отказ клетки
            v, mp = sel[0]
            if mp:   # правая часть карты — шаблон или готовое имя записи каталога
                hit = [t for x, _, t in (e.partition(":") for e in mp.split(";")) if x == v]
                hit = [t for t in hit if mech.match(t)]
            else:
                hit = [r["name_template"] for r in mech.ROWS if r["id"] == mid and f"{v}{{" in r["name_template"]]
            if len(hit) != 1:
                return None, None, set_name or stack_set, f"{mid}: значение «{v}» не выбирает одну запись каталога ({hit})"
            tmpl = hit[0]
            row = mech.match(tmpl)
            mid = row["id"]
            if tmpl != row["name_template"] or not row["params"]:   # готовое имя, не шаблон
                return None, tmpl, set_name or stack_set, None
        vals = {**fixed, **{par: g(k) * mul for k, (_, par, mul) in specs.items() if par not in ("~", "-")}}
        name, why = mech.build(mid, template=tmpl, **vals)
        return None, name, set_name or stack_set, None if name else f"{mid}: {why}"
    return False


R2_EXIT_RE = "(" + "|".join(mech.r2_prefixes()) + ")"   # ExitForm::is_r2 — из каталога механик


EARLY_OK = (0.5, 1, 2, 3, 5, 8, 13, 20, 30, 60)   # EARLY_EXITS_S в plan.rs + EARLY_EXIT_HALF_MS (TK-189)
TTL_OK = (10, 30, 60, 120, 300, 600, 900, 1200, 1800)   # ENTRY_TTL_SECS в forms.rs (TK-189)


def rust_axes(hyp, av, sv):
    """Гипотезы, выражаемые флагами/формами текущего Rust без новых кодов -> (потолок входа с, суффикс --exit-form, early с, причина отказа)
    или False (гипотеза не этого класса). Г-129 — ttl в имени формы; Г-110 — хвост -early<x>; Г-104 — gone<W>[be] (be=да)."""
    n = re.sub(r"\D", "", hyp)
    ttl = sv["entry_ttl_s"]
    if n == "129":
        t = _int_in(_frac(av["entry_ttl"]), TTL_OK)
        return t or ttl, "", None, None if t else f"ttl: ∈ {TTL_OK}"
    if n == "110":
        x = _frac(av["early_exit_s"])
        x = x if x in EARLY_OK else None
        x = int(x) if x is not None and float(x).is_integer() else x
        return ttl, "", x, None if x is not None else f"early<x>: секунды ∈ {EARLY_OK}"
    if n == "147":   # --signal touch: вход на касании вместо подхода — шапка прогона (touches-from без approach), не --cells; хеш по варианту
        if str(av["signal"]).strip() != "touch":
            return ttl, "", None, "signal: touch"
        return ttl, "", None, "--signal touch — режим шапки прогона (кэш касаний, не approach D20): отдельный заход, в --cells не идёт"
    if n == "117":   # tslx<G>t<Q>: γ = G/8, T = Q/16 дедлайна; работает только при trail_bps = 0 (strategy/plan.rs, PyramidCfg.sched_*)
        g8, t16 = _int_in(8 * _frac(av["gamma"]), range(1, 65)), _int_in(16 * _frac(av["T"]), range(1, 17))
        if not (g8 and t16):
            return ttl, None, None, "tslx<G>t<Q>: γ ∈ [1/8, 8], T ∈ [1/16, 1]"
        if g8 in (4, 8, 16) and t16 in (4, 8, 16):   # общие точки с прежней сеткой (γ ½,1,2 × T ¼,½,1) — прежние имена tsl<G>t<Q> (g/10, q/4), переиспользование
            name, why = mech.build("TakeSched", g10=g8 * 10 // 8, t4=t16 // 4)
        else:
            name, why = mech.build("TakeSchedX", g8=g8, t16=t16)   # имя и домен — запись каталога
        return ttl, name, None, why   # тейк клетки — 1to1 (стек B3, Судья 11.10: reviews/P-14-2026-10-11-f1-axes.md), см. p14_cfg
    return False


def p14_cfg(hyp, sv, av, fixed=(), mm=None):
    """Конфиг клетки П-14 без cfg_path: база B1 как в cellhash_reg (форма из стека, набор, исполнение, σ), вселенная — из стека.
    R2-гипотезы с кодировкой в Летописи (r2_exit) — форма с суффиксом выхода и набор, как у клеток Летописи; прочие — вариант
    «семья + оси». Стек, не совпадающий с константами cellhash_reg (набор, σ, очередь, busy), — отказ."""
    import cellhash_reg as cr
    sets = cr.load_sets()
    if sv.get("set") not in sets:
        sys.exit(f"plan: набор стека «{sv.get('set')}» не найден в --set прогонов (tk040-sets-jan.txt / EXTRA_SETS) — набор не назначаем молча")
    for k, want in (("sigma", cr.B1_SIGMA.rsplit("/", 1)[-1]), ("queue", cr.STD_EXEC["queue"]), ("busy", cr.STD_EXEC["busy_skip"])):
        if k in sv and str(sv[k]) != want:
            sys.exit(f"plan: стек {k}={sv[k]} расходится с константой cellhash_reg ({want}) — исполнение берётся из неё; решение Судьи")
    entry = sv["entry"]
    set_name = sv["set"]
    suffix, line_why, stop = "", None, sv["stop"]
    r2 = r2_exit(hyp, av, mm, set_name) if av else False
    if r2:
        e, suffix, set_name, line_why = r2
        entry = e or entry
        row = mech.match(suffix) if suffix else None
        if row and row["kind"] == "stop":   # запись каталога — стоп (s<a>): имя в стоп клетки, суффикса выхода нет
            stop, suffix = suffix, ""
        elif row and row["kind"] == "entry":   # запись каталога — вход (лестница): имя во вход клетки, суффикса выхода нет
            entry, suffix = suffix, ""
    ttl, early = sv["entry_ttl_s"], None
    ra = rust_axes(hyp, av, sv) if av and not r2 else False
    if ra:
        ttl, suffix, early, line_why = ra
        r2 = (None, suffix, set_name, line_why)   # кодируется формой/флагом -> ветка клетки с LINES
    take = "1to1" if re.sub(r"\D", "", hyp) == "117" else sv["take"]   # Г-117: стек B3 = B1 с тейком 1to1 без трейла (tsl не действует при трейле)
    form = f"{entry}-{stop}-{take}-{sv['deadline_s']}-ttl{ttl}" + (f"-{suffix}" if suffix else "") + (f"-early{early}" if early else "")
    st = cr.B1_SET if set_name == "t-bid-btc4h-q1" else cr.set_of(sets[set_name])
    uni = cr.UNIVERSE["v171c"] if "v171c" in sv.get("universe", "") else cr.UNIVERSE["v171b"]
    cfg = {"form": form, "set": st, "exec": dict(cr.STD_EXEC), "sigma": cr.B1_SIGMA, "universe": uni}
    if r2:
        if line_why:
            cfg["variant"] = {"family": family_of(hyp), "axes": {k: ch.num(v) for k, v in av.items()}}  # не кодируется формой: хеш по варианту, в --cells не идёт
        LINES[ch.cell_hash(cfg)] = (None if line_why else f"{form} {set_name}", line_why)
        if re.sub(r"\D", "", hyp) == "117" and re.fullmatch(r"tsl(5|10|20)t[124]", suffix or ""):   # 9 общих точек γ×T: посчитаны проходом r2t (TK-065, 07.10) на B3 — те же формы
            OUTSIDE[ch.cell_hash(cfg)] = "r2t TK-065: /data/tk065/wt-<сутки>/b5 (275 суток 01.01–02.10, форма без -tsl… = B3)"
    elif "keep_q" in av or "common_q" in av:  # R1: как у клеток Летописи — признак (None, если колонка одна, она в имени клетки не стоит), квантиль «kNN», порог по окну
        fam, _, feat = family_of(hyp).partition("-")
        col = None if "col" in fixed else av.get("col", feat or None)
        cfg["variant"] = {"family": fam, "axes": {"feature": col if col is not None else (feat or None), "q": "k" + str(ch.num(av.get("keep_q", av.get("common_q")))), "thr": "expanding-window"}}
        if "ctx_q" in av:   # В-227 М3 (Г-78/55/140): страта оси контекста — отдельная клетка, не r1ext
            cfg["variant"]["axes"]["ctx"] = str(av["ctx_q"])
        sign = R1_SIGN.get(hyp, {}).get(col)
        if "ctx_q" not in av and (col is None or "common_q" in av or (col in R1EXT_COLS and (sign is None or R1EXT_SIGN.get(col) == sign))):   # колонка/знак вне r1ext (В-227 М1) — не «посчитано»: идёт в «к счёту»
            R1COL[ch.cell_hash(cfg)] = cfg["variant"]["axes"]["feature"]
        elif ("ctx_q" in av or col is not None) and "common_q" not in av:   # В-227 М1/М3: колонка/знак вне r1ext и ctx×keep_q — посчитано tk202-r1x.py (calc /data/tk202/r1x, янв–сен)
            R1X.add(ch.cell_hash(cfg))
    elif av and re.sub(r"\D", "", hyp) == "133":   # chase<round(a·W_m)> мс по месяцам (Судья 11.10, f1-axes): хеш — по оси a (как e133-w* Летописи), строки — на месяц
        a = _frac(av["a"])
        cfg["variant"] = {"family": family_of(hyp), "axes": {"a": int(a) if float(a).is_integer() else a}}
        h = ch.cell_hash(cfg)
        LINES[h] = (None, "месячная форма chase<a·W_m>: строки --cells по месяцам (файлы <имя>-<ГГГГ-ММ>.cells)")
        MONTHLY[h] = {m: f"{form}-chase{int(round(w * a))} {set_name}" for m, w in w_median_ms().items()}
    elif av and re.sub(r"\D", "", hyp) == "112":   # tape<W>q<Q_m(W,q)>: Q из v7 по прошлым месяцам (Судья 11.10, f1-axes); хеш — по осям W, q
        w, q = _int_in(_frac(av["W"]), (5, 10, 15, 30, 60, 120, 300)), _int_in(_frac(av["q"]), (5, 10, 20, 33, 50, 67, 80, 90, 95))
        cfg["variant"] = {"family": family_of(hyp), "axes": {"W": w, "q": q}}
        h = ch.cell_hash(cfg)
        thr = json.load(open(_TAPE_THR, encoding="utf-8"))["summary"] if os.path.exists(_TAPE_THR) else []
        mm = {r["month"]: f"{form}-tape{w}q{'p' if r.get(f'e112_W{w}_q{q}', 0) <= 0 else repr(float(r[f'e112_W{w}_q{q}']))} {set_name}" for r in thr if w and q and f"e112_W{w}_q{q}" in r}
        if not (w and q):
            LINES[h] = (None, "Г-112: W ∈ {5,10,15,30,60,120,300} с, q ∈ {5,10,20,33,50,67,80,90,95}")
        elif not thr:
            LINES[h] = (None, f"Г-112: нет {os.path.basename(_TAPE_THR)} — v7 TK-120 (thr7/summary.json)")
        else:
            no = sorted(r["month"] for r in thr if r["month"] not in mm)
            LINES[h] = (None, "месячная форма tape<W>q<Q_m>: файлы <имя>-<ГГГГ-ММ>.cells" + (f"; нет порога в v7 — месяцы {','.join(no)}" if no else ""))
            MONTHLY[h] = mm
    elif av and re.sub(r"\D", "", hyp) == "10":   # btc<N>h_max=T_m(N,q): набор B1 с заменой порога, T_m по месяцам из прошлых месяцев (Судья 11.10, f1-axes)
        n_h, q = _int_in(_frac(av["btc_window_h"]), (1, 2, 3, 4, 6, 8, 12, 24)), _int_in(_frac(av["btc_thr_q"]), (5, 10, 20, 33, 50, 67, 80, 90, 95))
        cfg["variant"] = {"family": family_of(hyp), "axes": {"btc_window_h": n_h, "btc_thr_q": q}}
        h = ch.cell_hash(cfg)
        thr = json.load(open(_BTC_THR, encoding="utf-8")) if os.path.exists(_BTC_THR) else {}
        if not (n_h and q):
            LINES[h] = (None, "Г-10: окно ∈ {1,2,3,4,6,8,12,24} ч, q ∈ {5,10,20,33,50,67,80,90,95}")
        elif not thr:
            LINES[h] = (None, f"Г-10: нет {os.path.basename(_BTC_THR)} — btc-thresholds.py по study/regime (regime.py с btc_ret_6h…24h)")
        else:
            LINES[h] = (None, "месячный набор g10n<N>q<q>-<ГГГГ-ММ> (btc<N>h_max=T_m): файлы <имя>-<ГГГГ-ММ>.cells")
            mm = {}
            for m, per in thr.items():
                t = per.get(str(n_h), {}).get(str(q))
                if t is not None:
                    name = f"g10n{n_h}q{q}-{m}"
                    MSETS[name] = f"age=2700,side=bid,btc{n_h}h_max={t}"
                    mm[m] = f"{form} {name}"
            MONTHLY[h] = mm
    elif av and ("usd_min_q" in av or "behind_min_q" in av) and "keep_q" not in av:   # В-227 М3 (Г-89/107/27, Г-32): usd_min=T_m(q) по месяцам; Г-07 — behind_min=T_m(q), % (D20); Г-32 — ещё страта btc4h внутри базы B1 (Судья 12.10 04:14)
        ax, pre, key = ("behind_min_q", "b", "behind") if "behind_min_q" in av else ("usd_min_q", "u", "usd")   # ось, префикс набора, раздел порогов
        q = _int_in(_frac(av[ax]), (5, 10, 20, 33, 50, 67, 80, 90, 95))
        k = int(str(av["ctx_q"])[1:]) if "ctx_q" in av and re.fullmatch(r"q[1-5]", str(av["ctx_q"])) else None
        axes = {ax: q} if k is None else {ax: q, "ctx": f"btc4h-q{k}"}
        cfg["variant"] = {"family": "g32" if "ctx_q" in av else ("behind_min" if key == "behind" else "usd_min"), "axes": axes}   # Г-89/107/27 — одна ось, один хеш
        h = ch.cell_hash(cfg)
        thr = json.load(open(_TK202_THR, encoding="utf-8")) if os.path.exists(_TK202_THR) else {}
        if not q or ("ctx_q" in av and k is None):
            LINES[h] = (None, f"{ax} ∈ {{5,10,20,33,50,67,80,90,95}}, ctx_q ∈ q1…q5")
        elif not thr:
            LINES[h] = (None, f"нет {os.path.basename(_TK202_THR)} — tools/analyze/tk202-thresholds.py")
        else:
            LINES[h] = (None, f"месячный набор {pre}<q>[c<k>]-<ГГГГ-ММ> ({key}_min=T_m): файлы <имя>-<ГГГГ-ММ>.cells")
            mm = {}
            for m, per in thr.get(key, {}).items():
                cx = ""
                if k is not None:
                    c = thr["ctx"]["btc4h"].get(m)
                    if c is None:
                        continue
                    lo, hi = {1: (None, c["q20"]), 2: (c["q20"], c["q40"]), 3: (c["q40"], c["q60"]), 4: (c["q60"], c["q80"]), 5: (c["q80"], None)}[k]
                    cx = (f",btc4h_min={lo}" if lo is not None else "") + (f",btc4h_max={hi}" if hi is not None else ",btc4h_max=-44.55")
                name = f"{pre}{q}{'c' + str(k) if k else ''}-{m}"
                MSETS[name] = f"age=2700,side=bid,{key}_min={per[str(q)]}" + (cx if cx else ",btc4h_max=-44.55")
                mm[m] = f"{form} {name}"
            MONTHLY[h] = mm
    elif av:
        cfg["variant"] = {"family": family_of(hyp), "axes": {k: ch.num(v) for k, v in av.items()}}
        LINES[ch.cell_hash(cfg)] = (None, "нет кодировки формы в Летописи/forms.rs — вариант семьи, строки --cells нет")
    return cfg


def make_cfg(paths, sv, av, hyp="", fixed=(), mm=None):
    """Значения стека + значения осей -> конфиг для cellhash (ось перекрывает стек); без cfg_path — адаптер П-14."""
    if not paths:
        return p14_cfg(hyp, sv, av, fixed, mm)
    cfg = {}
    for k, v in {**sv, **av}.items():
        set_path(cfg, paths.get(k, k), _val(v))
    return cfg


def rules_of(c, protocol):
    r = dict(RULE_DEFAULTS)
    vers = [x[0] for x in c.execute("select distinct version from rules where protocol=?", (protocol,))]
    if vers:
        for k, v in c.execute("select key, value from rules where protocol=? and version=?", (protocol, max(vers))):
            r[k] = fval(v) if fval(v) is not None else v
        r["_version"] = max(vers)
    return r


def have_hashes(c=None):
    """{хеш: {месяцы с результатами}} — клетки Летописи по каноническому хешу, у которых есть results."""
    import cellhash_reg
    months = collections.defaultdict(set)
    for d in db.jl("results.jsonl"):
        months[d["cell_id"]].add(d["month"])
    out = collections.defaultdict(set)
    for row in cellhash_reg.build():
        out[row[1]] |= months.get(row[0], set())
    return {h: m for h, m in out.items() if m}


def connect(build=True):
    import registry
    return registry.build()[0] if build or not os.path.exists(db.DB) else sqlite3.connect(db.DB)


# ---------- plan ----------
def _axes(c, protocol, hyp):
    return c.execute("select param, points_f1, hyp_axes.stack, version, coalesce(role, ''), coalesce(mechanic, '') from hyp_axes where protocol=? and hyp=? order by rowid", (protocol, hyp)).fetchall()


def _round(x, res):
    if res:
        x = round(x / res) * res
    return int(x) if float(x).is_integer() else round(x, 6)


def refine(vals, grid, ptype, res, div, cap):
    """Окрестность плато (§3.3): значения плато + по соседу с каждой стороны, каждый промежуток дробится на div; потолок cap новых точек."""
    if ptype == "categorical":
        return sorted(vals, key=str)
    g = sorted(grid, key=fval)
    idx = sorted(g.index(v) for v in vals)
    lo, hi = max(idx[0] - 1, 0), min(idx[-1] + 1, len(g) - 1)
    pts = [g[i] for i in range(lo, hi + 1)]
    new = []
    for a, b in zip(pts, pts[1:]):
        fa, fb = fval(a), fval(b)
        step = (fb - fa) / div
        if step < (res or 0) - 1e-12 and res:   # шаг мельче разрешимости — ось в этой фракции не дробится
            continue
        for j in range(1, div):
            x = fa + step * j if ptype != "log" else fa * (fb / fa) ** (j / div)
            new.append(_round(x, res))
    centre = sum(fval(g[i]) for i in idx) / len(idx)
    new = sorted(set(new) - {fval(p) for p in pts}, key=lambda x: abs(x - centre))[:int(cap)]
    return pts + sorted(new)


def build_grid(c, protocol, k, hyps=None, rules=None):
    """{hyp: [(axes_dict, cfg)]} для фракции k; закрытые гипотезы -> список пуст (причина в closed)."""
    rules = rules or rules_of(c, protocol)
    paths = dict(c.execute("select name, cfg_path from params where cfg_path is not null and cfg_path != ''"))
    ptypes = {r[0]: (r[1], r[2]) for r in c.execute("select name, axis_type, resolution from params")}
    grids, closed = {}, {}
    eng = dict(c.execute("select hyp, engine from hyp_axes where engine is not null and engine != ''"))
    names = hyps or [r[0] for r in c.execute("select distinct hyp from hyp_axes where protocol=?", (protocol,))]
    for hyp in sorted(names):
        axes = _axes(c, protocol, hyp)
        if eng.get(hyp) == "python":
            closed[hyp] = "python-класс (§6.2): хешей нет — не в плане Rust"
            continue
        if str(eng.get(hyp, "")).startswith("bind:"):   # В-227 М4/М8: ось уже у другой гипотезы — привязка, своих клеток нет
            closed[hyp] = f"привязка: ось = {eng[hyp][5:]} (клетки там, своих нет)"
            continue
        if any(a[4] == "pending" for a in axes):
            closed[hyp] = "ось pending (список точек не назван) — не в сетке"
            continue
        if not axes:
            closed[hyp] = "нет осей в hyp_axes"
            continue
        sv, _ = stack_values(c, axes[0][2], axes[0][3] or None)
        if k == 1:
            pts = {a[0]: [x for x in a[1].split("|") if x] for a in axes}
        else:
            prev = {r[0]: json.loads(r[1]) for r in c.execute("select cell_hash, axes from plan_cells where protocol=? and fraction=? and hyp=?", (protocol, k - 1, hyp))}
            plat = [prev[r[0]] for r in c.execute("select cell_hash from sieve where protocol=? and fraction=? and hyp=? and side='A' and plateau=1", (protocol, k - 1, hyp)) if r[0] in prev]
            if not plat:
                closed[hyp] = f"плато нет на сетке F{k - 1} — гипотеза закрыта"
                continue
            cap = rules.get("cap_f2", 12) if k == 2 else rules.get("cap_f3", 8)
            pts = {}
            for a in axes:
                t, res = ptypes.get(a[0], ("numeric", None))
                pts[a[0]] = refine(sorted({str(p[a[0]]) for p in plat}), sorted({str(p[a[0]]) for p in prev.values()}, key=lambda v: (fval(v) is None, fval(v) or 0, v)), t, res, int(rules.get("step_div", 4)), cap)
        cells = []
        mm = {a[0]: a[5] for a in axes if a[5]}
        for combo in itertools.product(*[pts[a[0]] for a in axes]):
            av = dict(zip([a[0] for a in axes], combo))
            cells.append((av, make_cfg(paths, sv, av, hyp, {a[0] for a in axes if a[4] == 'fixed'}, mm)))
        lim = rules.get("cap_cells", 100)
        if k > 1 and len(cells) > lim:
            closed[hyp] = f"сетка {len(cells)} > потолка {int(lim)} клеток на гипотезу — сузить (решение Судьи)"
            continue
        grids[hyp] = cells
    return grids, closed


def plan(protocol, k, hyps=None, out=None, have=None):
    c = connect()
    rules = rules_of(c, protocol)
    grids, closed = build_grid(c, protocol, k, hyps, rules)
    c.close()
    have = have_hashes() if have is None else have
    months = int(rules["months"])
    rows, new_cfg, tot, lines, nocode = [], [], collections.Counter(), {}, collections.Counter()
    r2b = r2b_lines()
    mlines = collections.defaultdict(dict)   # ГГГГ-ММ -> {хеш: строка} для месячных форм
    for hyp, cells in grids.items():
        seen, n_have, n_new, hm = set(), 0, 0, 0
        for av, cfg in cells:
            h = ch.cell_hash(cfg)
            if h in seen:
                continue
            seen.add(h)
            rows.append({"protocol": protocol, "fraction": k, "hyp": hyp, "cell_hash": h, "axes": json.dumps(av, ensure_ascii=False), "cfg": json.dumps(ch.normalize(cfg), ensure_ascii=False)})
            if h in have:
                n_have += 1
                hm += len(have[h])
            elif h in R1COL and R1COL[h] in R1_NODATA:   # колонки нет в сигналах пула (охват 0): не считается, не «к счёту»
                tot["nodata"] += 1
            elif h in OUTSIDE or h in R1COL or h in R1X or r2b_covers(h, r2b):   # посчитано вне Летописи (В-213: не пересчитывать) — отдельная графа, в --cells не идёт
                tot["outside"] += 1
                tot["outside_r1" if h in R1COL else "outside_r1x" if h in R1X else "outside_r2b" if h not in OUTSIDE else "outside_r2t"] += 1
            else:
                n_new += 1
                if ch.cell_hash({**cfg, "universe": "v171b"}) in have:   # есть в Летописи на другой вселенной (p07a, v171b) — зачёт решает Исследователь/§6
                    tot["uni"] += 1
                new_cfg.append({"hash": h, "hyp": hyp, **ch.normalize(cfg)})
                line, why = LINES.get(h, (f"{cfg['form']} {cfg['set_name']}" if cfg.get("set_name") else None, "маршрут R1: фильтр на сигналах пула — tk064-r1cells.py --levels 95,90,80,67,50,33,20,10,5 (calc /data/tk189/r1ext/cells/<мес>/, 271 клетка/мес, фев…окт; прежние 5 точек побайтно = /data/tk064/r1/cells); строк --cells нет по построению"))
                if h in MONTHLY:
                    for m, ln in MONTHLY[h].items():
                        mlines[m][h] = ln
                elif line:
                    lines[h] = line
                else:
                    nocode[(hyp, why)] += 1
        tot.update(grid=len(seen), have=n_have, new=n_new, have_hm=hm)
        print(f"  {hyp}: в сетке {len(seen)}, в Летописи {n_have}, к счёту {n_new}")
    for hyp, why in closed.items():
        print(f"  {hyp}: {why}")
    keys = {(r["protocol"], r["fraction"], r["hyp"]) for r in rows} | {(protocol, k, h) for h in list(grids) + list(closed)}
    _wjl("plan_cells.jsonl", [d for d in db.jl("plan_cells.jsonl") if (d["protocol"], d["fraction"], d["hyp"]) not in keys] + rows)
    txt = (f"Выписка В-224 · {protocol} F{k} · правила {rules.get('_version', '—')}\n"
           f"в сетке {tot['grid']} хешей (× {months} мес. = {tot['grid'] * months} хеш-мес.); в Летописи {tot['have']} хешей "
           f"({tot['have_hm']} хеш-мес. с результатами); к счёту {tot['new']} хешей (× {months} = {tot['new'] * months} хеш-мес.); "
           f"посчитано вне Летописи (загрузить, не считать) {tot['outside']} хешей (R1 r1ext {tot['outside_r1']}, R1 r1x TK-202 {tot['outside_r1x']}, R2-B TK-115 янв–сен {tot['outside_r2b']}, r2t Г-117 {tot['outside_r2t']}); не считается — колонки нет в сигналах пула (Г-82 60s, охват 0) {tot['nodata']} хешей; гипотез закрыто {len(closed)}; из «к счёту» {tot['uni']} совпали бы с Летописью на вселенной v171b (не совпало: вселенная)")
    print(txt)
    out = out or os.path.join(ROOT, "data", "plan", f"{protocol}-F{k}-new.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for d in new_cfg:
            f.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")
    print("новые клетки (конфиг + хеш):", out)
    cf = out[:-6] + ".cells" if out.endswith(".jsonl") else out + ".cells"
    import cellhash_reg as cr
    sets = {**cr.load_sets(), **MSETS}

    def write_set(stem, uniq):
        """<stem>.cells / .args / .bin по списку строк --cells «форма набор»."""
        with open(stem + ".cells", "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(l + "\n" for l in uniq))
        used = sorted({l.split()[1] for l in uniq})
        forms = sorted({l.split()[0] for l in uniq})
        with open(stem + ".args", "w", encoding="utf-8", newline="\n") as f:   # аргументы bounce-grid к файлу --cells (формы выхода и наборы)
            for e in sorted({fm.split("-pct", 1)[0] for fm in forms}):
                f.write(f"--entry-form {e}\n")
            tails = [re.search(r"-ttl(\d+)(?:-(?!early[\d.]+$)(.+?))?(?:-early([\d.]+))?$", fm) for fm in forms]
            for t in sorted({m.group(1) for m in tails if m}, key=int):
                f.write(f"--entry-ttl-secs {t}\n")
            for x in sorted({m.group(2) for m in tails if m and m.group(2)}):
                f.write(f"--exit-form {x}\n")
            for x in sorted({m.group(3) for m in tails if m and m.group(3)}, key=float):
                f.write(f"--early-exit-secs {x}\n")
            parts = [re.match(r"^(.+?)-(.+?)-(.+?)-(\d+)-ttl", fm) for fm in forms]
            for flag, g in (("--stop-form", 2), ("--take-form", 3), ("--deadline-secs", 4)):
                for v in sorted({m.group(g) for m in parts if m}):
                    f.write(f"{flag} {v}\n")
            for u in used:
                f.write(f"--set {u}:{sets[u]}\n")
        r2 = any(re.match(R2_EXIT_RE, x) for x in (m.group(2) for m in tails if m and m.group(2)))
        with open(stem + ".bin", "w", encoding="utf-8", newline="\n") as f:   # какой бинарь ставить прогону: формы R2 — только cargo feature r2 (forms.rs, ExitForm::parse)
            f.write("r2\n" if r2 else "std\n")

    uniq = sorted(set(lines.values()))
    write_set(cf[:-6], uniq)
    print(f"--cells (только новые, кодируемые формой): {cf} — {len(uniq)} строк; аргументы: {cf[:-6]}.args; бинарь: {cf[:-6]}.bin")
    for m in sorted(mlines):   # месячные формы (Г-133): свой набор файлов на месяц — число в имени формы зависит от месяца
        write_set(f"{cf[:-6]}-{m}", sorted(set(mlines[m].values())))
        print(f"--cells месяца {m}: {cf[:-6]}-{m}.cells — {len(set(mlines[m].values()))} строк (+ .args, .bin)")
    if nocode:
        print("к счёту, но строки --cells нет (причина):")
        for (hyp, why), n in sorted(nocode.items()):
            print(f"  {hyp}: {n} — {why}")
    return {"grid": tot["grid"], "have": tot["have"], "new": tot["new"], "closed": closed, "text": txt, "out": out}


# ---------- sieve ----------
def sieve(protocol, k, stats_csv):
    """Правило §4 к статистике клеток (только сторона А): годна -> плато. Числа порогов — из rules."""
    c = connect()
    rules = rules_of(c, protocol)
    need = ("p_max", "min_trades", "min_days", "plateau_min_nb")
    miss = [x for x in need if x not in rules]
    if miss:
        sys.exit(f"sieve: в rules нет {miss} (protocol {protocol}); числа — из БД, не из кода")
    plan_rows = c.execute("select hyp, cell_hash, axes from plan_cells where protocol=? and fraction=?", (protocol, k)).fetchall()
    if not plan_rows:
        sys.exit(f"sieve: нет plan_cells {protocol} F{k} — сначала registry plan")
    types = dict(c.execute("select name, axis_type from params"))
    c.close()
    st = {}
    for r in csv.DictReader(open(stats_csv, encoding="utf-8-sig"), delimiter=";"):
        if (r.get("side") or "A").strip() == "A":   # половина Б до финального теста не читается
            st[r["cell_hash"].strip()] = r
    elig = {}
    for hyp, h, axes in plan_rows:
        r = st.get(h)
        p = float(r["p"]) if r and r.get("p") not in (None, "") else 1.0
        tr = int(float(r["trades"])) if r and r.get("trades") else 0
        dy = int(float(r["days"])) if r and r.get("days") else 0
        ok = p <= rules["p_max"] and tr >= rules["min_trades"] and dy >= rules["min_days"]
        elig[(hyp, h)] = (p if ok else 1.0, tr, dy, int(ok))
    out = []
    by_hyp = collections.defaultdict(list)
    for hyp, h, axes in plan_rows:
        by_hyp[hyp].append((h, json.loads(axes)))
    for hyp, cells in by_hyp.items():
        names = list(cells[0][1])
        num_axes = [a for a in names if types.get(a, "numeric") != "categorical"]
        cat_axes = [a for a in names if types.get(a, "numeric") == "categorical"]
        pos = {}
        for a in num_axes:
            vals = sorted({str(x[1][a]) for x in cells}, key=fval)
            pos[a] = {v: i for i, v in enumerate(vals)}
        grid = {(tuple(str(x[1][a]) for a in cat_axes), tuple(pos[a][str(x[1][a])] for a in num_axes)): x[0] for x in cells}
        for h, av in cells:
            cat = tuple(str(av[a]) for a in cat_axes)
            ix = tuple(pos[a][str(av[a])] for a in num_axes)
            p, tr, dy, ok = elig[(hyp, h)]
            plateau = edge = 0
            if ok and num_axes:
                nbs, full = [], 3 ** len(num_axes) - 1
                for d in itertools.product((-1, 0, 1), repeat=len(num_axes)):
                    if not any(d):
                        continue
                    j = []
                    for a, i, di in zip(num_axes, ix, d):
                        n = len(pos[a])
                        jj = i + di
                        if types.get(a) == "circular":
                            jj %= n
                        elif not 0 <= jj < n:
                            jj = None
                        j.append(jj)
                    if None not in j and (cat, tuple(j)) in grid and (cat, tuple(j)) != (cat, ix):
                        nbs.append(grid[(cat, tuple(j))])
                good = sum(elig[(hyp, x)][3] for x in set(nbs))
                n = len(set(nbs))
                if len(num_axes) == 1:
                    edge = int(n == 1)
                    plateau = int(good == n and n >= 1)
                else:
                    edge = int(n < full)
                    plateau = int(good >= rules["plateau_min_nb"] if n == full else good * 2 > n)
            out.append({"protocol": protocol, "fraction": k, "hyp": hyp, "cell_hash": h, "side": "A", "p": p, "trades": tr, "days": dy,
                        "eligible": ok, "plateau": plateau, "edge": edge, "rule_version": rules.get("_version")})
    _replace("sieve.jsonl", out, lambda d: (d["protocol"], d["fraction"], d["cell_hash"], d["side"]))
    n_pl = sum(r["plateau"] for r in out)
    print(f"сито {protocol} F{k} (сторона А, правило {rules.get('_version')}): клеток {len(out)}, годных {sum(r['eligible'] for r in out)}, в плато {n_pl}")
    return out


# ---------- render ----------
def _table(header, rows):
    esc = lambda x: str(x).replace("|", "\\|")
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(esc(x) for x in r) + " |" for r in rows])


def render(kind, date=None, outdir=FIND):
    date = date or datetime.date.today().isoformat()
    c = connect()
    have = have_hashes()
    protos = [r[0] for r in c.execute("select distinct protocol from hyp_axes")]
    if kind == "matrix":
        params = [r[0] for r in c.execute("select name from params order by rowid")]
        hdr = ["гипотеза", "стек"] + params + ["хешей в сетке", "посчитано", "осталось"]
        rows = []
        for hyp, stack, ver, prot in c.execute("select distinct hyp, stack, version, protocol from hyp_axes order by hyp"):
            sv, _ = stack_values(c, stack, ver or None)
            ax = {a[0]: a[1] for a in _axes(c, prot, hyp)}
            hs = [r[0] for r in c.execute("select distinct cell_hash from plan_cells where protocol=? and hyp=?", (prot, hyp))]
            done = sum(1 for h in hs if h in have)
            cells = [f"T:{ax[p]}" if p in ax else (f"={sv[p]}" if p in sv else "") for p in params]
            rows.append([hyp, f"{stack}@{ver}"] + cells + [len(hs), done, len(hs) - done])
        title, name = "Матрица гипотез × параметров (заморожено `=значение`, титруется `T:точки F1`)", "hyp-matrix"
    elif kind == "coverage":
        hdr = ["протокол", "фракция", "гипотез", "хешей в сетке", "в Летописи", "к счёту", "хеш-мес. в Летописи", "хеш-мес. к счёту"]
        rows = []
        for prot in protos:
            months = int(rules_of(c, prot)["months"])
            for k in sorted(r[0] for r in c.execute("select distinct fraction from plan_cells where protocol=?", (prot,))):
                hs = c.execute("select hyp, cell_hash from plan_cells where protocol=? and fraction=?", (prot, k)).fetchall()
                done = [h for _, h in hs if h in have]
                rows.append([prot, f"F{k}", len({y for y, _ in hs}), len(hs), len(done), len(hs) - len(done), sum(len(have[h]) for h in done), (len(hs) - len(done)) * months])
        title, name = "Покрытие плана (В-224): хеши клеток × месяц", "hyp-coverage"
    else:
        sys.exit("render: matrix | coverage")
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, f"{name}-{date}")
    with open(base + ".csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        w.writerow(hdr)
        w.writerows(rows)
    with open(base + ".md", "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# {title}\n\nСгенерировано `registry render {kind}` из БД Летописи ({date}); руками не править.\n\n{_table(hdr, rows)}\n")
    c.close()
    print(f"{base}.md, .csv — строк {len(rows)}")
    return base, rows
