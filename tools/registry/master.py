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


def p14_cfg(hyp, sv, av, fixed=()):
    """Конфиг клетки П-14 без cfg_path: база B1 как в cellhash_reg (форма из стека, набор, исполнение, σ), вселенная — из стека,
    вариант = семья гипотезы + значения осей (имена осей = ключи p12-cells: n, N, u, K, col, keep_q …)."""
    import cellhash_reg as cr
    form = f"{sv['entry']}-{sv['stop']}-{sv['take']}-{sv['deadline_s']}-ttl{sv['entry_ttl_s']}"
    sets = cr.load_sets()
    st = cr.B1_SET if sv.get("set") == "t-bid-btc4h-q1" or sv.get("set") not in sets else cr.set_of(sets[sv["set"]])
    uni = cr.UNIVERSE["v171c"] if "v171c" in sv.get("universe", "") else cr.UNIVERSE["v171b"]
    cfg = {"form": form, "set": st, "exec": dict(cr.STD_EXEC), "sigma": cr.B1_SIGMA, "universe": uni}
    if "keep_q" in av or "common_q" in av:  # R1: как у клеток Летописи — признак (None, если колонка одна, она в имени клетки не стоит), квантиль «kNN», порог по окну
        fam, _, feat = family_of(hyp).partition("-")
        col = None if "col" in fixed else av.get("col", feat or None)
        cfg["variant"] = {"family": fam, "axes": {"feature": col if col is not None else (feat or None), "q": "k" + str(ch.num(av.get("keep_q", av.get("common_q")))), "thr": "expanding-window"}}
    elif av:
        cfg["variant"] = {"family": family_of(hyp), "axes": {k: ch.num(v) for k, v in av.items()}}
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
    rows, new_cfg, tot = [], [], collections.Counter()
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
                new_cfg.append({"hash": h, "hyp": hyp, **ch.normalize(cfg)})
        tot.update(grid=len(seen), have=n_have, new=n_new, have_hm=hm)
        print(f"  {hyp}: в сетке {len(seen)}, в Летописи {n_have}, к счёту {n_new}")
    for hyp, why in closed.items():
        print(f"  {hyp}: {why}")
    keys = {(r["protocol"], r["fraction"], r["hyp"]) for r in rows} | {(protocol, k, h) for h in list(grids) + list(closed)}
    _wjl("plan_cells.jsonl", [d for d in db.jl("plan_cells.jsonl") if (d["protocol"], d["fraction"], d["hyp"]) not in keys] + rows)
    txt = (f"Выписка В-224 · {protocol} F{k} · правила {rules.get('_version', '—')}\n"
           f"в сетке {tot['grid']} хешей (× {months} мес. = {tot['grid'] * months} хеш-мес.); в Летописи {tot['have']} хешей "
           f"({tot['have_hm']} хеш-мес. с результатами); к счёту {tot['new']} хешей (× {months} = {tot['new'] * months} хеш-мес.); "
           f"гипотез закрыто {len(closed)}")
    print(txt)
    out = out or os.path.join(ROOT, "data", "plan", f"{protocol}-F{k}-new.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for d in new_cfg:
            f.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")
    print("новые клетки (конфиг + хеш):", out)
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
