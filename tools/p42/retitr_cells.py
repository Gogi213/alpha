"""П-42 §13.21 (TK-595): базы, список денежных клеток по стадиям, подсчёт испытаний, строки вариантов, хэши баз.

  python3 retitr_cells.py counts            # число денежных клеток по стадиям и сторонам (то, что стоит в §13.21)
  python3 retitr_cells.py bases             # точные строки баз и sha256 (то, что стоит в §13.21)
  python3 retitr_cells.py emit <L|S> <файл> [--stage <стадии>] [--accepted <JSON|путь|ключи>] [--features <json>]
        # без --stage — как раньше: база + все клетки E1, Els, NS, NT, T, P без накопленных значений.
        # --stage: одна или несколько стадий через запятую из E1, Els, NS, NT, E2, T, P, F, asm.
        # --accepted: накопленные принятые значения предыдущих стадий (JSON `accepted-<стадия>-<сторона>.json` из retitr_report.py, либо
        #   строка ключей `s=3 ls=1`): строка клетки = база + принятые ключи + изменение клетки; первая строка = <L|S>_base = база стадии
        #   (база + принятые) — по ней идёт парная Δ. Для asm первая строка — простая база стороны, затем <L|S>_C (сборка) и расширения Ext_*.
        # --features: JSON-список признаков для F: [{"name": "mv5", "fo": "lo", "cuts": {"10": 0.1, "20": 0.2, "25": 0.3}}]
        # --sig: в тот же файл — сигнальные двойники (`<id>_sig`, ключи SIGMODE, §13.21.1) клеток E1, Els, NS, NT, E2, T и базы (`<L|S>_base_sig`);
        # --sig-only: файл только из двойников (отдельный сигнальный прогон).
  python3 retitr_cells.py e2 <L|S> <S*|auto> <файл> [--accepted ...]  # совместная сетка E2 вокруг выбранного S* (по умолчанию — поверх базы + s=S*)

Клетка = одно изменение базы (оси «по одной»), кроме E2 (совместная сетка S × R × BE). Значение, равное значению базы, — контроль, не клетка.
Клетки E2, побайтово равные базе стадии (по эффективным ключам: база + принятые + изменение клетки), пропускаются — в Холм не идут.
Ключи движка v21 (новые: sn, sg, sb, sr, sd, tj, fa/fo/fv) — в протоколе §13.21.
Метаданные клетки (`spec_from_id`) читает retitr_report.py: вид оси (упорядоченная / пара / одиночная / совместная сетка), стоп-семейство.
Несколько признаков F в сборке пишутся повторяющимися тройками `fa= fo= fv=` — синтаксис нескольких признаков движка v21 сверить до запуска asm.
"""
import hashlib
import json
import os
import re
import sys

COMMON = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0"
BASES = {
    # основная база стороны (единственная, по которой идёт отбор)
    "L": COMMON + " rf=0.2 rs=20",
    "S": COMMON + " tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05",
}
CONTROLS = {  # помечены «контроль», не испытания, не участвуют в отборе
    "L_ow_e": COMMON,
    "S_ow_e": COMMON,
    "S_es15": COMMON + " e=0.15",
}
BM_BASE = {"L": 0, "S": 1}     # режим безубытка базы (bm): лонг — 0 (после 1-го тейка), шорт — 1 (выкл.)
S_GRID = [1, 1.5, 2, 3, 4, 5, 6, 8]  # 5 — база; 7 клеток
S_EXT = [0.5, 12]              # расширения за краем (одна клетка на край, если выбор на краю)
R_GRID = [1, 2, 3]             # 2 — база
S_EDGE_EXT = {"low": S_EXT[0], "high": S_EXT[1]}

# Сигнальный режим (§13.21.1 п. 3): позиции `p` без ограничения, все паузы выключены, `busy=0` (ключ движка v21, по умолчанию 1); остальные ключи — как у клетки.
# Парная Δ стадий E1, Els, NS, NT, E2, T читается на одинаковых сигналах; клетки P, F и сборка — только портфельные.
SIGMODE = "p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=0"
SIG_STAGES = ("E1", "Els", "NS", "NT", "E2", "T")
SIG_SUFFIX = "_sig"   # id сигнального двойника клетки: <id>_sig; двойник базы — <L|S>_base_sig

