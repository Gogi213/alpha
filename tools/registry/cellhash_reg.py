#!/usr/bin/env python3
"""Конфиг и канонический хеш клеток Летописи (TK-186): таблица старый id -> канонический, выписка плана сетки.
  python tools/registry/cellhash_reg.py map  [--out docs/findings/cellhash-map-2026-10-11.csv]
  python tools/registry/cellhash_reg.py plan <сетка.jsonl>   # строка = полный/частичный конфиг клетки (cellhash.normalize)
Конфиг клетки Летописи собирается БЕЗ пересчёта данных. Набор — из `--set` командных файлов прогонов на calc (не из имени
и не по умолчанию): пул TK-040 — docs/registry/files/tk040-sets-jan.txt (jall-jan-*.sh), e106-pop — tk115-delta-day.sh:38,
g87-fresh — r2-gen.py:21. Исполнение — из тех же команд (busy-skip off, hold-step skip, prob:3, рtt В-68, h3 notional).
Вселенная монет — версия пула прогона (+ исключения p12). map отказывает, если клетки с равным хешем разошлись в results."""
import csv, json, os, re, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellhash as ch
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REG = os.path.join(ROOT, "docs", "registry")
F = os.path.join(ROOT, "docs", "findings")
B1_SET = {"side": "bid", "min_age_s": 2700, "max_age_s": None, "ctx": "btc4h_max=-44.55", "min_flow_pct": None, "usd_min": None,
          "r1_born_shift_cbps_min": None, "signal": "approach", "frontrun_only": False, "driver": "setups", "lot": "usd:500x1",
          "entry_ttl": "60+300+1800", "post_only": True, "band_exit_bps": 20}
# флаги прогонов на calc (jall-jan-*.sh, poolc-c.sh, tk115-delta-day.sh) — одни и те же во всех клетках Летописи
STD_EXEC = {"place_ns": 4200000, "cancel_ns": 3980000, "taker_ns": 5650000, "queue": "prob:3", "h3": "Notional", "hold_step": "skip",
            "busy_skip": "off", "p95_place_ns": 4790000, "p95_cancel_ns": 4550000, "p95_taker_ns": 6420000, "h3_usd": 10000,
            "exit_group": "on", "approach_bps": 20, "touches_from": "study/approaches/D20", "verdict_csv": "tk044/final3/verdict.csv"}
B1_SIGMA = "study/sigma240"
B1_FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SETS_FILE = os.path.join(REG, "files", "tk040-sets-jan.txt")  # `--set` из /data/tk046/jan/home/alpha/tmp-p07/cells-by-day/jall-jan-2026-01-02.sh
EXTRA_SETS = {"e106-pop": "age=2700,side=bid,btc4h_max=-44.55,r1_born_shift_cbps_min=-9000000000000000000",
              "g87-fresh": "age=2700,agemax=3600,side=bid,btc4h_max=-44.55",
              "t-bid-btc4h-q1": "age=2700,side=bid,btc4h_max=-44.55"}
UNIVERSE = {"v171b": "v171b", "v171c": "v171c-drop:TRUMP,TRX,BCH"}  # исключения — params.source клеток p12


def set_of(spec):
    """Аргумент `--set имя:k=v,…` -> набор клетки: только названные ключи фильтруют, прочие None; сигнал/лот/ttl — шапка прогона."""
    kv = dict(x.split("=", 1) for x in spec.split(","))
    st = {k: None for k in B1_SET}
    st.update(signal="approach", frontrun_only=False, driver="setups", lot="usd:500x1", entry_ttl="60+300+1800", post_only=True, band_exit_bps=20)
    st["side"] = kv.get("side")
    st["min_age_s"] = int(kv["age"]) if "age" in kv else None
    if "agemax" in kv:
        st["max_age_s"] = int(kv["agemax"])
    for k, v in kv.items():
        if re.fullmatch(r"btc\d+h_max", k):
            st["ctx"] = f"{k}={v}"
    if "flow" in kv:
        st["min_flow_pct"] = float(kv["flow"])
    if "usd_min" in kv:
        st["usd_min"] = int(kv["usd_min"])
    if "r1_born_shift_cbps_min" in kv:
        st["r1_born_shift_cbps_min"] = int(kv["r1_born_shift_cbps_min"])
    for k in ch.OPT_SET_KEYS:  # A0: фильтры, которых раньше не было в ключе (заданные входят в хеш)
        if k in kv:
            st[k] = float(kv[k]) if k.startswith("eaten") else int(kv[k])
    for k, v in kv.items():  # A0: общий r1_<кол>_min|max (born_shift — выше, свой ключ)
        if k != "r1_born_shift_cbps_min" and ch._R1_KEY.fullmatch(k):
            st[k] = int(v)
    return st


