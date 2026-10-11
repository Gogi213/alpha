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
import cellhash as ch, db  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIND = os.path.join(ROOT, "docs", "findings")
LAYERS = {"params.csv": ("params.jsonl", ("name",)), "stacks.csv": ("stacks.jsonl", ("stack", "version", "param")),
          "hyp_axes.csv": ("hyp_axes.jsonl", ("hyp", "protocol", "param")), "rules.csv": ("rules.jsonl", ("protocol", "version", "key"))}
COLS = {"params.csv": ("name", "unit", "resolution", "axis_type", "cfg_path", "note"), "stacks.csv": ("stack", "version", "param", "value"),
        "hyp_axes.csv": ("hyp", "protocol", "version", "stack", "param", "lo", "hi", "points_f1", "old_points", "note", "role", "engine"),
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
             "role": r.get("role", ""), "engine": r.get("engine", "")}
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
_BTC_THR = os.path.join(FIND, "btc-thresholds-2026-10-11.json")   # tools/analyze/btc-thresholds.py: {месяц: {N: {q: T}}}


def w_median_ms():
    """{ГГГГ-ММ: W_ms_median} — медиана ожидания исполнения мейкер-лимитки по B1 прошлых месяцев (v6, TK-120; январь — по январю)."""
    return {r["month"]: r["W_ms_median"] for r in json.load(open(_THR, encoding="utf-8"))["summary"]}


LINES = {}  # хеш клетки -> (строка --cells «форма набор» | None, причина, что нет кодировки); заполняет build_grid, читает plan


def _frac(v):
    """'1/3' / '0.5' / 2 -> float."""
    n, _, d = str(v).partition("/")
    return float(n) / float(d) if d else float(n)


def _int_in(x, ok):
    """x — целое из ok (допуск на дробь 1e-9) -> int, иначе None."""
    r = round(x)
    return int(r) if abs(x - r) < 1e-9 and r in ok else None


def r2_exit(hyp, av):
    """R2-гипотеза Rust-класса -> (входная форма или None, суффикс --exit-form, имя набора, причина отказа); имена и наборы — как в клетках Летописи
    (tk065-r2-gen.py, tk115-delta-day.sh) и в forms.rs; значение оси вне ограничений формы -> суффикс None и причина."""
    n = re.sub(r"\D", "", hyp)
    g = lambda k: _frac(av[k])
    if n == "87":   # pyfresh<N>, N ∈ [2, 10]; набор g87-fresh
        v = _int_in(g("n"), range(2, 11))
        return None, (f"pyfresh{v}" if v else None), "g87-fresh", None if v else "pyfresh<N>: N ∈ [2, 10]"
    if n == "92":   # pyre<N>u<k>, N ∈ [1, 9], k = 3u ∈ {1, 2, 3}
        nn, k = _int_in(g("N"), range(1, 10)), _int_in(3 * g("u"), (1, 2, 3))
        return None, (f"pyre{nn}u{k}" if nn and k else None), "t-bid-btc4h-q1", None if nn and k else "pyre<N>u<k>: N ∈ [1, 9], u ∈ {1/3, 2/3, 1}"
    if n == "93":   # pynw<K>u<k>, K ∈ [1, 9], k = 3u ∈ {1, 2, 3}
        kk, k = _int_in(g("K"), range(1, 10)), _int_in(3 * g("u"), (1, 2, 3))
        return None, (f"pynw{kk}u{k}" if kk and k else None), "t-bid-btc4h-q1", None if kk and k else "pynw<K>u<k>: K ∈ [1, 9], u ∈ {1/3, 2/3, 1}"
    if n == "106":  # nostop<X2>, X2 = 2x ∈ {1, 2, 4}; набор e106-pop
        x2 = _int_in(2 * g("x"), (1, 2, 4))
        return None, (f"nostop{x2}" if x2 else None), "e106-pop", None if x2 else "nostop<X2>: x ∈ {1/2, 1, 2}"
    if n == "119":  # conv<t>a20 (A = D20 прогона подходов), t ∈ [0, 9]
        t = _int_in(g("tol_ticks"), range(0, 10))
        return None, (f"conv{t}a20" if t is not None else None), "t-bid-btc4h-q1", None if t is not None else "conv<t>: t ∈ [0, 9]"
    if n == "114":  # halfstop|halflevel[f1|f3]: f = q4/4, q4 ∈ {1, 2, 3}
        q4 = _int_in(4 * g("f"), (1, 2, 3))
        base = av["trigger"]
        return None, (None if not q4 else base if q4 == 2 else f"{base}f{q4}"), "t-bid-btc4h-q1", None if q4 else "half…: f ∈ {1/4, 1/2, 3/4}"
    if n == "65":   # цель = 2-я стена + k тиков, k ∈ −3..3 (Судья 11.10): wall2 ≡ −1, wall2x ≡ +1, wall20, wall2m<n>, wall2p<n> (forms.rs)
        k = _int_in(g("offset"), range(-3, 4))
        name = None if k is None else {-1: "wall2", 1: "wall2x", 0: "wall20"}.get(k, f"wall2m{-k}" if k < 0 else f"wall2p{k}")
        return None, name, "t-bid-btc4h-q1", None if name else "wall2: offset ∈ −3..+3 тиков"
    if n == "95":   # другая лестница входа: ladder3x0..0.0409sw<w>, без суффикса выхода
        w = _int_in(g("w"), range(2, 13))
        return (f"ladder3x0..0.0409sw{w}" if w else None), "", "t-bid-btc4h-q1", None if w else "sw<w>: целое"
    return False