# значения ключей по умолчанию (движок, params.rs / §13.21): для сравнения «побайтово равно базе» по эффективным ключам
DEFAULTS = {"s": 5.0, "rr": 2.0, "bm": 0.0, "ls": 0.0, "tt": 0.0, "tb": 0.0, "rf": 0.0, "rs": 0.0, "e2": 0.0, "e": 0.3,
            "t_high": 5.0, "q": 50.0, "natr_min": 0.4, "t1h": 0.5, "t24": 0.0, "sn": 0.0, "tj": 0.0}
MULTI = ("fa", "fo", "fv")     # признак F: тройка fa= fo= fv= (может повторяться в сборке)

STAGES = [  # (id, число клеток, описание)
    ("E1", 7, "жёсткий стоп S, % : 1, 1,5, 2, 3, 4, 6, 8 (база 5)"),
    ("E-ls", 1, "стоп за дальним краем зоны: ls = 1 NATR"),
    ("E2", 17, "совместная сетка S ∈ {соседи выбранного S* в E1, S*} × R ∈ {1, 2, 3} × BE ∈ {база, альтернатива} (18 клеток минус клетка, равная базе стадии)"),
    ("NS", 5, "стоп в NATR, семейства: const k0=3; const k0=5; линейно по времени k0=3, β=1/ч; геометрически k0=3, ρ=1,5 (BE выкл.); шаг после тейка k0=3, δ=0,5 (BE выкл.)"),
    ("NT", 1, "тейки: шаг после каждого тейка δ=0,25 (оставшиеся ступени × (1+δ·j), перестановка со следующей сделки)"),
    ("T", 6, "тейки и выходы: t_high ∈ {3, 8}; q ∈ {25, 75}; rf ∈ {0,1; 0,3}"),
    ("P", 6, "пул: natr_min ∈ {0,3; 0,6}; t1h ∈ {1; 2} млн; t24 ∈ {100; 500} млн"),
    ("F", 15, "признаки: ≤ 5 признаков × 3 среза (отбросить худшие 10 / 20 / 25 % кандидатов сигнала)"),
    ("ext+asm", 2, "расширение S за край (1 клетка) + сборка кандидата (1 клетка)"),
]
TOOL_NAME = {"E1": "E1", "E-ls": "Els", "E2": "E2", "NS": "NS", "NT": "NT", "T": "T", "P": "P", "F": "F", "ext+asm": "asm"}
STAGE_ORDER = ["E1", "Els", "NS", "NT", "E2", "T", "P", "F", "asm"]   # порядок счёта (Els, NS, NT — одна стадия по сути, E2 после них)


# ---- ключи: разбор, слияние, эффективное сравнение -------------------------------------------------------------------

def _num(v):
    try:
        return float(v)
    except ValueError:
        return v


def parse_keys(s):
    """Строка `k=v k=v` → (список обычных ключей [(k, v)] без повторов (последнее значение), список признаков F [{fa, fo, fv}])."""
    plain, feats = {}, []
    for tok in (s or "").split():
        k, _, v = tok.partition("=")
        if not _:
            raise ValueError("ожидалось ключ=значение: %r" % tok)
        if k == "fa":
            feats.append({"fa": v})
        elif k in ("fo", "fv"):
            if not feats:
                raise ValueError("%s без fa" % k)
            feats[-1][k] = v
        else:
            plain[k] = v  # последнее значение главнее, место — по первому появлению
    return list(plain.items()), feats


def merge_keys(*parts):
    """Слияние строк ключей слева направо: значение ключа — последнее; место — по первому появлению; признаки F — тройки (та же fa+fo — заменяется)."""
    plain, feats = {}, []
    for p in parts:
        pl, ft = parse_keys(p)
        for k, v in pl:
            plain[k] = v
        for f in ft:
            for i, g in enumerate(feats):
                if g.get("fa") == f.get("fa") and g.get("fo") == f.get("fo"):
                    feats[i] = f
                    break
            else:
                feats.append(f)
    toks = ["%s=%s" % kv for kv in plain.items()]
    for f in feats:
        toks += ["%s=%s" % (k, f[k]) for k in MULTI if k in f]
    return " ".join(toks)


def canon(s):
    """Эффективная конфигурация: ключи со значением по умолчанию отбрасываются; числа сравниваются как числа. Для сравнения с базой стадии."""
    pl, ft = parse_keys(s)
    out = {}
    for k, v in pl:
        nv = _num(v)
        if k in DEFAULTS and nv == DEFAULTS[k]:
            continue
        out[k] = nv
    return (tuple(sorted(out.items())), tuple(sorted(tuple(sorted(f.items())) for f in ft)))


