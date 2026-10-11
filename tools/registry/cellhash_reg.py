#!/usr/bin/env python3
"""Конфиг и канонический хеш клеток Летописи (TK-186): таблица старый id -> канонический, выписка плана сетки.
  python tools/registry/cellhash_reg.py map  [--out docs/findings/cellhash-map-2026-10-11.csv]
  python tools/registry/cellhash_reg.py plan <сетка.jsonl>   # строка = полный/частичный конфиг клетки (cellhash.normalize)
Конфиг клетки Летописи собирается БЕЗ пересчёта данных: пул TK-040 — из имени формы + шапки группы (hdr_*, rtt, queue, h3);
остальные (TK-182/183: p12-r1/p12-ext/r2a/r3a/r3c) — из имени формы + набор B1 `t-bid-btc4h-q1` (P-10 §4) + спецификация
варианта p12-cells (family, axes) + исполнение «B1-деревья без пропуска занятости» (P-12: busy-skip off, closes portfolio-sim)."""
import csv, json, os, re, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellhash as ch
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REG = os.path.join(ROOT, "docs", "registry")
F = os.path.join(ROOT, "docs", "findings")
B1_SET = {"side": "bid", "min_age_s": 2700, "ctx": "btc4h_max=-44.55", "min_flow_pct": None, "usd_min": None, "signal": "approach",
          "frontrun_only": False, "driver": "setups", "lot": "usd:500x1", "entry_ttl": "60+300+1800", "post_only": True, "band_exit_bps": 20}
STD_EXEC = {"place_ns": 4200000, "cancel_ns": 3980000, "taker_ns": 5650000, "queue": "prob:3", "h3": "Notional", "hold_step": None}
B1_SIGMA = "study/sigma240"
B1_FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SET_BY_NAME = {"t-bid-btc4h-q1": B1_SET}


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


def config_of(c, var):
    """(конфиг, источник конфига) клетки реестра."""
    p = c.get("params") or {}
    g = p.get("group", "")
    if "hdr_side" in p:  # пул TK-040: шапка группы прогона
        h = lambda k: p.get("hdr_" + k)
        st = {"side": h("side"), "min_age_s": h("min_age_secs"), "ctx": h("ctx"), "min_flow_pct": h("min_flow_pct"),
              "usd_min": h("usd_min"), "signal": h("signal"), "frontrun_only": h("frontrun_only"), "driver": h("driver"),
              "lot": h("lot"), "entry_ttl": h("entry_ttl"), "post_only": h("entry_post_only"), "band_exit_bps": h("band_exit_bps")}
        m = re.fullmatch(r"p05-(?:f([\d_]+)-)?(?:a(\d+))?(?:-?u(\d+)k)?", c["set_name"].split("/", 1)[1])
        if g.startswith("p05") and m:  # p05: признаки набора (age в мин, flow в %, usd в тыс.) — в имени набора; шапка группы хранит только первый набор
            if m.group(2): st["min_age_s"] = int(m.group(2)) * 60
            if m.group(1): st["min_flow_pct"] = float(m.group(1).replace("_", "."))
            if m.group(3): st["usd_min"] = int(m.group(3)) * 1000
        ex = {**rtt(p["rtt_ns"]), "queue": c.get("queue"), "h3": c.get("h3_mode"), "hold_step": c.get("hold_step"), "busy_skip": "on"}
        return {"form": c["form"], "set": st, "exec": ex, "sigma": p.get("hdr_sigma_from")}, "шапка группы " + g
    name = c["set_name"].split("/", 1)
    cfg = {"form": c["form"], "set": B1_SET, "exec": {**STD_EXEC, "busy_skip": "off"}, "sigma": B1_SIGMA}
    if g.startswith("p12-r1"):  # клетка R1/R2 П-12: форма = имя клетки, вариант в спецификации
        if c["form"] == "B1":
            cfg["form"] = B1_FORM
            return cfg, "B1 (набор B1, P-10 §4)"
        cfg["form"] = B1_FORM
        if c["form"] in var:
            r = var[c["form"]]
            ax = ch.parse_axes(r["axes"]) if "=" in r["axes"] else {"cell": c["form"], "axes": r["axes"]}  # «source»: различает только имя клетки
            cfg["variant"] = {"family": r["family"], "axes": ax}
            return cfg, "p12-cells: B1 + вариант " + r["family"]
        # R1 (П-11 §3/П-12 §5): клетка = признак + квантиль порога; пороги — расширяющимся окном по месяцам (правило, не число)
        m = re.fullmatch(r"(g\d+)(?:-(.+))?-(k\d+|nz)", c["form"])
        cfg["variant"] = {"family": m.group(1), "axes": {"feature": m.group(2) or "", "q": m.group(3), "thr": "expanding-window"}}
        return cfg, "имя клетки R1 (признак, квантиль): B1 + порог признака"
    if name[0] in SET_BY_NAME or len(name) == 2 and name[1] in SET_BY_NAME:
        cfg["set"] = SET_BY_NAME[name[1]]
    return cfg, "имя формы + набор B1 (волна " + name[0] + ")"


def build():
    var = variants()
    rc = {d["cell_id"]: d for d in jl("run_cells.jsonl")}
    rows = []
    for c in jl("cells.jsonl"):
        cfg, src = config_of(c, var)
        rows.append((c["id"], ch.cell_hash(cfg), src, c["form"], c["set_name"], rc.get(c["id"], {}).get("run_id", ""), ch.canonical_json(cfg)))
    return rows


def cmd_map(out):
    rows = build()
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        w.writerow(["старый_id", "канонический_хеш", "источник_конфига", "форма", "набор", "прогон"])
        for r in rows:
            w.writerow(r[:6])
    by = collections.defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    dup = {h: v for h, v in by.items() if len(v) > 1}
    print(f"клеток {len(rows)}; канонических хешей {len(by)}; совпавших групп {len(dup)} (клеток в них {sum(len(v) for v in dup.values())})")
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