R2_EXIT_RE = r"(pyeat|pyfresh|pyre|pynw|halfstop|halflevel|tsl|conv|nostop|chase|tape|cxl|wall2)"   # ExitForm::is_r2


EARLY_OK = (1, 2, 3, 5, 8, 13, 20, 30, 60)       # EARLY_EXITS_S в plan.rs (TK-189)
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
        x = _int_in(_frac(av["early_exit_s"]), EARLY_OK)
        return ttl, "", x, None if x else f"early<x>: целые секунды ∈ {EARLY_OK} (0,5 с — вне целого типа флага)"
    if n == "104":
        w = _int_in(_frac(av["exit_share"]), (10, 25, 50, 75, 90))
        be = str(av["be"]).strip().lower()
        if be not in ("да", "нет"):
            return ttl, None, None, f"be: «да»|«нет», получено {av['be']!r}"
        return ttl, f"gone{w}" + ("be" if be == "да" else ""), None, None if w else "gone<W>: W ∈ {10, 25, 50, 75, 90}"
    if n == "117":   # tslx<G>t<Q>: γ = G/8, T = Q/16 дедлайна; работает только при trail_bps = 0 (strategy/plan.rs, PyramidCfg.sched_*)
        g8, t16 = _int_in(8 * _frac(av["gamma"]), range(1, 65)), _int_in(16 * _frac(av["T"]), range(1, 17))
        if not (g8 and t16):
            return ttl, None, None, "tslx<G>t<Q>: γ ∈ [1/8, 8], T ∈ [1/16, 1]"
        if g8 in (4, 8, 16) and t16 in (4, 8, 16):   # общие точки с прежней сеткой (γ ½,1,2 × T ¼,½,1) — прежние имена tsl<G>t<Q> (g/10, q/4), переиспользование
            return ttl, f"tsl{g8 * 10 // 8}t{t16 // 4}", None, None
        return ttl, f"tslx{g8}t{t16}", None, None   # тейк клетки — 1to1 (стек B3, Судья 11.10: reviews/P-14-2026-10-11-f1-axes.md), см. p14_cfg
    return False