def line(side, cid, keys, acc=""):
    """Строка варианта: id + база + принятые ключи + изменение клетки (повторы ключей схлопнуты, последнее значение главнее)."""
    return ("%s %s" % (cid, merge_keys(BASES[side], acc, keys))).strip()


# ---- клетки ---------------------------------------------------------------------------------------------------------

def _ordered(stage, cid, keys, axis, value, family=None, ext=None):
    return dict(id=cid, keys=keys, stage=stage, kind="ordered", group=axis, coords={axis: value}, ordered_axes=[axis], group_axes=[],
                family=family, ext=ext)


def _pair(stage, cid, keys, axis, value):
    """Двузначная ось: по одному значению с каждой стороны базы — соседей нет, принятие Δ_A > 0 «без плато» (§13.21)."""
    return dict(id=cid, keys=keys, stage=stage, kind="pair", group=axis, coords={axis: value}, ordered_axes=[], group_axes=[], family=None, ext=None)


def _single(stage, cid, keys, family=None):
    return dict(id=cid, keys=keys, stage=stage, kind="single", group=cid, coords={}, ordered_axes=[], group_axes=[], family=family, ext=None)


def simple_specs():
    """Клетки стадий, которые не зависят от результатов, с метаданными."""
    out = []
    for s in S_GRID:
        if s != 5:
            out.append(_ordered("E1", "E1_s%g" % s, "s=%g" % s, "s", float(s), ext=S_EDGE_EXT))
    out.append(_single("Els", "Els_ls1", "ls=1", family="stop"))
    out += [_ordered("NS", "NS_c3", "sn=3 sg=0", "sn", 3.0, family="stop"),
            _ordered("NS", "NS_c5", "sn=5 sg=0", "sn", 5.0, family="stop"),
            _single("NS", "NS_lin", "sn=3 sg=1 sb=1", family="stop"),
            _single("NS", "NS_geo", "sn=3 sg=2 sr=1.5 bm=1", family="stop"),
            _single("NS", "NS_step", "sn=3 sg=3 sd=0.5 bm=1", family="stop")]
    out.append(_single("NT", "NT_step", "tj=0.25"))
    out += [_pair("T", "T_th3", "t_high=3", "t_high", 3.0), _pair("T", "T_th8", "t_high=8", "t_high", 8.0),
            _pair("T", "T_q25", "q=25", "q", 25.0), _pair("T", "T_q75", "q=75", "q", 75.0),
            _pair("T", "T_rf01", "rf=0.1", "rf", 0.1), _pair("T", "T_rf03", "rf=0.3", "rf", 0.3)]
    out += [_pair("P", "P_n03", "natr_min=0.3", "natr_min", 0.3), _pair("P", "P_n06", "natr_min=0.6", "natr_min", 0.6),
            _pair("P", "P_t1", "t1h=1", "t1h", 1.0), _pair("P", "P_t2", "t1h=2", "t1h", 2.0),
            _pair("P", "P_t24_100", "t24=100", "t24", 100.0), _pair("P", "P_t24_500", "t24=500", "t24", 500.0)]
    return out


def cells_simple():
    """Клетки стадий, которые не зависят от результатов: (id, строка ключей поверх базы)."""
    return [(c["id"], c["keys"]) for c in simple_specs()]


_RE_E2 = re.compile(r"^E2_s([\d.]+)_r([\d.]+)_bm(\d)$")
_RE_F = re.compile(r"^F_(.+)_(lo|hi)_(\d+)$")
_RE_EXT = re.compile(r"^Ext_s([\d.]+)$")