def load_sets():
    d = {}
    with open(SETS_FILE, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                n, _, spec = ln.strip().partition(":")
                d[n] = spec
    for n, spec in EXTRA_SETS.items():
        d.setdefault(n, spec)
    return d


def jl(n):
    with open(os.path.join(REG, n), encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def variants():
    v = {}
    for fn in ("p12-cells-2026-10-06.csv", "p12-cells-ext-2026-10-07.csv"):
        with open(os.path.join(F, fn), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                v[r["cell"]] = r
    return v


def rtt(s):
    return {k + "_ns": v for k, v in (x.split("=") for x in s.split(","))}


def config_of(c, var, sets, run_pool):
    """(конфиг, источник конфига) клетки реестра."""
    p = c.get("params") or {}
    g = p.get("group", "")
    nm = c["set_name"].split("/", 1)[-1]
    uni = UNIVERSE[run_pool]
    if p.get("cfg_json"):  # клетка загружена по готовому конфигу плана (R2-B, load_r2b.py): конфиг хранится с клеткой
        return json.loads(p["cfg_json"]), "конфиг плана P-14 (params.cfg_json)"
    if "hdr_side" in p:  # пул TK-040: набор по имени из --set прогона; нет в файле — шапка группы
        if g.startswith("p05") and nm in sets:  # p05: имена наборов уникальны и несут признаки; в прочих группах набор t-bid-btc4h-q1 — метка, признаки в шапке группы
            st, src = set_of(sets[nm]), "--set прогона TK-040 (" + nm + ")"
        else:
            h = lambda k: p.get("hdr_" + k)
            st = {"side": h("side"), "min_age_s": h("min_age_secs"), "max_age_s": None, "ctx": h("ctx"), "min_flow_pct": h("min_flow_pct"),
                  "usd_min": h("usd_min"), "r1_born_shift_cbps_min": None, "signal": h("signal"), "frontrun_only": h("frontrun_only"),
                  "driver": h("driver"), "lot": h("lot"), "entry_ttl": h("entry_ttl"), "post_only": h("entry_post_only"),
                  "band_exit_bps": h("band_exit_bps")}
            src = "шапка группы " + g
        ex = {**STD_EXEC, **rtt(p["rtt_ns"]), "queue": c.get("queue"), "h3": c.get("h3_mode")}
        return {"form": c["form"], "set": st, "exec": ex, "sigma": p.get("hdr_sigma_from"), "universe": uni}, src
    cfg = {"form": c["form"], "set": B1_SET, "exec": dict(STD_EXEC), "sigma": B1_SIGMA, "universe": uni}
    if g.startswith("p12-"):  # клетка R1/R2 П-12: форма = имя клетки, вариант в спецификации
        if g == "p12-ext":  # форма записана полностью, вариант не нужен; партия отдельная: B1 разошёлся с p12-r1 (авг 398 vs 402 сделки)
            cfg["batch"] = g
            return cfg, "p12-ext: полное имя формы + набор B1"
        cfg["form"] = B1_FORM
        if c["form"] == "B1":
            return cfg, "B1 (набор B1, P-10 §4)"
        if c["form"] in var:
            r = var[c["form"]]
            ax = ch.parse_axes(r["axes"]) if "=" in r["axes"] else {"cell": c["form"], "axes": r["axes"]}  # «source»: различает только имя клетки
            cfg["variant"] = {"family": r["family"], "axes": ax}
            return cfg, "p12-cells: B1 + вариант " + r["family"]
        # R1 (П-11 §3/П-12 §5): клетка = признак + квантиль порога; пороги — расширяющимся окном по месяцам (правило, не число)
        m = re.fullmatch(r"(g\d+)(?:-(.+))?-(k\d+|nz)", c["form"])
        cfg["variant"] = {"family": m.group(1), "axes": {"feature": m.group(2) or "", "q": m.group(3), "thr": "expanding-window"}}
        return cfg, "имя клетки R1 (признак, квантиль): B1 + порог признака"
    if nm in sets:  # R2-A (g87-fresh) / e106-pop: --set из r2-gen.py / tk115-delta-day.sh
        cfg["set"] = set_of(sets[nm])
        return cfg, "--set " + nm + " (calc)"
    raise SystemExit("нет набора для клетки " + c["id"] + " " + c["set_name"])


def build():
    var, sets = variants(), load_sets()
    runs = {d["id"]: d for d in jl("runs.jsonl")}
    rc = {d["cell_id"]: d for d in jl("run_cells.jsonl")}
    rows = []
    for c in jl("cells.jsonl"):
        run = rc.get(c["id"], {}).get("run_id", "")
        cfg, src = config_of(c, var, sets, runs[run]["data_pool"] if run in runs else "v171b")
        rows.append((c["id"], ch.cell_hash(cfg), src, c["form"], c["set_name"], run, ch.canonical_json(cfg)))
    return rows


def check(rows):
    """Самопроверка: клетки с равным хешем обязаны дать равные trades/sum_net_bps на всех общих месяцах results.jsonl."""
    res = collections.defaultdict(dict)
    for d in jl("results.jsonl"):
        res[d["cell_id"]][d["month"]] = (d["trades"], round(float(d.get("ext", {}).get("sum_net_bps", 0) or 0), 3))
    by = collections.defaultdict(list)
    for r in rows:
        by[r[1]].append(r[0])
    bad = []
    for h, ids in by.items():
        for i in ids[1:]:
            for m in sorted(set(res[ids[0]]) & set(res[i])):
                if res[ids[0]][m] != res[i][m]:
                    bad.append((h, ids[0], i, m, res[ids[0]][m], res[i][m]))
    return bad


def cmd_map(out):
    rows = build()
    bad = check(rows)
    if bad:
        for b in bad[:20]:
            print("РАСХОЖДЕНИЕ", *b)
        sys.exit(f"самопроверка: {len(bad)} расхождений результатов при равном хеше — отказ")
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        w.writerow(["старый_id", "канонический_хеш", "источник_конфига", "форма", "набор", "прогон"])
        for r in rows:
            w.writerow(r[:6])
    by = collections.defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    dup = {h: v for h, v in by.items() if len(v) > 1}
    print(f"клеток {len(rows)}; канонических хешей {len(by)}; совпавших групп {len(dup)} (клеток в них {sum(len(v) for v in dup.values())}); самопроверка results: ок")
    for h, v in sorted(dup.items()):
        print(" ", h, len(v), [x[4] for x in v][:3])
    return rows


def cmd_plan(path, rows=None):
    """Выписка «в сетке / уже в Летописи / к счёту» по каноническому хешу."""
    have = {r[1]: r for r in (rows or build())}
    seen, done, todo = set(), [], []
    with open(path, encoding="utf-8") as f:
        cfgs = [json.loads(ln) for ln in f if ln.strip()]
    for cfg in cfgs:
        h = ch.cell_hash(cfg)
        if h in seen:
            continue
        seen.add(h)
        (done if h in have else todo).append(h)
    print(f"в сетке {len(seen)} уникальных; уже в Летописи {len(done)}; к счёту {len(todo)}")
    for h in done:
        print("  есть", h, "=", have[h][0], have[h][4])
    return done, todo


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["map"]:
        cmd_map(a[a.index("--out") + 1] if "--out" in a else os.path.join(F, "cellhash-map-2026-10-11.csv"))
    elif a[:1] == ["plan"] and len(a) == 2:
        cmd_plan(a[1])
    else:
        sys.exit(__doc__)