def p14_cfg(hyp, sv, av, fixed=()):
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
    suffix, line_why = "", None
    r2 = r2_exit(hyp, av) if av else False
    if r2:
        e, suffix, set_name, line_why = r2
        entry = e or entry
    ttl, early = sv["entry_ttl_s"], None
    ra = rust_axes(hyp, av, sv) if av and not r2 else False
    if ra:
        ttl, suffix, early, line_why = ra
        r2 = (None, suffix, set_name, line_why)   # кодируется формой/флагом -> ветка клетки с LINES
    take = "1to1" if re.sub(r"\D", "", hyp) == "117" else sv["take"]   # Г-117: стек B3 = B1 с тейком 1to1 без трейла (tsl не действует при трейле)
    form = f"{entry}-{sv['stop']}-{take}-{sv['deadline_s']}-ttl{ttl}" + (f"-{suffix}" if suffix else "") + (f"-early{early}" if early else "")
    st = cr.B1_SET if set_name == "t-bid-btc4h-q1" else cr.set_of(sets[set_name])
    uni = cr.UNIVERSE["v171c"] if "v171c" in sv.get("universe", "") else cr.UNIVERSE["v171b"]
    cfg = {"form": form, "set": st, "exec": dict(cr.STD_EXEC), "sigma": cr.B1_SIGMA, "universe": uni}
    if r2:
        if line_why:
            cfg["variant"] = {"family": family_of(hyp), "axes": {k: ch.num(v) for k, v in av.items()}}  # не кодируется формой: хеш по варианту, в --cells не идёт
        LINES[ch.cell_hash(cfg)] = (None if line_why else f"{form} {set_name}", line_why)
    elif "keep_q" in av or "common_q" in av:  # R1: как у клеток Летописи — признак (None, если колонка одна, она в имени клетки не стоит), квантиль «kNN», порог по окну
        fam, _, feat = family_of(hyp).partition("-")
        col = None if "col" in fixed else av.get("col", feat or None)
        cfg["variant"] = {"family": fam, "axes": {"feature": col if col is not None else (feat or None), "q": "k" + str(ch.num(av.get("keep_q", av.get("common_q")))), "thr": "expanding-window"}}
    elif av and re.sub(r"\D", "", hyp) == "133":   # chase<round(a·W_m)> мс по месяцам (Судья 11.10, f1-axes): хеш — по оси a (как e133-w* Летописи), строки — на месяц
        a = _frac(av["a"])
        cfg["variant"] = {"family": family_of(hyp), "axes": {"a": int(a) if float(a).is_integer() else a}}
        h = ch.cell_hash(cfg)
        LINES[h] = (None, "месячная форма chase<a·W_m>: строки --cells по месяцам (файлы <имя>-<ГГГГ-ММ>.cells)")
        MONTHLY[h] = {m: f"{form}-chase{int(round(w * a))} {set_name}" for m, w in w_median_ms().items()}
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
    elif av:
        cfg["variant"] = {"family": family_of(hyp), "axes": {k: ch.num(v) for k, v in av.items()}}
        LINES[ch.cell_hash(cfg)] = (None, "нет кодировки формы в Летописи/forms.rs — вариант семьи, строки --cells нет")
    return cfg


def make_cfg(paths, sv, av, hyp="", fixed=()):
    """Значения стека + значения осей -> конфиг для cellhash (ось перекрывает стек); без cfg_path — адаптер П-14."""
    if not paths:
        return p14_cfg(hyp, sv, av, fixed)
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
    return c.execute("select param, points_f1, hyp_axes.stack, version, coalesce(role, '') from hyp_axes where protocol=? and hyp=? order by rowid", (protocol, hyp)).fetchall()


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
        for combo in itertools.product(*[pts[a[0]] for a in axes]):
            av = dict(zip([a[0] for a in axes], combo))
            cells.append((av, make_cfg(paths, sv, av, hyp, {a[0] for a in axes if a[4] == 'fixed'})))
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
            else:
                n_new += 1
                if ch.cell_hash({**cfg, "universe": "v171b"}) in have:   # есть в Летописи на другой вселенной (p07a, v171b) — зачёт решает Исследователь/§6
                    tot["uni"] += 1
                new_cfg.append({"hash": h, "hyp": hyp, **ch.normalize(cfg)})
                line, why = LINES.get(h, (f"{cfg['form']} {cfg['set_name']}" if cfg.get("set_name") else None, "вариант R1 (признак×квантиль) — не форма bounce-grid, строки --cells нет"))
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
           f"гипотез закрыто {len(closed)}; из «к счёту» {tot['uni']} совпали бы с Летописью на вселенной v171b (не совпало: вселенная)")
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
            tails = [re.search(r"-ttl(\d+)(?:-(?!early\d+$)(.+?))?(?:-early(\d+))?$", fm) for fm in forms]
            for t in sorted({m.group(1) for m in tails if m}, key=int):
                f.write(f"--entry-ttl-secs {t}\n")
            for x in sorted({m.group(2) for m in tails if m and m.group(2)}):
                f.write(f"--exit-form {x}\n")
            for x in sorted({m.group(3) for m in tails if m and m.group(3)}, key=int):
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
