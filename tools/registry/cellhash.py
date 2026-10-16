"""Канонический хеш клетки Летописи (TK-186, В-213/В-224): одна функция `cell_hash(cfg)` = sha256 нормализованного
ПОЛНОГО конфига клетки. Один и тот же конфиг, записанный разными путями (имя формы / шапка прогона / спецификация
сетки), даёт один хеш; происхождение (группа, источник, имя набора, путь σ-каталога) в хеш не входит.

Конфиг (все ключи есть всегда, отсутствие = None):
  logic      версия логики/семантики бэктеста (`bounce-v1`);
  entry      форма входа: single@fr[+k] | market | ladder{n,lo,hi,unit,w};
  stop,take  разобранные части имени (pct2 -> bps 200; tr1x1 -> trail 1,1; 1to1 -> ratio 1,1);
  deadline_s, ttl (секунды|wall|touch), early_s (выход по прилипанию), exit (форма выхода: токены имени);
  set        фильтры/параметры набора: side, min_age_s, ctx, min_flow_pct, usd_min, signal, frontrun_only, driver,
             lot, entry_ttl (цепочка), post_only, band_exit_bps, max_age_s (agemax), r1_born_shift_cbps_min (фильтр e106);
  sigma      каталог σ (последние два компонента пути) | None;
  exec       латентность place/cancel/taker (нс) и их p95 (--p95-rtt-ns), queue, h3 (+h3_usd), hold_step, busy_skip, exit_group,
             approach_bps и источник касаний (touches_from), файл брака (verdict_csv: последние два компонента пути);
  universe   вселенная монет: версия пула + исключения (`v171b`, `v171c-drop:TRUMP,TRX,BCH`) — одна клетка на разных
             вселенных даёт разные результаты, поэтому в ключе; batch — партия прогона (только p12-ext: самопроверка
             cellhash_reg.check нашла расхождение результатов с p12-r1 при равном конфиге);
  variant    спецификация R1/R2-варианта, если он не кодируется именем формы: {family, axes}.
Нормализация: числа — один вид (200.0 == 200), `Some(2700)` -> 2700, `None` -> null, `halfstop` == `halfstopf2`.
"""
import hashlib, json, re

LOGIC = "bounce-v1"
_TOK = re.compile(r"[A-Za-z@]+|\d+(?:\.\d+)?|\.\.|\+")


def num(x):
    """Число в одном виде: целое, если целое; строки 'Some(2700)'/'None'/'true' — в значения."""
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, (list, tuple)):  # уже нормализованное значение (ctx = [признак, порог]) — повторная нормализация идемпотентна
        return [num(v) for v in x]
    if isinstance(x, (int, float)):
        return int(x) if float(x).is_integer() else float(x)
    s = str(x).strip()
    if s in ("", "None", "none", "null"):
        return None
    m = re.fullmatch(r"Some\((.*)\)", s)
    if m:
        s = m.group(1)
    if s in ("true", "false"):
        return s == "true"
    try:
        return num(float(s))
    except ValueError:
        return s


def tokens(s):
    """Имя формы -> список токенов (буквы как есть, числа нормализованы): `gone30wallk0` -> [gone,30,wallk,0]."""
    return [num(t) if re.match(r"\d", t) else t for t in _TOK.findall(s)]


def parse_entry(s):
    if s == "market":
        return {"kind": "market"}
    m = re.fullmatch(r"single@fr(?:\+(\d+))?", s)
    if m:
        return {"kind": "single@fr", "k": num(m.group(1) or 0)}
    m = re.fullmatch(r"ladder(\d+)x([\d.]+)\.\.([\d.]+)(s?)(?:w(\d+))?", s)
    if m:
        return {"kind": "ladder", "n": num(m.group(1)), "lo": num(m.group(2)), "hi": num(m.group(3)),
                "unit": "sigma" if m.group(4) else "bps", "w": num(m.group(5))}
    m = re.fullmatch(r"retreat(\d+)t(\d+)", s)  # Г-102: цена входа как у single@fr, k тиков, полураспад T мин
    if m:
        return {"kind": "retreat", "k": num(m.group(1)), "t": num(m.group(2))}
    return {"kind": "raw", "label": s}


def parse_stop(s):
    m = re.fullmatch(r"pct([\d.]+)", s)
    return {"kind": "pct_bps", "v": num(float(m.group(1)) * 100)} if m else {"kind": "raw", "t": tokens(s)}


def parse_take(s):
    m = re.fullmatch(r"tr([\d.]+)x([\d.]+)", s)
    if m:
        return {"kind": "trail", "a": num(m.group(1)), "b": num(m.group(2))}
    m = re.fullmatch(r"([\d.]+)to([\d.]+)", s)
    if m:
        return {"kind": "ratio", "a": num(m.group(1)), "b": num(m.group(2))}
    return {"kind": "raw", "t": tokens(s)}


def parse_exit(s):
    if s is None or s == "none":
        return None
    m = re.fullmatch(r"(halfstop|halflevel)(f[13])?", s)  # без суффикса = половина = f2
    if m:
        return [m.group(1), "f", {"f1": 1, "f3": 3}.get(m.group(2), 2)]
    return tokens(s)