def spec_from_id(cid):
    """Метаданные клетки по её id (для retitr_report.py); None — неизвестный id. Ключи F не в id (`keys` = None) — берутся из файла вариантов."""
    for c in simple_specs():
        if c["id"] == cid:
            return c
    m = _RE_E2.match(cid)
    if m:
        s, r, bm = float(m.group(1)), float(m.group(2)), int(m.group(3))
        return dict(id=cid, keys="s=%g rr=%g bm=%d" % (s, r, bm), stage="E2", kind="grid", group="E2",
                    coords={"s": s, "rr": r, "bm": float(bm)}, ordered_axes=["s", "rr"], group_axes=["bm"], family=None, ext=None)
    m = _RE_F.match(cid)
    if m:
        name, fo, pct = m.group(1), m.group(2), float(m.group(3))
        return dict(id=cid, keys=None, stage="F", kind="ordered", group="F:%s:%s" % (name, fo), coords={"cut": pct}, ordered_axes=["cut"],
                    group_axes=[], family=None, ext=None)
    m = _RE_EXT.match(cid)
    if m:
        return _single("asm", cid, "s=%s" % m.group(1))
    if cid in ("L_C", "S_C"):
        return dict(id=cid, keys="", stage="asm", kind="asm", group=cid, coords={}, ordered_axes=[], group_axes=[], family=None, ext=None)
    return None


def cell_order():
    """Порядок печати клеток: по списку simple_specs, прочие — по алфавиту после них."""
    return [c["id"] for c in simple_specs()]


def e2_s_values(s_star):
    """S* и два соседа по посчитанной сетке E1 (7 клеток, база 5 не посчитана и в соседи не идёт — правило соседей §13.21); на краю сетки — один сосед."""
    grid = [s for s in S_GRID if s != 5]
    lo = [s for s in grid if s < s_star]
    hi = [s for s in grid if s > s_star]
    return ([lo[-1]] if lo else []) + [s_star] + ([hi[0]] if hi else [])


def e2_cells(side, s_star, acc_keys=None):
    """Совместная сетка E2: s ∈ {S* и соседи по сетке E1} × rr ∈ {1, 2, 3} × bm ∈ {база стороны, альтернатива}; поверх базы + принятых ключей.
    acc_keys=None — подразумевается принятый в E1 `s=S*`. Клетки, побайтово равные базе стадии (по эффективным ключам), пропускаются."""
    if acc_keys is None:
        acc_keys = "s=%g" % s_star
    bm_alt = 1 if BM_BASE[side] == 0 else 0
    base_c = canon(merge_keys(BASES[side], acc_keys))
    out = []
    for s in e2_s_values(s_star):
        for r in R_GRID:
            for bm in (BM_BASE[side], bm_alt):
                keys = "s=%g rr=%g bm=%d" % (s, r, bm)
                if canon(merge_keys(BASES[side], acc_keys, keys)) == base_c:
                    continue  # равна базе стадии (в E1 посчитана как клетка S*, или это сама база)
                out.append(("E2_s%g_r%g_bm%d" % (s, r, bm), keys))
    return out


def f_cells(features):
    """Клетки F: по признаку и направлению — три среза; ключи `fa=<имя> fo=<lo|hi> fv=<значение>`."""
    out = []
    for f in features:
        for pct in sorted(f["cuts"], key=float):
            out.append(("F_%s_%s_%s" % (f["name"], f["fo"], pct), "fa=%s fo=%s fv=%s" % (f["name"], f["fo"], f["cuts"][pct])))
    return out


# ---- накопленные принятые значения ------------------------------------------------------------------------------------

def load_accepted(arg):
    """JSON-файл `accepted-*.json`, JSON-строка или строка ключей → dict(cumulative_keys, extend, side)."""
    if not arg:
        return dict(cumulative_keys="", extend=[], side=None)
    if os.path.exists(arg):
        d = json.load(open(arg, encoding="utf-8"))
    elif arg.lstrip().startswith("{"):
        d = json.loads(arg)
    else:
        d = dict(cumulative_keys=arg)
    return dict(cumulative_keys=d.get("cumulative_keys", ""), extend=d.get("extend", []), side=d.get("side"))


def stage_rows(side, stage, acc, features=None):
    """Строки (id, ключи изменения) одной стадии поверх базы + принятых ключей acc."""
    sp = simple_specs()
    if stage in ("E1", "Els", "NS", "NT", "T", "P"):
        return [(c["id"], c["keys"]) for c in sp if c["stage"] == stage]
    if stage == "E2":
        s_star = _num(dict(parse_keys(acc)[0]).get("s", "5"))
        return e2_cells(side, float(s_star), acc)
    if stage == "F":
        if not features:
            raise SystemExit("F: нужен --features <json>")
        return f_cells(features)
    raise SystemExit("неизвестная стадия %r (E1, Els, NS, NT, E2, T, P, F, asm)" % stage)


