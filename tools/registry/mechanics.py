"""Каталог механик для Python (В-227, пакет A): читает снимок `mechanics.json` = `lob mechanics --json`.
Источник — Rust (`bounce_grid/mechanics.rs`); снимок не отстаёт: тест `python_snapshot_matches_catalog`.
Здесь — имя формы по шаблону с проверкой домена и признак R2; ветвей по номерам гипотез нет."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = json.load(open(os.path.join(HERE, "mechanics.json"), encoding="utf-8"))
ROWS = CATALOG["mechanics"]


def _fmt(x):
    """Число как `{}` у f64 в Rust: целое без «.0»."""
    x = float(x)
    return str(int(x)) if x.is_integer() else repr(x)


def admits(dom, v):
    """v (число) лежит в домене параметра и не исключено."""
    if dom["type"] == "int":
        r = round(v)
        if abs(v - r) > 1e-9 or r < dom["lo"] or (dom["hi"] is not None and r > dom["hi"]) or r in dom["excl"]:
            return False
        return True
    if dom["type"] == "set":
        return any(abs(v - x) < 1e-9 for x in dom["values"])
    lo, hi = dom["lo"], dom["hi"]
    if v < lo or (dom["lo_open"] and v == lo):
        return False
    return hi is None or v < hi or (v == hi and not dom["hi_open"])


# грубый параметр = тонкий / делитель, если делится нацело (pyre{n}u{u3} ≡ pyre{n}w{4·u3}; nostop{x2} ≡ nostopx{4·x2})
DERIVED = {"u3": ("u12", 4), "x2": ("x8", 4)}


def _with_derived(vals):
    out = dict(vals)
    for name, (src, div) in DERIVED.items():
        v = vals.get(src)
        if name not in out and v is not None and abs(v - round(v)) < 1e-9 and round(v) % div == 0:
            out[name] = round(v) // div
    return out


# тонкая запись → прежняя грубая, когда значения лежат на её сетке: те же клетки, старые имена (tslx<g8>t<t16> ≡ tsl<g10>t<t4> при
# g8 ∈ {4,8,16}, t16 ∈ {4,8,16}: g10 = g8·10/8, t4 = t16/4)
REUSE = {"TakeSchedX": {"to": "TakeSched", "when": {"g8": (4, 8, 16), "t16": (4, 8, 16)}, "map": {"g10": ("g8", 10, 8), "t4": ("t16", 1, 4)}}}


def build(mech_id, template=None, **vals):
    """Каноническое имя формы `mech_id` по значениям параметров -> (имя | None, причина).
    Берётся запись каталога, чьи параметры все заданы в vals и лежат в домене; несколько подходящих — ошибка каталога.
    template — точный шаблон имени, когда у механики несколько записей-вариантов (gone{pct}wall… / …wallx… / …wallk…)."""
    ru = REUSE.get(mech_id)
    if ru and template is None and all(vals.get(k) in ok for k, ok in ru["when"].items()):
        return build(ru["to"], **{t: vals[src] * mul // div for t, (src, mul, div) in ru["map"].items()})
    rows = [r for r in ROWS if r["id"] == mech_id and template in (None, r["name_template"])]
    if not rows:
        raise KeyError(f"нет механики {mech_id!r} в каталоге")
    vals = _with_derived(vals)
    hits = []
    for r in rows:
        ps = r["params"]
        if ps and all(p["name"] in vals and vals[p["name"]] is not None and admits(p["dom"], vals[p["name"]]) for p in ps):
            name = r["name_template"]
            for p in ps:
                v = vals[p["name"]]
                name = name.replace("{%s}" % p["name"], str(int(round(v))) if p["dom"]["type"] == "int" else _fmt(v))
            hits.append(name)
    if len(hits) == 1:
        return hits[0], None
    if hits:
        raise ValueError(f"{mech_id}: {len(hits)} записей каталога подходят к {vals}: {hits}")
    return None, "; ".join(f"{r['name_template']}: " + ", ".join(f"{p['name']} {_dom_text(p['dom'])}" for p in r["params"]) for r in rows if r["params"])


def _dom_text(d):
    if d["type"] == "int":
        return f"∈ [{d['lo']}, {d['hi'] if d['hi'] is not None else '∞'}]" + (f" без {d['excl']}" if d["excl"] else "")
    return f"∈ {'(' if d['lo_open'] else '['}{_fmt(d['lo'])}, {_fmt(d['hi']) if d['hi'] is not None else '∞'}{')' if d['hi_open'] else ']'}"


def fixed(mech_id, name):
    """Имя без параметров — запись каталога механики `mech_id` (halfstopf1, wall2x, …); иначе KeyError."""
    if not any(r["id"] == mech_id and not r["params"] and r["name_template"] == name for r in ROWS):
        raise KeyError(f"{name!r} — нет в каталоге механики {mech_id!r}")
    return name


def match(name):
    """Шаблон или каноническое имя формы -> запись каталога, чей шаблон и домены его допускают (иначе None)."""
    import re
    for r in ROWS:
        if r["name_template"] == name:
            return r
    for r in ROWS:
        rx = re.escape(r["name_template"])
        for p in r["params"]:
            rx = rx.replace(re.escape("{%s}" % p["name"]), "(?P<%s>-?[0-9.]+)" % p["name"])
        m = re.fullmatch(rx, name)
        if m and all(admits(p["dom"], float(m.group(p["name"]))) for p in r["params"]):
            return r
    return None


def r2_prefixes():
    """Начала имён форм, доступных только R2-бинарнику (ExitForm::is_r2)."""
    return sorted({r["prefix"] for r in ROWS if r["is_r2"]})


def is_r2_exit(name):
    """Имя `--exit-form` относится к R2 (по началу имени, как прежний R2_EXIT_RE)."""
    return any(name.startswith(p) for p in r2_prefixes())


def sample_ints(dom):
    lo, hi = dom["lo"], dom["hi"] if dom["hi"] is not None else dom["lo"] + 5
    pts = {lo, lo + 1, (lo + hi) // 2, hi - 1, hi}
    return [str(x) for x in sorted(pts) if lo <= x <= hi and x not in dom["excl"]]


def sample_reals(dom):
    lo = dom["lo"]
    hi = dom["hi"] if dom["hi"] is not None else lo + 10.0
    pts = [(lo + hi) / 2.0, lo + (hi - lo) / 4.0]
    if not dom["lo_open"]:
        pts.append(lo)
    if not dom["hi_open"] and dom["hi"] is not None:
        pts.append(hi)
    return [_fmt(x) for x in pts]


def sample_names(row):
    """Имена по точкам доменов — тем же алгоритмом, что `MechSpec::sample_names` в Rust (тест-мост)."""
    acc = [[]]
    for p in row["params"]:
        d = p["dom"]
        pts = sample_ints(d) if d["type"] == "int" else [_fmt(x) for x in d["values"]] if d["type"] == "set" else sample_reals(d)
        acc = [a + [x] for a in acc for x in pts]
    out = []
    for vals in acc:
        n = row["name_template"]
        for p, v in zip(row["params"], vals):
            n = n.replace("{%s}" % p["name"], v)
        out.append(n)
    return out