def parse_form(form):
    """Имя формы `<вход>-<стоп>-<тейк>-<H>[-ttl<..>][-early<..>][-<выход>]` (как bounce_verdict::parse_form_fields)."""
    p = form.split("-")
    entry = "single@fr"
    if len(p) >= 4 and parse_entry(p[0])["kind"] != "raw":
        entry, p = p[0], p[1:]
    out = {"entry": parse_entry(entry), "stop": parse_stop(p[0]), "take": parse_take(p[1]), "deadline_s": num(p[2]),
           "ttl": None, "early_s": None, "exit": None}
    for t in p[3:]:
        if t.startswith("ttl"):
            out["ttl"] = num(t[3:])
        elif t.startswith("early"):
            out["early_s"] = num(t[5:])
        else:
            out["exit"] = parse_exit(t)
    return out


def sigma_key(path):
    """Каталог σ: последние два компонента (`study/sigma240`) — место на диске в семантику не входит."""
    if not path:
        return None
    return "/".join(str(path).replace("\\", "/").rstrip("/").split("/")[-2:])


SET_KEYS = ("side", "min_age_s", "max_age_s", "ctx", "min_flow_pct", "usd_min", "r1_born_shift_cbps_min", "signal",
            "frontrun_only", "driver", "lot", "entry_ttl", "post_only", "band_exit_bps")
EXEC_KEYS = ("place_ns", "cancel_ns", "taker_ns", "p95_place_ns", "p95_cancel_ns", "p95_taker_ns", "queue", "h3", "h3_usd",
             "hold_step", "busy_skip", "exit_group", "approach_bps", "touches_from", "verdict_csv")
# A0 (TK-199, В-227): ключи FilterSet, которых не было в SET_KEYS. В канон-JSON входят ТОЛЬКО заданные (None пропускается):
# иначе все 487 прежних хешей сменились бы. `r1_<кол>_min|max` (кроме r1_born_shift_cbps_min — он в SET_KEYS) сводятся в set.r1.
OPT_SET_KEYS = ("eaten", "eaten_min", "frontrun_min", "behind_min", "stack_min", "lot_first")
_R1_KEY = re.compile(r"r1_(.+)_(min|max)")
PATH_EXEC_KEYS = ("touches_from", "verdict_csv")  # путь машины в ключ не входит: последние два компонента


def _deep(x):
    """Числа во вложенных словарях/списках — в один вид (200.0 == 200)."""
    if isinstance(x, dict):
        return {k: _deep(v) for k, v in sorted(x.items())}
    if isinstance(x, (list, tuple)):
        return [_deep(v) for v in x]
    return num(x) if isinstance(x, float) else x


def _r1_bounds(st):
    """Границы колонок R1 набора -> отсортированный [[кол, min, max], …] (нет границы = None); пусто -> []."""
    b = {}
    for col, mn, mx in (st.get("r1") or []):
        b[col] = [num(mn), num(mx)]
    for k, v in st.items():
        m = _R1_KEY.fullmatch(k)
        if m and k != "r1_born_shift_cbps_min" and num(v) is not None:
            b.setdefault(m.group(1), [None, None])[m.group(2) == "max"] = num(v)
    return [[c, *b[c]] for c in sorted(b)]


def normalize(cfg):
    """Любой частичный конфиг -> полный нормализованный (ключи на месте, значения в одном виде)."""
    form = cfg.get("form")
    parsed = parse_form(form) if form else {}
    out = {"logic": cfg.get("logic", LOGIC)}
    for k in ("entry", "stop", "take", "deadline_s", "ttl", "early_s", "exit"):
        out[k] = cfg[k] if k in cfg else parsed.get(k)
        if k in ("deadline_s", "ttl", "early_s"):
            out[k] = num(out[k])
    out["set"] = {k: num((cfg.get("set") or {}).get(k)) for k in SET_KEYS}
    st = cfg.get("set") or {}
    for k in OPT_SET_KEYS:
        if num(st.get(k)) is not None:
            out["set"][k] = num(st[k])
    r1 = _r1_bounds(st)
    if r1:
        out["set"]["r1"] = r1
    if isinstance(out["set"]["ctx"], str):
        a, _, b = out["set"]["ctx"].partition("=")
        out["set"]["ctx"] = [a, num(b)]
    out["sigma"] = sigma_key(cfg.get("sigma"))
    out["exec"] = {k: sigma_key(v) if k in PATH_EXEC_KEYS else num(v) for k, v in ((k, (cfg.get("exec") or {}).get(k)) for k in EXEC_KEYS)}
    v = cfg.get("variant")
    out["universe"] = cfg.get("universe")
    out["batch"] = cfg.get("batch")
    out["variant"] = {"family": v["family"], "axes": {k: num(x) for k, x in sorted(v["axes"].items())}} if v else None
    return _deep(out)


def canonical_json(cfg):
    return json.dumps(normalize(cfg), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def cell_hash(cfg):
    """Канонический хеш клетки: sha256(нормализованный полный конфиг)[:16]."""
    return hashlib.sha256(canonical_json(cfg).encode()).hexdigest()[:16]


def parse_axes(axes):
    """`N=3;u=1/3;K=5` -> {N:3,u:'1/3',K:5} (значения: число или строка как есть)."""
    return {k.strip(): num(v) for k, _, v in (a.partition("=") for a in axes.split(";") if a.strip())}