def emit(side, stages, path, acc_arg=None, features=None, sig="no"):
    """Пишет файл вариантов: первая строка — база стадии (база + принятые); далее клетки стадий. Возвращает число клеток (без базы и двойников).
    sig: "no" — только портфельные строки; "both" — плюс сигнальные двойники (`<id>_sig`, ключи SIGMODE) клеток стадий E1, Els, NS, NT, E2, T и базы;
    "only" — только двойники (отдельный сигнальный прогон). Клетка в Холме считается один раз независимо от режима."""
    acc_d = load_accepted(acc_arg)
    acc = acc_d["cumulative_keys"]
    if acc_d["side"] and {"long": "L", "short": "S"}.get(acc_d["side"], acc_d["side"]) != side:
        raise SystemExit("--accepted другой стороны: %s" % acc_d["side"])
    rows, sig_rows, base_row = [], [], None
    for st in stages:
        if st == "asm":
            base_row = line(side, "%s_base" % side, "")           # простая база: Δ сборки против неё
            rows.append(("%s_C" % side, acc))                     # сборка C (+1 к Холму)
            for e in acc_d["extend"]:
                rows.append(("Ext_%s%g" % (e["axis"], e["value"]), "%s=%g" % (e["axis"], e["value"])))
        else:
            rs = stage_rows(side, st, acc, features)
            rows += rs
            if st in SIG_STAGES:
                sig_rows += rs
    if base_row is None:
        base_row = line(side, "%s_base" % side, "", acc)          # база стадии
    plain = [base_row] + [line(side, c, k, acc) for c, k in rows]
    twins = []
    if sig != "no" and sig_rows:
        sm = merge_keys(acc, SIGMODE)
        twins = [line(side, "%s_base%s" % (side, SIG_SUFFIX), "", sm)] + [line(side, c + SIG_SUFFIX, k, sm) for c, k in sig_rows]
    lines = (twins if sig == "only" else plain + twins)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return len(rows)


def counts():
    tot = 0
    for sid, n, d in STAGES:
        print("%-8s %3d  %s%s" % (sid, n, d, "" if TOOL_NAME[sid] == sid else "  [стадия в инструментах: %s]" % TOOL_NAME[sid]))
        tot += n
    print("итого денежных клеток на сторону (верхняя граница):", tot)
    print("клеток по списку без E2/F/ext:", len(cells_simple()))
    for side in ("L", "S"):
        print("E2 при S*=3:", side, len(e2_cells(side, 3)), "при S*=5:", len(e2_cells(side, 5)), "при S*=8 (край):", len(e2_cells(side, 8)))


def _opt(argv, name):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return None


if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "counts"
    if cmd == "counts":
        counts()
    elif cmd == "bases":
        for k, v in list(BASES.items()) + list(CONTROLS.items()):
            print(k, hashlib.sha256(v.encode()).hexdigest()[:16], v)
    elif cmd == "emit":
        rest = argv[1:]
        sig = "both" if "--sig" in rest else ("only" if "--sig-only" in rest else "no")
        rest = [x for x in rest if x not in ("--sig", "--sig-only")]
        stage_arg, acc_arg, feat_arg = _opt(rest, "--stage"), _opt(rest, "--accepted"), _opt(rest, "--features")
        side, path = rest[0], rest[1]
        stages = stage_arg.split(",") if stage_arg else ["E1", "Els", "NS", "NT", "T", "P"]
        feats = json.load(open(feat_arg, encoding="utf-8")) if feat_arg else None
        n = emit(side, stages, path, acc_arg, feats, sig)
        print(n, "клеток + база")
    elif cmd == "e2":
        rest = argv[1:]
        acc_arg = _opt(rest, "--accepted")
        side, s_arg, path = rest[0], rest[1], rest[2]
        if acc_arg is None:
            cs = e2_cells(side, float(s_arg))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(line(side, c, k) for c, k in cs) + "\n")
            print(len(cs), "клеток E2")
        else:
            s_acc = float(dict(parse_keys(load_accepted(acc_arg)["cumulative_keys"])[0]).get("s", "5"))
            if s_arg not in ("auto", "-") and float(s_arg) != s_acc:
                raise SystemExit("S* = %s не совпадает с s=%g в --accepted" % (s_arg, s_acc))
            n = emit(side, ["E2"], path, acc_arg)
            print(n, "клеток E2 + база стадии; S* = %g" % s_acc)
    else:
        raise SystemExit(__doc__)
